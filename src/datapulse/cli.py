"""Interface en ligne de commande DataPulse V8."""

from __future__ import annotations

import argparse
import sys
import tracemalloc
from pathlib import Path
from typing import Any

import pandas as pd

from datapulse.benchmark import evaluate_against_reference
from datapulse.config_loader import ConfigError
from datapulse.detector import detect_anomalies
from datapulse.drift import (
    attach_distribution_sketches,
    detect_data_drift,
    load_run_column_profiles,
    save_run_sketches,
)
from datapulse.exceptions import DataPulseError, ExitCode
from datapulse.ingestion import ingest_file, iter_file_chunks
from datapulse.models import RunMeta
from datapulse.observability import PipelineTracker, get_structured_logger
from datapulse.profiler import StreamingProfiler, detect_schema_drift, profile_dataframe
from datapulse.quarantine import (
    export_quarantine_to_csv,
    partition_clean_and_quarantine,
    replay_corrected_file,
)
from datapulse.reconciler import reconcile_findings
from datapulse.reporter import generate_reports
from datapulse.schema_inference import generate_domain_yaml, resolve_domain
from datapulse.sql_connector import (
    build_select_sql,
    compute_sql_fingerprint,
    extract_sql_to_dataframe,
    iter_sql_chunks,
)
from datapulse.storage import DataPulseStorage
from datapulse.validator import validate_dataframe


