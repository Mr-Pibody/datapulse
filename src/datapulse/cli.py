"""Interface en ligne de commande DataPulse V16."""

from __future__ import annotations

import argparse
import json
import sys
import tracemalloc
from pathlib import Path
from typing import Any

import pandas as pd

from datapulse.alerts import dispatch_run_alerts, list_alert_history, send_test_alert
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
from datapulse.lineage import (
    compute_quality_score,
    export_audit_certificate,
    get_run_lineage,
    record_run_lineage,
    verify_audit_certificate,
)
from datapulse.models import RunMeta
from datapulse.observability import PipelineTracker, get_structured_logger
from datapulse.pii import mask_file_pii, scan_dataframe_pii
from datapulse.profiler import StreamingProfiler, detect_schema_drift, profile_dataframe
from datapulse.prometheus_metrics import collect_observability_snapshot, render_prometheus_metrics
from datapulse.quarantine import (
    export_quarantine_to_csv,
    partition_clean_and_quarantine,
    replay_corrected_file,
)
from datapulse.reconciler import reconcile_findings
from datapulse.reporter import generate_reports
from datapulse.schema_inference import generate_domain_yaml, resolve_domain
from datapulse.security import (
    create_api_key,
    list_api_keys,
    list_security_audit,
    revoke_api_key,
)
from datapulse.sql_connector import (
    build_select_sql,
    compute_sql_fingerprint,
    extract_sql_to_dataframe,
    iter_sql_chunks,
)
from datapulse.storage import DataPulseStorage
from datapulse.synth import generate_synthetic_file
from datapulse.validator import validate_dataframe
from datapulse.watcher import get_watcher_status, watch_loop


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
    """Exécute détection statistique, drift, lineage, persistance, rapports et alertes."""
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

    quality_info = compute_quality_score(
        row_count=row_count,
        completeness_pct=float(profile.get("completeness_pct", 100.0)),
        findings=findings,
        schema_drifts=schema_drifts,
        data_drifts=data_drifts,
    )

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
        config_version=str(config.get("version", "16.0")),
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

    lineage_record = record_run_lineage(
        db_path=storage.db_path,
        run_id=run_id,
        domain=resolved_domain,
        source_uri=source_label,
        source_sha256=file_hash,
        config=config,
        quality_info=quality_info,
        input_rows=row_count,
        quarantined_rows=len(quarantined_records),
        clean_artifact_path=str(clean_file) if clean_file else "",
        report_html_path=str(artifacts.get("html", "")),
    )

    alert_results = dispatch_run_alerts(
        db_path=storage.db_path,
        run_meta=run_meta,
        findings=findings,
        schema_drifts=schema_drifts,
        data_drifts=data_drifts,
        config=config,
    )

    print(f"\n=== DataPulse V16 | Run {run_id[:8]} ({resolved_domain} | {mode_label}) ===")
    print(f"Source      : {source_label} ({row_count} lignes)")
    print(
        f"Score Qual. : {quality_info['score']}/100 (Grade {quality_info['grade']}) | "
        f"Complétude : {profile['completeness_pct']}%"
    )
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
    if alert_results:
        summary_alerts = ", ".join(f"{a['channel']}={a['status']}" for a in alert_results)
        print(f"Alertes     : {summary_alerts}")
    print(
        f"Lineage SHA : {str(lineage_record['certificate_sha256'])[:16]}... | "
        f"Performance : {telemetry.total_duration_ms} ms ({peak_memory_kb} Ko)"
    )
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


