"""Interface en ligne de commande DataPulse V3."""

from __future__ import annotations

import argparse
import sys
import tracemalloc
from pathlib import Path
from typing import Any

import pandas as pd

from datapulse.benchmark import evaluate_against_reference
from datapulse.config_loader import ConfigError, load_domain_config
from datapulse.detector import detect_anomalies
from datapulse.exceptions import DataPulseError, ExitCode
from datapulse.ingestion import ingest_file, iter_file_chunks
from datapulse.models import RunMeta
from datapulse.observability import PipelineTracker, get_structured_logger
from datapulse.profiler import StreamingProfiler, detect_schema_drift, profile_dataframe
from datapulse.reconciler import reconcile_findings
from datapulse.reporter import generate_reports
from datapulse.storage import DataPulseStorage
from datapulse.validator import validate_dataframe


def run_pipeline(
    file_path: str,
    domain: str,
    ref_path: str | None = None,
    db_path: str = "db/datapulse.sqlite",
    reports_dir: str = "reports",
    processed_dir: str = "data/processed",
    *,
    strict: bool = False,
    json_logs: bool = False,
    chunk_size: int | None = None,
) -> int:
    logger = get_structured_logger(enabled=json_logs)
    tracker = PipelineTracker(pipeline_name=f"datapulse.{domain}", logger=logger)
    run_id = tracker.telemetry.run_id
    timestamp = tracker.telemetry.started_at

    tracemalloc.start()
    try:
        with tracker.track_step("config_load"):
            config = load_domain_config(domain)

        storage = DataPulseStorage(db_path=db_path)
        previous_profile = storage.get_previous_run_profile(domain, exclude_run_id=run_id)

        if chunk_size is not None:
            # Mode Streaming : profilage et validation par chunks à empreinte mémoire constante
            with tracker.track_step("streaming_ingestion_profile_validate") as step:
                raw_copy_path, file_hash, chunk_iter = iter_file_chunks(
                    file_path, chunk_size=chunk_size
                )
                s_profiler = StreamingProfiler()
                all_violations: list[dict[str, Any]] = []
                sample_frames: list[pd.DataFrame] = []
                sampled_rows = 0
                max_sample_rows = 50_000

                for offset, chunk in chunk_iter:
                    s_profiler.update(chunk)
                    chunk_violations = validate_dataframe(chunk, config)
                    for v in chunk_violations:
                        v["row_index"] = int(v["row_index"]) + offset
                        all_violations.append(v)
                    if sampled_rows < max_sample_rows:
                        take = chunk.iloc[: max_sample_rows - sampled_rows]
                        sample_frames.append(take)
                        sampled_rows += len(take)

                profile = s_profiler.finalize()
                row_count = int(profile["row_count"])
                df = (
                    pd.concat(sample_frames, ignore_index=True) if sample_frames else pd.DataFrame()
                )
                violations = all_violations
                step.rows_in = row_count
                step.rows_out = row_count
        else:
            with tracker.track_step("ingestion") as step:
                ingestion = ingest_file(file_path)
                df = ingestion.dataframe
                raw_copy_path = ingestion.raw_copy_path
                file_hash = ingestion.file_hash_sha256
                row_count = ingestion.row_count
                step.rows_in = row_count
                step.rows_out = row_count

            with tracker.track_step("profiling", rows_in=row_count) as step:
                profile = profile_dataframe(df)
                step.rows_out = len(profile.get("columns", []))

            with tracker.track_step("validation", rows_in=row_count) as step:
                violations = validate_dataframe(df, config)
                step.rows_out = len(violations)

        with tracker.track_step("anomaly_detection", rows_in=len(df)) as step:
            anomalies, warnings = detect_anomalies(df, config)
            step.rows_out = len(anomalies)

        with tracker.track_step("reconciliation", rows_in=len(df)) as step:
            findings, annotated_df = reconcile_findings(df, violations, anomalies)
            step.rows_out = len(findings)

        drifts: list[dict[str, Any]] = []
        if previous_profile is not None:
            _, baseline_cols = previous_profile
            drifts = detect_schema_drift(profile, baseline_cols)
            for d in drifts:
                warnings.append(f"[SCHEMA DRIFT] {d['explanation']}")

        with tracker.track_step("export_processed", rows_in=len(annotated_df)) as step:
            out_processed = Path(processed_dir)
            out_processed.mkdir(parents=True, exist_ok=True)
            processed_file = out_processed / f"{domain}_{run_id[:8]}_annotated.csv"
            annotated_df.to_csv(processed_file, index=False)
            step.rows_out = len(annotated_df)

        benchmark_metrics = None
        if ref_path:
            with tracker.track_step("benchmark", rows_in=row_count):
                benchmark_metrics = evaluate_against_reference(df, ref_path, findings, domain)

        telemetry = tracker.finalize()
        _, peak_bytes = tracemalloc.get_traced_memory()
        peak_memory_kb = round(peak_bytes / 1024, 2)

        status = "QUALITY_ALERT" if (strict and (findings or drifts)) else "SUCCESS"
        meta_obj = RunMeta(
            run_id=run_id,
            timestamp=timestamp,
            domain=domain,
            source_file=str(raw_copy_path),
            file_hash_sha256=file_hash,
            config_version=str(config.get("version", "3.0")),
            row_count=row_count,
            duration_ms=telemetry.total_duration_ms,
            peak_memory_kb=peak_memory_kb,
            status=status,
        )
        run_meta = meta_obj.to_dict()

        steps_payload = [s.to_dict() for s in telemetry.steps]
        storage.save_run(run_meta, profile, findings, steps=steps_payload)
        if benchmark_metrics:
            storage.save_benchmark(run_id, benchmark_metrics)

        artifacts = generate_reports(
            run_meta=run_meta,
            profile=profile,
            findings=findings,
            warnings=warnings,
            df=df,
            benchmark=benchmark_metrics,
            reports_dir=reports_dir,
        )

        mode_label = f"streaming={chunk_size}" if chunk_size else "batch"
        print(f"\n=== DataPulse V3 | Run {run_id[:8]} ({domain} | {mode_label}) ===")
        print(f"Fichier     : {raw_copy_path} ({row_count} lignes)")
        print(f"Complétude  : {profile['completeness_pct']}%")
        print(
            f"Incidents   : {len(findings)} uniques | "
            f"Dérives schéma : {len(drifts)} | Statut : {status}"
        )
        print(f"Performance : {telemetry.total_duration_ms} ms | Mémoire max : {peak_memory_kb} Ko")
        if benchmark_metrics:
            print(
                f"Benchmark   : Précision={benchmark_metrics['precision'] * 100:.1f}% | "
                f"Rappel={benchmark_metrics['recall'] * 100:.1f}% | "
                f"F1={benchmark_metrics['f1_score'] * 100:.1f}%"
            )
        print(f"Rapport HTML: {artifacts['html']}")
        print(f"Données ann.: {processed_file}\n")

        if strict and (findings or drifts):
            return int(ExitCode.QUALITY_ALERT)
        return int(ExitCode.SUCCESS)
    finally:
        if tracemalloc.is_tracing():
            tracemalloc.stop()


