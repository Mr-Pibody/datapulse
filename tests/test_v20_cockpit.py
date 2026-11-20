from __future__ import annotations

from fastapi.testclient import TestClient

from datapulse.api import create_app


def test_v20_cockpit_and_studio_ui(tmp_path) -> None:
    app = create_app(db_path=tmp_path / "v20.sqlite")
    client = TestClient(app)

    home = client.get("/")
    assert home.status_code == 200
    assert "v20.0.0" in home.text

    res = client.get("/api/analytics/cockpit")
    assert res.status_code == 200
    payload = res.json()
    assert "kpis" in payload
    assert "test_breakdown" in payload
    assert len(payload["six_dimensions"]) == 6
    assert "stacked_history" in payload
    assert "corridor_series" in payload