def synth_cmd(
    *,
    domain: str,
    rows: int = 200,
    chaos_rate: float = 0.0,
    chaos_modes: str | None = None,
    seed: int = 42,
    output_path: str | None = None,
    run_audit: bool = False,
    db_path: str = "db/datapulse.sqlite",
    reports_dir: str = "reports",
    processed_dir: str = "data/processed",
    configs_dir: str = "configs",
) -> int:
    modes_list = [m.strip() for m in chaos_modes.split(",") if m.strip()] if chaos_modes else None
    manifest = generate_synthetic_file(
        domain=domain,
        rows=rows,
        chaos_rate=chaos_rate,
        chaos_modes=modes_list,
        seed=seed,
        output_path=output_path,
        processed_dir=processed_dir,
        configs_dir=configs_dir,
    )
    print(f"\n=== Génération Synthétique & Chaos DataPulse V16 ({manifest['domain']}) ===")
    print(f"Lignes générées   : {manifest['rows_generated']} (seed={manifest['seed']})")
    print(
        f"Chaos injecté     : taux={manifest['chaos_rate'] * 100:.1f}% | "
        f"fautes={manifest['injected_faults_count']}"
    )
    print(f"Fichier généré    : {manifest['output_path']}\n")

    if run_audit:
        return run_pipeline(
            file_path=str(manifest["output_path"]),
            domain=str(manifest["domain"]),
            db_path=db_path,
            reports_dir=reports_dir,
            processed_dir=processed_dir,
            configs_dir=configs_dir,
            quarantine=True,
        )
    return int(ExitCode.SUCCESS)


def pii_scan_cmd(file_path: str) -> int:
    ingestion = ingest_file(file_path)
    report = scan_dataframe_pii(ingestion.dataframe)
    print(f"\n=== Scan RGPD / Données Personnelles (PII) : {file_path} ===")
    print(
        f"Lignes analysées : {report['row_count']} | "
        f"Colonnes PII : {report['pii_columns_count']} | "
        f"Cellules sensibles : {report['total_pii_cells']}"
    )
    for col in report["columns"]:
        print(
            f"  - {col['column']:<20} | type={col['pii_type']:<11} | "
            f"{col['match_rate_pct']:>5.1f}% | aperçu={col['sample_masked']}"
        )
    print()
    return int(ExitCode.SUCCESS)


def pii_mask_cmd(
    *,
    file_path: str,
    strategy: str = "partial",
    columns: str | None = None,
    output_path: str | None = None,
) -> int:
    col_list = [c.strip() for c in columns.split(",") if c.strip()] if columns else None
    summary = mask_file_pii(
        file_path,
        output_path=output_path,
        strategy=strategy,
        columns=col_list,
    )
    print(f"\n=== Anonymisation RGPD DataPulse V16 (stratégie={summary['strategy']}) ===")
    print(f"Colonnes masquées : {', '.join(summary['masked_columns']) or 'aucune'}")
    print(f"Cellules traitées : {summary['masked_cells_total']}")
    print(f"Export RGPD       : {summary['output_path']}\n")
    return int(ExitCode.SUCCESS)


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


def show_lineage_cmd(run_id: str, db_path: str = "db/datapulse.sqlite") -> int:
    lineage = get_run_lineage(db_path, run_id)
    if lineage is None:
        print(f"Aucun lineage trouvé pour le run '{run_id}'.", file=sys.stderr)
        return int(ExitCode.EXECUTION_ERROR)

    bd = lineage["score_breakdown"]
    print(f"\n=== Data Lineage & Score Qualité | Run {str(lineage['run_id'])[:8]} ===")
    print(f"Domaine        : {lineage['domain']} (Contrat v{lineage['contract_version']})")
    print(f"Score Qualité  : {lineage['quality_score']}/100 (Grade {lineage['quality_grade']})")
    print(
        f"Détail Score   : Complétude={bd['completeness']}% | Validité={bd['validity']}% | "
        f"Drift={bd['statistical_stability']}% | Schéma={bd['schema_stability']}%"
    )
    print(
        f"Source         : {lineage['source_uri']} (SHA256={str(lineage['source_sha256'])[:16]}...)"
    )
    print(f"Contrat SHA256 : {str(lineage['contract_sha256'])[:16]}...")
    print(
        f"Flux Lignes    : {lineage['input_rows']} entrantes -> "
        f"{lineage['clean_rows']} saines + {lineage['quarantined_rows']} en quarantaine"
    )
    print(f"Sceau Audit    : {lineage['certificate_sha256']}\n")
    return int(ExitCode.SUCCESS)