def show_history(domain: str | None = None, db_path: str = "db/datapulse.sqlite") -> int:
    storage = DataPulseStorage(db_path=db_path)
    runs = storage.get_history(domain=domain, limit=10)
    if not runs:
        print("Aucun historique enregistré dans SQLite.")
        return int(ExitCode.SUCCESS)

    print("\n=== Historique des exécutions DataPulse ===")
    for r in runs:
        print(
            f"[{r['timestamp'][:19]}] {r['domain']:<14} | run={r['run_id'][:8]} | "
            f"lignes={r['row_count']} | incidents={r['findings_count']} | "
            f"{r['duration_ms']} ms | {r['status']}"
        )
    print()
    return int(ExitCode.SUCCESS)


def inspect_run(run_id: str, db_path: str = "db/datapulse.sqlite") -> int:
    storage = DataPulseStorage(db_path=db_path)
    details = storage.get_run_details(run_id)
    if details is None:
        print(f"Aucun run trouvé pour l'identifiant '{run_id}'.", file=sys.stderr)
        return int(ExitCode.EXECUTION_ERROR)

    print(f"\n=== Détail du Run {details['run_id']} ===")
    print(
        f"Domaine : {details['domain']} | Statut : {details['status']} | "
        f"Lignes : {details['row_count']} | Incidents : {details['findings_count']}"
    )
    if details["steps"]:
        print("\nTélémétrie par étape :")
        for s in details["steps"]:
            print(
                f"  - {s['step_name']:<32} | {s['duration_ms']:>8.2f} ms | "
                f"in={s['rows_in']} -> out={s['rows_out']} | "
                f"{s['throughput_rows_per_sec']:.0f} rows/s"
            )
    print()
    return int(ExitCode.SUCCESS)


