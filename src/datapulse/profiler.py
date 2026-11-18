"""Profilage statistique (batch et streaming) et détection de dérive de schéma."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


def profile_dataframe(df: pd.DataFrame) -> dict[str, Any]:
    total_rows = len(df)
    total_cells = total_rows * len(df.columns) if total_rows > 0 else 0
    total_nulls = int(df.isna().sum().sum())
    completeness = (
        round(((total_cells - total_nulls) / total_cells) * 100, 2) if total_cells > 0 else 100.0
    )

    columns_profile: list[dict[str, Any]] = []

    for col in df.columns:
        series = df[col]
        null_count = int(series.isna().sum())
        col_info: dict[str, Any] = {
            "column_name": str(col),
            "dtype": str(series.dtype),
            "null_count": null_count,
            "null_pct": round((null_count / total_rows) * 100, 2) if total_rows > 0 else 0.0,
            "mean": None,
            "median": None,
            "std": None,
            "min": None,
            "max": None,
        }

        if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
            clean_s = series.dropna()
            if not clean_s.empty:
                col_info["mean"] = round(float(clean_s.mean()), 4)
                col_info["median"] = round(float(clean_s.median()), 4)
                col_info["std"] = round(float(clean_s.std(ddof=1)), 4) if len(clean_s) > 1 else 0.0
                col_info["min"] = round(float(clean_s.min()), 4)
                col_info["max"] = round(float(clean_s.max()), 4)

        columns_profile.append(col_info)

    return {
        "row_count": total_rows,
        "column_count": len(df.columns),
        "total_nulls": total_nulls,
        "completeness_pct": completeness,
        "columns": columns_profile,
    }


@dataclass
class _StreamingColumnAccumulator:
    """Accumulateur incrémental (algorithme de Welford + réservoir borné pour la médiane)."""

    column_name: str
    dtype: str
    null_count: int = 0
    numeric_count: int = 0
    mean: float = 0.0
    m2: float = 0.0
    min_val: float | None = None
    max_val: float | None = None
    reservoir: list[float] = field(default_factory=list)
    reservoir_cap: int = 10_000
    _rng: np.random.Generator = field(default_factory=lambda: np.random.default_rng(42))

    def update(self, series: pd.Series[Any]) -> None:
        self.null_count += int(series.isna().sum())
        if not pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(series):
            return

        values = series.dropna().to_numpy(dtype=float)
        if values.size == 0:
            return

        batch_min = float(np.min(values))
        batch_max = float(np.max(values))
        self.min_val = batch_min if self.min_val is None else min(self.min_val, batch_min)
        self.max_val = batch_max if self.max_val is None else max(self.max_val, batch_max)

        # Combinaison vectorisée de Chan / Welford entre l'état courant et le chunk
        n_a = self.numeric_count
        n_b = int(values.size)
        mean_b = float(np.mean(values))
        m2_b = float(np.sum((values - mean_b) ** 2))

        if n_a == 0:
            self.numeric_count = n_b
            self.mean = mean_b
            self.m2 = m2_b
        else:
            delta = mean_b - self.mean
            total_n = n_a + n_b
            self.mean = self.mean + delta * (n_b / total_n)
            self.m2 = self.m2 + m2_b + (delta**2) * (n_a * n_b / total_n)
            self.numeric_count = total_n

        # Échantillonnage de réservoir borné (O(1) mémoire) pour estimer la médiane
        if len(self.reservoir) < self.reservoir_cap:
            space = self.reservoir_cap - len(self.reservoir)
            self.reservoir.extend(values[:space].tolist())
        elif values.size > 0:
            idx_sample = self._rng.integers(0, self.numeric_count, size=min(64, values.size))
            for idx, val in zip(idx_sample, values[: len(idx_sample)], strict=False):
                if int(idx) < self.reservoir_cap:
                    self.reservoir[int(idx)] = float(val)

    def to_profile_dict(self, total_rows: int) -> dict[str, Any]:
        col_info: dict[str, Any] = {
            "column_name": self.column_name,
            "dtype": self.dtype,
            "null_count": self.null_count,
            "null_pct": round((self.null_count / total_rows) * 100, 2) if total_rows > 0 else 0.0,
            "mean": None,
            "median": None,
            "std": None,
            "min": None,
            "max": None,
        }
        if self.numeric_count > 0 and self.min_val is not None and self.max_val is not None:
            variance = self.m2 / (self.numeric_count - 1) if self.numeric_count > 1 else 0.0
            col_info["mean"] = round(self.mean, 4)
            col_info["median"] = round(float(np.median(self.reservoir)), 4)
            col_info["std"] = round(math.sqrt(max(0.0, variance)), 4)
            col_info["min"] = round(self.min_val, 4)
            col_info["max"] = round(self.max_val, 4)
        return col_info


class StreamingProfiler:
    """Calcule le profil complet d'un dataset chunk par chunk à mémoire constante."""

    def __init__(self) -> None:
        self.total_rows: int = 0
        self._columns_order: list[str] = []
        self._accumulators: dict[str, _StreamingColumnAccumulator] = {}

    def update(self, chunk: pd.DataFrame) -> None:
        if not self._columns_order:
            self._columns_order = [str(c) for c in chunk.columns]
            for col in self._columns_order:
                self._accumulators[col] = _StreamingColumnAccumulator(
                    column_name=col,
                    dtype=str(chunk[col].dtype),
                )

        self.total_rows += len(chunk)
        for col in self._columns_order:
            if col in chunk.columns:
                self._accumulators[col].update(chunk[col])

    def finalize(self) -> dict[str, Any]:
        col_count = len(self._columns_order)
        total_cells = self.total_rows * col_count if self.total_rows > 0 else 0
        total_nulls = sum(acc.null_count for acc in self._accumulators.values())
        completeness = (
            round(((total_cells - total_nulls) / total_cells) * 100, 2)
            if total_cells > 0
            else 100.0
        )
        columns_profile = [
            self._accumulators[col].to_profile_dict(self.total_rows) for col in self._columns_order
        ]
        return {
            "row_count": self.total_rows,
            "column_count": col_count,
            "total_nulls": total_nulls,
            "completeness_pct": completeness,
            "columns": columns_profile,
        }


