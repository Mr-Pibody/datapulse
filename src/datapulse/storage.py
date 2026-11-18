"""Persistance SQLite en mode WAL avec gestion transactionnelle stricte."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from datapulse.exceptions import StorageTransactionError
from datapulse.profiler import detect_schema_drift


class DataPulseStorage:
    def __init__(self, db_path: str = "db/datapulse.sqlite") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.execute("PRAGMA foreign_keys=ON;")
            yield conn
            conn.commit()
        except sqlite3.Error as exc:
            if conn is not None:
                conn.rollback()
            raise StorageTransactionError(
                "Échec de transaction SQLite",
                context={"db_path": str(self.db_path), "sqlite_error": str(exc)},
            ) from exc
        finally:
            if conn is not None:
                conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    source_file TEXT NOT NULL,
                    file_hash_sha256 TEXT NOT NULL,
                    config_version TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    findings_count INTEGER NOT NULL,
                    duration_ms REAL NOT NULL,
                    peak_memory_kb REAL NOT NULL,
                    status TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS profiling_stats (
                    stat_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    column_name TEXT NOT NULL,
                    dtype TEXT NOT NULL,
                    null_count INTEGER NOT NULL,
                    mean REAL,
                    median REAL,
                    std REAL,
                    min REAL,
                    max REAL,
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS findings (
                    finding_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    row_index INTEGER NOT NULL,
                    column_name TEXT NOT NULL,
                    raw_value TEXT,
                    category TEXT NOT NULL,
                    rule_id TEXT,
                    stat_method TEXT,
                    stat_score REAL,
                    severity TEXT NOT NULL,
                    explanation TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS benchmark_metrics (
                    bench_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    true_positives INTEGER NOT NULL,
                    false_positives INTEGER NOT NULL,
                    false_negatives INTEGER NOT NULL,
                    precision REAL NOT NULL,
                    recall REAL NOT NULL,
                    f1_score REAL NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS step_metrics (
                    step_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    step_order INTEGER NOT NULL,
                    step_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    duration_ms REAL NOT NULL,
                    rows_in INTEGER NOT NULL,
                    rows_out INTEGER NOT NULL,
                    throughput_rows_per_sec REAL NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS quarantine_rows (
                    quarantine_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    row_index INTEGER NOT NULL,
                    row_payload_json TEXT NOT NULL,
                    issues_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    created_at TEXT NOT NULL,
                    replayed_at TEXT,
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                );

                CREATE INDEX IF NOT EXISTS idx_runs_domain_ts ON runs(domain, timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_findings_run_id ON findings(run_id);
                CREATE INDEX IF NOT EXISTS idx_profiling_run_id ON profiling_stats(run_id);
                CREATE INDEX IF NOT EXISTS idx_step_metrics_run_id ON step_metrics(run_id);
                CREATE INDEX IF NOT EXISTS idx_quarantine_domain_status
                    ON quarantine_rows(domain, status);
                """
            )

    def save_run(
        self,
        run_meta: dict[str, Any],
        profile: dict[str, Any],
        findings: list[dict[str, Any]],
        steps: list[dict[str, Any]] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    run_id, timestamp, domain, source_file, file_hash_sha256,
                    config_version, row_count, findings_count, duration_ms, peak_memory_kb, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_meta["run_id"],
                    run_meta["timestamp"],
                    run_meta["domain"],
                    run_meta["source_file"],
                    run_meta["file_hash_sha256"],
                    run_meta["config_version"],
                    run_meta["row_count"],
                    len(findings),
                    run_meta["duration_ms"],
                    run_meta["peak_memory_kb"],
                    run_meta["status"],
                ),
            )

            for col in profile.get("columns", []):
                conn.execute(
                    """
                    INSERT INTO profiling_stats (
                        run_id, column_name, dtype, null_count, mean, median, std, min, max
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_meta["run_id"],
                        col["column_name"],
                        col["dtype"],
                        col["null_count"],
                        col["mean"],
                        col["median"],
                        col["std"],
                        col["min"],
                        col["max"],
                    ),
                )

            for f in findings:
                conn.execute(
                    """
                    INSERT INTO findings (
                        run_id, row_index, column_name, raw_value, category,
                        rule_id, stat_method, stat_score, severity, explanation
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_meta["run_id"],
                        f["row_index"],
                        f["column_name"],
                        f["raw_value"],
                        f["category"],
                        f["rule_id"],
                        f["stat_method"],
                        f["stat_score"],
                        f["severity"],
                        f["explanation"],
                    ),
                )

            for idx, step in enumerate(steps or [], start=1):
                conn.execute(
                    """
                    INSERT INTO step_metrics (
                        run_id, step_order, step_name, status,
                        duration_ms, rows_in, rows_out, throughput_rows_per_sec
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_meta["run_id"],
                        idx,
                        step["step"],
                        step["status"],
                        step["duration_ms"],
                        step["rows_in"],
                        step["rows_out"],
                        step["throughput_rows_per_sec"],
                    ),
                )

    def save_quarantine_rows(self, run_id: str, domain: str, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            for item in rows:
                conn.execute(
                    """
                    INSERT INTO quarantine_rows (
                        run_id, domain, row_index, row_payload_json,
                        issues_json, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, 'PENDING', ?)
                    """,
                    (
                        run_id,
                        domain,
                        item["row_index"],
                        item["row_payload_json"],
                        item["issues_json"],
                        now_iso,
                    ),
                )
        return len(rows)

    def list_quarantine(
        self, domain: str | None = None, status: str = "PENDING", limit: int = 50
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM quarantine_rows WHERE status = ?"
        params: list[Any] = [status]
        if domain:
            query += " AND domain = ?"
            params.append(domain)
        query += " ORDER BY quarantine_id ASC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def mark_quarantine_replayed(self, quarantine_ids: list[int]) -> int:
        if not quarantine_ids:
            return 0
        now_iso = datetime.now(timezone.utc).isoformat()
        placeholders = ",".join("?" for _ in quarantine_ids)
        sql = (
            f"UPDATE quarantine_rows SET status = 'REPLAYED', replayed_at = ? "
            f"WHERE quarantine_id IN ({placeholders}) AND status = 'PENDING'"
        )
        with self._connect() as conn:
            cursor = conn.execute(sql, [now_iso, *quarantine_ids])
            return int(cursor.rowcount)

    def save_benchmark(self, run_id: str, metrics: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO benchmark_metrics (
                    run_id, true_positives, false_positives, false_negatives,
                    precision, recall, f1_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    metrics["true_positives"],
                    metrics["false_positives"],
                    metrics["false_negatives"],
                    metrics["precision"],
                    metrics["recall"],
                    metrics["f1_score"],
                ),
            )

    def get_history(self, domain: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        query = "SELECT * FROM runs"
        params: list[Any] = []
        if domain:
            query += " WHERE domain = ?"
            params.append(domain)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def get_run_details(self, run_id_prefix: str) -> dict[str, Any] | None:
        """Retourne un run (par ID complet ou préfixe) avec ses étapes et ses incidents."""
        with self._connect() as conn:
            query = (
                "SELECT * FROM runs WHERE run_id = ? OR run_id LIKE ? "
                "ORDER BY timestamp DESC LIMIT 1"
            )
            run_row = conn.execute(
                query,
                (run_id_prefix, f"{run_id_prefix}%"),
            ).fetchone()
            if run_row is None:
                return None

            run_dict = dict(run_row)
            full_id = run_dict["run_id"]
            steps = conn.execute(
                "SELECT * FROM step_metrics WHERE run_id = ? ORDER BY step_order ASC",
                (full_id,),
            ).fetchall()
            findings = conn.execute(
                "SELECT * FROM findings WHERE run_id = ? ORDER BY finding_id ASC",
                (full_id,),
            ).fetchall()
            run_dict["steps"] = [dict(s) for s in steps]
            run_dict["findings"] = [dict(f) for f in findings]
            return run_dict

    def get_previous_run_profile(
        self, domain: str, exclude_run_id: str | None = None
    ) -> tuple[str, list[dict[str, Any]]] | None:
        """Retourne (run_id, profiling_stats) de la précédente exécution du même domaine."""
        with self._connect() as conn:
            if exclude_run_id:
                row = conn.execute(
                    "SELECT run_id FROM runs WHERE domain = ? AND run_id != ? "
                    "ORDER BY timestamp DESC LIMIT 1",
                    (domain, exclude_run_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT run_id FROM runs WHERE domain = ? ORDER BY timestamp DESC LIMIT 1",
                    (domain,),
                ).fetchone()

            if row is None:
                return None

            prev_id = str(row["run_id"])
            cols = conn.execute(
                "SELECT * FROM profiling_stats WHERE run_id = ? ORDER BY stat_id ASC",
                (prev_id,),
            ).fetchall()
            return prev_id, [dict(c) for c in cols]

    def compare_runs(self, run_a_prefix: str, run_b_prefix: str) -> dict[str, Any]:
        """Compare deux exécutions (run_a = référence, run_b = cible) et calcule les deltas."""
        run_a = self.get_run_details(run_a_prefix)
        run_b = self.get_run_details(run_b_prefix)
        if run_a is None or run_b is None:
            missing = run_a_prefix if run_a is None else run_b_prefix
            raise StorageTransactionError(
                f"Impossible de comparer : run introuvable '{missing}'",
                context={"run_a": run_a_prefix, "run_b": run_b_prefix},
            )

        with self._connect() as conn:
            cols_a = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM profiling_stats WHERE run_id = ? ORDER BY stat_id ASC",
                    (run_a["run_id"],),
                ).fetchall()
            ]
            cols_b = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM profiling_stats WHERE run_id = ? ORDER BY stat_id ASC",
                    (run_b["run_id"],),
                ).fetchall()
            ]

        drifts = detect_schema_drift({"columns": cols_b}, cols_a)
        return {
            "run_a": {
                "run_id": run_a["run_id"],
                "domain": run_a["domain"],
                "timestamp": run_a["timestamp"],
                "row_count": run_a["row_count"],
                "findings_count": run_a["findings_count"],
                "duration_ms": run_a["duration_ms"],
            },
            "run_b": {
                "run_id": run_b["run_id"],
                "domain": run_b["domain"],
                "timestamp": run_b["timestamp"],
                "row_count": run_b["row_count"],
                "findings_count": run_b["findings_count"],
                "duration_ms": run_b["duration_ms"],
            },
            "deltas": {
                "row_count_delta": int(run_b["row_count"]) - int(run_a["row_count"]),
                "findings_count_delta": (
                    int(run_b["findings_count"]) - int(run_a["findings_count"])
                ),
                "duration_ms_delta": round(
                    float(run_b["duration_ms"]) - float(run_a["duration_ms"]), 2
                ),
            },
            "schema_drifts": drifts,
        }
