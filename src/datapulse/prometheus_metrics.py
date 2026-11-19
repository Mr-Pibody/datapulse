"""Exposition Prometheus / OpenMetrics et sondes de santé — DataPulse V12."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _escape_label(value: Any) -> str:
    """Échappe une valeur d'étiquette selon la spécification Prometheus."""
    text = str(value)
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def collect_observability_snapshot(db_path: str | Path = "db/datapulse.sqlite") -> dict[str, Any]:
    """Agrège toutes les métriques opérationnelles et qualité depuis SQLite."""
    path = Path(db_path)
    snapshot: dict[str, Any] = {
        "version": "17.0.0",
        "runs_by_domain_status": [],
        "totals_by_domain": [],
        "latest_quality_by_domain": [],
        "quarantine_pending_by_domain": [],
        "alerts_by_channel_status": [],
        "watcher_tracked_files": 0,
        "scheduler_jobs": 0,
    }
    if not path.exists():
        return snapshot

    with sqlite3.connect(path) as conn:
        if _table_exists(conn, "runs"):
            rows = conn.execute(
                "SELECT domain, status, COUNT(*) FROM runs GROUP BY domain, status"
            ).fetchall()
            snapshot["runs_by_domain_status"] = [
                {"domain": str(r[0]), "status": str(r[1]), "count": int(r[2])} for r in rows
            ]

            dom_rows = conn.execute(
                "SELECT domain, COALESCE(SUM(row_count), 0), "
                "COALESCE(SUM(findings_count), 0), COALESCE(AVG(duration_ms), 0.0) "
                "FROM runs GROUP BY domain"
            ).fetchall()
            snapshot["totals_by_domain"] = [
                {
                    "domain": str(r[0]),
                    "rows_processed": int(r[1]),
                    "findings": int(r[2]),
                    "avg_duration_ms": round(float(r[3]), 3),
                }
                for r in dom_rows
            ]

        if _table_exists(conn, "run_lineage"):
            lin_rows = conn.execute(
                "SELECT l.domain, l.quality_score, l.quality_grade "
                "FROM run_lineage l "
                "INNER JOIN ("
                "  SELECT domain, MAX(created_at) AS max_ts FROM run_lineage GROUP BY domain"
                ") latest ON l.domain = latest.domain AND l.created_at = latest.max_ts"
            ).fetchall()
            snapshot["latest_quality_by_domain"] = [
                {
                    "domain": str(r[0]),
                    "quality_score": float(r[1]),
                    "quality_grade": str(r[2]),
                }
                for r in lin_rows
            ]

        if _table_exists(conn, "quarantine_rows"):
            q_rows = conn.execute(
                "SELECT domain, COUNT(*) FROM quarantine_rows "
                "WHERE status = 'PENDING' GROUP BY domain"
            ).fetchall()
            snapshot["quarantine_pending_by_domain"] = [
                {"domain": str(r[0]), "pending": int(r[1])} for r in q_rows
            ]

        if _table_exists(conn, "alert_history"):
            a_rows = conn.execute(
                "SELECT channel, status, COUNT(*) FROM alert_history GROUP BY channel, status"
            ).fetchall()
            snapshot["alerts_by_channel_status"] = [
                {"channel": str(r[0]), "status": str(r[1]), "count": int(r[2])} for r in a_rows
            ]

        if _table_exists(conn, "watcher_files"):
            wf = conn.execute("SELECT COUNT(*) FROM watcher_files").fetchone()
            snapshot["watcher_tracked_files"] = int(wf[0]) if wf else 0

        if _table_exists(conn, "scheduler_jobs"):
            sj = conn.execute("SELECT COUNT(*) FROM scheduler_jobs").fetchone()
            snapshot["scheduler_jobs"] = int(sj[0]) if sj else 0

    return snapshot


