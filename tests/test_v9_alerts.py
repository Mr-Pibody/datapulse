"""Tests pour DataPulse V9 : alertes temps réel (Webhook HMAC, Slack, Discord, SMTP)."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
from fastapi.testclient import TestClient

from datapulse.alerts import (
    dispatch_run_alerts,
    list_alert_history,
    should_trigger_alert,
)
from datapulse.api import create_app
from datapulse.cli import run_pipeline, show_alerts_history
from datapulse.exceptions import ExitCode


class _WebhookRecorder:
    """Serveur HTTP local en mémoire pour capturer les requêtes Webhooks."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        recorder = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(length)
                recorder.requests.append(
                    {
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "raw": raw_body,
                        "json": json.loads(raw_body.decode("utf-8")),
                    }
                )
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, format: str, *args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def test_webhook_hmac_slack_and_discord_dispatch(tmp_path: Path) -> None:
    rec = _WebhookRecorder()
    try:
        db_file = tmp_path / "alerts_v9.sqlite"
        secret = "super_secret_key"
        settings = {
            "on": "alert",
            "webhook_url": f"{rec.base_url}/generic",
            "webhook_secret": secret,
            "slack_webhook_url": f"{rec.base_url}/slack",
            "discord_webhook_url": f"{rec.base_url}/discord",
        }
        run_meta = {
            "run_id": "12345678-aaaa-bbbb-cccc-1234567890ab",
            "domain": "commerce",
            "row_count": 200,
            "duration_ms": 42.0,
            "status": "QUALITY_ALERT",
        }
        findings = [{"row_index": 3, "column": "prix", "severity": "CRITICAL"}]

        results = dispatch_run_alerts(
            db_path=db_file,
            run_meta=run_meta,
            findings=findings,
            schema_drifts=[],
            data_drifts=[],
            override_settings=settings,
        )
        assert len(results) == 3
        assert all(r["status"] == "SENT" for r in results)
        assert len(rec.requests) == 3

        by_path = {r["path"]: r for r in rec.requests}
        expected_sig = hmac.new(
            secret.encode("utf-8"), by_path["/generic"]["raw"], hashlib.sha256
        ).hexdigest()
        assert by_path["/generic"]["headers"]["x-datapulse-signature"] == f"sha256={expected_sig}"
        assert "blocks" in by_path["/slack"]["json"]
        assert "embeds" in by_path["/discord"]["json"]
    finally:
        rec.close()


def test_trigger_policies_filter_correctly() -> None:
    clean_meta = {"status": "SUCCESS"}
    warn_finding = [{"severity": "MEDIUM"}]
    crit_finding = [{"severity": "CRITICAL"}]

    assert should_trigger_alert("never", clean_meta, crit_finding, [], []) is False
    assert should_trigger_alert("always", clean_meta, [], [], []) is True
    assert should_trigger_alert("alert", clean_meta, [], [], []) is False
    assert should_trigger_alert("alert", clean_meta, warn_finding, [], []) is True
    assert should_trigger_alert("critical", clean_meta, warn_finding, [], []) is False
    assert should_trigger_alert("critical", clean_meta, crit_finding, [], []) is True


def test_smtp_email_alert_dispatch(tmp_path: Path) -> None:
    db_file = tmp_path / "smtp_alerts.sqlite"
    settings = {
        "on": "always",
        "smtp": {
            "host": "smtp.example.local",
            "port": 587,
            "to_email": "ops@example.local",
            "from_email": "datapulse@example.local",
            "use_tls": True,
        },
    }
    mock_smtp = MagicMock()
    with patch("smtplib.SMTP", return_value=mock_smtp) as smtp_cls:
        results = dispatch_run_alerts(
            db_path=db_file,
            run_meta={"run_id": "abcd1234", "domain": "finance", "status": "SUCCESS"},
            findings=[],
            schema_drifts=[],
            data_drifts=[],
            override_settings=settings,
        )
    assert smtp_cls.called
    assert len(results) == 1
    assert results[0]["channel"] == "smtp"
    assert results[0]["status"] == "SENT"


def test_end_to_end_pipeline_dispatches_yaml_webhook(tmp_path: Path) -> None:
    rec = _WebhookRecorder()
    try:
        cfg_dir = tmp_path / "configs"
        cfg_dir.mkdir()
        (cfg_dir / "iot.yaml").write_text(
            f"""
domain: iot
version: "1.0"
rules:
  - id: R_TEMP
    column: temp
    check: gt
    value: 0
    severity: CRITICAL
anomaly_detection:
  columns: []
alerts:
  on: alert
  webhook_url: "{rec.base_url}/iot-hook"
""".strip(),
            encoding="utf-8",
        )
        csv_file = tmp_path / "iot_data.csv"
        pd.DataFrame({"temp": [22.0, -5.0]}).to_csv(csv_file, index=False)
        db_file = tmp_path / "e2e_alerts.sqlite"

        code = run_pipeline(
            file_path=str(csv_file),
            domain="iot",
            configs_dir=str(cfg_dir),
            db_path=str(db_file),
            reports_dir=str(tmp_path / "reports"),
            processed_dir=str(tmp_path / "processed"),
            strict=True,
        )
        assert code == int(ExitCode.QUALITY_ALERT)
        assert len(rec.requests) == 1
        assert rec.requests[0]["json"]["run"]["domain"] == "iot"

        history = list_alert_history(db_path=db_file, domain="iot")
        assert len(history) == 1
        assert history[0]["status"] == "SENT"
        assert show_alerts_history(domain="iot", db_path=str(db_file)) == int(ExitCode.SUCCESS)
    finally:
        rec.close()


def test_api_alerts_test_and_history_endpoints(tmp_path: Path) -> None:
    rec = _WebhookRecorder()
    try:
        db_file = tmp_path / "api_alerts.sqlite"
        app = create_app(db_path=str(db_file))
        client = TestClient(app)

        resp = client.post(
            "/api/alerts/test",
            json={"domain": "commerce", "slack_webhook_url": f"{rec.base_url}/slack"},
        )
        assert resp.status_code == 200
        assert resp.json()["results"][0]["status"] == "SENT"

        hist_resp = client.get("/api/alerts")
        assert hist_resp.status_code == 200
        assert hist_resp.json()["count"] == 1
    finally:
        rec.close()
