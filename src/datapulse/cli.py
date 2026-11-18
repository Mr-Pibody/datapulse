"""Interface en ligne de commande DataPulse V2."""

from __future__ import annotations

import argparse
import sys
import tracemalloc
from pathlib import Path

from datapulse.benchmark import evaluate_against_reference
from datapulse.config_loader import ConfigError, load_domain_config
from datapulse.detector import detect_anomalies
from datapulse.exceptions import DataPulseError, ExitCode
from datapulse.ingestion import ingest_file
from datapulse.observability import PipelineTracker, get_structured_logger
from datapulse.profiler import profile_dataframe
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
) -> int:
    logger = get_structured_logger(enabled=json_logs)
    tracker = PipelineTracker(pipeline_name=f"datapulse.{domain}", logger=logger)
    run_id = tracker.telemetry.run_id
    timestamp = tracker.telemetry.started_at

    tracemalloc.start()
    try:
        with tracker.track_step("config_load"):
            config = load_domain_config(domain)

        with tracker.track_step("ingestion") as step:
            ingestion = ingest_file(file_path)
            df = ingestion.dataframe
            step.rows_in = ingestion.row_count
            step.rows_out = ingestion.row_count

        with tracker.track_step("profiling", rows_in=ingestion.row_count) as step:
            profile = profile_dataframe(df)
            step.rows_out = len(profile.get("columns", []))

        with tracker.track_step("validation", rows_in=ingestion.row_count) as step:
            violations = validate_dataframe(df, config)
            step.rows_out = len(violations)

        with tracker.track_step("anomaly_detection", rows_in=ingestion.row_count) as step:
            anomalies, warnings = detect_anomalies(df, config)
            step.rows_out = len(anomalies)

        with tracker.track_step("reconciliation", rows_in=ingestion.row_count) as step:
            findings, annotated_df = reconcile_findings(df, violations, anomalies)
            step.rows_out = len(findings)

        with tracker.track_step("export_processed", rows_in=len(annotated_df)) as step:
            out_processed = Path(processed_dir)
            out_processed.mkdir(parents=True, exist_ok=True)
            processed_file = out_processed / f"{domain}_{run_id[:8]}_annotated.csv"
            annotated_df.to_csv(processed_file, index=False)
            step.rows_out = len(annotated_df)

        benchmark_metrics = None
        if ref_path:
            with tracker.track_step("benchmark", rows_in=ingestion.row_count):
                benchmark_metrics = evaluate_against_reference(df, ref_path, findings, domain)

        telemetry = tracker.finalize()
        _, peak_bytes = tracemalloc.get_traced_memory()
        peak_memory_kb = round(peak_bytes / 1024, 2)

        status = "QUALITY_ALERT" if (strict and findings) else "SUCCESS"
        run_meta = {
            "run_id": run_id,
            "timestamp": timestamp,
            "domain": domain,
            "source_file": str(ingestion.raw_copy_path),
            "file_hash_sha256": ingestion.file_hash_sha256,
            "config_version": str(config.get("version", "2.0")),
            "row_count": ingestion.row_count,
            "duration_ms": telemetry.total_duration_ms,
            "peak_memory_kb": peak_memory_kb,
            "status": status,
        }

        storage = DataPulseStorage(db_path=db_path)
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

        print(f"\n=== DataPulse V2 | Run {run_id[:8]} ({domain}) ===")
        print(f"Fichier     : {ingestion.raw_copy_path} ({ingestion.row_count} lignes)")
        print(f"Complétude  : {profile['completeness_pct']}%")
        print(f"Incidents   : {len(findings)} uniques | Statut : {status}")
        print(f"Performance : {telemetry.total_duration_ms} ms | Mémoire max : {peak_memory_kb} Ko")
        if benchmark_metrics:
            print(
                f"Benchmark   : Précision={benchmark_metrics['precision'] * 100:.1f}% | "
                f"Rappel={benchmark_metrics['recall'] * 100:.1f}% | "
                f"F1={benchmark_metrics['f1_score'] * 100:.1f}%"
            )
        print(f"Rapport HTML: {artifacts['html']}")
        print(f"Données ann.: {processed_file}\n")

        if strict and findings:
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
                f"  - {s['step_name']:<20} | {s['duration_ms']:>8.2f} ms | "
                f"in={s['rows_in']} -> out={s['rows_out']} | "
                f"{s['throughput_rows_per_sec']:.0f} rows/s"
            )
    print()
    return int(ExitCode.SUCCESS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="datapulse",
        description="DataPulse V2 — Observatoire générique de qualité et d'anomalies de données",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run-all", help="Exécute le pipeline complet sur un fichier")
    p_run.add_argument("--file", required=True, help="Chemin vers le fichier CSV/Excel/JSON")
    p_run.add_argument(
        "--domain", required=True, help="Nom du domaine (commerce, finance, administration)"
    )
    p_run.add_argument(
        "--ref", required=False, help="Fichier propre de référence pour le benchmark"
    )
    p_run.add_argument(
        "--strict",
        action="store_true",
        help="Retourne le code de sortie 1 si au moins un incident est détecté",
    )
    p_run.add_argument(
        "--json-logs",
        action="store_true",
        help="Émet la télémétrie structurée au format JSON sur stderr",
    )

    p_hist = sub.add_parser("history", help="Affiche l'historique des analyses SQLite")
    p_hist.add_argument("--domain", required=False, help="Filtrer par domaine")

    p_insp = sub.add_parser("inspect", help="Affiche la télémétrie détaillée d'un run")
    p_insp.add_argument("--run-id", required=True, help="ID complet ou préfixe (8 caractères)")

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
            )
        if args.command == "history":
            return show_history(domain=args.domain)
        if args.command == "inspect":
            return inspect_run(run_id=args.run_id)
        return int(ExitCode.EXECUTION_ERROR)
    except (DataPulseError, ConfigError, FileNotFoundError, ValueError) as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return int(ExitCode.EXECUTION_ERROR)


if __name__ == "__main__":
    raise SystemExit(main())
