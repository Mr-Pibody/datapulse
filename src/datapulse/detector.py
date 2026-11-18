"""Détection d'anomalies statistiques univariées (Z-score, IQR, MAD) et multivariées."""

from __future__ import annotations

from typing import Any

import numpy as np
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
    mad_thresh = float(ad_cfg.get("mad_threshold", 3.5))
    enable_mad = bool(ad_cfg.get("enable_mad", True))
    enable_multivariate = bool(ad_cfg.get("enable_multivariate", False))
    multivariate_thresh = float(ad_cfg.get("multivariate_threshold", 4.5))
    min_samples = int(ad_cfg.get("min_sample_size", 30))

    valid_numeric_cols: list[str] = []

    for col in target_cols:
        if col not in df.columns:
            warnings.append(f"Colonne '{col}' absente du jeu de données : détection ignorée.")
            continue

        series = pd.to_numeric(df[col], errors="coerce").dropna()
        n = len(series)

        if n < min_samples:
            warnings.append(
                f"Limite statistique sur '{col}' : échantillon trop faible "
                f"(n={n} < {min_samples}), détection statistique désactivée."
            )
            continue

        valid_numeric_cols.append(col)
        mean = float(series.mean())
        std = float(series.std(ddof=1))
        median = float(series.median())
        mad = float((series - median).abs().median())
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
            mod_z = (0.6745 * abs(val_f - median) / mad) if (enable_mad and mad > 0) else 0.0

            is_z = std > 0 and z_score > z_thresh
            is_iqr = iqr > 0 and (val_f < lower_iqr or val_f > upper_iqr)
            is_mad = enable_mad and mad > 0 and mod_z > mad_thresh

            if is_z or is_iqr or is_mad:
                methods: list[str] = []
                details: list[str] = []
                score = 0.0

                if is_z:
                    methods.append("ZSCORE")
                    score = max(score, round(z_score, 3))
                    details.append(f"Z-score={round(z_score, 3)} (> {z_thresh})")
                if is_mad:
                    methods.append("MAD")
                    score = max(score, round(mod_z, 3))
                    details.append(f"Modified-Z={round(mod_z, 3)} (> {mad_thresh})")
                if is_iqr:
                    methods.append("IQR")
                    iqr_dist = abs(val_f - q3) / iqr if val_f > upper_iqr else abs(q1 - val_f) / iqr
                    score = max(score, round(iqr_dist, 3))
                    details.append(
                        f"Hors bornes IQR [{round(lower_iqr, 2)}, {round(upper_iqr, 2)}]"
                    )

                method_str = "+".join(methods)
                detail_str = " et ".join(details)
                anomalies.append(
                    {
                        "row_index": int(idx),
                        "column_name": col,
                        "raw_value": str(df.at[idx, col]),
                        "stat_method": method_str,
                        "stat_score": score,
                        "severity": "HIGH" if (is_z or is_mad) else "MEDIUM",
                        "explanation": (
                            f"Anomalie statistique sur '{col}' (valeur={val_f}) : {detail_str}"
                        ),
                    }
                )

    # Détection multivariée robuste (norme L2 des écarts MAD standardisés sur >= 2 colonnes)
    if enable_multivariate and len(valid_numeric_cols) >= 2:
        sub = df[valid_numeric_cols].apply(pd.to_numeric, errors="coerce").dropna()
        if len(sub) >= min_samples:
            medians = sub.median()
            mads = (sub - medians).abs().median().replace(0.0, np.nan)
            usable_cols = [c for c in valid_numeric_cols if pd.notna(mads[c])]
            if len(usable_cols) >= 2:
                z_mat = 0.6745 * (sub[usable_cols] - medians[usable_cols]).abs() / mads[usable_cols]
                l2_scores = np.sqrt((z_mat**2).sum(axis=1))
                col_group = "+".join(usable_cols)
                for idx, mv_score in l2_scores.items():
                    mv_f = float(mv_score)
                    if mv_f > multivariate_thresh:
                        raw_pair = {c: str(df.at[idx, c]) for c in usable_cols}
                        anomalies.append(
                            {
                                "row_index": int(idx),
                                "column_name": col_group,
                                "raw_value": str(raw_pair),
                                "stat_method": "MULTIVARIATE_MAD",
                                "stat_score": round(mv_f, 3),
                                "severity": "HIGH",
                                "explanation": (
                                    f"Anomalie multivariée sur ({col_group}) : "
                                    f"score L2-MAD={round(mv_f, 3)} (> {multivariate_thresh})"
                                ),
                            }
                        )

    return anomalies, warnings