def audit_cert_cmd(
    *,
    run_id: str | None = None,
    output_path: str | None = None,
    verify_file: str | None = None,
    db_path: str = "db/datapulse.sqlite",
) -> int:
    if verify_file:
        cert_path = Path(verify_file)
        if not cert_path.exists():
            print(f"Certificat introuvable : '{verify_file}'", file=sys.stderr)
            return int(ExitCode.EXECUTION_ERROR)
        cert_payload = json.loads(cert_path.read_text(encoding="utf-8"))
        check = verify_audit_certificate(cert_payload)
        state = (
            "VALIDE (Intégrité vérifiée)" if check["valid"] else "INVALIDE (Altération détectée)"
        )
        print("\n=== Vérification Certificat d'Audit DataPulse ===")
        print(f"Statut         : {state}")
        print(f"Run ID         : {check['run_id']}")
        print(f"Score / Grade  : {check['quality_score']}/100 (Grade {check['quality_grade']})\n")
        return int(ExitCode.SUCCESS) if check["valid"] else int(ExitCode.QUALITY_ALERT)

    if not run_id:
        print(
            "Précisez --run-id pour générer un certificat, ou --verify <fichier>.", file=sys.stderr
        )
        return int(ExitCode.EXECUTION_ERROR)

    out_file = export_audit_certificate(db_path=db_path, run_id=run_id, output_path=output_path)
    print(f"\n=== Certificat d'audit signé généré : {out_file} ===\n")
    return int(ExitCode.SUCCESS)


