"""Tests pour DataPulse V6 : compatibilité de contrat, --domain auto et init-config."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from datapulse.api import create_app
from datapulse.cli import init_config_cmd, main, run_pipeline
from datapulse.exceptions import ExitCode
from datapulse.schema_inference import SchemaContractError, resolve_domain


def test_incompatible_domain_raises_schema_contract_error(tmp_path: Path) -> None:
    df_alien = pd.DataFrame({"col_x": [1, 2, 3], "col_y": [4, 5, 6]})
    with pytest.raises(SchemaContractError):
        resolve_domain(df_alien, "commerce")

    alien_csv = tmp_path / "alien.csv"
    df_alien.to_csv(alien_csv, index=False)
    exit_code = main(["run-all", "--file", str(alien_csv), "--domain", "commerce"])
    assert exit_code == int(ExitCode.EXECUTION_ERROR)


def test_auto_domain_matches_existing_contract(tmp_path: Path) -> None:
    sample_csv = Path("data/raw/ventes.csv")
    code = run_pipeline(
        file_path=str(sample_csv),
        domain="auto",
        db_path=str(tmp_path / "auto_match.sqlite"),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
    )
    assert code == int(ExitCode.SUCCESS)


def test_auto_domain_infers_contract_on_unknown_dataset(tmp_path: Path) -> None:
    unknown_csv = tmp_path / "capteurs_iot.csv"
    df = pd.DataFrame(
        {
            "sensor_id": [f"SNS-{i:03d}" for i in range(40)],
            "zone": ["NORD", "SUD", "EST", "OUEST"] * 10,
            "temperature": [22.5] * 39 + [98.0],
            "voltage": [3.3] * 39 + [-1.5],
        }
    )
    df.to_csv(unknown_csv, index=False)

    code = run_pipeline(
        file_path=str(unknown_csv),
        domain="auto",
        db_path=str(tmp_path / "auto_infer.sqlite"),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
        quarantine=True,
    )
    assert code == int(ExitCode.SUCCESS)


def test_init_config_generates_yaml_and_runs_pipeline(tmp_path: Path) -> None:
    logistics_csv = tmp_path / "livraisons.csv"
    df = pd.DataFrame(
        {
            "colis_id": [f"PKG-{i:03d}" for i in range(10)],
            "statut": ["LIVRE", "EN_COURS", "RETOUR", "LIVRE", "EN_COURS"] * 2,
            "poids_kg": [1.2, 3.5, 0.8, 4.1, 2.0, 5.0, 1.5, 2.7, 3.1, 0.9],
        }
    )
    df.to_csv(logistics_csv, index=False)

    cfg_dir = tmp_path / "custom_configs"
    code = init_config_cmd(
        file_path=str(logistics_csv),
        domain="logistique",
        output_dir=str(cfg_dir),
    )
    assert code == int(ExitCode.SUCCESS)
    assert (cfg_dir / "logistique.yaml").exists()

    # Vérifie que réexécuter sans --force échoue proprement
    err_code = main(
        [
            "init-config",
            "--file",
            str(logistics_csv),
            "--domain",
            "logistique",
            "--output-dir",
            str(cfg_dir),
        ]
    )
    assert err_code == int(ExitCode.EXECUTION_ERROR)

    run_code = run_pipeline(
        file_path=str(logistics_csv),
        domain="logistique",
        configs_dir=str(cfg_dir),
        db_path=str(tmp_path / "logistique.sqlite"),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
    )
    assert run_code == int(ExitCode.SUCCESS)


def test_api_domains_and_auto_upload(tmp_path: Path) -> None:
    app = create_app(
        db_path=str(tmp_path / "v6.sqlite"),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
    )
    client = TestClient(app)

    dom_resp = client.get("/api/domains")
    assert dom_resp.status_code == 200
    domains = [d["domain"] for d in dom_resp.json()["domains"]]
    assert "auto" in domains
    assert "commerce" in domains

    # Upload avec domaine incompatible -> 400 explicite
    bad_resp = client.post(
        "/api/runs/upload",
        files={"file": ("alien.csv", b"foo,bar\n1,2\n", "text/csv")},
        data={"domain": "commerce"},
    )
    assert bad_resp.status_code == 400

    # Upload avec domain="auto" -> 200 et résolution automatique
    with open("data/raw/ventes.csv", "rb") as f:
        ok_resp = client.post(
            "/api/runs/upload",
            files={"file": ("ventes.csv", f, "text/csv")},
            data={"domain": "auto"},
        )
    assert ok_resp.status_code == 200
    assert ok_resp.json()["run"]["domain"] == "commerce"
