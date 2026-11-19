"""Tests d'intégration pour l'API REST FastAPI et le Dashboard Web DataPulse V5."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from datapulse.api import create_app
from datapulse.cli import build_parser, run_pipeline


def test_api_health_and_dashboard_html(tmp_path: Path) -> None:
    app = create_app(
        db_path=str(tmp_path / "api.sqlite"),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
    )
    client = TestClient(app)

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["version"] == "12.0.0"

    home = client.get("/")
    assert home.status_code == 200
    assert "DataPulse" in home.text
    assert "v12.0.0" in home.text


def test_api_upload_runs_pipeline_and_lists_history(tmp_path: Path) -> None:
    db_file = tmp_path / "upload.sqlite"
    app = create_app(
        db_path=str(db_file),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
    )
    client = TestClient(app)

    sample_csv = Path("data/raw/ventes.csv")
    with open(sample_csv, "rb") as f:
        resp = client.post(
            "/api/runs/upload",
            files={"file": ("ventes.csv", f, "text/csv")},
            data={"domain": "commerce", "quarantine": "true"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["exit_code"] == 0
    assert body["run"]["domain"] == "commerce"

    runs_resp = client.get("/api/runs", params={"domain": "commerce"})
    assert runs_resp.status_code == 200
    assert runs_resp.json()["count"] == 1

    run_id = body["run"]["run_id"]
    detail_resp = client.get(f"/api/runs/{run_id[:8]}")
    assert detail_resp.status_code == 200
    assert len(detail_resp.json()["steps"]) >= 6

    quar_resp = client.get("/api/quarantine", params={"domain": "commerce"})
    assert quar_resp.status_code == 200
    assert quar_resp.json()["count"] > 0


def test_api_diff_and_error_responses(tmp_path: Path) -> None:
    db_file = tmp_path / "diff_api.sqlite"
    app = create_app(
        db_path=str(db_file),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
    )
    client = TestClient(app)

    assert client.get("/api/runs/nonexistent").status_code == 404
    assert client.get("/api/diff").status_code == 400
    assert client.get("/api/diff", params={"domain": "commerce"}).status_code == 400

    sample_csv = Path("data/raw/ventes.csv")
    for _ in range(2):
        run_pipeline(
            file_path=str(sample_csv),
            domain="commerce",
            db_path=str(db_file),
            reports_dir=str(tmp_path / "reports"),
            processed_dir=str(tmp_path / "processed"),
        )

    diff_resp = client.get("/api/diff", params={"domain": "commerce"})
    assert diff_resp.status_code == 200
    assert diff_resp.json()["deltas"]["row_count_delta"] == 0


def test_api_upload_invalid_domain_returns_400(tmp_path: Path) -> None:
    app = create_app(db_path=str(tmp_path / "err.sqlite"))
    client = TestClient(app)

    resp = client.post(
        "/api/runs/upload",
        files={"file": ("test.csv", b"a,b\n1,2\n", "text/csv")},
        data={"domain": "domaine_inexistant"},
    )
    assert resp.status_code == 400


def test_cli_serve_parser_options() -> None:
    parser = build_parser()
    args = parser.parse_args(["serve", "--host", "0.0.0.0", "--port", "9000"])
    assert args.command == "serve"
    assert args.host == "0.0.0.0"
    assert args.port == 9000
