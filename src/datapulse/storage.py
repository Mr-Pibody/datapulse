import sqlite3
from pathlib import Path
from typing import Any


class DataPulseStorage:
    def __init__(self, db_path: str = "db/datapulse.sqlite") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

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

                CREATE INDEX IF NOT EXISTS idx_runs_domain_ts ON runs(domain, timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_findings_run_id ON findings(run_id);
                CREATE INDEX IF NOT EXISTS idx_profiling_run_id ON profiling_stats(run_id);
                """
            )

    def save_run(
        self,
        run_meta: dict[str, Any],
        profile: dict[str, Any],
        findings: list[dict[str, Any]],
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
