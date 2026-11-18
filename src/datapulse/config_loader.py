"""Chargement et validation structurelle des configurations métier YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from datapulse.exceptions import ConfigError, ConfigValidationError

__all__ = ["ConfigError", "ConfigValidationError", "load_domain_config"]


def load_domain_config(domain: str, config_dir: str = "configs") -> dict[str, Any]:
    config_path = Path(config_dir) / f"{domain}.yaml"
    if not config_path.exists():
        raise ConfigValidationError(
            f"Configuration introuvable pour le domaine '{domain}'",
            context={"config_path": str(config_path)},
        )

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if not isinstance(config, dict):
        raise ConfigValidationError(
            "Le fichier de configuration YAML est vide ou mal formaté",
            context={"config_path": str(config_path)},
        )

    required_keys = ["domain", "version", "rules", "anomaly_detection"]
    missing = [k for k in required_keys if k not in config]
    if missing:
        raise ConfigValidationError(
            "Clés obligatoires manquantes dans la configuration",
            context={"config_path": str(config_path), "missing": missing},
        )

    if not isinstance(config["rules"], list):
        raise ConfigValidationError(
            "La section 'rules' doit être une liste de règles",
            context={"config_path": str(config_path)},
        )

    if not isinstance(config["anomaly_detection"], dict):
        raise ConfigValidationError(
            "La section 'anomaly_detection' doit être un dictionnaire",
            context={"config_path": str(config_path)},
        )

    return config
