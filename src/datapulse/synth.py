"""Générateur de données synthétiques et injection de chaos contrôlé — DataPulse V16."""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from datapulse.config_loader import ConfigError

VALID_CHAOS_MODES: tuple[str, ...] = (
    "nulls",
    "outliers",
    "negatives",
    "invalid_category",
    "duplicates",
    "schema_drift",
)


def _load_contract_yaml(domain: str, configs_dir: str = "configs") -> dict[str, Any]:
    """Charge un contrat YAML de domaine ou fournit un schéma par défaut."""
    clean_domain = domain.strip().lower()
    if clean_domain in {"", "auto", "default"}:
        return {
            "domain": "synthetic_default",
            "columns": {
                "transaction_id": {"type": "str"},
                "client_email": {"type": "str"},
                "categorie": {"type": "str"},
                "montant": {"type": "float"},
                "quantite": {"type": "int"},
                "date_operation": {"type": "date"},
            },
            "rules": [
                {"column": "transaction_id", "rule": "unique"},
                {"column": "montant", "rule": "gt", "value": 0},
                {"column": "quantite", "rule": "between", "min": 1, "max": 500},
                {
                    "column": "categorie",
                    "rule": "in_set",
                    "values": ["STANDARD", "PREMIUM", "ENTERPRISE"],
                },
            ],
            "anomaly_detection": {"columns": ["montant", "quantite"]},
        }

    yaml_path = Path(configs_dir) / f"{clean_domain}.yaml"
    if not yaml_path.exists():
        raise ConfigError(f"Contrat YAML introuvable pour le domaine '{domain}' : {yaml_path}")
    loaded = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ConfigError(f"Contrat YAML invalide : {yaml_path}")
    return loaded


