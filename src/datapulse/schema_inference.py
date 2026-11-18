"""Inférence automatique de contrats YAML et vérification de compatibilité de schéma."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from datapulse.config_loader import load_domain_config
from datapulse.exceptions import DataPulseError
from datapulse.ingestion import ingest_file


class SchemaContractError(DataPulseError):
    """Erreur levée lorsqu'un jeu de données est incompatible avec le contrat du domaine."""


def extract_expected_columns(config: dict[str, Any]) -> set[str]:
    """Extrait l'ensemble des colonnes référencées par les règles et la détection statistique."""
    expected: set[str] = set()
    for rule in config.get("rules", []):
        if isinstance(rule, dict):
            col = rule.get("column")
            if isinstance(col, str) and col:
                expected.add(col)
            cols = rule.get("columns")
            if isinstance(cols, list):
                expected.update(str(c) for c in cols if c)
            for side in ("left_column", "right_column"):
                side_col = rule.get(side)
                if isinstance(side_col, str) and side_col:
                    expected.add(side_col)

    ad_cfg = config.get("anomaly_detection", {})
    if isinstance(ad_cfg, dict):
        ad_cols = ad_cfg.get("columns", [])
        if isinstance(ad_cols, list):
            expected.update(str(c) for c in ad_cols if c)

    return expected


def verify_contract_compatibility(
    df: pd.DataFrame,
    config: dict[str, Any],
    domain: str,
) -> dict[str, Any]:
    """Vérifie qu'au moins une colonne attendue par le contrat existe dans le DataFrame."""
    expected_cols = extract_expected_columns(config)
    if not expected_cols:
        return {"matched_columns": [], "missing_columns": [], "coverage_ratio": 1.0}

    df_cols = {str(c) for c in df.columns}
    matched = sorted(expected_cols & df_cols)
    missing = sorted(expected_cols - df_cols)
    ratio = round(len(matched) / len(expected_cols), 3)

    if not matched:
        raise SchemaContractError(
            f"Le fichier est incompatible avec le domaine '{domain}' : aucune colonne attendue "
            f"({', '.join(sorted(expected_cols))}) n'a été trouvée dans le fichier "
            f"({', '.join(sorted(df_cols))}). Utilisez '--domain auto' ou "
            f"'datapulse init-config --file <fichier> --domain <nom>'.",
            context={
                "domain": domain,
                "expected_columns": sorted(expected_cols),
                "actual_columns": sorted(df_cols),
            },
        )

    return {
        "matched_columns": matched,
        "missing_columns": missing,
        "coverage_ratio": ratio,
    }


def list_available_domains(configs_dir: str | Path = "configs") -> list[dict[str, str]]:
    """Liste tous les contrats YAML disponibles dans le répertoire configs/."""
    base = Path(configs_dir)
    if not base.exists():
        return []

    domains: list[dict[str, str]] = []
    for yaml_file in sorted(base.glob("*.y*ml")):
        try:
            raw = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        if not isinstance(raw, dict):
            continue
        domain_name = str(raw.get("domain") or yaml_file.stem)
        description = str(raw.get("description") or f"Contrat qualité {domain_name}")
        domains.append({"domain": domain_name, "description": description, "file": str(yaml_file)})
    return domains


def _load_config(domain: str, configs_dir: str | Path = "configs") -> dict[str, Any]:
    """Charge la configuration d'un domaine en passant le répertoire en positionnel si personnalisé."""
    if str(configs_dir) == "configs":
        return load_domain_config(domain)
    return load_domain_config(domain, str(configs_dir))


