import argparse
import sys
import time
import tracemalloc
import uuid
from datetime import datetime, timezone
from pathlib import Path

from datapulse.benchmark import evaluate_against_reference
from datapulse.config_loader import ConfigError, load_domain_config
from datapulse.detector import detect_anomalies
from datapulse.ingestion import ingest_file
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
) -> int:
    run_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()

    tracemalloc.start()
    t0 = time.perf_counter()

    config = load_domain_config(domain)
    ingestion = ingest_file(file_path)
    df = ingestion.dataframe

    profile = profile_dataframe(df)
    violations = validate_dataframe(df, config)
    anomalies, warnings = detect_anomalies(df, config)
    findings, annotated_df = reconcile_findings(df, violations, anomalies)

    # Export non-destructif dans data/processed/
    out_processed = Path(processed_dir)
    out_processed.mkdir(parents=True, exist_ok=True)
    processed_file = out_processed / f"{domain}_{run_id[:8]}_annotated.csv"
    annotated_df.to_csv(processed_file, index=False)

    benchmark_metrics = None
    if ref_path:
        benchmark_metrics = evaluate_against_reference(df, ref_path, findings, domain)

    duration_ms = round((time.perf_counter() - t0) * 1000, 2)
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    peak_memory_kb = round(peak_bytes / 1024, 2)

    run_meta = {
        "run_id": run_id,
        "timestamp": timestamp,
        "domain": domain,
        "source_file": str(ingestion.raw_copy_path),
        "file_hash_sha256": ingestion.file_hash_sha256,
        "config_version": str(config.get("version", "1.0")),
        "row_count": ingestion.row_count,
        "duration_ms": duration_ms,
        "peak_memory_kb": peak_memory_kb,
        "status": "SUCCESS",
    }

    storage = DataPulseStorage(db_path=db_path)
    storage.save_run(run_meta, profile, findings)
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

    print(f"\n=== DataPulse V1 | Run {run_id[:8]} ({domain}) ===")
    print(f"Fichier     : {ingestion.raw_copy_path} ({ingestion.row_count} lignes)")
    print(f"Complétude  : {profile['completeness_pct']}%")
    print(f"Incidents   : {len(findings)} uniques")
    print(f"Performance : {duration_ms} ms | Mémoire max : {peak_memory_kb} Ko")
    if benchmark_metrics:
        print(
            f"Benchmark   : Précision={benchmark_metrics['precision'] * 100:.1f}% | "
            f"Rappel={benchmark_metrics['recall'] * 100:.1f}% | "
            f"F1={benchmark_metrics['f1_score'] * 100:.1f}%"
        )
    print(f"Rapport HTML: {artifacts['html']}")
    print(f"Données ann.: {processed_file}\n")
    return 0


def show_history(domain: str | None = None, db_path: str = "db/datapulse.sqlite") -> int:
    storage = DataPulseStorage(db_path=db_path)
    runs = storage.get_history(domain=domain, limit=10)
    if not runs:
        print("Aucun historique enregistré dans SQLite.")
        return 0

    print("\n=== Historique des exécutions DataPulse ===")
    for r in runs:
        print(
            f"[{r['timestamp'][:19]}] {r['domain']:<14} | run={r['run_id'][:8]} | "
            f"lignes={r['row_count']} | incidents={r['findings_count']} | "
            f"{r['duration_ms']} ms"
        )
    print()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="datapulse",
        description="DataPulse V1 — Observatoire générique de qualité et d'anomalies de données",
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

    p_hist = sub.add_parser("history", help="Affiche l'historique des analyses SQLite")
    p_hist.add_argument("--domain", required=False, help="Filtrer par domaine")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "run-all":
            return run_pipeline(file_path=args.file, domain=args.domain, ref_path=args.ref)
        if args.command == "history":
            return show_history(domain=args.domain)
        return 1
    except (ConfigError, FileNotFoundError, ValueError) as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
