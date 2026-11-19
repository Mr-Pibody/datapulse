"""Authentification par clés API SHA-256, RBAC et journal d'audit — DataPulse V13."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VALID_ROLES: tuple[str, ...] = ("viewer", "operator", "admin")
ROLE_LEVELS: dict[str, int] = {
    "viewer": 1,
    "operator": 2,
    "admin": 3,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_api_key(raw_key: str) -> str:
    """Calcule l'empreinte SHA-256 hexadécimale d'une clé API."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def role_satisfies(actual_role: str, required_role: str) -> bool:
    """Vérifie si le niveau de privilège du rôle actuel couvre le rôle requis."""
    actual_lvl = ROLE_LEVELS.get(actual_role, 0)
    req_lvl = ROLE_LEVELS.get(required_role, 99)
    return actual_lvl >= req_lvl


def extract_bearer_or_api_key(
    *,
    x_api_key: str | None = None,
    authorization: str | None = None,
) -> str | None:
    """Extrait le jeton API depuis X-API-Key ou Authorization: Bearer <token>."""
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    if authorization and authorization.strip():
        parts = authorization.strip().split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
            return parts[1].strip()
    return None


def ensure_security_tables(db_path: str | Path = "db/datapulse.sqlite") -> None:
    """Initialise les tables SQLite de gestion des clés API et du journal d'audit."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                key_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                key_prefix TEXT NOT NULL,
                key_hash TEXT NOT NULL UNIQUE,
                role TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_used_at TEXT,
                revoked_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS security_audit_log (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                actor_key_id TEXT NOT NULL,
                actor_name TEXT NOT NULL,
                actor_role TEXT NOT NULL,
                action TEXT NOT NULL,
                resource TEXT NOT NULL,
                outcome TEXT NOT NULL,
                detail TEXT NOT NULL
            )
            """
        )
        conn.commit()


def record_security_audit(
    db_path: str | Path,
    *,
    actor_key_id: str,
    actor_name: str,
    actor_role: str,
    action: str,
    resource: str,
    outcome: str,
    detail: str = "",
) -> None:
    """Enregistre un événement immuable dans le journal d'audit de sécurité."""
    ensure_security_tables(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO security_audit_log (
                timestamp, actor_key_id, actor_name, actor_role,
                action, resource, outcome, detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _utc_now_iso(),
                actor_key_id,
                actor_name,
                actor_role,
                action,
                resource,
                outcome,
                detail,
            ),
        )
        conn.commit()


def create_api_key(
    db_path: str | Path,
    name: str,
    role: str = "viewer",
    *,
    raw_key: str | None = None,
) -> dict[str, Any]:
    """Crée une clé API, stocke uniquement son hash SHA-256 et renvoie le secret."""
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Le nom de la clé API ne peut pas être vide.")
    clean_role = role.strip().lower()
    if clean_role not in VALID_ROLES:
        raise ValueError(f"Rôle invalide '{role}'. Rôles autorisés : {', '.join(VALID_ROLES)}.")

    ensure_security_tables(db_path)
    secret_token = raw_key.strip() if raw_key else f"dp_live_{secrets.token_hex(16)}"
    key_id = f"key_{secrets.token_hex(4)}"
    key_prefix = secret_token[:14]
    digest = hash_api_key(secret_token)
    created_at = _utc_now_iso()

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO api_keys (
                key_id, name, key_prefix, key_hash, role, is_active, created_at
            ) VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            (key_id, clean_name, key_prefix, digest, clean_role, created_at),
        )
        conn.commit()

    record_security_audit(
        db_path,
        actor_key_id=key_id,
        actor_name=clean_name,
        actor_role=clean_role,
        action="KEY_CREATED",
        resource=f"api_key:{key_id}",
        outcome="ALLOWED",
        detail=f"Clé API créée (préfixe={key_prefix}, rôle={clean_role})",
    )

    return {
        "key_id": key_id,
        "name": clean_name,
        "role": clean_role,
        "key_prefix": key_prefix,
        "api_key": secret_token,
        "created_at": created_at,
    }