def metrics_cmd(
    *,
    fmt: str = "prometheus",
    output_path: str | None = None,
    db_path: str = "db/datapulse.sqlite",
) -> int:
    if fmt == "json":
        payload = json.dumps(collect_observability_snapshot(db_path), indent=2, ensure_ascii=False)
    else:
        payload = render_prometheus_metrics(db_path)

    if output_path:
        dest = Path(output_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(payload, encoding="utf-8")
        print(f"Métriques exportées vers : {dest}")
    else:
        print(payload)
    return int(ExitCode.SUCCESS)


def key_create_cmd(
    *,
    name: str,
    role: str = "viewer",
    db_path: str = "db/datapulse.sqlite",
) -> int:
    created = create_api_key(db_path=db_path, name=name, role=role)
    print("\n=== Nouvelle Clé API DataPulse Zero-Trust ===")
    print(f"Key ID         : {created['key_id']}")
    print(f"Nom            : {created['name']}")
    print(f"Rôle RBAC      : {created['role']}")
    print(f"Clé secrète    : {created['api_key']}")
    print("IMPORTANT      : Conservez cette clé maintenant ; seul son hash SHA-256 est stocké.\n")
    return int(ExitCode.SUCCESS)


def key_list_cmd(db_path: str = "db/datapulse.sqlite") -> int:
    keys = list_api_keys(db_path)
    if not keys:
        print("Aucune clé API configurée (mode ouvert local).")
        return int(ExitCode.SUCCESS)

    print("\n=== Clés API DataPulse enregistrées ===")
    for k in keys:
        status = "ACTIVE" if k["is_active"] else "REVOKED"
        print(
            f"  [{k['key_id']}] {k['name']:<22} | role={k['role']:<8} | "
            f"prefix={k['key_prefix']}... | {status}"
        )
    print()
    return int(ExitCode.SUCCESS)


def key_revoke_cmd(key_id: str, db_path: str = "db/datapulse.sqlite") -> int:
    revoked = revoke_api_key(db_path, key_id=key_id)
    print(f"\n=== Clé API révoquée : {revoked['key_id']} ({revoked['name']}) ===\n")
    return int(ExitCode.SUCCESS)


def audit_log_cmd(limit: int = 30, db_path: str = "db/datapulse.sqlite") -> int:
    events = list_security_audit(db_path, limit=limit)
    if not events:
        print("Aucun événement enregistré dans le journal d'audit de sécurité.")
        return int(ExitCode.SUCCESS)

    print("\n=== Journal d'Audit de Sécurité DataPulse ===")
    for ev in events:
        print(
            f"[{str(ev['timestamp'])[:19]}] {ev['outcome']:<7} | "
            f"{ev['action']:<14} | actor={ev['actor_name']} ({ev['actor_role']}) | "
            f"{ev['resource']}"
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


def alerts_test_cmd(
    *,
    domain: str = "test_domain",
    webhook_url: str = "",
    slack_url: str = "",
    discord_url: str = "",
    secret: str = "",
    db_path: str = "db/datapulse.sqlite",
) -> int:
    results = send_test_alert(
        db_path=db_path,
        domain=domain,
        webhook_url=webhook_url,
        slack_webhook_url=slack_url,
        discord_webhook_url=discord_url,
        webhook_secret=secret,
    )
    if not results:
        print("Aucun canal configuré (--webhook-url, --slack-url, --discord-url ou env).")
        return int(ExitCode.EXECUTION_ERROR)

    print("\n=== Test des notifications DataPulse V16 ===")
    has_failure = False
    for r in results:
        print(f"  - {r['channel']:<10} : {r['status']} ({r['detail']})")
        if r["status"] != "SENT":
            has_failure = True
    print()
    return int(ExitCode.EXECUTION_ERROR) if has_failure else int(ExitCode.SUCCESS)


def show_alerts_history(
    domain: str | None = None,
    limit: int = 20,
    db_path: str = "db/datapulse.sqlite",
) -> int:
    items = list_alert_history(db_path=db_path, domain=domain, limit=limit)
    if not items:
        print("Aucune notification enregistrée dans SQLite.")
        return int(ExitCode.SUCCESS)

    print("\n=== Historique des notifications DataPulse ===")
    for it in items:
        print(
            f"[{str(it['created_at'])[:19]}] {it['domain']:<14} | "
            f"{it['channel']:<8} | {it['status']:<6} | {it['detail']}"
        )
    print()
    return int(ExitCode.SUCCESS)


def watch_cmd(
    *,
    watch_dir: str | None = None,
    config_path: str | None = None,
    domain: str = "auto",
    interval: float = 5.0,
    max_cycles: int | None = None,
    archive_dir: str | None = None,
    strict: bool = False,
    quarantine: bool = True,
    chunk_size: int | None = None,
    db_path: str = "db/datapulse.sqlite",
) -> int:
    target = config_path or watch_dir or ""
    print(f"\n=== Orchestrateur DataPulse V16 démarré ({target}) ===")
    summary = watch_loop(
        watch_dir=watch_dir,
        config_path=config_path,
        domain=domain,
        interval_sec=interval,
        max_cycles=max_cycles,
        db_path=db_path,
        archive_dir=archive_dir,
        strict=strict,
        quarantine=quarantine,
        chunk_size=chunk_size,
    )
    print(
        f"=== Orchestrateur arrêté | Cycles={summary['cycles']} | "
        f"Événements traités={summary['processed_count']} ===\n"
    )
    return int(ExitCode.SUCCESS)


def show_watch_status(db_path: str = "db/datapulse.sqlite", limit: int = 20) -> int:
    status = get_watcher_status(db_path=db_path, limit=limit)
    print("\n=== État de l'Orchestrateur DataPulse V16 ===")
    print(f"Fichiers suivis : {status['files_count']} | Tâches planifiées : {status['jobs_count']}")
    for f in status["recent_files"]:
        print(
            f"  [FILE] {Path(str(f['file_path'])).name:<25} | "
            f"domaine={f['domain']} | code={f['exit_code']} | {str(f['processed_at'])[:19]}"
        )
    for j in status["jobs"]:
        print(
            f"  [JOB ] {j['job_name']:<25} | type={j['job_type']} | "
            f"runs={j['run_count']} | code={j['last_exit_code']}"
        )
    print()
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
        description="DataPulse V16 — Observatoire Zero-Trust de qualité, RGPD/PII, Synthèse Chaos",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- v17.0 : Data Catalog, SLA & Glossaire ---
    p_cat = sub.add_parser(
        "catalog",
        help="Gouvernance Data Catalog, fraicheur SLA et glossaire metier",
    )
    cat_sub = p_cat.add_subparsers(dest="catalog_action", required=True)

    p_cat_sync = cat_sub.add_parser("sync", help="Synchroniser les contrats YAML")
    p_cat_sync.add_argument("--config-dir", default="config", help="Repertoire YAML")
    p_cat_sync.add_argument("--db", default="data/datapulse.db", help="Base SQLite cible")

    p_cat_list = cat_sub.add_parser("list", help="Lister les actifs du catalogue")
    p_cat_list.add_argument("--classification", default=None, help="Classification")
    p_cat_list.add_argument("--sla-status", default=None, help="Statut SLA")
    p_cat_list.add_argument("--q", default=None, help="Recherche texte")
    p_cat_list.add_argument("--db", default="data/datapulse.db", help="Base SQLite cible")

    p_cat_reg = cat_sub.add_parser("register", help="Enregistrer un actif")
    p_cat_reg.add_argument("--domain", required=True, help="Nom du domaine")
    p_cat_reg.add_argument("--display-name", default=None, help="Nom d'affichage")
    p_cat_reg.add_argument(
        "--owner", default="data-engineering@datapulse.local", help="Proprietaire"
    )
    p_cat_reg.add_argument("--steward", default="governance@datapulse.local", help="Data steward")
    p_cat_reg.add_argument("--classification", default="INTERNAL", help="Sensibilite")
    p_cat_reg.add_argument("--description", default="", help="Description metier")
    p_cat_reg.add_argument("--sla-hours", type=float, default=24.0, help="SLA (heures)")
    p_cat_reg.add_argument("--db", default="data/datapulse.db", help="Base SQLite cible")

    p_cat_glo = cat_sub.add_parser("glossary", help="Ajouter un terme metier")
    p_cat_glo.add_argument("--term", required=True, help="Terme metier")
    p_cat_glo.add_argument("--definition", required=True, help="Definition officielle")
    p_cat_glo.add_argument("--domain", default="global", help="Domaine associe")
    p_cat_glo.add_argument("--formula", default="", help="Formule de calcul")
    p_cat_glo.add_argument("--db", default="data/datapulse.db", help="Base SQLite cible")

    p_cat_search = cat_sub.add_parser("search", help="Rechercher dans le catalogue")
    p_cat_search.add_argument("query", help="Mot-cle de recherche")
    p_cat_search.add_argument("--db", default="data/datapulse.db", help="Base SQLite cible")

    p_run = sub.add_parser("run-all", help="Exécute le pipeline complet sur un fichier")
    p_run.add_argument("--file", required=True, help="Chemin vers CSV/Excel/JSON/JSONL/Parquet")
    p_run.add_argument("--domain", required=True, help="Nom du domaine ou 'auto'")
    p_run.add_argument("--ref", required=False, help="Fichier propre de référence")
    p_run.add_argument("--strict", action="store_true", help="Code retour 1 si incident/drift")
    p_run.add_argument("--json-logs", action="store_true", help="Logs JSON sur stderr")
    p_run.add_argument("--chunk-size", type=int, default=None, help="Mode streaming par blocs")
    p_run.add_argument("--quarantine", action="store_true", help="Isole les lignes rejetées")

    p_sql = sub.add_parser("run-sql", help="Audit qualité directement sur une table/requête SQL")
    p_sql.add_argument("--uri", required=True, help="URI SQL (ex: sqlite:///db/source.sqlite)")
    p_sql.add_argument("--table", required=False, help="Nom de la table SQL")
    p_sql.add_argument("--query", required=False, help="Requête SQL SELECT en lecture seule")
    p_sql.add_argument("--domain", default="auto", help="Domaine cible ou 'auto'")
    p_sql.add_argument("--strict", action="store_true", help="Code retour 1 si anomalie")
    p_sql.add_argument("--json-logs", action="store_true", help="Logs structurés JSON")
    p_sql.add_argument("--chunk-size", type=int, default=None, help="Streaming SQL par blocs")
    p_sql.add_argument("--quarantine", action="store_true", help="Isole les lignes rejetées")

    p_synth = sub.add_parser("synth", help="Génère un dataset synthétique avec chaos optionnel")
    p_synth.add_argument("--domain", default="commerce", help="Domaine YAML cible")
    p_synth.add_argument("--rows", type=int, default=200, help="Nombre de lignes à générer")
    p_synth.add_argument("--chaos-rate", type=float, default=0.0, help="Taux de chaos (0.0 à 1.0)")
    p_synth.add_argument(
        "--chaos-modes",
        required=False,
        help="Modes séparés par virgule (nulls, outliers, negatives, duplicates, schema_drift)",
    )
    p_synth.add_argument("--seed", type=int, default=42, help="Graine aléatoire")
    p_synth.add_argument("--output", required=False, help="Chemin CSV de sortie")
    p_synth.add_argument(
        "--run-audit", action="store_true", help="Lance immédiatement l'audit qualité"
    )

    p_pscan = sub.add_parser("pii-scan", help="Détecte les données personnelles (PII/RGPD)")
    p_pscan.add_argument("--file", required=True, help="Fichier à analyser")

    p_pmask = sub.add_parser("mask", help="Anonymise les données personnelles d'un fichier")
    p_pmask.add_argument("--file", required=True, help="Fichier source")
    p_pmask.add_argument(
        "--strategy",
        default="partial",
        choices=["partial", "redact", "hash"],
        help="Stratégie d'anonymisation",
    )
    p_pmask.add_argument("--columns", required=False, help="Colonnes cibles séparées par virgule")
    p_pmask.add_argument("--output", required=False, help="Chemin CSV de sortie")

    p_init = sub.add_parser("init-config", help="Infère et génère un contrat YAML")
    p_init.add_argument("--file", required=True, help="Fichier échantillon")
    p_init.add_argument("--domain", required=True, help="Nom du nouveau domaine")
    p_init.add_argument("--output-dir", default="configs", help="Répertoire de destination")
    p_init.add_argument("--force", action="store_true", help="Écrase le contrat existant")

    p_hist = sub.add_parser("history", help="Affiche l'historique des analyses SQLite")
    p_hist.add_argument("--domain", required=False, help="Filtrer par domaine")

    p_insp = sub.add_parser("inspect", help="Affiche la télémétrie détaillée d'un run")
    p_insp.add_argument("--run-id", required=True, help="ID complet ou préfixe (8 caractères)")

    p_lin = sub.add_parser("lineage", help="Affiche le Data Lineage et le Score Qualité d'un run")
    p_lin.add_argument("--run-id", required=True, help="ID complet ou préfixe")

    p_cert = sub.add_parser("audit-cert", help="Génère ou vérifie un certificat d'audit signé")
    p_cert.add_argument("--run-id", required=False, help="ID du run à certifier")
    p_cert.add_argument("--output", required=False, help="Chemin du fichier JSON de sortie")
    p_cert.add_argument("--verify", required=False, help="Chemin d'un certificat JSON à vérifier")

    p_met = sub.add_parser("metrics", help="Affiche ou exporte les métriques Prometheus")
    p_met.add_argument(
        "--format",
        default="prometheus",
        choices=["prometheus", "json"],
        help="Format de sortie (prometheus ou json)",
    )
    p_met.add_argument("--output", required=False, help="Fichier de sortie optionnel")

    p_kc = sub.add_parser("key-create", help="Génère une nouvelle clé API RBAC")
    p_kc.add_argument("--name", required=True, help="Nom descriptif de la clé")
    p_kc.add_argument(
        "--role",
        default="viewer",
        choices=["viewer", "operator", "admin"],
        help="Rôle RBAC attribué",
    )

    sub.add_parser("key-list", help="Liste les clés API enregistrées")

    p_kr = sub.add_parser("key-revoke", help="Révoque une clé API existante")
    p_kr.add_argument("--key-id", required=True, help="Identifiant de la clé (ex: key_a1b2c3d4)")

    p_alog = sub.add_parser("audit-log", help="Affiche le journal d'audit de sécurité")
    p_alog.add_argument("--limit", type=int, default=30, help="Nombre maximal d'événements")

    p_diff = sub.add_parser("diff", help="Compare deux exécutions (Schema & Data Drift)")
    p_diff.add_argument("--run-a", required=False, help="ID du run de référence (base)")
    p_diff.add_argument("--run-b", required=False, help="ID du run cible")
    p_diff.add_argument("--domain", required=False, help="Compare les 2 derniers runs du domaine")

    p_quar = sub.add_parser("quarantine", help="Liste ou exporte les lignes en quarantaine")
    p_quar.add_argument("--domain", required=False, help="Filtrer par domaine")
    p_quar.add_argument("--status", default="PENDING", choices=["PENDING", "REPLAYED"])
    p_quar.add_argument("--export", required=False, help="Chemin CSV d'export")

    p_rep = sub.add_parser("replay", help="Rejoue un fichier corrigé et clôture la quarantaine")
    p_rep.add_argument("--file", required=True, help="Chemin vers le CSV corrigé")
    p_rep.add_argument("--domain", required=True, help="Domaine métier cible")

    p_atest = sub.add_parser("alerts-test", help="Envoie une notification de test")
    p_atest.add_argument("--domain", default="test_domain", help="Nom de domaine")
    p_atest.add_argument("--webhook-url", default="", help="URL Webhook JSON")
    p_atest.add_argument("--slack-url", default="", help="URL Slack Webhook")
    p_atest.add_argument("--discord-url", default="", help="URL Discord Webhook")
    p_atest.add_argument("--secret", default="", help="Secret HMAC-SHA256")

    p_ahist = sub.add_parser("alerts-history", help="Affiche l'historique des alertes")
    p_ahist.add_argument("--domain", required=False, help="Filtrer par domaine")
    p_ahist.add_argument("--limit", type=int, default=20, help="Nombre maximal d'entrées")

    p_watch = sub.add_parser("watch", help="Surveille un dossier ou exécute un plan YAML")
    p_watch.add_argument("--dir", required=False, help="Répertoire de dépôt à surveiller")
    p_watch.add_argument("--config", required=False, help="Fichier watch.yaml multi-tâches")
    p_watch.add_argument("--domain", default="auto", help="Domaine cible ou 'auto'")
    p_watch.add_argument("--interval", type=float, default=5.0, help="Intervalle en secondes")
    p_watch.add_argument("--max-cycles", type=int, default=None, help="Nombre max de cycles")
    p_watch.add_argument("--archive-dir", required=False, help="Dossier d'archive")
    p_watch.add_argument("--strict", action="store_true", help="Active le mode strict")
    p_watch.add_argument("--no-quarantine", action="store_true", help="Désactive la quarantaine")
    p_watch.add_argument("--chunk-size", type=int, default=None, help="Mode streaming par blocs")

    p_wstat = sub.add_parser("watch-status", help="Affiche l'état du watcher et des tâches")
    p_wstat.add_argument("--limit", type=int, default=20, help="Nombre maximal d'entrées")

    p_srv = sub.add_parser("serve", help="Démarre l'API REST et le Dashboard Web DataPulse")
    p_srv.add_argument("--host", default="127.0.0.1", help="Adresse d'écoute")
    p_srv.add_argument("--port", type=int, default=8000, help="Port HTTP")
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
        if args.command == "synth":
            return synth_cmd(
                domain=args.domain,
                rows=args.rows,
                chaos_rate=args.chaos_rate,
                chaos_modes=args.chaos_modes,
                seed=args.seed,
                output_path=args.output,
                run_audit=args.run_audit,
            )
        if args.command == "pii-scan":
            return pii_scan_cmd(file_path=args.file)
        if args.command == "mask":
            return pii_mask_cmd(
                file_path=args.file,
                strategy=args.strategy,
                columns=args.columns,
                output_path=args.output,
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
        if args.command == "lineage":
            return show_lineage_cmd(run_id=args.run_id)
        if args.command == "audit-cert":
            return audit_cert_cmd(
                run_id=args.run_id,
                output_path=args.output,
                verify_file=args.verify,
            )
        if args.command == "metrics":
            return metrics_cmd(fmt=args.format, output_path=args.output)
        if args.command == "key-create":
            return key_create_cmd(name=args.name, role=args.role)
        if args.command == "key-list":
            return key_list_cmd()
        if args.command == "key-revoke":
            return key_revoke_cmd(key_id=args.key_id)
        if args.command == "audit-log":
            return audit_log_cmd(limit=args.limit)
        if args.command == "diff":
            return diff_runs(run_a=args.run_a, run_b=args.run_b, domain=args.domain)
        if args.command == "quarantine":
            return manage_quarantine(domain=args.domain, status=args.status, export_csv=args.export)
        if args.command == "replay":
            return run_replay(file_path=args.file, domain=args.domain)
        if args.command == "alerts-test":
            return alerts_test_cmd(
                domain=args.domain,
                webhook_url=args.webhook_url,
                slack_url=args.slack_url,
                discord_url=args.discord_url,
                secret=args.secret,
            )
        if args.command == "alerts-history":
            return show_alerts_history(domain=args.domain, limit=args.limit)
        if args.command == "watch":
            return watch_cmd(
                watch_dir=args.dir,
                config_path=args.config,
                domain=args.domain,
                interval=args.interval,
                max_cycles=args.max_cycles,
                archive_dir=args.archive_dir,
                strict=args.strict,
                quarantine=not args.no_quarantine,
                chunk_size=args.chunk_size,
            )
        if args.command == "watch-status":
            return show_watch_status(limit=args.limit)
        if args.command == "catalog":
            import json as _json

            from datapulse.catalog import (
                list_assets,
                register_asset,
                search_catalog,
                sync_catalog_from_configs,
                upsert_glossary_term,
            )

            if args.catalog_action == "sync":
                res = sync_catalog_from_configs(args.db, config_dir=args.config_dir)
                print(_json.dumps(res, indent=2, ensure_ascii=False))
                return 0
            if args.catalog_action == "list":
                items = list_assets(
                    args.db,
                    classification=args.classification,
                    sla_status=args.sla_status,
                    query=args.q,
                )
                print(
                    _json.dumps(
                        {"count": len(items), "assets": items}, indent=2, ensure_ascii=False
                    )
                )
                return 0
            if args.catalog_action == "register":
                asset = register_asset(
                    args.db,
                    domain=args.domain,
                    display_name=args.display_name,
                    owner=args.owner,
                    steward=args.steward,
                    classification=args.classification,
                    description=args.description,
                    sla_max_age_hours=args.sla_hours,
                )
                print(_json.dumps(asset, indent=2, ensure_ascii=False))
                return 0
            if args.catalog_action == "glossary":
                term_obj = upsert_glossary_term(
                    args.db,
                    term=args.term,
                    definition=args.definition,
                    domain=args.domain,
                    formula=args.formula,
                )
                print(_json.dumps(term_obj, indent=2, ensure_ascii=False))
                return 0
            if args.catalog_action == "search":
                found = search_catalog(args.db, args.query)
                print(_json.dumps(found, indent=2, ensure_ascii=False))
                return 0

        if args.command == "serve":
            return serve_api(host=args.host, port=args.port, db_path=args.db)
        return int(ExitCode.EXECUTION_ERROR)
    except (DataPulseError, ConfigError, FileNotFoundError, ValueError) as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return int(ExitCode.EXECUTION_ERROR)


if __name__ == "__main__":
    raise SystemExit(main())
