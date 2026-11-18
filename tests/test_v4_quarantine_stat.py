"""Tests pour DataPulse V4 : règles étendues, MAD/multivarié, quarantaine et replay."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from datapulse.cli import manage_quarantine, run_pipeline, run_replay
from datapulse.detector import detect_anomalies
from datapulse.exceptions import ExitCode
from datapulse.quarantine import export_quarantine_to_csv, replay_corrected_file
from datapulse.storage import DataPulseStorage
from datapulse.validator import validate_dataframe


def test_validator_regex_between_and_cross_column() -> None:
    df = pd.DataFrame(
        {
            "code_client": ["CLI-001", "BAD_99", "CLI-002"],
            "remise_pct": [10.0, 150.0, 20.0],
            "prix_net": [90.0, 80.0, 120.0],
            "prix_brut": [100.0, 100.0, 100.0],
        }
    )
    cfg = {
        "rules": [
            {
                "id": "R_CODE",
                "column": "code_client",
                "check": "regex",
                "pattern": r"^CLI-\d{3}$",
                "message": "Format client invalide",
            },
            {
                "id": "R_REMISE",
                "column": "remise_pct",
                "check": "between",
                "min": 0.0,
                "max": 100.0,
                "message": "Remise hors [0, 100]",
            },
            {
                "id": "R_PRIX",
                "check": "cross_column",
                "left_column": "prix_net",
                "right_column": "prix_brut",
                "operator": "lte",
                "message": "Prix net supérieur au prix brut",
            },
        ]
    }
    violations = validate_dataframe(df, cfg)
    rule_ids = {v["rule_id"] for v in violations}
    assert rule_ids == {"R_CODE", "R_REMISE", "R_PRIX"}


def test_detector_mad_and_multivariate_anomalies() -> None:
    rng = np.random.default_rng(7)
    n = 60
    x = rng.normal(100.0, 2.0, size=n)
    y = rng.normal(50.0, 1.0, size=n)
    # Injection d'une anomalie multivariée modérée sur chaque axe mais forte conjointement
    x[5] = 109.0
    y[5] = 54.5

    df = pd.DataFrame({"x": x, "y": y})
    cfg = {
        "anomaly_detection": {
            "columns": ["x", "y"],
            "zscore_threshold": 3.5,
            "iqr_multiplier": 2.0,
            "mad_threshold": 3.5,
            "enable_mad": True,
            "enable_multivariate": True,
            "multivariate_threshold": 4.0,
            "min_sample_size": 30,
        }
    }
    anomalies, _ = detect_anomalies(df, cfg)
    methods = {a["stat_method"] for a in anomalies if a["row_index"] == 5}
    assert "MULTIVARIATE_MAD" in methods


def test_quarantine_isolation_export_and_replay_workflow(tmp_path: Path) -> None:
    # Jeu de données commerce minimal avec 1 ligne valide et 1 ligne invalide
    raw_csv = tmp_path / "ventes_quar.csv"
    sample_csv = Path("data/raw/ventes.csv")
    df_sample = pd.read_csv(sample_csv).head(5).copy()

    db_file = tmp_path / "quarantine.sqlite"
    processed_dir = tmp_path / "processed"
    reports_dir = tmp_path / "reports"

    code = run_pipeline(
        file_path=str(sample_csv),
        domain="commerce",
        db_path=str(db_file),
        reports_dir=str(reports_dir),
        processed_dir=str(processed_dir),
        quarantine=True,
    )
    assert code == int(ExitCode.SUCCESS)

    clean_files = list(processed_dir.glob("*_clean.csv"))
    assert len(clean_files) == 1

    storage = DataPulseStorage(db_path=str(db_file))
    pending = storage.list_quarantine(domain="commerce", status="PENDING")
    assert len(pending) > 0

    # Export des lignes en quarantaine vers CSV
    export_file = tmp_path / "to_fix.csv"
    export_quarantine_to_csv(pending, export_file)
    assert export_file.exists()

    # Remplacement par des données valides issues du jeu de référence
    clean_ref = pd.read_csv("data/reference/ventes_clean.csv").head(len(pending)).copy()
    clean_ref.insert(0, "_quarantine_id", [rec["quarantine_id"] for rec in pending])
    fixed_csv = tmp_path / "fixed_ventes.csv"
    clean_ref.to_csv(fixed_csv, index=False)

    replay_summary = replay_corrected_file(
        str(fixed_csv),
        "commerce",
        db_path=str(db_file),
        raw_dir=str(tmp_path / "raw_replay"),
    )
    assert replay_summary["remaining_violations"] == 0
    assert replay_summary["marked_replayed_count"] == len(pending)
    assert storage.list_quarantine(domain="commerce", status="PENDING") == []
    assert len(storage.list_quarantine(domain="commerce", status="REPLAYED")) == len(pending)
    assert len(df_sample) == 5
    assert raw_csv.name == "ventes_quar.csv"


def test_cli_quarantine_and_replay_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "cli_quar.sqlite"
    sample_csv = Path("data/raw/ventes.csv")
    export_csv = tmp_path / "exported_q.csv"

    run_pipeline(
        file_path=str(sample_csv),
        domain="commerce",
        db_path=str(db_file),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
        quarantine=True,
    )
    capsys.readouterr()

    q_code = manage_quarantine(
        domain="commerce", status="PENDING", export_csv=str(export_csv), db_path=str(db_file)
    )
    out = capsys.readouterr().out
    assert q_code == int(ExitCode.SUCCESS)
    assert "Quarantaine DataPulse" in out
    assert export_csv.exists()

    # Rejeu d'un fichier corrigé sans violations -> ExitCode.SUCCESS
    df_exported = pd.read_csv(export_csv)
    clean_ref = pd.read_csv("data/reference/ventes_clean.csv").head(len(df_exported)).copy()
    clean_ref.insert(0, "_quarantine_id", df_exported["_quarantine_id"].tolist())
    fixed_path = tmp_path / "fixed_cli.csv"
    clean_ref.to_csv(fixed_path, index=False)

    rep_code = run_replay(str(fixed_path), "commerce", db_path=str(db_file))
    assert rep_code == int(ExitCode.SUCCESS)
