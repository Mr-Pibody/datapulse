"""Détection statistique de Data Drift (KS, PSI, Jensen-Shannon) pour DataPulse V8."""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def build_column_sketch(series: pd.Series[Any]) -> dict[str, Any]:
    """Construit une esquisse distributionnelle compacte pour une colonne."""
    clean = series.dropna()
    if clean.empty:
        return {"kind": "empty", "count": 0}

    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        arr = pd.to_numeric(clean, errors="coerce").dropna().to_numpy(dtype=np.float64)
        arr = arr[np.isfinite(arr)]
        if arr.size == 0:
            return {"kind": "empty", "count": 0}

        arr_sorted = np.sort(arr)
        q_grid = np.linspace(0.0, 1.0, 21)
        quantiles = [round(float(x), 6) for x in np.quantile(arr_sorted, q_grid)]
        sample_size = min(int(arr_sorted.size), 200)
        indices = np.linspace(0, int(arr_sorted.size) - 1, sample_size, dtype=int)
        sample = [round(float(x), 6) for x in arr_sorted[indices]]

        return {
            "kind": "numeric",
            "count": int(arr_sorted.size),
            "mean": round(float(np.mean(arr_sorted)), 6),
            "std": round(float(np.std(arr_sorted)), 6),
            "quantiles": quantiles,
            "sample": sample,
        }

    str_vals = clean.astype(str)
    vc = str_vals.value_counts(normalize=True)
    top_probs = {str(k): round(float(v), 6) for k, v in vc.head(25).items()}
    remainder = round(max(0.0, 1.0 - sum(top_probs.values())), 6)
    if remainder > 0.0:
        top_probs["__OTHER__"] = remainder

    return {
        "kind": "categorical",
        "count": len(clean),
        "probs": top_probs,
    }


def attach_distribution_sketches(
    profile: dict[str, Any],
    df: pd.DataFrame,
) -> dict[str, Any]:
    """Enrichit chaque colonne du profil avec son esquisse distributionnelle."""
    columns = profile.get("columns", [])
    if not isinstance(columns, list):
        return profile

    for col_entry in columns:
        if not isinstance(col_entry, dict):
            continue
        col_name = str(col_entry.get("name") or col_entry.get("column_name") or "")
        if col_name and col_name in df.columns:
            col_entry["dist_sketch"] = build_column_sketch(df[col_name])
    return profile


def compute_ks_2samp(sample_a: list[float], sample_b: list[float]) -> tuple[float, float]:
    """Calcule la statistique de Kolmogorov-Smirnov D et sa p-value asymptotique."""
    a = np.sort(np.asarray(sample_a, dtype=np.float64))
    b = np.sort(np.asarray(sample_b, dtype=np.float64))
    if a.size == 0 or b.size == 0:
        return 0.0, 1.0

    all_vals = np.concatenate([a, b])
    cdf_a = np.searchsorted(a, all_vals, side="right") / float(a.size)
    cdf_b = np.searchsorted(b, all_vals, side="right") / float(b.size)
    d_stat = float(np.max(np.abs(cdf_a - cdf_b)))

    en = math.sqrt((a.size * b.size) / float(a.size + b.size))
    lam = (en + 0.12 + (0.11 / max(en, 1e-9))) * d_stat
    if lam <= 0.0:
        return round(d_stat, 6), 1.0

    # Série de Smirnov : 2 * sum_{k=1..100} (-1)^(k-1) * exp(-2 * k^2 * lam^2)
    series_sum = 0.0
    for k in range(1, 101):
        term = math.exp(-2.0 * (k * k) * (lam * lam))
        if term < 1e-12:
            break
        series_sum += term if (k % 2 == 1) else -term
    p_val = max(0.0, min(1.0, 2.0 * series_sum))
    return round(d_stat, 6), round(p_val, 6)


def compute_wasserstein_1d(sample_a: list[float], sample_b: list[float]) -> float:
    """Calcule la distance de Wasserstein-1 (Earth Mover's Distance) normalisee."""
    a = np.sort(np.asarray(sample_a, dtype=np.float64))
    b = np.sort(np.asarray(sample_b, dtype=np.float64))
    if a.size == 0 or b.size == 0:
        return 0.0
    grid = np.linspace(0.0, 1.0, 101)
    qa = np.quantile(a, grid)
    qb = np.quantile(b, grid)
    scale = max(float(np.std(a)), 1e-9)
    return round(float(np.mean(np.abs(qa - qb))) / scale, 6)


