"""Tests pour DataPulse V11 : Score Qualité (0-100), Data Lineage et Certificats signés."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from datapulse.api import create_app
from datapulse.cli import audit_cert_cmd, run_pipeline, show_lineage_cmd
from datapulse.exceptions import ExitCode
from datapulse.lineage import compute_quality_score, verify_audit_certificate
from datapulse.storage import DataPulseStorage


def test_quality_score_grades_and_penalties() -> None:
    perfect = compute_quality_score(
        row_count=100,
        completeness_pct=100.0,
        findings=[],
        schema_drifts=[],
        data_drifts=[],
    )
    assert perfect["score"] == 100.0
    assert perfect["grade"] == "A"

    degraded = compute_quality_score(
        row_count=100,
        completeness_pct=70.0,
        findings=[{"row_index": i} for i in range(40)],
        schema_drifts=[{"severity": "CRITICAL"}],
        data_drifts=[{"severity": "CRITICAL"}, {"severity": "WARNING"}],
    )
    assert 60.0 <= degraded["score"] < 70.0
    assert degraded["grade"] == "D"

    critical_fail = compute_quality_score(
        row_count=100,
        completeness_pct=50.0,
        findings=[{"row_index": i} for i in range(60)],
        schema_drifts=[{"severity": "CRITICAL"}],
        data_drifts=[{"severity": "CRITICAL"}],
    )
    assert critical_fail["score"] < 60.0
    assert critical_fail["grade"] == "F"


def test_end_to_end_lineage_and_tamper_evident_certificate(tmp_path: Path) -> None:
    db_file = tmp_path / "lineage.sqlite"
    code = run_pipeline(
        file_path="data/raw/ventes.csv",
        domain="commerce",
        db_path=str(db_file),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
        quarantine=True,
    )
    assert code == int(ExitCode.SUCCESS)

    latest = DataPulseStorage(db_path=db_file).get_history(domain="commerce", limit=1)[0]
    run_id = str(latest["run_id"])

    assert show_lineage_cmd(run_id=run_id, db_path=str(db_file)) == int(ExitCode.SUCCESS)

    cert_file = tmp_path / "reports" / "cert_audit.json"
    assert audit_cert_cmd(run_id=run_id, output_path=str(cert_file), db_path=str(db_file)) == int(
        ExitCode.SUCCESS
    )
    assert cert_file.exists()

    # Certificat authentique -> valide
    assert audit_cert_cmd(verify_file=str(cert_file)) == int(ExitCode.SUCCESS)

    # Falsification du score qualité -> détection immédiate de l'altération
    tampered = json.loads(cert_file.read_text(encoding="utf-8"))
    tampered["quality_score"] = 100.0
    tampered_file = tmp_path / "reports" / "cert_tampered.json"
    tampered_file.write_text(json.dumps(tampered), encoding="utf-8")
    assert audit_cert_cmd(verify_file=str(tampered_file)) == int(ExitCode.QUALITY_ALERT)


def test_verify_certificate_rejects_empty_seal() -> None:
    check = verify_audit_certificate({"run_id": "abc", "quality_score": 95.0})
    assert check["valid"] is False


def test_api_lineage_and_certificate_endpoints(tmp_path: Path) -> None:
    db_file = tmp_path / "api_lin.sqlite"
    app = create_app(
        db_path=str(db_file),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
    )
    client = TestClient(app)

    with open("data/reference/ventes_clean.csv", "rb") as f:
        up = client.post(
            "/api/runs/upload",
            files={"file": ("ventes_clean.csv", f, "text/csv")},
            data={"domain": "commerce", "quarantine": "true"},
        )
    assert up.status_code == 200
    run_id = up.json()["run"]["run_id"]

    lin_res = client.get(f"/api/runs/{run_id}/lineage")
    assert lin_res.status_code == 200
    lin_body = lin_res.json()
    assert lin_body["quality_grade"] == "A"
    assert len(lin_body["certificate_sha256"]) == 64

    cert_res = client.get(f"/api/runs/{run_id}/certificate")
    assert cert_res.status_code == 200
    cert_body = cert_res.json()

    ver_res = client.post("/api/certificates/verify", json=cert_body)
    assert ver_res.status_code == 200
    assert ver_res.json()["valid"] is True

    # Altération testée via l'API
    cert_body["clean_rows"] = 999999
    ver_bad = client.post("/api/certificates/verify", json=cert_body)
    assert ver_bad.status_code == 200
    assert ver_bad.json()["valid"] is False


def test_api_lineage_404_for_unknown_run(tmp_path: Path) -> None:
    db_file = tmp_path / "empty_lin.sqlite"
    app = create_app(db_path=str(db_file))
    client = TestClient(app)

    assert client.get("/api/runs/does-not-exist/lineage").status_code == 404
    assert client.get("/api/runs/does-not-exist/certificate").status_code == 404
