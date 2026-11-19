"""Score de Santé Qualité (0-100), Data Lineage et Certificats d'Audit — DataPulse V11."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def compute_file_sha256(file_path: str | Path | None) -> str:
    """Calcule l'empreinte SHA-256 d'un fichier s'il existe sur le disque."""
    if not file_path:
        return ""
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        return ""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _normalize_json_keys(value: Any) -> Any:
    """Convertit récursivement toutes les clés de dictionnaire en str pour tri JSON sûr."""
    if isinstance(value, dict):
        return {str(k): _normalize_json_keys(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_json_keys(item) for item in value]
    return value


def compute_contract_sha256(config: dict[str, Any]) -> str:
    """Calcule l'empreinte SHA-256 canonique d'un contrat de données YAML."""
    normalized = _normalize_json_keys(config)
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_quality_score(
    *,
    row_count: int,
    completeness_pct: float,
    findings: list[dict[str, Any]],
    schema_drifts: list[dict[str, Any]],
    data_drifts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Calcule un score de santé qualité global (0-100) et un grade (A-F)."""
    completeness = max(0.0, min(100.0, float(completeness_pct)))

    if row_count <= 0:
        validity = 100.0 if not findings else 0.0
    else:
        affected_rows: set[int] = set()
        for f in findings:
            idx = f.get("row_index")
            if isinstance(idx, int) and idx >= 0:
                affected_rows.add(idx)
        impacted = len(affected_rows) if affected_rows else len(findings)
        error_ratio = min(1.0, impacted / max(row_count, 1))
        validity = max(0.0, 100.0 * (1.0 - error_ratio))

    stat_penalty = 0.0
    for dd in data_drifts:
        sev = str(dd.get("severity", "WARNING")).upper()
        stat_penalty += 25.0 if sev == "CRITICAL" else 10.0
    statistical_stability = max(0.0, 100.0 - stat_penalty)

    schema_penalty = 0.0
    for sd in schema_drifts:
        sev = str(sd.get("severity", "WARNING")).upper()
        schema_penalty += 35.0 if sev == "CRITICAL" else 15.0
    schema_stability = max(0.0, 100.0 - schema_penalty)

    total_score = (
        0.30 * completeness
        + 0.35 * validity
        + 0.20 * statistical_stability
        + 0.15 * schema_stability
    )
    rounded = round(total_score, 2)

    if rounded >= 90.0:
        grade = "A"
    elif rounded >= 80.0:
        grade = "B"
    elif rounded >= 70.0:
        grade = "C"
    elif rounded >= 60.0:
        grade = "D"
    else:
        grade = "F"

    return {
        "score": rounded,
        "grade": grade,
        "breakdown": {
            "completeness": round(completeness, 2),
            "validity": round(validity, 2),
            "statistical_stability": round(statistical_stability, 2),
            "schema_stability": round(schema_stability, 2),
        },
    }


def _ensure_lineage_table(db_path: str | Path) -> None:
    """Crée la table SQLite run_lineage si elle n'existe pas encore."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS run_lineage ("
            "run_id TEXT PRIMARY KEY, "
            "domain TEXT NOT NULL, "
            "source_uri TEXT NOT NULL, "
            "source_sha256 TEXT NOT NULL, "
            "contract_version TEXT NOT NULL, "
            "contract_sha256 TEXT NOT NULL, "
            "quality_score REAL NOT NULL, "
            "quality_grade TEXT NOT NULL, "
            "score_breakdown_json TEXT NOT NULL, "
            "input_rows INTEGER NOT NULL, "
            "clean_rows INTEGER NOT NULL, "
            "quarantined_rows INTEGER NOT NULL, "
            "clean_artifact_path TEXT NOT NULL, "
            "clean_artifact_sha256 TEXT NOT NULL, "
            "report_html_path TEXT NOT NULL, "
            "certificate_sha256 TEXT NOT NULL, "
            "created_at TEXT NOT NULL)"
        )


def _extract_seal_payload(record: dict[str, Any]) -> dict[str, Any]:
    """Extrait les champs canoniques protégés par le sceau SHA-256."""
    return {
        "run_id": str(record.get("run_id", "")),
        "domain": str(record.get("domain", "")),
        "source_uri": str(record.get("source_uri", "")),
        "source_sha256": str(record.get("source_sha256", "")),
        "contract_version": str(record.get("contract_version", "")),
        "contract_sha256": str(record.get("contract_sha256", "")),
        "quality_score": round(float(record.get("quality_score", 0.0)), 2),
        "quality_grade": str(record.get("quality_grade", "")),
        "input_rows": int(record.get("input_rows", 0)),
        "clean_rows": int(record.get("clean_rows", 0)),
        "quarantined_rows": int(record.get("quarantined_rows", 0)),
        "clean_artifact_sha256": str(record.get("clean_artifact_sha256", "")),
        "created_at": str(record.get("created_at", "")),
    }


def _compute_seal(record: dict[str, Any]) -> str:
    """Calcule le sceau cryptographique SHA-256 d'un enregistrement de lineage."""
    payload = _extract_seal_payload(record)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_run_lineage(
    *,
    db_path: str | Path,
    run_id: str,
    domain: str,
    source_uri: str,
    source_sha256: str,
    config: dict[str, Any],
    quality_info: dict[str, Any],
    input_rows: int,
    quarantined_rows: int,
    clean_artifact_path: str = "",
    report_html_path: str = "",
) -> dict[str, Any]:
    """Enregistre la chaîne complète de lignage et génère son sceau d'audit."""
    _ensure_lineage_table(db_path)
    contract_version = str(config.get("version", "11.0"))
    contract_sha256 = compute_contract_sha256(config)
    clean_sha256 = compute_file_sha256(clean_artifact_path)
    clean_rows = max(0, int(input_rows) - int(quarantined_rows))
    created_at = datetime.now(timezone.utc).isoformat()

    record: dict[str, Any] = {
        "run_id": run_id,
        "domain": domain,
        "source_uri": source_uri,
        "source_sha256": source_sha256,
        "contract_version": contract_version,
        "contract_sha256": contract_sha256,
        "quality_score": float(quality_info["score"]),
        "quality_grade": str(quality_info["grade"]),
        "score_breakdown": quality_info["breakdown"],
        "input_rows": int(input_rows),
        "clean_rows": clean_rows,
        "quarantined_rows": int(quarantined_rows),
        "clean_artifact_path": clean_artifact_path,
        "clean_artifact_sha256": clean_sha256,
        "report_html_path": report_html_path,
        "created_at": created_at,
    }
    cert_sha = _compute_seal(record)
    record["certificate_sha256"] = cert_sha

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO run_lineage ("
            "run_id, domain, source_uri, source_sha256, contract_version, contract_sha256, "
            "quality_score, quality_grade, score_breakdown_json, input_rows, clean_rows, "
            "quarantined_rows, clean_artifact_path, clean_artifact_sha256, report_html_path, "
            "certificate_sha256, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                domain,
                source_uri,
                source_sha256,
                contract_version,
                contract_sha256,
                record["quality_score"],
                record["quality_grade"],
                json.dumps(record["score_breakdown"], sort_keys=True),
                record["input_rows"],
                record["clean_rows"],
                record["quarantined_rows"],
                clean_artifact_path,
                clean_sha256,
                report_html_path,
                cert_sha,
                created_at,
            ),
        )
    return record