def _psi_and_jsd_from_probs(
    p_raw: np.ndarray[Any, np.dtype[np.float64]],
    q_raw: np.ndarray[Any, np.dtype[np.float64]],
) -> tuple[float, float]:
    """Calcule le PSI (lissage de Laplace) et la divergence de Jensen-Shannon."""
    k_bins = max(int(p_raw.size), 1)
    alpha = 0.5 / float(k_bins)
    p = (np.maximum(p_raw, 0.0) + alpha) / (float(np.sum(np.maximum(p_raw, 0.0))) + 0.5)
    q = (np.maximum(q_raw, 0.0) + alpha) / (float(np.sum(np.maximum(q_raw, 0.0))) + 0.5)

    psi = float(np.sum((q - p) * np.log(q / p)))
    m = 0.5 * (p + q)
    jsd = float(0.5 * np.sum(p * np.log(p / m)) + 0.5 * np.sum(q * np.log(q / m)))
    return round(max(0.0, psi), 6), round(max(0.0, jsd), 6)


def _compare_numeric_sketches(
    sketch_a: dict[str, Any],
    sketch_b: dict[str, Any],
) -> tuple[float, float, float, float]:
    """Compare deux esquisses numériques et retourne (ks_d, ks_p, psi, jsd)."""
    sample_a = [float(x) for x in sketch_a.get("sample", [])]
    sample_b = [float(x) for x in sketch_b.get("sample", [])]
    if not sample_a or not sample_b:
        return 0.0, 1.0, 0.0, 0.0

    ks_d, ks_p = compute_ks_2samp(sample_a, sample_b)
    quantiles_a = [float(x) for x in sketch_a.get("quantiles", [])]
    inner_edges = np.unique(np.asarray(quantiles_a[2:-1:2], dtype=np.float64))

    arr_a = np.asarray(sample_a, dtype=np.float64)
    arr_b = np.asarray(sample_b, dtype=np.float64)

    if inner_edges.size == 0:
        const_val = float(arr_a[0])
        shifted_ratio = float(np.mean(arr_b != const_val))
        p_vec = np.array([1.0, 0.0], dtype=np.float64)
        q_vec = np.array([1.0 - shifted_ratio, shifted_ratio], dtype=np.float64)
        psi, jsd = _psi_and_jsd_from_probs(p_vec, q_vec)
        return ks_d, ks_p, psi, jsd

    bin_edges = np.concatenate(([-np.inf], inner_edges, [np.inf]))
    counts_a, _ = np.histogram(arr_a, bins=bin_edges)
    counts_b, _ = np.histogram(arr_b, bins=bin_edges)
    p_vec = counts_a.astype(np.float64) / max(float(arr_a.size), 1.0)
    q_vec = counts_b.astype(np.float64) / max(float(arr_b.size), 1.0)
    psi, jsd = _psi_and_jsd_from_probs(p_vec, q_vec)
    return ks_d, ks_p, psi, jsd


def _compare_categorical_sketches(
    sketch_a: dict[str, Any],
    sketch_b: dict[str, Any],
) -> tuple[float, float, float]:
    """Compare deux esquisses catégorielles et retourne (tv_distance, psi, jsd)."""
    probs_a_map = sketch_a.get("probs", {})
    probs_b_map = sketch_b.get("probs", {})
    if not isinstance(probs_a_map, dict) or not isinstance(probs_b_map, dict):
        return 0.0, 0.0, 0.0

    all_keys = sorted(set(probs_a_map.keys()) | set(probs_b_map.keys()))
    if not all_keys:
        return 0.0, 0.0, 0.0

    p_vec = np.array([float(probs_a_map.get(k, 0.0)) for k in all_keys], dtype=np.float64)
    q_vec = np.array([float(probs_b_map.get(k, 0.0)) for k in all_keys], dtype=np.float64)
    tv_dist = round(float(0.5 * np.sum(np.abs(q_vec - p_vec))), 6)
    psi, jsd = _psi_and_jsd_from_probs(p_vec, q_vec)
    return tv_dist, psi, jsd


