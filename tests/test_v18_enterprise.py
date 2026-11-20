"""Tests v18.2 : Durcissement Zero-Trust, Replay Quarantaine, PSI Laplace & Artefacts."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from datapulse.api import create_app
from datapulse.cli import run_pipeline
from datapulse.drift import _psi_and_jsd_from_probs, compute_wasserstein_1d
from datapulse.lineage import build_audit_certificate, verify_audit_certificate
from datapulse.security import create_api_key
from datapulse.storage import DataPulseStorage


def test_v18_demo_bootstrap_hardening_and_least_privilege(tmp_path: Path, monkeypatch) -> None:
    db_file = tmp_path / "sec_v18.sqlite"
    app = create_app(db_path=str(db_file))
    client = TestClient(app)

    # 1. Mode demo actif -> genere une cle 'operator' (jamais 'admin')
    res = client.post("/api/demo/bootstrap")
    assert res.status_code == 200
    body = res.json()
    assert body["role"] == "operator"
    assert str(body["api_key"]).startswith("dp_live_")

    # Une cle demo operator ne peut pas creer de nouvelles cles admin
    forbidden = client.post(
        "/api/security/keys",
        headers={"X-API-Key": body["api_key"]},
        json={"name": "escalade", "role": "admin"},
    )
    assert forbidden.status_code == 403

    # 2. Desactivation explicite en production -> 403
    monkeypatch.setenv("DATAPULSE_DEMO_MODE", "false")
    blocked = client.post("/api/demo/bootstrap")
    assert blocked.status_code == 403


def test_v18_quarantine_resolve_and_replay_workflow(tmp_path: Path) -> None:
    db_file = tmp_path / "quar_v18.sqlite"
    proc_dir = tmp_path / "processed"
    rep_dir = tmp_path / "reports"
    op_key = str(create_api_key(db_file, name="op-v18", role="operator")["api_key"])

    csv_path = tmp_path / "bad_sales.csv"
    pd.DataFrame(
        {
            "date": ["2026-05-01", "2026-05-02"],
            "produit": ["Clavier", "Souris"],
            "quantite": [5, -10],
            "prix_unitaire": [25.0, 15.0],
        }
    ).to_csv(csv_path, index=False)

    run_pipeline(
        file_path=str(csv_path),
        domain="commerce",
        db_path=str(db_file),
        reports_dir=str(rep_dir),
        processed_dir=str(proc_dir),
        configs_dir="configs",
        quarantine=True,
    )

    app = create_app(
        db_path=str(db_file),
        reports_dir=str(rep_dir),
        processed_dir=str(proc_dir),
    )
    client = TestClient(app)
    hdrs = {"X-API-Key": op_key}

    q_list = client.get("/api/quarantine?status=PENDING", headers=hdrs).json()["items"]
    assert len(q_list) >= 1
    qid = q_list[0]["quarantine_id"]

    # Approbation via API
    r_ok = client.post(
        "/api/quarantine/resolve",
        headers=hdrs,
        json={"quarantine_id": qid, "status": "APPROVED"},
    )
    assert r_ok.status_code == 200

    # Replay reel dans un fichier CSV consolide
    replay = client.post("/api/quarantine/replay", headers=hdrs, json={"domain": "commerce"})
    assert replay.status_code == 200
    rdata = replay.json()
    assert rdata["replayed_count"] == 1
    assert Path(rdata["artifact_path"]).exists()


def test_v18_hmac_tamper_detection_and_laplace_psi_stability(tmp_path: Path) -> None:
    # 1. PSI avec lissage de Laplace ne doit pas exploser sur modalites rares
    p = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float64)
    q = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float64)
    psi, jsd = _psi_and_jsd_from_probs(p, q)
    assert psi < 5.0
    assert 0.0 <= jsd <= 0.70

    # 2. Distance de Wasserstein-1
    w_dist = compute_wasserstein_1d([10.0, 11.0, 12.0], [20.0, 21.0, 22.0])
    assert w_dist > 5.0

    # 3. Falsification d'un certificat HMAC detectee
    db_file = tmp_path / "hmac.sqlite"
    csv_path = tmp_path / "ok.csv"
    pd.DataFrame(
        {
            "date": ["2026-05-01"],
            "produit": ["Ecran"],
            "quantite": [2],
            "prix_unitaire": [150.0],
        }
    ).to_csv(csv_path, index=False)

    run_pipeline(
        file_path=str(csv_path),
        domain="commerce",
        db_path=str(db_file),
        reports_dir=str(tmp_path / "rep"),
        processed_dir=str(tmp_path / "proc"),
        configs_dir="configs",
    )
    run_id = DataPulseStorage(str(db_file)).get_history(limit=1)[0]["run_id"]
    cert = build_audit_certificate(db_file, run_id)
    assert cert is not None
    assert verify_audit_certificate(cert)["valid"] is True

    # Tentative de falsification du score
    cert["quality_score"] = 12.5
    assert verify_audit_certificate(cert)["valid"] is False


def test_v19_analytics_cockpit_endpoint(tmp_path: Path) -> None:
    db_file = tmp_path / "cockpit_v19.sqlite"
    op_key = str(create_api_key(db_file, name="op-cockpit", role="operator")["api_key"])
    app = create_app(db_path=str(db_file))
    client = TestClient(app)
    resp = client.get("/api/analytics/cockpit", headers={"X-API-Key": op_key})
    assert resp.status_code == 200
    payload = resp.json()
    assert "kpis" in payload
    assert "latest_score" in payload
    assert "score_series" in payload
