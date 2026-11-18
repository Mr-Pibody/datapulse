from typing import Any

import pandas as pd


def detect_anomalies(
    df: pd.DataFrame, config: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    anomalies: list[dict[str, Any]] = []
    warnings: list[str] = []

    ad_cfg = config.get("anomaly_detection", {})
    target_cols = ad_cfg.get("columns", [])
    z_thresh = float(ad_cfg.get("zscore_threshold", 3.0))
    iqr_mult = float(ad_cfg.get("iqr_multiplier", 1.5))
    min_samples = int(ad_cfg.get("min_sample_size", 30))

    for col in target_cols:
        if col not in df.columns:
            warnings.append(f"Colonne '{col}' absente du jeu de données : détection ignorée.")
            continue

        series = pd.to_numeric(df[col], errors="coerce").dropna()
        n = len(series)

        if n < min_samples:
            warnings.append(
                f"Limite statistique sur '{col}' : échantillon trop faible (n={n} < {min_samples}), "
                "détection Z-score/IQR désactivée."
            )
            continue

        mean = float(series.mean())
        std = float(series.std(ddof=1))
        q1 = float(series.quantile(0.25))
        q3 = float(series.quantile(0.75))
        iqr = q3 - q1
        lower_iqr = q1 - iqr_mult * iqr
        upper_iqr = q3 + iqr_mult * iqr

        if std == 0.0:
            warnings.append(
                f"Limite statistique sur '{col}' : écart-type nul, Z-score inapplicable."
            )

        for idx, val in series.items():
            val_f = float(val)
            z_score = abs((val_f - mean) / std) if std > 0 else 0.0
            is_z = std > 0 and z_score > z_thresh
            is_iqr = iqr > 0 and (val_f < lower_iqr or val_f > upper_iqr)

            if is_z or is_iqr:
                if is_z and is_iqr:
                    method = "ZSCORE+IQR"
                    score = round(z_score, 3)
                    detail = (
                        f"Z-score={score} (> {z_thresh}) et hors bornes IQR "
                        f"[{round(lower_iqr, 2)}, {round(upper_iqr, 2)}]"
                    )
                elif is_z:
                    method = "ZSCORE"
                    score = round(z_score, 3)
                    detail = f"Z-score={score} (> {z_thresh})"
                else:
                    method = "IQR"
                    score = round(
                        abs(val_f - q3) / iqr if val_f > upper_iqr else abs(q1 - val_f) / iqr, 3
                    )
                    detail = f"Hors bornes IQR [{round(lower_iqr, 2)}, {round(upper_iqr, 2)}]"

                anomalies.append(
                    {
                        "row_index": int(idx),
                        "column_name": col,
                        "raw_value": str(df.at[idx, col]),
                        "stat_method": method,
                        "stat_score": score,
                        "severity": "HIGH" if is_z else "MEDIUM",
                        "explanation": f"Anomalie statistique sur '{col}' (valeur={val_f}) : {detail}",
                    }
                )

    return anomalies, warnings