def detect_data_drift(
    current_cols: list[dict[str, Any]],
    baseline_cols: list[dict[str, Any]],
    *,
    psi_warning: float = 0.10,
    psi_critical: float = 0.25,
) -> list[dict[str, Any]]:
    """Détecte les dérives statistiques entre la baseline (run A) et le run courant (B)."""
    base_map: dict[str, dict[str, Any]] = {}
    for col in baseline_cols:
        name = str(col.get("name") or col.get("column_name") or "")
        if name:
            base_map[name] = col

    drifts: list[dict[str, Any]] = []
    for curr in current_cols:
        name = str(curr.get("name") or curr.get("column_name") or "")
        if not name or name not in base_map:
            continue

        base = base_map[name]
        sketch_a = base.get("dist_sketch")
        sketch_b = curr.get("dist_sketch")
        if not isinstance(sketch_a, dict) or not isinstance(sketch_b, dict):
            continue

        kind_a = sketch_a.get("kind")
        kind_b = sketch_b.get("kind")
        if kind_a != kind_b or kind_a == "empty":
            continue

        if kind_a == "numeric":
            ks_d, ks_p, psi, jsd = _compare_numeric_sketches(sketch_a, sketch_b)
            severity = "NONE"
            if psi >= psi_critical or (ks_p < 0.01 and ks_d >= 0.20):
                severity = "CRITICAL"
            elif psi >= psi_warning or (ks_p < 0.05 and ks_d >= 0.12):
                severity = "WARNING"

            if severity != "NONE":
                drifts.append(
                    {
                        "column_name": name,
                        "drift_kind": "NUMERIC_DISTRIBUTION",
                        "severity": severity,
                        "psi": psi,
                        "js_divergence": jsd,
                        "ks_statistic": ks_d,
                        "ks_pvalue": ks_p,
                        "explanation": (
                            f"Dérive numérique sur '{name}' : PSI={psi:.3f}, "
                            f"KS D={ks_d:.3f} (p={ks_p:.4f}), JSD={jsd:.3f}"
                        ),
                    }
                )
        elif kind_a == "categorical":
            tv_dist, psi, jsd = _compare_categorical_sketches(sketch_a, sketch_b)
            severity = "NONE"
            if psi >= psi_critical or tv_dist >= 0.25:
                severity = "CRITICAL"
            elif psi >= psi_warning or tv_dist >= 0.12:
                severity = "WARNING"

            if severity != "NONE":
                drifts.append(
                    {
                        "column_name": name,
                        "drift_kind": "CATEGORICAL_DISTRIBUTION",
                        "severity": severity,
                        "psi": psi,
                        "js_divergence": jsd,
                        "ks_statistic": tv_dist,
                        "ks_pvalue": None,
                        "explanation": (
                            f"Dérive catégorielle sur '{name}' : PSI={psi:.3f}, "
                            f"TV={tv_dist:.3f}, JSD={jsd:.3f}"
                        ),
                    }
                )

    return drifts


def save_run_sketches(db_path: str | Path, run_id: str, profile: dict[str, Any]) -> None:
    """Persiste les esquisses distributionnelles d'un run dans SQLite."""
    columns = profile.get("columns", [])
    if not isinstance(columns, list):
        return

    rows_to_insert: list[tuple[str, str, str]] = []
    for col in columns:
        if not isinstance(col, dict):
            continue
        col_name = str(col.get("name") or col.get("column_name") or "")
        sketch = col.get("dist_sketch")
        if col_name and isinstance(sketch, dict):
            rows_to_insert.append((run_id, col_name, json.dumps(sketch)))

    if not rows_to_insert:
        return

    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS column_sketches ("
            "run_id TEXT NOT NULL, "
            "column_name TEXT NOT NULL, "
            "sketch_json TEXT NOT NULL, "
            "PRIMARY KEY (run_id, column_name))"
        )
        conn.executemany(
            "INSERT OR REPLACE INTO column_sketches (run_id, column_name, sketch_json) "
            "VALUES (?, ?, ?)",
            rows_to_insert,
        )


def load_run_column_profiles(db_path: str | Path, run_id: str) -> list[dict[str, Any]]:
    """Charge les profils de colonnes et leurs esquisses distributionnelles depuis SQLite."""
    path = Path(db_path)
    if not path.exists():
        return []

    try:
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            tbl = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='column_sketches'"
            ).fetchone()
            if tbl is None:
                return []

            rows = conn.execute(
                "SELECT column_name, sketch_json FROM column_sketches WHERE run_id = ?",
                (run_id,),
            ).fetchall()
    except sqlite3.Error:
        return []

    profiles: list[dict[str, Any]] = []
    for row in rows:
        try:
            sketch = json.loads(str(row["sketch_json"]))
        except json.JSONDecodeError:
            continue
        if isinstance(sketch, dict):
            col_name = str(row["column_name"])
            profiles.append(
                {
                    "name": col_name,
                    "column_name": col_name,
                    "dist_sketch": sketch,
                }
            )
    return profiles