def resolve_domain(
    df: pd.DataFrame,
    requested_domain: str,
    configs_dir: str | Path = "configs",
) -> tuple[str, dict[str, Any]]:
    """Résout le domaine cible (explicite avec vérification, ou auto-détecté)."""
    if requested_domain.strip().lower() != "auto":
        config = _load_config(requested_domain, configs_dir)
        verify_contract_compatibility(df, config, requested_domain)
        return requested_domain, config

    df_cols = {str(c) for c in df.columns}
    best_domain: str | None = None
    best_config: dict[str, Any] | None = None
    best_score: tuple[float, int] = (0.0, 0)

    for entry in list_available_domains(configs_dir=configs_dir):
        dom = entry["domain"]
        try:
            cfg = _load_config(dom, configs_dir)
        except Exception:
            continue
        expected = extract_expected_columns(cfg)
        if not expected:
            continue
        matched_count = len(expected & df_cols)
        if matched_count > 0:
            ratio = matched_count / len(expected)
            score = (ratio, matched_count)
            if score > best_score:
                best_score = score
                best_domain = dom
                best_config = cfg

    if best_domain is not None and best_config is not None:
        return best_domain, best_config

    inferred_cfg = infer_config_from_dataframe(
        df,
        domain_name="auto_inferred",
        description="Contrat généré automatiquement à la volée (--domain auto)",
    )
    return "auto_inferred", inferred_cfg


def resolve_domain(
    df: pd.DataFrame,
    requested_domain: str,
    configs_dir: str | Path = "configs",
) -> tuple[str, dict[str, Any]]:
    """Résout le domaine cible (explicite avec vérification, ou auto-détecté)."""
    if requested_domain.strip().lower() != "auto":
        config = _load_config(requested_domain, configs_dir)
        verify_contract_compatibility(df, config, requested_domain)
        return requested_domain, config

    df_cols = {str(c) for c in df.columns}
    best_domain: str | None = None
    best_config: dict[str, Any] | None = None
    best_score: tuple[float, int] = (0.0, 0)

    for entry in list_available_domains(configs_dir=configs_dir):
        dom = entry["domain"]
        try:
            cfg = _load_config(dom, configs_dir)
        except Exception:
            continue
        expected = extract_expected_columns(cfg)
        if not expected:
            continue
        matched_count = len(expected & df_cols)
        if matched_count > 0:
            ratio = matched_count / len(expected)
            score = (ratio, matched_count)
            if score > best_score:
                best_score = score
                best_domain = dom
                best_config = cfg

    if best_domain is not None and best_config is not None:
        return best_domain, best_config

    inferred_cfg = infer_config_from_dataframe(
        df,
        domain_name="auto_inferred",
        description="Contrat généré automatiquement à la volée (--domain auto)",
    )
    return "auto_inferred", inferred_cfg


