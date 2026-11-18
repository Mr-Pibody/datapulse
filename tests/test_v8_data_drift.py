"""Tests pour DataPulse V8 : détection statistique de Data Drift (KS, PSI, JSD)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from datapulse.api import create_app
from datapulse.cli import diff_runs, run_pipeline
from datapulse.drift import (
    attach_distribution_sketches,
    compute_ks_2samp,
    detect_data_drift,
)
from datapulse.exceptions import ExitCode
from datapulse.profiler import profile_dataframe


def test_ks_psi_and_jsd_detect_numeric_and_categorical_drift() -> None:
    rng = np.random.default_rng(42)
    df_base = pd.DataFrame(
        {
            "montant": rng.normal(loc=100.0, scale=10.0, size=300),
            "canal": ["WEB"] * 240 + ["AGENCE"] * 60,
        }
    )
    df_shifted = pd.DataFrame(
        {
            "montant": rng.normal(loc=180.0, scale=15.0, size=300),
            "canal": ["WEB"] * 30 + ["MOBILE"] * 270,
        }
    )

    prof_a = attach_distribution_sketches(profile_dataframe(df_base), df_base)
    prof_b = attach_distribution_sketches(profile_dataframe(df_shifted), df_shifted)

    # Identique -> aucun drift
    assert detect_data_drift(prof_a["columns"], prof_a["columns"]) == []

    # Décalage fort -> CRITICAL sur montant (KS + PSI + JSD) et canal (PSI + JSD)
    drifts = detect_data_drift(prof_b["columns"], prof_a["columns"])
    by_col = {d["column_name"]: d for d in drifts}

    assert "montant" in by_col
    assert by_col["montant"]["severity"] == "CRITICAL"
    assert by_col["montant"]["psi"] >= 0.25
    assert by_col["montant"]["ks_statistic"] > 0.5
    assert by_col["montant"]["ks_pvalue"] < 0.01

    assert "canal" in by_col
    assert by_col["canal"]["severity"] == "CRITICAL"
    assert by_col["canal"]["psi"] >= 0.25
    assert by_col["canal"]["js_divergence"] > 0.1


def test_ks_2samp_edge_cases() -> None:
    assert compute_ks_2samp([], [1.0, 2.0]) == (0.0, 1.0)
    d_same, p_same = compute_ks_2samp([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
    assert d_same == 0.0
    assert p_same == 1.0


def test_pipeline_strict_triggers_quality_alert_on_critical_data_drift(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "configs"
    cfg_dir.mkdir()
    (cfg_dir / "capteurs.yaml").write_text(
        """
domain: capteurs
version: "1.0"
rules:
  - id: R_VAL_POS
    column: valeur
    check: gt
    value: 0
    severity: HIGH
anomaly_detection:
  columns: ["valeur"]
  min_sample_size: 500
""".strip(),
        encoding="utf-8",
    )

    rng = np.random.default_rng(7)
    f1 = tmp_path / "batch1.csv"
    f2 = tmp_path / "batch2.csv"
    pd.DataFrame({"valeur": rng.normal(50.0, 5.0, 200)}).to_csv(f1, index=False)
    pd.DataFrame({"valeur": rng.normal(120.0, 5.0, 200)}).to_csv(f2, index=False)

    db_file = tmp_path / "drift_strict.sqlite"

    # Run 1 (baseline) -> SUCCESS (0)
    c1 = run_pipeline(
        file_path=str(f1),
        domain="capteurs",
        configs_dir=str(cfg_dir),
        db_path=str(db_file),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
        strict=True,
    )
    assert c1 == int(ExitCode.SUCCESS)

    # Run 2 (dérive massive de moyenne 50 -> 120, sans violation de règle) -> QUALITY_ALERT (1)
    c2 = run_pipeline(
        file_path=str(f2),
        domain="capteurs",
        configs_dir=str(cfg_dir),
        db_path=str(db_file),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
        strict=True,
    )
    assert c2 == int(ExitCode.QUALITY_ALERT)


def test_diff_cli_and_api_expose_data_drift(tmp_path: Path, capsys: object) -> None:
    db_file = tmp_path / "api_drift.sqlite"
    app = create_app(
        db_path=str(db_file),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
    )
    client = TestClient(app)

    df_clean = pd.read_csv("data/reference/ventes_clean.csv")
    df_drifted = df_clean.copy()
    df_drifted["prix_unitaire"] = df_drifted["prix_unitaire"] * 5.0

    p1 = tmp_path / "v1.csv"
    p2 = tmp_path / "v2.csv"
    df_clean.to_csv(p1, index=False)
    df_drifted.to_csv(p2, index=False)

    run_pipeline(
        file_path=str(p1),
        domain="commerce",
        db_path=str(db_file),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
    )
    run_pipeline(
        file_path=str(p2),
        domain="commerce",
        db_path=str(db_file),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
    )

    assert diff_runs(domain="commerce", db_path=str(db_file)) == int(ExitCode.SUCCESS)

    resp = client.get("/api/diff", params={"domain": "commerce"})
    assert resp.status_code == 200
    payload = resp.json()
    assert "data_drifts" in payload
    assert any(d["column_name"] == "prix_unitaire" for d in payload["data_drifts"])