def render_prometheus_metrics(db_path: str | Path = "db/datapulse.sqlite") -> str:
    """Formate les métriques DataPulse au format texte Prometheus 0.0.4."""
    snap = collect_observability_snapshot(db_path)
    lines: list[str] = [
        "# HELP datapulse_info Informations de build et version de DataPulse.",
        "# TYPE datapulse_info gauge",
        f'datapulse_info{{version="{_escape_label(snap["version"])}"}} 1',
        "",
        "# HELP datapulse_runs_total Nombre total d'exécutions par domaine et statut.",
        "# TYPE datapulse_runs_total counter",
    ]

    for item in snap["runs_by_domain_status"]:
        dom = _escape_label(item["domain"])
        st = _escape_label(item["status"])
        lines.append(f'datapulse_runs_total{{domain="{dom}",status="{st}"}} {item["count"]}')

    lines.extend(
        [
            "",
            "# HELP datapulse_rows_processed_total Volume total de lignes auditées par domaine.",
            "# TYPE datapulse_rows_processed_total counter",
        ]
    )
    for item in snap["totals_by_domain"]:
        dom = _escape_label(item["domain"])
        lines.append(f'datapulse_rows_processed_total{{domain="{dom}"}} {item["rows_processed"]}')

    lines.extend(
        [
            "",
            "# HELP datapulse_findings_total Nombre total d'incidents détectés par domaine.",
            "# TYPE datapulse_findings_total counter",
        ]
    )
    for item in snap["totals_by_domain"]:
        dom = _escape_label(item["domain"])
        lines.append(f'datapulse_findings_total{{domain="{dom}"}} {item["findings"]}')

    lines.extend(
        [
            "",
            "# HELP datapulse_run_duration_milliseconds_avg Latence moyenne des runs (ms).",
            "# TYPE datapulse_run_duration_milliseconds_avg gauge",
        ]
    )
    for item in snap["totals_by_domain"]:
        dom = _escape_label(item["domain"])
        lines.append(
            f'datapulse_run_duration_milliseconds_avg{{domain="{dom}"}} {item["avg_duration_ms"]}'
        )

    lines.extend(
        [
            "",
            "# HELP datapulse_quality_score_latest Dernier score de santé qualité (0-100).",
            "# TYPE datapulse_quality_score_latest gauge",
        ]
    )
    for item in snap["latest_quality_by_domain"]:
        dom = _escape_label(item["domain"])
        grade = _escape_label(item["quality_grade"])
        lines.append(
            f'datapulse_quality_score_latest{{domain="{dom}",grade="{grade}"}} '
            f"{item['quality_score']}"
        )

    lines.extend(
        [
            "",
            "# HELP datapulse_quarantine_pending_rows Lignes actuellement en quarantaine PENDING.",
            "# TYPE datapulse_quarantine_pending_rows gauge",
        ]
    )
    for item in snap["quarantine_pending_by_domain"]:
        dom = _escape_label(item["domain"])
        lines.append(f'datapulse_quarantine_pending_rows{{domain="{dom}"}} {item["pending"]}')

    lines.extend(
        [
            "",
            "# HELP datapulse_alerts_dispatched_total Alertes envoyées par canal et statut.",
            "# TYPE datapulse_alerts_dispatched_total counter",
        ]
    )
    for item in snap["alerts_by_channel_status"]:
        ch = _escape_label(item["channel"])
        st = _escape_label(item["status"])
        lines.append(
            f'datapulse_alerts_dispatched_total{{channel="{ch}",status="{st}"}} {item["count"]}'
        )

    lines.extend(
        [
            "",
            "# HELP datapulse_watcher_tracked_files_total Fichiers suivis par le hot-folder.",
            "# TYPE datapulse_watcher_tracked_files_total gauge",
            f"datapulse_watcher_tracked_files_total {snap['watcher_tracked_files']}",
            "",
            "# HELP datapulse_scheduler_jobs_total Tâches périodiques enregistrées.",
            "# TYPE datapulse_scheduler_jobs_total gauge",
            f"datapulse_scheduler_jobs_total {snap['scheduler_jobs']}",
            "",
        ]
    )
    return "\n".join(lines)


def check_liveness() -> dict[str, Any]:
    """Sonde Liveness (processus HTTP actif)."""
    return {
        "status": "alive",
        "version": "17.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def check_readiness(db_path: str | Path = "db/datapulse.sqlite") -> tuple[bool, dict[str, Any]]:
    """Sonde Readiness (vérifie la disponibilité opérationnelle de SQLite)."""
    path = Path(db_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as conn:
            ping = conn.execute("SELECT 1").fetchone()
            journal_row = conn.execute("PRAGMA journal_mode").fetchone()
            tables_row = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()
        ok = ping is not None and int(ping[0]) == 1
        journal = str(journal_row[0]) if journal_row else "unknown"
        tables_count = int(tables_row[0]) if tables_row else 0
        payload: dict[str, Any] = {
            "status": "ready" if ok else "degraded",
            "version": "17.0.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": {
                "sqlite_ping": ok,
                "journal_mode": journal,
                "tables_count": tables_count,
                "db_path": str(path),
            },
        }
        return ok, payload
    except Exception as exc:
        return False, {
            "status": "unavailable",
            "version": "17.0.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": {
                "sqlite_ping": False,
                "error": str(exc),
                "db_path": str(path),
            },
        }
