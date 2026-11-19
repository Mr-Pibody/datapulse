"""Tests pour DataPulse V12 : métriques Prometheus (/metrics) et sondes de santé."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from datapulse.api import create_app
from datapulse.cli import metrics_cmd, run_pipeline
from datapulse.exceptions import ExitCode
from datapulse.prometheus_metrics import check_readiness, render_prometheus_metrics


def test_prometheus_metrics_exposition_and_cli_export(tmp_path: Path) -> None:
    db_file = tmp_path / "prom.sqlite"
    code = run_pipeline(
        file_path="data/raw/ventes.csv",
        domain="commerce",
        db_path=str(db_file),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
        quarantine=True,
    )
    assert code == int(ExitCode.SUCCESS)

    prom_text = render_prometheus_metrics(db_file)
    assert "# HELP datapulse_runs_total" in prom_text
    assert "# TYPE datapulse_runs_total counter" in prom_text
    assert 'datapulse_runs_total{domain="commerce",status="SUCCESS"} 1' in prom_text
    assert 'datapulse_quality_score_latest{domain="commerce"' in prom_text
    assert 'datapulse_quarantine_pending_rows{domain="commerce"}' in prom_text

    out_prom = tmp_path / "metrics.prom"
    assert metrics_cmd(fmt="prometheus", output_path=str(out_prom), db_path=str(db_file)) == 0
    assert out_prom.exists()
    assert "datapulse_info" in out_prom.read_text(encoding="utf-8")

    assert metrics_cmd(fmt="json", db_path=str(db_file)) == 0


def test_api_metrics_and_health_probes(tmp_path: Path) -> None:
    db_file = tmp_path / "api_prom.sqlite"
    app = create_app(
        db_path=str(db_file),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
    )
    client = TestClient(app)

    live = client.get("/health/live")
    assert live.status_code == 200
    assert live.json()["status"] == "alive"
    assert live.json()["version"] == "14.0.0"

    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["checks"]["sqlite_ping"] is True

    with open("data/reference/ventes_clean.csv", "rb") as f:
        up = client.post(
            "/api/runs/upload",
            files={"file": ("ventes_clean.csv", f, "text/csv")},
            data={"domain": "commerce"},
        )
    assert up.status_code == 200

    metrics_res = client.get("/metrics")
    assert metrics_res.status_code == 200
    assert "text/plain" in metrics_res.headers["content-type"]
    body = metrics_res.text
    assert 'datapulse_runs_total{domain="commerce",status="SUCCESS"} 1' in body
    assert 'datapulse_quality_score_latest{domain="commerce",grade="A"} 99.82' in body


def test_readiness_probe_returns_503_when_db_unwritable(tmp_path: Path) -> None:
    # Un chemin pointant vers un répertoire existant fait échouer sqlite3.connect
    bad_db_path = tmp_path / "not_a_file_dir"
    bad_db_path.mkdir()

    ok, payload = check_readiness(bad_db_path)
    assert ok is False
    assert payload["status"] == "unavailable"

    app = create_app(db_path=str(bad_db_path))
    client = TestClient(app)
    res = client.get("/health/ready")
    assert res.status_code == 503
    assert res.json()["status"] == "unavailable"


def test_prometheus_empty_db_renders_zero_gauges(tmp_path: Path) -> None:
    missing_db = tmp_path / "nonexistent.sqlite"
    text = render_prometheus_metrics(missing_db)
    assert 'datapulse_info{version="14.0.0"} 1' in text
    assert "datapulse_watcher_tracked_files_total 0" in text
    assert "datapulse_scheduler_jobs_total 0" in text