def get_run_lineage(db_path: str | Path, run_id: str) -> dict[str, Any] | None:
    """Récupère le lineage et le score qualité d'un run (ID complet ou préfixe)."""
    path = Path(db_path)
    if not path.exists():
        return None
    _ensure_lineage_table(path)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM run_lineage WHERE run_id = ? OR run_id LIKE ? "
            "ORDER BY created_at DESC LIMIT 1",
            (run_id, f"{run_id}%"),
        ).fetchone()
    if row is None:
        return None
    data = dict(row)
    raw_breakdown = str(data.pop("score_breakdown_json", "{}"))
    data["score_breakdown"] = json.loads(raw_breakdown)
    return data


def build_audit_certificate(db_path: str | Path, run_id: str) -> dict[str, Any] | None:
    """Construit le certificat d'audit portable et signé d'un run."""
    lineage = get_run_lineage(db_path, run_id)
    if lineage is None:
        return None
    return {
        "certificate_type": "DataPulseCertifiedAudit",
        "issuer": "DataPulse Observatory v11.0.0",
        **lineage,
    }


def verify_audit_certificate(certificate: dict[str, Any]) -> dict[str, Any]:
    """Vérifie l'intégrité cryptographique SHA-256 d'un certificat d'audit."""
    expected = _compute_seal(certificate)
    actual = str(certificate.get("certificate_sha256", ""))
    is_valid = bool(actual) and expected == actual
    return {
        "valid": is_valid,
        "run_id": str(certificate.get("run_id", "")),
        "domain": str(certificate.get("domain", "")),
        "quality_score": certificate.get("quality_score"),
        "quality_grade": certificate.get("quality_grade"),
        "expected_sha256": expected,
        "actual_sha256": actual,
    }


def export_audit_certificate(
    db_path: str | Path,
    run_id: str,
    output_path: str | Path | None = None,
) -> Path:
    """Exporte le certificat d'audit signé vers un fichier JSON."""
    cert = build_audit_certificate(db_path, run_id)
    if cert is None:
        raise FileNotFoundError(f"Lineage introuvable pour le run '{run_id}'.")
    full_id = str(cert["run_id"])
    dest = Path(output_path) if output_path else Path("reports") / f"cert_{full_id[:8]}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(cert, indent=2, ensure_ascii=False), encoding="utf-8")
    return dest