def diff_runs(
    run_a: str | None = None,
    run_b: str | None = None,
    domain: str | None = None,
    db_path: str = "db/datapulse.sqlite",
) -> int:
    storage = DataPulseStorage(db_path=db_path)
    if domain and (not run_a or not run_b):
        recent = storage.get_history(domain=domain, limit=2)
        if len(recent) < 2:
            print(
                f"Au moins 2 exécutions sont requises pour le domaine '{domain}'.",
                file=sys.stderr,
            )
            return int(ExitCode.EXECUTION_ERROR)
        run_b = str(recent[0]["run_id"])
        run_a = str(recent[1]["run_id"])

    if not run_a or not run_b:
        print("Précisez --run-a et --run-b, ou --domain.", file=sys.stderr)
        return int(ExitCode.EXECUTION_ERROR)

    comparison = storage.compare_runs(run_a, run_b)
    ra = comparison["run_a"]
    rb = comparison["run_b"]
    deltas = comparison["deltas"]

    print(f"\n=== Diff DataPulse : {ra['run_id'][:8]} (base) -> {rb['run_id'][:8]} (cible) ===")
    print(f"Domaine    : {rb['domain']}")
    print(f"Lignes     : {ra['row_count']} -> {rb['row_count']} ({deltas['row_count_delta']:+d})")
    print(
        f"Incidents  : {ra['findings_count']} -> {rb['findings_count']} "
        f"({deltas['findings_count_delta']:+d})"
    )
    print(
        f"Durée (ms) : {ra['duration_ms']} -> {rb['duration_ms']} "
        f"({deltas['duration_ms_delta']:+.2f} ms)"
    )

    drifts = comparison["schema_drifts"]
    if drifts:
        print("\nDérives de schéma détectées :")
        for d in drifts:
            print(f"  - [{d['severity']}] {d['drift_type']} : {d['explanation']}")
    else:
        print("\nSchéma     : Aucune dérive structurelle détectée.")
    print()
    return int(ExitCode.SUCCESS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="datapulse",
        description="DataPulse V3 — Observatoire générique de qualité et d'anomalies de données",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run-all", help="Exécute le pipeline complet sur un fichier")
    p_run.add_argument("--file", required=True, help="Chemin vers CSV/Excel/JSON/JSONL/Parquet")
    p_run.add_argument(
        "--domain", required=True, help="Nom du domaine (commerce, finance, administration)"
    )
    p_run.add_argument(
        "--ref", required=False, help="Fichier propre de référence pour le benchmark"
    )
    p_run.add_argument(
        "--strict",
        action="store_true",
        help="Retourne le code de sortie 1 si au moins un incident ou drift est détecté",
    )
    p_run.add_argument(
        "--json-logs",
        action="store_true",
        help="Émet la télémétrie structurée au format JSON sur stderr",
    )
    p_run.add_argument(
        "--chunk-size",
        type=int,
        default=None,
        help="Active l'ingestion en streaming par blocs de N lignes (ex: 50000)",
    )

    p_hist = sub.add_parser("history", help="Affiche l'historique des analyses SQLite")
    p_hist.add_argument("--domain", required=False, help="Filtrer par domaine")

    p_insp = sub.add_parser("inspect", help="Affiche la télémétrie détaillée d'un run")
    p_insp.add_argument("--run-id", required=True, help="ID complet ou préfixe (8 caractères)")

    p_diff = sub.add_parser("diff", help="Compare deux exécutions et détecte le schema drift")
    p_diff.add_argument("--run-a", required=False, help="ID du run de référence (base)")
    p_diff.add_argument("--run-b", required=False, help="ID du run cible")
    p_diff.add_argument(
        "--domain", required=False, help="Compare automatiquement les 2 derniers runs du domaine"
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "run-all":
            return run_pipeline(
                file_path=args.file,
                domain=args.domain,
                ref_path=args.ref,
                strict=args.strict,
                json_logs=args.json_logs,
                chunk_size=args.chunk_size,
            )
        if args.command == "history":
            return show_history(domain=args.domain)
        if args.command == "inspect":
            return inspect_run(run_id=args.run_id)
        if args.command == "diff":
            return diff_runs(run_a=args.run_a, run_b=args.run_b, domain=args.domain)
        return int(ExitCode.EXECUTION_ERROR)
    except (DataPulseError, ConfigError, FileNotFoundError, ValueError) as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return int(ExitCode.EXECUTION_ERROR)


if __name__ == "__main__":
    raise SystemExit(main())
