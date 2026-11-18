"""Gestion de la quarantaine (Dead-Letter Queue) et du rejeu des données corrigées."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from datapulse.config_loader import load_domain_config
from datapulse.detector import detect_anomalies
from datapulse.ingestion import ingest_file
from datapulse.reconciler import reconcile_findings
from datapulse.storage import DataPulseStorage
from datapulse.validator import validate_dataframe


def partition_clean_and_quarantine(
    annotated_df: pd.DataFrame,
    findings: list[dict[str, Any]],
    *,
    include_stat_anomalies: bool = False,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Sépare les lignes saines des lignes mises en quarantaine."""
    allowed_categories = {"RULE_VIOLATION", "BOTH"}
    if include_stat_anomalies:
        allowed_categories.add("STAT_ANOMALY")

    issues_by_row: dict[int, list[dict[str, Any]]] = {}
    for f in findings:
        if f["category"] in allowed_categories:
            issues_by_row.setdefault(int(f["row_index"]), []).append(f)

    quarantined_indices = sorted(issues_by_row.keys())
    clean_df = annotated_df.drop(index=quarantined_indices, errors="ignore").copy()
    clean_df = clean_df.drop(columns=["_datapulse_status", "_datapulse_issues"], errors="ignore")

    data_cols = [c for c in annotated_df.columns if not str(c).startswith("_datapulse_")]
    quarantine_records: list[dict[str, Any]] = []

    for row_idx in quarantined_indices:
        if row_idx not in annotated_df.index:
            continue
        raw_row = {
            str(col): (
                None if pd.isna(annotated_df.at[row_idx, col]) else annotated_df.at[row_idx, col]
            )
            for col in data_cols
        }
        quarantine_records.append(
            {
                "row_index": row_idx,
                "row_payload_json": json.dumps(raw_row, ensure_ascii=False, default=str),
                "issues_json": json.dumps(issues_by_row[row_idx], ensure_ascii=False, default=str),
            }
        )

    return clean_df, quarantine_records


def export_quarantine_to_csv(
    records: list[dict[str, Any]],
    output_path: str | Path,
) -> Path:
    """Exporte les lignes en quarantaine vers un fichier CSV éditable pour correction."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for rec in records:
        payload = json.loads(rec["row_payload_json"])
        row_dict: dict[str, Any] = {
            "_quarantine_id": rec["quarantine_id"],
            "_source_run_id": rec["run_id"],
            **payload,
        }
        rows.append(row_dict)

    df_out = pd.DataFrame(rows)
    df_out.to_csv(out, index=False)
    return out


def replay_corrected_file(
    file_path: str,
    domain: str,
    *,
    db_path: str = "db/datapulse.sqlite",
    raw_dir: str = "data/raw",
) -> dict[str, Any]:
    """Rejoue un fichier corrigé : valide les lignes et clôture les tickets résolus."""
    config = load_domain_config(domain)
    ingestion = ingest_file(file_path, raw_dir=raw_dir)
    df = ingestion.dataframe

    quarantine_ids: list[int] = []
    if "_quarantine_id" in df.columns:
        quarantine_ids = [
            int(qid) for qid in pd.to_numeric(df["_quarantine_id"], errors="coerce").dropna()
        ]

    eval_df = df.drop(
        columns=["_quarantine_id", "_source_run_id", "_datapulse_status", "_datapulse_issues"],
        errors="ignore",
    )

    violations = validate_dataframe(eval_df, config)
    anomalies, _ = detect_anomalies(eval_df, config)
    findings, _ = reconcile_findings(eval_df, violations, anomalies)

    failed_indices = {
        int(f["row_index"]) for f in findings if f["category"] in {"RULE_VIOLATION", "BOTH"}
    }

    resolved_qids: list[int] = []
    if "_quarantine_id" in df.columns:
        for idx, qid_val in df["_quarantine_id"].items():
            if int(idx) not in failed_indices and pd.notna(qid_val):
                resolved_qids.append(int(qid_val))

    storage = DataPulseStorage(db_path=db_path)
    marked_count = 0
    if resolved_qids:
        marked_count = storage.mark_quarantine_replayed(resolved_qids)

    return {
        "total_replayed_rows": len(eval_df),
        "remaining_violations": len(failed_indices),
        "tracked_quarantine_ids": len(quarantine_ids),
        "resolved_quarantine_ids": resolved_qids,
        "marked_replayed_count": marked_count,
    }
