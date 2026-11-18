from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    """Erreur levée lorsqu'une configuration métier YAML est invalide."""


def load_domain_config(domain: str, config_dir: str = "configs") -> dict[str, Any]:
    config_path = Path(config_dir) / f"{domain}.yaml"
    if not config_path.exists():
        raise ConfigError(f"Configuration introuvable pour le domaine '{domain}' : {config_path}")

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if not isinstance(config, dict):
        raise ConfigError(f"Le fichier {config_path} est vide ou mal formaté.")

    required_keys = ["domain", "version", "rules", "anomaly_detection"]
    missing = [k for k in required_keys if k not in config]
    if missing:
        raise ConfigError(f"Clés manquantes dans {config_path} : {missing}")

    return config