def detect_schema_drift(
    current_profile: dict[str, Any],
    baseline_columns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compare le schéma courant avec un profil de référence et liste les dérives."""
    if not baseline_columns:
        return []

    base_map = {str(c["column_name"]): c for c in baseline_columns}
    curr_map = {str(c["column_name"]): c for c in current_profile.get("columns", [])}
    drifts: list[dict[str, Any]] = []

    for col_name in sorted(set(curr_map) - set(base_map)):
        drifts.append(
            {
                "drift_type": "ADDED_COLUMN",
                "column_name": col_name,
                "previous_value": None,
                "current_value": curr_map[col_name]["dtype"],
                "severity": "MEDIUM",
                "explanation": (
                    f"Nouvelle colonne détectée : '{col_name}' ({curr_map[col_name]['dtype']})"
                ),
            }
        )

    for col_name in sorted(set(base_map) - set(curr_map)):
        drifts.append(
            {
                "drift_type": "REMOVED_COLUMN",
                "column_name": col_name,
                "previous_value": base_map[col_name]["dtype"],
                "current_value": None,
                "severity": "HIGH",
                "explanation": f"Colonne attendue absente : '{col_name}'",
            }
        )

    for col_name in sorted(set(base_map) & set(curr_map)):
        base_col = base_map[col_name]
        curr_col = curr_map[col_name]
        if str(base_col["dtype"]) != str(curr_col["dtype"]):
            drifts.append(
                {
                    "drift_type": "TYPE_CHANGED",
                    "column_name": col_name,
                    "previous_value": str(base_col["dtype"]),
                    "current_value": str(curr_col["dtype"]),
                    "severity": "HIGH",
                    "explanation": (
                        f"Changement de type sur '{col_name}' : "
                        f"{base_col['dtype']} -> {curr_col['dtype']}"
                    ),
                }
            )

        base_nulls = int(base_col.get("null_count", 0))
        curr_nulls = int(curr_col.get("null_count", 0))
        if base_nulls == 0 and curr_nulls > 0:
            drifts.append(
                {
                    "drift_type": "NULLABILITY_SPIKE",
                    "column_name": col_name,
                    "previous_value": 0,
                    "current_value": curr_nulls,
                    "severity": "MEDIUM",
                    "explanation": (
                        f"Apparition de {curr_nulls} valeur(s) nulle(s) sur '{col_name}' "
                        "(0 auparavant)"
                    ),
                }
            )

    return drifts
