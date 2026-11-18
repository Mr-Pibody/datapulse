from datetime import datetime
from typing import Any

import pandas as pd


def validate_dataframe(df: pd.DataFrame, config: dict[str, Any]) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    rules = config.get("rules", [])
    today = pd.Timestamp(datetime.now().date())

    for rule in rules:
        rule_id = rule["id"]
        check = rule["check"]
        severity = rule.get("severity", "MEDIUM")
        message = rule.get("message", f"Violation de la règle {rule_id}")

        # Règles multi-colonnes : doublons
        if check == "unique_combination":
            cols = rule["columns"]
            if all(c in df.columns for c in cols):
                dup_mask = df.duplicated(subset=cols, keep="first")
                col_label = ",".join(cols)
                for idx in df.index[dup_mask]:
                    val = {c: str(df.at[idx, c]) for c in cols}
                    violations.append(
                        {
                            "row_index": int(idx),
                            "column_name": col_label,
                            "raw_value": str(val),
                            "rule_id": rule_id,
                            "severity": severity,
                            "explanation": f"[{rule_id}] {message} (valeur={val})",
                        }
                    )
            continue

        # Règles mono-colonne
        col = rule.get("column")
        if not col or col not in df.columns:
            continue

        series = df[col]

        if check == "not_null_or_empty":
            mask = series.isna() | (series.astype(str).str.strip() == "")
        elif check == "gt":
            num_s = pd.to_numeric(series, errors="coerce")
            mask = num_s.notna() & (num_s <= rule["value"])
        elif check == "gte":
            num_s = pd.to_numeric(series, errors="coerce")
            mask = num_s.notna() & (num_s < rule["value"])
        elif check == "lt":
            num_s = pd.to_numeric(series, errors="coerce")
            mask = num_s.notna() & (num_s >= rule["value"])
        elif check == "lte":
            num_s = pd.to_numeric(series, errors="coerce")
            mask = num_s.notna() & (num_s > rule["value"])
        elif check == "in_set":
            allowed = set(rule.get("values", []))
            mask = series.notna() & (~series.astype(str).isin(allowed))
        elif check == "not_future":
            dt_s = pd.to_datetime(series, errors="coerce")
            mask = dt_s.notna() & (dt_s > today)
        else:
            continue

        for idx in df.index[mask]:
            raw_val = df.at[idx, col]
            violations.append(
                {
                    "row_index": int(idx),
                    "column_name": col,
                    "raw_value": "" if pd.isna(raw_val) else str(raw_val),
                    "rule_id": rule_id,
                    "severity": severity,
                    "explanation": f"[{rule_id}] {message} (valeur={raw_val})",
                }
            )

    return violations
