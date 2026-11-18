"""Système d'alertes et Webhooks temps réel (Slack, Discord, JSON HMAC, SMTP) — DataPulse V9."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import smtplib
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any


def _validate_http_url(url: str) -> str:
    """Vérifie que l'URL cible utilise exclusivement HTTP ou HTTPS."""
    clean = url.strip()
    parsed = urllib.parse.urlparse(clean)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"URL de webhook invalide (http/https requis) : '{url}'")
    return clean


def resolve_alert_settings(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fusionne la section `alerts:` du contrat YAML avec les variables d'environnement."""
    yaml_alerts = (config or {}).get("alerts", {})
    if not isinstance(yaml_alerts, dict):
        yaml_alerts = {}

    smtp_yaml = yaml_alerts.get("smtp", {})
    if not isinstance(smtp_yaml, dict):
        smtp_yaml = {}

    trigger_on = (
        str(yaml_alerts.get("on") or os.getenv("DATAPULSE_ALERT_ON") or "alert").strip().lower()
    )
    if trigger_on not in {"always", "alert", "critical", "never"}:
        trigger_on = "alert"

    smtp_host = str(smtp_yaml.get("host") or os.getenv("DATAPULSE_SMTP_HOST") or "").strip()
    smtp_to = str(smtp_yaml.get("to_email") or os.getenv("DATAPULSE_SMTP_TO") or "").strip()
    smtp_cfg: dict[str, Any] | None = None
    if smtp_host and smtp_to:
        smtp_cfg = {
            "host": smtp_host,
            "port": int(smtp_yaml.get("port") or os.getenv("DATAPULSE_SMTP_PORT") or 587),
            "to_email": smtp_to,
            "from_email": str(
                smtp_yaml.get("from_email")
                or os.getenv("DATAPULSE_SMTP_FROM")
                or "datapulse@localhost"
            ).strip(),
            "username": str(
                smtp_yaml.get("username") or os.getenv("DATAPULSE_SMTP_USER") or ""
            ).strip(),
            "password": str(
                smtp_yaml.get("password") or os.getenv("DATAPULSE_SMTP_PASSWORD") or ""
            ).strip(),
            "use_tls": bool(smtp_yaml.get("use_tls", True)),
        }

    return {
        "on": trigger_on,
        "webhook_url": str(
            yaml_alerts.get("webhook_url") or os.getenv("DATAPULSE_WEBHOOK_URL") or ""
        ).strip(),
        "webhook_secret": str(
            yaml_alerts.get("webhook_secret") or os.getenv("DATAPULSE_WEBHOOK_SECRET") or ""
        ).strip(),
        "slack_webhook_url": str(
            yaml_alerts.get("slack_webhook_url") or os.getenv("DATAPULSE_SLACK_WEBHOOK_URL") or ""
        ).strip(),
        "discord_webhook_url": str(
            yaml_alerts.get("discord_webhook_url")
            or os.getenv("DATAPULSE_DISCORD_WEBHOOK_URL")
            or ""
        ).strip(),
        "smtp": smtp_cfg,
    }


def should_trigger_alert(
    policy: str,
    run_meta: dict[str, Any],
    findings: list[dict[str, Any]],
    schema_drifts: list[dict[str, Any]],
    data_drifts: list[dict[str, Any]],
) -> bool:
    """Détermine si la politique de déclenchement impose l'envoi d'une notification."""
    if policy == "never":
        return False
    if policy == "always":
        return True

    has_any_issue = bool(
        findings or schema_drifts or data_drifts or run_meta.get("status") == "QUALITY_ALERT"
    )
    if policy == "alert":
        return has_any_issue

    # policy == "critical"
    has_critical_finding = any(str(f.get("severity", "")).upper() == "CRITICAL" for f in findings)
    has_critical_drift = any(
        str(d.get("severity", "")).upper() == "CRITICAL" for d in [*schema_drifts, *data_drifts]
    )
    return bool(
        run_meta.get("status") == "QUALITY_ALERT" or has_critical_finding or has_critical_drift
    )


def build_alert_payloads(
    run_meta: dict[str, Any],
    findings: list[dict[str, Any]],
    schema_drifts: list[dict[str, Any]],
    data_drifts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Construit les charges utiles JSON, Slack, Discord et Email pour un run."""
    run_id = str(run_meta.get("run_id", "unknown"))
    domain = str(run_meta.get("domain", "unknown"))
    status = str(run_meta.get("status", "SUCCESS"))
    row_count = int(run_meta.get("row_count", 0))
    duration_ms = float(run_meta.get("duration_ms", 0.0))

    summary_line = (
        f"[DataPulse] Domaine '{domain}' | Run {run_id[:8]} | Statut: {status} | "
        f"Lignes: {row_count} | Incidents: {len(findings)} | "
        f"Schema drift: {len(schema_drifts)} | Data drift: {len(data_drifts)}"
    )

    generic_payload: dict[str, Any] = {
        "event": "datapulse.run.completed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": summary_line,
        "run": run_meta,
        "counts": {
            "findings": len(findings),
            "schema_drifts": len(schema_drifts),
            "data_drifts": len(data_drifts),
        },
        "top_findings": findings[:5],
        "schema_drifts": schema_drifts[:5],
        "data_drifts": data_drifts[:5],
    }

    slack_payload: dict[str, Any] = {
        "text": summary_line,
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"DataPulse — {domain} ({status})",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Run ID:*\n`{run_id[:8]}`"},
                    {"type": "mrkdwn", "text": f"*Lignes:*\n{row_count}"},
                    {"type": "mrkdwn", "text": f"*Incidents:*\n{len(findings)}"},
                    {
                        "type": "mrkdwn",
                        "text": f"*Drifts:*\n{len(schema_drifts)} schéma / {len(data_drifts)} data",
                    },
                ],
            },
        ],
    }

    has_critical = status == "QUALITY_ALERT" or any(
        str(x.get("severity", "")).upper() == "CRITICAL"
        for x in [*findings, *schema_drifts, *data_drifts]
    )
    embed_color = 0xEF4444 if has_critical else (0xF59E0B if findings or data_drifts else 0x10B981)

    discord_payload: dict[str, Any] = {
        "content": summary_line,
        "embeds": [
            {
                "title": f"DataPulse — Audit {domain} ({status})",
                "color": embed_color,
                "fields": [
                    {"name": "Run ID", "value": f"`{run_id[:8]}`", "inline": True},
                    {"name": "Lignes", "value": str(row_count), "inline": True},
                    {"name": "Durée", "value": f"{duration_ms:.1f} ms", "inline": True},
                    {"name": "Incidents", "value": str(len(findings)), "inline": True},
                    {"name": "Schema Drift", "value": str(len(schema_drifts)), "inline": True},
                    {"name": "Data Drift", "value": str(len(data_drifts)), "inline": True},
                ],
            }
        ],
    }

    email_subject = f"[DataPulse][{status}] {domain} — {len(findings)} incident(s)"
    email_body = (
        f"{summary_line}\n"
        f"Source : {run_meta.get('source_file', 'N/A')}\n"
        f"Durée  : {duration_ms:.2f} ms\n"
    )

    return {
        "generic": generic_payload,
        "slack": slack_payload,
        "discord": discord_payload,
        "email_subject": email_subject,
        "email_body": email_body,
    }


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    secret: str = "",
    timeout: float = 8.0,
) -> str:
    """Envoie un POST JSON avec signature HMAC-SHA256 optionnelle."""
    valid_url = _validate_http_url(url)
    body_bytes = json.dumps(payload, default=str).encode("utf-8")
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "User-Agent": "DataPulse-AlertEngine/9.0",
    }
    if secret:
        sig = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
        headers["X-DataPulse-Signature"] = f"sha256={sig}"

    req = urllib.request.Request(valid_url, data=body_bytes, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        code = getattr(resp, "status", 200)
        return f"HTTP {code}"


def _send_smtp_email(smtp_cfg: dict[str, Any], subject: str, body: str) -> str:
    """Envoie une notification email via SMTP."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = str(smtp_cfg["from_email"])
    msg["To"] = str(smtp_cfg["to_email"])
    msg.set_content(body)

    host = str(smtp_cfg["host"])
    port = int(smtp_cfg["port"])
    with smtplib.SMTP(host, port, timeout=10) as server:
        if smtp_cfg.get("use_tls", True):
            server.starttls()
        username = str(smtp_cfg.get("username") or "")
        password = str(smtp_cfg.get("password") or "")
        if username and password:
            server.login(username, password)
        server.send_message(msg)
    return f"SMTP sent to {smtp_cfg['to_email']}"


def _record_alert_delivery(
    db_path: str | Path,
    run_id: str,
    domain: str,
    channel: str,
    status: str,
    detail: str,
) -> None:
    """Enregistre le statut d'envoi d'une notification dans SQLite."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS alert_deliveries ("
            "alert_id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "run_id TEXT NOT NULL, "
            "domain TEXT NOT NULL, "
            "channel TEXT NOT NULL, "
            "status TEXT NOT NULL, "
            "detail TEXT NOT NULL, "
            "created_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO alert_deliveries (run_id, domain, channel, status, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, domain, channel, status, detail, datetime.now(timezone.utc).isoformat()),
        )


def list_alert_history(
    db_path: str | Path = "db/datapulse.sqlite",
    domain: str | None = None,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Retourne l'historique des notifications envoyées depuis SQLite."""
    path = Path(db_path)
    if not path.exists():
        return []

    try:
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            tbl = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='alert_deliveries'"
            ).fetchone()
            if tbl is None:
                return []

            if domain:
                rows = conn.execute(
                    "SELECT * FROM alert_deliveries WHERE domain = ? "
                    "ORDER BY alert_id DESC LIMIT ?",
                    (domain, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM alert_deliveries ORDER BY alert_id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]
    except sqlite3.Error:
        return []


def dispatch_run_alerts(
    *,
    db_path: str | Path,
    run_meta: dict[str, Any],
    findings: list[dict[str, Any]],
    schema_drifts: list[dict[str, Any]],
    data_drifts: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
    override_settings: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Évalue les règles de déclenchement et expédie les alertes sur les canaux actifs."""
    settings = (
        override_settings if override_settings is not None else resolve_alert_settings(config)
    )
    run_id = str(run_meta.get("run_id", "manual-test"))
    domain = str(run_meta.get("domain", "unknown"))

    if not should_trigger_alert(
        str(settings.get("on", "alert")),
        run_meta,
        findings,
        schema_drifts,
        data_drifts,
    ):
        return []

    payloads = build_alert_payloads(run_meta, findings, schema_drifts, data_drifts)
    results: list[dict[str, str]] = []

    channels: list[tuple[str, Any]] = []
    if settings.get("webhook_url"):
        channels.append(
            (
                "webhook",
                lambda: _post_json(
                    str(settings["webhook_url"]),
                    payloads["generic"],
                    secret=str(settings.get("webhook_secret", "")),
                ),
            )
        )
    if settings.get("slack_webhook_url"):
        channels.append(
            (
                "slack",
                lambda: _post_json(str(settings["slack_webhook_url"]), payloads["slack"]),
            )
        )
    if settings.get("discord_webhook_url"):
        channels.append(
            (
                "discord",
                lambda: _post_json(str(settings["discord_webhook_url"]), payloads["discord"]),
            )
        )
    if settings.get("smtp"):
        smtp_cfg = dict(settings["smtp"])
        channels.append(
            (
                "smtp",
                lambda: _send_smtp_email(
                    smtp_cfg,
                    str(payloads["email_subject"]),
                    str(payloads["email_body"]),
                ),
            )
        )

    for channel_name, sender in channels:
        try:
            detail = str(sender())
            status = "SENT"
        except (urllib.error.URLError, OSError, ValueError, smtplib.SMTPException) as exc:
            detail = str(exc)
            status = "FAILED"

        _record_alert_delivery(db_path, run_id, domain, channel_name, status, detail)
        results.append({"channel": channel_name, "status": status, "detail": detail})

    return results


def send_test_alert(
    *,
    db_path: str | Path = "db/datapulse.sqlite",
    domain: str = "test_domain",
    webhook_url: str = "",
    slack_webhook_url: str = "",
    discord_webhook_url: str = "",
    webhook_secret: str = "",
) -> list[dict[str, str]]:
    """Envoie une notification de test immédiate sur les canaux spécifiés ou configurés."""
    resolved = resolve_alert_settings()
    settings = {
        "on": "always",
        "webhook_url": webhook_url.strip() or resolved["webhook_url"],
        "webhook_secret": webhook_secret.strip() or resolved["webhook_secret"],
        "slack_webhook_url": slack_webhook_url.strip() or resolved["slack_webhook_url"],
        "discord_webhook_url": discord_webhook_url.strip() or resolved["discord_webhook_url"],
        "smtp": resolved["smtp"],
    }
    synthetic_meta = {
        "run_id": "test0000-0000-0000-0000-000000000000",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "domain": domain,
        "source_file": "datapulse://alerts-test",
        "row_count": 100,
        "duration_ms": 12.5,
        "status": "QUALITY_ALERT",
    }
    synthetic_findings = [
        {
            "row_index": 0,
            "column": "montant",
            "rule_id": "TEST_ALERT",
            "severity": "CRITICAL",
            "message": "Notification de test DataPulse V9",
        }
    ]
    return dispatch_run_alerts(
        db_path=db_path,
        run_meta=synthetic_meta,
        findings=synthetic_findings,
        schema_drifts=[],
        data_drifts=[],
        override_settings=settings,
    )
