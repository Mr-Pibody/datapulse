from dataclasses import asdict, dataclass
from typing import Any, Literal

FindingCategory = Literal["RULE_VIOLATION", "STAT_ANOMALY", "BOTH"]
SeverityLevel = Literal["LOW", "MEDIUM", "HIGH"]


@dataclass(frozen=True)
class Finding:
    row_index: int
    column_name: str
    raw_value: str
    category: FindingCategory
    rule_id: str
    stat_method: str
    stat_score: float | None
    severity: SeverityLevel
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RunMeta:
    run_id: str
    timestamp: str
    domain: str
    source_file: str
    file_hash_sha256: str
    config_version: str
    row_count: int
    duration_ms: float
    peak_memory_kb: float
    status: str = "SUCCESS"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
