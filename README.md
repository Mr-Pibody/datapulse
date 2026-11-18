# DataPulse (v5.0.0)

**DataPulse** is an observable, rule-based and statistical data quality engine engineered for multi-domain pipelines (`commerce`, `finance`, `administration`). It combines declarative YAML business contracts, univariate/multivariate statistical anomaly detection, chunked streaming ingestion, SQLite WAL persistence, a Dead-Letter Quarantine workflow, and a FastAPI + Web Dashboard interface.

---

## Architecture & Pipeline Flow

```text
Source File (CSV / XLSX / JSON / JSONL / Parquet)
   │
   ▼
[1. Ingestion] ──► Immutable raw copy + SHA-256 fingerprint (batch or chunked stream)
   │
   ▼
[2. Profiling] ──► Column stats + Welford online stats + inter-run Schema Drift detection
   │
   ▼
[3. Validation] ─► Declarative YAML rules (not_null, gt/gte/lt/lte, between, regex, in_set, unique, cross_column)
   │
   ▼
[4. Detection] ──► Univariate (Z-Score, IQR, Modified-Z MAD) + Multivariate L2-MAD
   │
   ▼
[5. Reconciler] ─► Cell-level deduplication (RULE_VIOLATION / STAT_ANOMALY / BOTH)
   │
   ├─► [Quarantine DLQ] ─► Isolates rejected rows in SQLite + exports *_clean.csv & replay CSV
   ├─► [Storage (WAL)] ──► Atomic SQLite transactions (runs, profiling_stats, findings, step_metrics, quarantine_rows)
   └─► [Reporters/API] ──► HTML/Markdown/PNG artifacts + FastAPI REST endpoints & Web Dashboard