def _sanitize_rule_id(col_name: str, suffix: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", col_name.strip()).strip("_").upper()
    return f"R_{cleaned or 'COL'}_{suffix}"


def infer_config_from_dataframe(
    df: pd.DataFrame,
    domain_name: str,
    description: str | None = None,
) -> dict[str, Any]:
    """Infère un contrat YAML complet (règles métier + détection statistique) depuis un DataFrame."""
    rules: list[dict[str, Any]] = []
    numeric_anomaly_cols: list[str] = []
    n_rows = len(df)

    for col in df.columns:
        col_str = str(col)
        series = df[col]
        non_null = series.dropna()
        n_non_null = len(non_null)
        if n_non_null == 0:
            continue

        null_ratio = 1.0 - (n_non_null / max(n_rows, 1))
        if null_ratio <= 0.05:
            rules.append(
                {
                    "id": _sanitize_rule_id(col_str, "NOT_NULL"),
                    "column": col_str,
                    "check": "not_null_or_empty",
                    "severity": "HIGH" if null_ratio == 0.0 else "MEDIUM",
                    "message": f"La colonne '{col_str}' ne doit pas être vide",
                }
            )

        if pd.api.types.is_bool_dtype(series):
            continue

        if pd.api.types.is_numeric_dtype(series):
            num_s = pd.to_numeric(non_null, errors="coerce").dropna()
            if num_s.empty:
                continue
            min_val = float(num_s.min())
            nunique = int(num_s.nunique())

            if min_val > 0.0:
                rules.append(
                    {
                        "id": _sanitize_rule_id(col_str, "POS"),
                        "column": col_str,
                        "check": "gt",
                        "value": 0,
                        "severity": "HIGH",
                        "message": f"'{col_str}' doit être strictement positif",
                    }
                )
            elif min_val >= 0.0:
                rules.append(
                    {
                        "id": _sanitize_rule_id(col_str, "NON_NEG"),
                        "column": col_str,
                        "check": "gte",
                        "value": 0,
                        "severity": "MEDIUM",
                        "message": f"'{col_str}' ne peut pas être négatif",
                    }
                )

            if nunique >= 10:
                numeric_anomaly_cols.append(col_str)
            continue

        # Colonnes textuelles / dates
        str_s = non_null.astype(str).str.strip()
        nunique_str = int(str_s.nunique())

        is_id_like = bool(re.search(r"(^id$|_id$|^code|matricule|ref|uuid|sku)", col_str.lower()))
        if is_id_like and n_rows >= 5 and nunique_str == n_rows:
            rules.append(
                {
                    "id": _sanitize_rule_id(col_str, "UNIQUE"),
                    "check": "unique_combination",
                    "columns": [col_str],
                    "severity": "HIGH",
                    "message": f"Doublon détecté sur l'identifiant '{col_str}'",
                }
            )
            continue

        if "date" in col_str.lower() or "timestamp" in col_str.lower():
            parsed_dt = pd.to_datetime(str_s, errors="coerce")
            if parsed_dt.notna().mean() >= 0.9:
                rules.append(
                    {
                        "id": _sanitize_rule_id(col_str, "NOT_FUTURE"),
                        "column": col_str,
                        "check": "not_future",
                        "severity": "MEDIUM",
                        "message": f"Date future non autorisée sur '{col_str}'",
                    }
                )
                continue

        if 2 <= nunique_str <= 12 and (nunique_str / max(n_non_null, 1)) <= 0.25:
            allowed_vals = sorted(str_s.unique().tolist())
            rules.append(
                {
                    "id": _sanitize_rule_id(col_str, "IN_SET"),
                    "column": col_str,
                    "check": "in_set",
                    "values": allowed_vals,
                    "severity": "MEDIUM",
                    "message": f"Valeur non autorisée dans '{col_str}'",
                }
            )

    return {
        "domain": domain_name,
        "version": "1.0",
        "description": description or f"Contrat qualité auto-généré pour {domain_name}",
        "rules": rules,
        "anomaly_detection": {
            "columns": numeric_anomaly_cols,
            "zscore_threshold": 3.5,
            "iqr_multiplier": 2.0,
            "mad_threshold": 4.0,
            "enable_mad": True,
            "enable_multivariate": len(numeric_anomaly_cols) >= 2,
            "multivariate_threshold": 5.0,
            "min_sample_size": 30,
        },
    }


def generate_domain_yaml(
    file_path: str,
    domain_name: str,
    *,
    output_dir: str | Path = "configs",
    overwrite: bool = False,
) -> tuple[Path, dict[str, Any]]:
    """Ingère un fichier échantillon et écrit son contrat YAML dans configs/."""
    clean_domain = re.sub(r"[^a-zA-Z0-9_-]+", "_", domain_name.strip()).strip("_").lower()
    if not clean_domain or clean_domain == "auto":
        raise SchemaContractError(
            f"Nom de domaine invalide pour la génération : '{domain_name}'",
            context={"domain_name": domain_name},
        )

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target_path = out_dir / f"{clean_domain}.yaml"

    if target_path.exists() and not overwrite:
        raise SchemaContractError(
            f"Le contrat '{target_path}' existe déjà. Ajoutez --force pour l'écraser.",
            context={"target_path": str(target_path)},
        )

    ingestion = ingest_file(file_path)
    config = infer_config_from_dataframe(ingestion.dataframe, domain_name=clean_domain)
    yaml_text = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
    target_path.write_text(yaml_text, encoding="utf-8")
    return target_path, config
