"""Hiérarchie d'exceptions métier et codes de sortie CLI pour DataPulse."""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """Codes de retour standardisés pour l'exécution en ligne de commande."""

    SUCCESS = 0
    QUALITY_ALERT = 1
    EXECUTION_ERROR = 2


class DataPulseError(Exception):
    """Exception racine de la plateforme DataPulse."""

    exit_code: ExitCode = ExitCode.EXECUTION_ERROR

    def __init__(self, message: str, *, context: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.context: dict[str, object] = context or {}

    def __str__(self) -> str:
        if not self.context:
            return self.message
        details = ", ".join(f"{k}={v!r}" for k, v in self.context.items())
        return f"{self.message} ({details})"


class ConfigValidationError(DataPulseError):
    """Erreur levée lorsqu'un fichier de configuration YAML est invalide ou incomplet."""


# Alias rétrocompatible avec v1.x (config_loader.py)
ConfigError = ConfigValidationError


class IngestionError(DataPulseError):
    """Erreur levée lors de la lecture ou du décodage d'une source de données."""


class SchemaContractError(DataPulseError):
    """Erreur levée lorsqu'un jeu de données viole le contrat de colonnes attendu."""


class ReconciliationError(DataPulseError):
    """Erreur levée lors d'un échec critique de réconciliation inter-sources."""


class StorageTransactionError(DataPulseError):
    """Erreur levée lors d'une opération d'écriture ou de transaction SQLite."""
