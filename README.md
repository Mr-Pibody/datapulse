# DataPulse V1

**DataPulse** is a generic, configuration-driven data quality and statistical anomaly observatory written in Python. It inspects tabular datasets across multiple business domains (**Commerce**, **Finance**, **Public Administration**) without changing a single line of engine code.

## Architecture

```text
[Source CSV/JSON/XLSX] + [configs/<domain>.yaml]
        │
        ▼
  1. INGESTION   ──► Immutable copy in data/raw/ + SHA-256 fingerprint
        │
        ▼
  2. PROFILING   ──► Schema types, completeness %, mean, median, std, min/max
        │
        ├────────────────────────┐
        ▼                        ▼
  3. VALIDATOR              4. DETECTOR
  (Deterministic YAML)      (Z-score & IQR with min_sample_size guard)
        │                        │
        └───────────┬────────────┘
                    ▼
  5. RECONCILER  ──► Deduplicates (row, col) into RULE_VIOLATION | STAT_ANOMALY | BOTH
        │
        ▼
  6. STORAGE     ──► SQLite audit trail (runs, profiling_stats, findings, benchmarks)
        │
        ▼
  7. REPORTER    ──► Matplotlib charts + Markdown & standalone HTML reports