def _extract_column_specs(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Construit la spécification de génération de chaque colonne à partir du contrat."""
    specs: dict[str, dict[str, Any]] = {}

    raw_cols = contract.get("columns")
    if isinstance(raw_cols, dict):
        for col_name, meta in raw_cols.items():
            dtype = meta.get("type", "str") if isinstance(meta, dict) else str(meta)
            specs[str(col_name)] = {"dtype": str(dtype).lower()}
    elif isinstance(raw_cols, list):
        for col_name in raw_cols:
            specs[str(col_name)] = {"dtype": "auto"}

    for req_col in contract.get("required_columns", []) or []:
        specs.setdefault(str(req_col), {"dtype": "auto"})

    anom_cols = (contract.get("anomaly_detection") or {}).get("columns") or []
    for acol in anom_cols:
        specs.setdefault(str(acol), {"dtype": "float"})
        if specs[str(acol)]["dtype"] == "auto":
            specs[str(acol)]["dtype"] = "float"

    for rule in contract.get("rules", []) or []:
        if not isinstance(rule, dict):
            continue
        col = str(rule.get("column", "")).strip()
        if not col:
            continue
        spec = specs.setdefault(col, {"dtype": "auto"})
        rtype = str(rule.get("rule") or rule.get("type") or "").strip().lower()

        if rtype == "unique":
            spec["unique"] = True
        elif rtype in {"gt", "gte"}:
            raw_min = rule.get("value") if rule.get("value") is not None else rule.get("min")
            val = float(raw_min) if raw_min is not None else 0.0
            spec["min"] = val + (0.01 if rtype == "gt" else 0.0)
            if spec["dtype"] == "auto":
                spec["dtype"] = "float"
        elif rtype in {"lt", "lte"}:
            raw_max = rule.get("value") if rule.get("value") is not None else rule.get("max")
            val = float(raw_max) if raw_max is not None else 1000.0
            spec["max"] = val - (0.01 if rtype == "lt" else 0.0)
            if spec["dtype"] == "auto":
                spec["dtype"] = "float"
        elif rtype == "between":
            spec["min"] = float(rule.get("min", 1))
            spec["max"] = float(rule.get("max", 1000))
            if spec["dtype"] == "auto":
                spec["dtype"] = "float"
        elif rtype == "in_set":
            allowed = rule.get("values") or rule.get("allowed") or []
            if isinstance(allowed, list) and allowed:
                spec["allowed"] = list(allowed)
                spec["dtype"] = "category"
        elif rtype == "regex":
            spec["pattern"] = str(rule.get("pattern", ""))

    if not specs:
        specs = {
            "record_id": {"dtype": "str", "unique": True},
            "valeur": {"dtype": "float", "min": 10.0, "max": 500.0},
            "statut": {"dtype": "category", "allowed": ["OK", "PENDING"]},
        }

    return specs


def _generate_column_series(
    col_name: str,
    spec: dict[str, Any],
    rows: int,
    rng: np.random.Generator,
) -> list[Any]:
    """Génère une colonne réaliste conforme aux règles du contrat et à son nom."""
    lower = col_name.lower()
    dtype = spec.get("dtype", "auto")

    if spec.get("allowed"):
        choices = spec["allowed"]
        idx = rng.integers(0, len(choices), size=rows)
        return [choices[int(i)] for i in idx]

    if any(k in lower for k in ("email", "mail", "courriel")):
        domains = ["example.com", "corp.org", "entreprise.bj", "client.fr"]
        return [f"user{i + 1}@{domains[int(rng.integers(0, len(domains)))]}" for i in range(rows)]

    if any(k in lower for k in ("phone", "tel", "telephone", "mobile")):
        return [f"+22997{int(rng.integers(100000, 999999))}" for _ in range(rows)]

    if "iban" in lower:
        return [f"FR763000600001{int(rng.integers(10**11, 10**12 - 1))}89" for _ in range(rows)]

    if any(k in lower for k in ("date", "timestamp", "jour")) or dtype == "date":
        base = date(2026, 1, 1)
        offsets = rng.integers(0, 320, size=rows)
        return [(base + timedelta(days=int(d))).isoformat() for d in offsets]

    if any(k in lower for k in ("id", "code", "ref", "matricule", "numero")) or spec.get("unique"):
        prefix = re.sub(r"[^A-Z0-9]", "", col_name.upper())[:4] or "REC"
        if dtype in {"int", "integer"}:
            return list(range(1001, 1001 + rows))
        return [f"{prefix}-{i + 1:06d}" for i in range(rows)]

    is_int = dtype in {"int", "integer"} or any(
        k in lower for k in ("quantite", "qty", "count", "nb_", "delai", "age")
    )
    is_float = dtype in {"float", "number", "numeric", "decimal"} or any(
        k in lower
        for k in ("montant", "amount", "prix", "price", "total", "solde", "taux", "revenu")
    )

    if is_int:
        low = int(spec.get("min", 1))
        high = max(low + 1, int(spec.get("max", 100)))
        return [int(v) for v in rng.integers(low, high + 1, size=rows)]

    if is_float:
        low_f = float(spec.get("min", 10.0))
        high_f = max(low_f + 1.0, float(spec.get("max", 1000.0)))
        # Distribution normale centrée pour des tests statistiques réalistes
        mean = (low_f + high_f) / 2.0
        std = max((high_f - low_f) / 6.0, 1.0)
        vals = np.clip(rng.normal(loc=mean, scale=std, size=rows), low_f, high_f)
        return [round(float(v), 2) for v in vals]

    categories = ["ACTIF", "VALIDE", "STANDARD", "PRIORITAIRE"]
    idx = rng.integers(0, len(categories), size=rows)
    return [categories[int(i)] for i in idx]


def generate_synthetic_dataframe(
    domain: str,
    *,
    rows: int = 200,
    chaos_rate: float = 0.0,
    chaos_modes: list[str] | None = None,
    seed: int = 42,
    configs_dir: str = "configs",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Génère un DataFrame synthétique avec injection optionnelle d'anomalies."""
    if rows < 1 or rows > 500_000:
        raise ValueError("Le nombre de lignes doit être compris entre 1 et 500 000.")
    if chaos_rate < 0.0 or chaos_rate > 1.0:
        raise ValueError("Le taux de chaos (chaos_rate) doit être compris entre 0.0 et 1.0.")

    contract = _load_contract_yaml(domain, configs_dir=configs_dir)
    resolved_domain = str(contract.get("domain", domain))
    specs = _extract_column_specs(contract)
    rng = np.random.default_rng(seed)

    data: dict[str, list[Any]] = {}
    for col_name, spec in specs.items():
        data[col_name] = _generate_column_series(col_name, spec, rows, rng)

    df = pd.DataFrame(data)
    active_modes: list[str] = []
    if chaos_rate > 0.0:
        requested = chaos_modes if chaos_modes else ["nulls", "outliers", "negatives"]
        for m in requested:
            clean_m = m.strip().lower()
            if clean_m not in VALID_CHAOS_MODES:
                raise ValueError(
                    f"Mode de chaos invalide '{m}'. Choix : {', '.join(VALID_CHAOS_MODES)}."
                )
            if clean_m not in active_modes:
                active_modes.append(clean_m)

    injected_faults = 0
    if chaos_rate > 0.0 and active_modes:
        target_count = max(1, round(rows * chaos_rate))
        numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
        cat_cols = [
            c
            for c in df.columns
            if not pd.api.types.is_numeric_dtype(df[c]) and not specs.get(c, {}).get("unique")
        ]

        for mode in active_modes:
            sample_idx = rng.choice(len(df), size=min(target_count, len(df)), replace=False)
            if mode == "nulls" and len(df.columns) > 0:
                target_col = str(df.columns[min(1, len(df.columns) - 1)])
                df[target_col] = df[target_col].astype(object)
                df.loc[sample_idx, target_col] = None
                injected_faults += len(sample_idx)
            elif mode == "outliers" and numeric_cols:
                ncol = numeric_cols[0]
                df[ncol] = df[ncol].astype(float)
                df.loc[sample_idx, ncol] = (df.loc[sample_idx, ncol].abs() + 50.0) * 100.0
                injected_faults += len(sample_idx)
            elif mode == "negatives" and numeric_cols:
                ncol = numeric_cols[0]
                df[ncol] = df[ncol].astype(float)
                df.loc[sample_idx, ncol] = -1.0 * (df.loc[sample_idx, ncol].abs() + 10.0)
                injected_faults += len(sample_idx)
            elif mode == "invalid_category" and cat_cols:
                ccol = cat_cols[0]
                df[ccol] = df[ccol].astype(object)
                df.loc[sample_idx, ccol] = "CHAOS_INVALID_999"
                injected_faults += len(sample_idx)
            elif mode == "duplicates" and len(df) > 0:
                dup_rows = df.iloc[sample_idx].copy()
                df = pd.concat([df, dup_rows], ignore_index=True)
                injected_faults += len(dup_rows)
            elif mode == "schema_drift":
                df["chaos_unexpected_col"] = "DRIFT_EVENT"
                injected_faults += 1

    manifest = {
        "domain": resolved_domain,
        "rows_generated": len(df),
        "columns": [str(c) for c in df.columns],
        "seed": seed,
        "chaos_rate": chaos_rate,
        "chaos_modes_applied": active_modes,
        "injected_faults_count": injected_faults,
    }
    return df, manifest


def generate_synthetic_file(
    domain: str,
    *,
    rows: int = 200,
    chaos_rate: float = 0.0,
    chaos_modes: list[str] | None = None,
    seed: int = 42,
    output_path: str | None = None,
    processed_dir: str = "data/processed",
    configs_dir: str = "configs",
) -> dict[str, Any]:
    """Génère un fichier CSV synthétique et retourne son rapport d'exécution."""
    df, manifest = generate_synthetic_dataframe(
        domain=domain,
        rows=rows,
        chaos_rate=chaos_rate,
        chaos_modes=chaos_modes,
        seed=seed,
        configs_dir=configs_dir,
    )

    if output_path:
        dest = Path(output_path)
    else:
        out_dir = Path(processed_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"synth_{manifest['domain']}_{seed}.csv"

    dest.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(dest, index=False)
    manifest["output_path"] = str(dest)
    manifest["preview_rows"] = df.head(5).fillna("").to_dict(orient="records")
    return manifest