def list_api_keys(db_path: str | Path) -> list[dict[str, Any]]:
    """Liste les métadonnées des clés API sans jamais exposer leurs empreintes."""
    ensure_security_tables(db_path)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT key_id, name, key_prefix, role, is_active,
                   created_at, last_used_at, revoked_at
            FROM api_keys
            ORDER BY created_at DESC
            """
        ).fetchall()

    return [
        {
            "key_id": str(r[0]),
            "name": str(r[1]),
            "key_prefix": str(r[2]),
            "role": str(r[3]),
            "is_active": bool(r[4]),
            "created_at": str(r[5]),
            "last_used_at": str(r[6]) if r[6] else None,
            "revoked_at": str(r[7]) if r[7] else None,
        }
        for r in rows
    ]


def revoke_api_key(db_path: str | Path, key_id: str) -> dict[str, Any]:
    """Révoque immédiatement une clé API existante."""
    ensure_security_tables(db_path)
    revoked_at = _utc_now_iso()
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT key_id, name, role, is_active FROM api_keys WHERE key_id = ?",
            (key_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Clé API introuvable : '{key_id}'.")
        conn.execute(
            "UPDATE api_keys SET is_active = 0, revoked_at = ? WHERE key_id = ?",
            (revoked_at, key_id),
        )
        conn.commit()

    record_security_audit(
        db_path,
        actor_key_id=str(row[0]),
        actor_name=str(row[1]),
        actor_role=str(row[2]),
        action="KEY_REVOKED",
        resource=f"api_key:{key_id}",
        outcome="ALLOWED",
        detail=f"Clé API '{key_id}' révoquée",
    )
    return {
        "key_id": str(row[0]),
        "name": str(row[1]),
        "role": str(row[2]),
        "is_active": False,
        "revoked_at": revoked_at,
    }


def has_active_api_keys(db_path: str | Path) -> bool:
    """Indique si au moins une clé API active est configurée dans la base."""
    path = Path(db_path)
    if not path.exists():
        return False
    ensure_security_tables(db_path)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM api_keys WHERE is_active = 1").fetchone()
    return bool(row and int(row[0]) > 0)


def bootstrap_env_api_key(db_path: str | Path) -> None:
    """Enregistre automatiquement la clé administrateur DATAPULSE_API_KEY si définie."""
    env_key = os.environ.get("DATAPULSE_API_KEY", "").strip()
    if not env_key:
        return
    ensure_security_tables(db_path)
    digest = hash_api_key(env_key)
    with sqlite3.connect(db_path) as conn:
        existing = conn.execute(
            "SELECT 1 FROM api_keys WHERE key_hash = ?",
            (digest,),
        ).fetchone()
    if existing is None:
        create_api_key(db_path, name="env-bootstrap-admin", role="admin", raw_key=env_key)


def authenticate_api_key(db_path: str | Path, raw_key: str) -> dict[str, Any] | None:
    """Vérifie une clé API en temps constant et met à jour son horodatage d'usage."""
    if not raw_key or not raw_key.strip():
        return None
    ensure_security_tables(db_path)
    candidate_hash = hash_api_key(raw_key.strip())
    matched: dict[str, Any] | None = None

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT key_id, name, key_prefix, key_hash, role FROM api_keys WHERE is_active = 1"
        ).fetchall()
        for r in rows:
            stored_hash = str(r[3])
            if hmac.compare_digest(candidate_hash, stored_hash):
                matched = {
                    "key_id": str(r[0]),
                    "name": str(r[1]),
                    "key_prefix": str(r[2]),
                    "role": str(r[4]),
                }
        if matched is not None:
            conn.execute(
                "UPDATE api_keys SET last_used_at = ? WHERE key_id = ?",
                (_utc_now_iso(), matched["key_id"]),
            )
            conn.commit()

    return matched


def list_security_audit(db_path: str | Path, limit: int = 50) -> list[dict[str, Any]]:
    """Retourne les événements récents du journal d'audit de sécurité."""
    ensure_security_tables(db_path)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT audit_id, timestamp, actor_key_id, actor_name, actor_role,
                   action, resource, outcome, detail
            FROM security_audit_log
            ORDER BY audit_id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [
        {
            "audit_id": int(r[0]),
            "timestamp": str(r[1]),
            "actor_key_id": str(r[2]),
            "actor_name": str(r[3]),
            "actor_role": str(r[4]),
            "action": str(r[5]),
            "resource": str(r[6]),
            "outcome": str(r[7]),
            "detail": str(r[8]),
        }
        for r in rows
    ]
