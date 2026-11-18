from typing import Any

import pandas as pd


def reconcile_findings(
    df: pd.DataFrame,
    violations: list[dict[str, Any]],
    anomalies: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    merged: dict[tuple[int, str], dict[str, Any]] = {}

    for v in violations:
        key = (v["row_index"], v["column_name"])
        if key not in merged:
            merged[key] = {
                "row_index": v["row_index"],
                "column_name": v["column_name"],
                "raw_value": v["raw_value"],
                "category": "RULE_VIOLATION",
                "rule_id": v["rule_id"],
                "stat_method": "NONE",
                "stat_score": None,
                "severity": v["severity"],
                "explanation": v["explanation"],
            }
        else:
            # Si plusieurs règles échouent sur la même cellule
            merged[key]["rule_id"] += f",{v['rule_id']}"
            merged[key]["explanation"] += f" | {v['explanation']}"

    for a in anomalies:
        key = (a["row_index"], a["column_name"])
        if key in merged:
            merged[key]["category"] = "BOTH"
            merged[key]["stat_method"] = a["stat_method"]
            merged[key]["stat_score"] = a["stat_score"]
            merged[key]["severity"] = "HIGH"
            merged[key]["explanation"] += f" ET {a['explanation']}"
        else:
            merged[key] = {
                "row_index": a["row_index"],
                "column_name": a["column_name"],
                "raw_value": a["raw_value"],
                "category": "STAT_ANOMALY",
                "rule_id": "NONE",
                "stat_method": a["stat_method"],
                "stat_score": a["stat_score"],
                "severity": a["severity"],
                "explanation": a["explanation"],
            }

    findings = sorted(merged.values(), key=lambda x: (x["row_index"], x["column_name"]))

    # Prépare une copie annotée pour data/processed/ (sans toucher à data/raw/)
    annotated_df = df.copy()
    annotated_df["_datapulse_status"] = "OK"
    annotated_df["_datapulse_issues"] = ""

    for f in findings:
        idx = f["row_index"]
        if idx in annotated_df.index:
            current_status = annotated_df.at[idx, "_datapulse_status"]
            if current_status == "OK":
                annotated_df.at[idx, "_datapulse_status"] = f["category"]
            elif current_status != f["category"]:
                annotated_df.at[idx, "_datapulse_status"] = "BOTH"

            prev = annotated_df.at[idx, "_datapulse_issues"]
            sep = " ; " if prev else ""
            annotated_df.at[idx, "_datapulse_issues"] = f"{prev}{sep}{f['explanation']}"

    return findings, annotated_df
