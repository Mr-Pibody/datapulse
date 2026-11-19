"""Tests pour DataPulse V16 : génération de données synthétiques, chaos engineering et Web UI."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from datapulse.api import create_app
from datapulse.cli import synth_cmd
from datapulse.security import create_api_key
from datapulse.storage import DataPulseStorage
from datapulse.synth import generate_synthetic_dataframe


def test_generate_clean_synthetic_dataframe_passes_contract() -> None:
    df, manifest = generate_synthetic_dataframe(
        domain="commerce",
        rows=150,
        chaos_rate=0.0,
        seed=123,
    )

    assert len(df) == 150
    assert manifest["domain"] == "commerce"
    assert manifest["injected_faults_count"] == 0
    assert manifest["chaos_modes_applied"] == []


def test_chaos_injection_corrupts_data_and_triggers_pipeline_detection(tmp_path: Path) -> None:
    df_chaos, manifest = generate_synthetic_dataframe(
        domain="finance",
        rows=100,
        chaos_rate=0.20,
        chaos_modes=["nulls", "outliers", "negatives", "duplicates", "schema_drift"],
        seed=7,
    )

    assert manifest["injected_faults_count"] > 0
    assert "chaos_unexpected_col" in df_chaos.columns
    assert len(df_chaos) == 120  # 100 lignes + 20 doublons

    with pytest.raises(ValueError, match="Mode de chaos invalide"):
        generate_synthetic_dataframe("commerce", rows=10, chaos_rate=0.1, chaos_modes=["unknown"])


def test_cli_synth_command_with_auto_audit(tmp_path: Path) -> None:
    out_csv = tmp_path / "synth_commerce.csv"
    db_file = tmp_path / "v16_cli.sqlite"

    code = synth_cmd(
        domain="commerce",
        rows=80,
        chaos_rate=0.15,
        chaos_modes="nulls,negatives",
        seed=99,
        output_path=str(out_csv),
        run_audit=True,
        db_path=str(db_file),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
    )
    assert code == 0
    assert out_csv.exists()

    history = DataPulseStorage(db_path=str(db_file)).get_history(domain="commerce", limit=5)
    assert len(history) == 1
    assert history[0]["findings_count"] > 0


def test_api_synth_endpoint_rbac_and_v16_dashboard(tmp_path: Path) -> None:
    db_file = tmp_path / "v16_api.sqlite"
    view_key = str(create_api_key(db_file, name="chaos-viewer", role="viewer")["api_key"])
    op_key = str(create_api_key(db_file, name="chaos-op", role="operator")["api_key"])

    app = create_app(
        db_path=str(db_file),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
    )
    client = TestClient(app)

    # Dashboard v16 avec Labo Chaos & Synthèse
    dash = client.get("/")
    assert dash.status_code == 200
    assert "DataPulse Observatoire" in dash.text
    assert "Labo Chaos &amp; Synthèse" in dash.text

    payload = {
        "domain": "commerce",
        "rows": 60,
        "chaos_rate": 0.25,
        "chaos_modes": ["nulls", "outliers"],
        "seed": 11,
        "auto_audit": True,
    }

    # Viewer refusé (403)
    denied = client.post("/api/synth/generate", headers={"X-API-Key": view_key}, json=payload)
    assert denied.status_code == 403

    # Operator autorisé (200) + audit automatique exécuté
    allowed = client.post("/api/synth/generate", headers={"X-API-Key": op_key}, json=payload)
    assert allowed.status_code == 200
    body = allowed.json()
    assert body["rows_generated"] == 60
    assert body["injected_faults_count"] > 0
    assert body["audit"] is not None
    assert body["audit"]["run"]["findings_count"] > 0