def _finalize_and_report(
    *,
    tracker: PipelineTracker,
    storage: DataPulseStorage,
    resolved_domain: str,
    config: dict[str, Any],
    source_label: str,
    file_hash: str,
    row_count: int,
    profile: dict[str, Any],
    df: pd.DataFrame,
    violations: list[dict[str, Any]],
    previous_profile: tuple[str, list[dict[str, Any]]] | None,
    ref_path: str | None,
    reports_dir: str,
    processed_dir: str,
    strict: bool,
    quarantine: bool,
    mode_label: str,
) -> int:
    """Exécute la détection statistique, la détection de drift, l'export et la persistance."""
    run_id = tracker.telemetry.run_id
    timestamp = tracker.telemetry.started_at

    attach_distribution_sketches(profile, df)

    with tracker.track_step("anomaly_detection", rows_in=len(df)) as step:
        anomalies, warnings = detect_anomalies(df, config)
        step.rows_out = len(anomalies)

    with tracker.track_step("reconciliation", rows_in=len(df)) as step:
        findings, annotated_df = reconcile_findings(df, violations, anomalies)
        step.rows_out = len(findings)

    schema_drifts: list[dict[str, Any]] = []
    data_drifts: list[dict[str, Any]] = []
    if previous_profile is not None:
        prev_run_id, baseline_cols = previous_profile
        schema_drifts = detect_schema_drift(profile, baseline_cols)
        for d in schema_drifts:
            warnings.append(f"[SCHEMA DRIFT] {d['explanation']}")

        current_cols = profile.get("columns", [])
        if isinstance(current_cols, list):
            baseline_sketches = load_run_column_profiles(storage.db_path, prev_run_id)
            data_drifts = detect_data_drift(
                current_cols, baseline_sketches if baseline_sketches else baseline_cols
            )
            for dd in data_drifts:
                warnings.append(f"[DATA DRIFT:{dd['severity']}] {dd['explanation']}")

    critical_data_drifts = [d for d in data_drifts if d.get("severity") == "CRITICAL"]

    quarantined_records: list[dict[str, Any]] = []
    clean_file: Path | None = None
    with tracker.track_step("export_processed", rows_in=len(annotated_df)) as step:
        out_processed = Path(processed_dir)
        out_processed.mkdir(parents=True, exist_ok=True)
        processed_file = out_processed / f"{resolved_domain}_{run_id[:8]}_annotated.csv"
        annotated_df.to_csv(processed_file, index=False)
        if quarantine:
            clean_df, quarantined_records = partition_clean_and_quarantine(annotated_df, findings)
            clean_file = out_processed / f"{resolved_domain}_{run_id[:8]}_clean.csv"
            clean_df.to_csv(clean_file, index=False)
        step.rows_out = len(annotated_df)

    benchmark_metrics = None
    if ref_path:
        with tracker.track_step("benchmark", rows_in=row_count):
            benchmark_metrics = evaluate_against_reference(df, ref_path, findings, resolved_domain)

    telemetry = tracker.finalize()
    _, peak_bytes = tracemalloc.get_traced_memory()
    peak_memory_kb = round(peak_bytes / 1024, 2)

    has_strict_alert = bool(findings or schema_drifts or critical_data_drifts)
    status = "QUALITY_ALERT" if (strict and has_strict_alert) else "SUCCESS"
    meta_obj = RunMeta(
        run_id=run_id,
        timestamp=timestamp,
        domain=resolved_domain,
        source_file=source_label,
        file_hash_sha256=file_hash,
        config_version=str(config.get("version", "8.0")),
        row_count=row_count,
        duration_ms=telemetry.total_duration_ms,
        peak_memory_kb=peak_memory_kb,
        status=status,
    )
    run_meta = meta_obj.to_dict()

    steps_payload = [s.to_dict() for s in telemetry.steps]
    storage.save_run(run_meta, profile, findings, steps=steps_payload)
    save_run_sketches(storage.db_path, run_id, profile)
    if quarantine and quarantined_records:
        storage.save_quarantine_rows(run_id, resolved_domain, quarantined_records)
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

    print(f"\n=== DataPulse V8 | Run {run_id[:8]} ({resolved_domain} | {mode_label}) ===")
    print(f"Source      : {source_label} ({row_count} lignes)")
    print(f"Complétude  : {profile['completeness_pct']}%")
    print(
        f"Incidents   : {len(findings)} uniques | "
        f"Schema drift : {len(schema_drifts)} | Data drift : {len(data_drifts)} | "
        f"Statut : {status}"
    )
    if quarantine:
        print(
            f"Quarantaine : {len(quarantined_records)} ligne(s) isolée(s) | "
            f"Export sain : {clean_file}"
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

    if strict and has_strict_alert:
        return int(ExitCode.QUALITY_ALERT)
    return int(ExitCode.SUCCESS)


def run_pipeline(
    file_path: str,
    domain: str,
    ref_path: str | None = None,
    db_path: str = "db/datapulse.sqlite",
    reports_dir: str = "reports",
    processed_dir: str = "data/processed",
    configs_dir: str = "configs",
    *,
    strict: bool = False,
    json_logs: bool = False,
    chunk_size: int | None = None,
    quarantine: bool = False,
) -> int:
    logger = get_structured_logger(enabled=json_logs)
    tracker = PipelineTracker(pipeline_name=f"datapulse.{domain}", logger=logger)
    run_id = tracker.telemetry.run_id

    tracemalloc.start()
    try:
        storage = DataPulseStorage(db_path=db_path)
        resolved_domain = domain
        config: dict[str, Any] = {}

        if chunk_size is not None:
            with tracker.track_step("streaming_ingestion_profile_validate") as step:
                raw_copy_path, file_hash, chunk_iter = iter_file_chunks(
                    file_path, chunk_size=chunk_size
                )
                s_profiler = StreamingProfiler()
                all_violations: list[dict[str, Any]] = []
                sample_frames: list[pd.DataFrame] = []
                sampled_rows = 0
                max_sample_rows = 50_000
                contract_resolved = False

                for offset, chunk in chunk_iter:
                    if not contract_resolved:
                        resolved_domain, config = resolve_domain(
                            chunk, domain, configs_dir=configs_dir
                        )
                        tracker.telemetry.pipeline_name = f"datapulse.{resolved_domain}"
                        contract_resolved = True

                    s_profiler.update(chunk)
                    chunk_violations = validate_dataframe(chunk, config)
                    for v in chunk_violations:
                        v["row_index"] = int(v["row_index"]) + offset
                        all_violations.append(v)
                    if sampled_rows < max_sample_rows:
                        take = chunk.iloc[: max_sample_rows - sampled_rows]
                        sample_frames.append(take)
                        sampled_rows += len(take)

                if not contract_resolved:
                    resolved_domain, config = resolve_domain(
                        pd.DataFrame(), domain, configs_dir=configs_dir
                    )

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

            with tracker.track_step("config_resolution"):
                resolved_domain, config = resolve_domain(df, domain, configs_dir=configs_dir)
                tracker.telemetry.pipeline_name = f"datapulse.{resolved_domain}"

            with tracker.track_step("profiling", rows_in=row_count) as step:
                profile = profile_dataframe(df)
                step.rows_out = len(profile.get("columns", []))

            with tracker.track_step("validation", rows_in=row_count) as step:
                violations = validate_dataframe(df, config)
                step.rows_out = len(violations)

        previous_profile = storage.get_previous_run_profile(resolved_domain, exclude_run_id=run_id)
        mode_label = f"streaming={chunk_size}" if chunk_size else "batch"

        return _finalize_and_report(
            tracker=tracker,
            storage=storage,
            resolved_domain=resolved_domain,
            config=config,
            source_label=str(raw_copy_path),
            file_hash=file_hash,
            row_count=row_count,
            profile=profile,
            df=df,
            violations=violations,
            previous_profile=previous_profile,
            ref_path=ref_path,
            reports_dir=reports_dir,
            processed_dir=processed_dir,
            strict=strict,
            quarantine=quarantine,
            mode_label=mode_label,
        )
    finally:
        if tracemalloc.is_tracing():
            tracemalloc.stop()


def run_sql_pipeline(
    uri: str,
    domain: str,
    *,
    table: str | None = None,
    query: str | None = None,
    db_path: str = "db/datapulse.sqlite",
    reports_dir: str = "reports",
    processed_dir: str = "data/processed",
    configs_dir: str = "configs",
    strict: bool = False,
    json_logs: bool = False,
    chunk_size: int | None = None,
    quarantine: bool = False,
) -> int:
    """Exécute le pipeline qualité DataPulse directement sur une table ou requête SQL."""
    logger = get_structured_logger(enabled=json_logs)
    tracker = PipelineTracker(pipeline_name=f"datapulse.sql.{domain}", logger=logger)
    run_id = tracker.telemetry.run_id

    tracemalloc.start()
    try:
        storage = DataPulseStorage(db_path=db_path)
        sql_stmt, _ = build_select_sql(table=table, query=query)
        resolved_domain = domain
        config: dict[str, Any] = {}

        if chunk_size is not None:
            with tracker.track_step("sql_streaming_profile_validate") as step:
                source_label, chunk_iter = iter_sql_chunks(
                    uri, table=table, query=query, chunk_size=chunk_size
                )
                s_profiler = StreamingProfiler()
                all_violations: list[dict[str, Any]] = []
                sample_frames: list[pd.DataFrame] = []
                sampled_rows = 0
                max_sample_rows = 50_000
                contract_resolved = False

                for offset, chunk in chunk_iter:
                    if not contract_resolved:
                        resolved_domain, config = resolve_domain(
                            chunk, domain, configs_dir=configs_dir
                        )
                        tracker.telemetry.pipeline_name = f"datapulse.{resolved_domain}"
                        contract_resolved = True

                    s_profiler.update(chunk)
                    chunk_violations = validate_dataframe(chunk, config)
                    for v in chunk_violations:
                        v["row_index"] = int(v["row_index"]) + offset
                        all_violations.append(v)
                    if sampled_rows < max_sample_rows:
                        take = chunk.iloc[: max_sample_rows - sampled_rows]
                        sample_frames.append(take)
                        sampled_rows += len(take)

                if not contract_resolved:
                    resolved_domain, config = resolve_domain(
                        pd.DataFrame(), domain, configs_dir=configs_dir
                    )

                profile = s_profiler.finalize()
                row_count = int(profile["row_count"])
                df = (
                    pd.concat(sample_frames, ignore_index=True) if sample_frames else pd.DataFrame()
                )
                violations = all_violations
                file_hash = compute_sql_fingerprint(
                    uri, sql_stmt, row_count, [str(c) for c in df.columns]
                )
                step.rows_in = row_count
                step.rows_out = row_count
        else:
            with tracker.track_step("sql_extraction") as step:
                df, source_label, file_hash = extract_sql_to_dataframe(
                    uri, table=table, query=query
                )
                row_count = len(df)
                step.rows_in = row_count
                step.rows_out = row_count

            with tracker.track_step("config_resolution"):
                resolved_domain, config = resolve_domain(df, domain, configs_dir=configs_dir)
                tracker.telemetry.pipeline_name = f"datapulse.{resolved_domain}"

            with tracker.track_step("profiling", rows_in=row_count) as step:
                profile = profile_dataframe(df)
                step.rows_out = len(profile.get("columns", []))

            with tracker.track_step("validation", rows_in=row_count) as step:
                violations = validate_dataframe(df, config)
                step.rows_out = len(violations)

        previous_profile = storage.get_previous_run_profile(resolved_domain, exclude_run_id=run_id)
        mode_label = f"sql-streaming={chunk_size}" if chunk_size else "sql-batch"

        return _finalize_and_report(
            tracker=tracker,
            storage=storage,
            resolved_domain=resolved_domain,
            config=config,
            source_label=source_label,
            file_hash=file_hash,
            row_count=row_count,
            profile=profile,
            df=df,
            violations=violations,
            previous_profile=previous_profile,
            ref_path=None,
            reports_dir=reports_dir,
            processed_dir=processed_dir,
            strict=strict,
            quarantine=quarantine,
            mode_label=mode_label,
        )
    finally:
        if tracemalloc.is_tracing():
            tracemalloc.stop()


def init_config_cmd(
    file_path: str,
    domain: str,
    output_dir: str = "configs",
    *,
    force: bool = False,
) -> int:
    target_path, config = generate_domain_yaml(
        file_path=file_path,
        domain_name=domain,
        output_dir=output_dir,
        overwrite=force,
    )
    rules_count = len(config.get("rules", []))
    stat_cols = config.get("anomaly_detection", {}).get("columns", [])
    print(f"\n=== Contrat DataPulse généré : {target_path} ===")
    print(f"Domaine           : {config['domain']}")
    print(f"Règles inférées   : {rules_count}")
    print(f"Colonnes MAD/Stat : {', '.join(stat_cols) if stat_cols else 'aucune'}\n")
    return int(ExitCode.SUCCESS)


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

    cols_a = load_run_column_profiles(db_path, str(ra["run_id"]))
    cols_b = load_run_column_profiles(db_path, str(rb["run_id"]))
    data_drifts = detect_data_drift(cols_b, cols_a)

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

    if data_drifts:
        print("\nDérives statistiques de données (Data Drift) :")
        for dd in data_drifts:
            print(f"  - [{dd['severity']}] {dd['explanation']}")
    else:
        print("Data Drift : Aucune dérive statistique détectée.")
    print()
    return int(ExitCode.SUCCESS)


def manage_quarantine(
    domain: str | None = None,
    status: str = "PENDING",
    export_csv: str | None = None,
    db_path: str = "db/datapulse.sqlite",
) -> int:
    storage = DataPulseStorage(db_path=db_path)
    records = storage.list_quarantine(domain=domain, status=status)
    if not records:
        print(f"Aucun enregistrement en quarantaine (statut={status}).")
        return int(ExitCode.SUCCESS)

    print(f"\n=== Quarantaine DataPulse ({len(records)} ligne(s) | statut={status}) ===")
    for rec in records:
        print(
            f"  [ID={rec['quarantine_id']}] run={rec['run_id'][:8]} | "
            f"domaine={rec['domain']} | ligne={rec['row_index']}"
        )

    if export_csv:
        out_path = export_quarantine_to_csv(records, export_csv)
        print(f"\nExport CSV éditable généré : {out_path}")
    print()
    return int(ExitCode.SUCCESS)


def run_replay(
    file_path: str,
    domain: str,
    db_path: str = "db/datapulse.sqlite",
) -> int:
    summary = replay_corrected_file(file_path, domain, db_path=db_path)
    print(f"\n=== Replay Quarantaine DataPulse ({domain}) ===")
    print(f"Lignes rejouées      : {summary['total_replayed_rows']}")
    print(f"Violations restantes : {summary['remaining_violations']}")
    print(f"Tickets clôturés     : {summary['marked_replayed_count']}\n")
    if summary["remaining_violations"] > 0:
        return int(ExitCode.QUALITY_ALERT)
    return int(ExitCode.SUCCESS)


def serve_api(
    host: str = "127.0.0.1",
    port: int = 8000,
    db_path: str = "db/datapulse.sqlite",
) -> int:
    try:
        import uvicorn
    except ImportError as exc:
        print(
            "[ERREUR] Dépendances API manquantes. Installez : pip install -e '.[api]'",
            file=sys.stderr,
        )
        raise SystemExit(int(ExitCode.EXECUTION_ERROR)) from exc

    from datapulse.api import create_app

    fastapi_app = create_app(db_path=db_path)
    uvicorn.run(fastapi_app, host=host, port=port)
    return int(ExitCode.SUCCESS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="datapulse",
        description="DataPulse V8 — Observatoire générique de qualité et d'anomalies de données",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run-all", help="Exécute le pipeline complet sur un fichier")
    p_run.add_argument("--file", required=True, help="Chemin vers CSV/Excel/JSON/JSONL/Parquet")
    p_run.add_argument(
        "--domain",
        required=True,
        help="Nom du domaine (commerce, finance, administration, diamonds, ou 'auto')",
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
    p_run.add_argument(
        "--quarantine",
        action="store_true",
        help="Isole les lignes rejetées dans SQLite et exporte un fichier *_clean.csv",
    )

    p_sql = sub.add_parser("run-sql", help="Audit qualité directement sur une table ou requête SQL")
    p_sql.add_argument(
        "--uri", required=True, help="URI de connexion SQL (ex: sqlite:///db/source.sqlite)"
    )
    p_sql.add_argument("--table", required=False, help="Nom de la table SQL à analyser")
    p_sql.add_argument("--query", required=False, help="Requête SQL SELECT en lecture seule")
    p_sql.add_argument("--domain", default="auto", help="Domaine cible ou 'auto'")
    p_sql.add_argument("--strict", action="store_true", help="Code retour 1 si anomalie")
    p_sql.add_argument("--json-logs", action="store_true", help="Logs structurés JSON")
    p_sql.add_argument("--chunk-size", type=int, default=None, help="Streaming SQL par blocs")
    p_sql.add_argument("--quarantine", action="store_true", help="Isole les lignes rejetées")

    p_init = sub.add_parser(
        "init-config", help="Infère et génère un contrat YAML depuis un fichier de données"
    )
    p_init.add_argument("--file", required=True, help="Fichier échantillon (CSV/XLSX/JSON/Parquet)")
    p_init.add_argument("--domain", required=True, help="Nom du nouveau domaine à créer")
    p_init.add_argument(
        "--output-dir", default="configs", help="Répertoire de destination (défaut: configs)"
    )
    p_init.add_argument(
        "--force", action="store_true", help="Écrase le contrat YAML s'il existe déjà"
    )

    p_hist = sub.add_parser("history", help="Affiche l'historique des analyses SQLite")
    p_hist.add_argument("--domain", required=False, help="Filtrer par domaine")

    p_insp = sub.add_parser("inspect", help="Affiche la télémétrie détaillée d'un run")
    p_insp.add_argument("--run-id", required=True, help="ID complet ou préfixe (8 caractères)")

    p_diff = sub.add_parser("diff", help="Compare deux exécutions (Schema & Data Drift)")
    p_diff.add_argument("--run-a", required=False, help="ID du run de référence (base)")
    p_diff.add_argument("--run-b", required=False, help="ID du run cible")
    p_diff.add_argument(
        "--domain", required=False, help="Compare automatiquement les 2 derniers runs du domaine"
    )

    p_quar = sub.add_parser("quarantine", help="Liste ou exporte les lignes en quarantaine")
    p_quar.add_argument("--domain", required=False, help="Filtrer par domaine")
    p_quar.add_argument(
        "--status", default="PENDING", choices=["PENDING", "REPLAYED"], help="Statut des lignes"
    )
    p_quar.add_argument(
        "--export", required=False, help="Chemin CSV d'export pour correction manuelle"
    )

    p_rep = sub.add_parser("replay", help="Rejoue un fichier corrigé et clôture la quarantaine")
    p_rep.add_argument("--file", required=True, help="Chemin vers le CSV corrigé")
    p_rep.add_argument("--domain", required=True, help="Domaine métier cible")

    p_srv = sub.add_parser("serve", help="Démarre l'API REST et le Dashboard Web DataPulse")
    p_srv.add_argument("--host", default="127.0.0.1", help="Adresse d'écoute (défaut: 127.0.0.1)")
    p_srv.add_argument("--port", type=int, default=8000, help="Port HTTP (défaut: 8000)")
    p_srv.add_argument("--db", default="db/datapulse.sqlite", help="Chemin de la base SQLite")

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
                quarantine=args.quarantine,
            )
        if args.command == "run-sql":
            return run_sql_pipeline(
                uri=args.uri,
                domain=args.domain,
                table=args.table,
                query=args.query,
                strict=args.strict,
                json_logs=args.json_logs,
                chunk_size=args.chunk_size,
                quarantine=args.quarantine,
            )
        if args.command == "init-config":
            return init_config_cmd(
                file_path=args.file,
                domain=args.domain,
                output_dir=args.output_dir,
                force=args.force,
            )
        if args.command == "history":
            return show_history(domain=args.domain)
        if args.command == "inspect":
            return inspect_run(run_id=args.run_id)
        if args.command == "diff":
            return diff_runs(run_a=args.run_a, run_b=args.run_b, domain=args.domain)
        if args.command == "quarantine":
            return manage_quarantine(domain=args.domain, status=args.status, export_csv=args.export)
        if args.command == "replay":
            return run_replay(file_path=args.file, domain=args.domain)
        if args.command == "serve":
            return serve_api(host=args.host, port=args.port, db_path=args.db)
        return int(ExitCode.EXECUTION_ERROR)
    except (DataPulseError, ConfigError, FileNotFoundError, ValueError) as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return int(ExitCode.EXECUTION_ERROR)


if __name__ == "__main__":
    raise SystemExit(main())
