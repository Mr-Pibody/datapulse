"""Tests pour DataPulse V13 : sécurité Zero-Trust, clés API SHA-256, RBAC et audit."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from datapulse.api import create_app
from datapulse.cli import audit_log_cmd, key_create_cmd, key_list_cmd, key_revoke_cmd
from datapulse.security import (
    authenticate_api_key,
    create_api_key,
    list_api_keys,
    list_security_audit,
    revoke_api_key,
)


def test_api_key_sha256_lifecycle_and_revocation(tmp_path: Path) -> None:
    db_file = tmp_path / "sec_keys.sqlite"
    created = create_api_key(db_file, name="ci-operator", role="operator")
    raw_secret = str(created["api_key"])
    assert raw_secret.startswith("dp_live_")

    # La liste ne doit jamais exposer la clé brute ni le hash
    keys = list_api_keys(db_file)
    assert len(keys) == 1
    assert "api_key" not in keys[0]
    assert "key_hash" not in keys[0]
    assert keys[0]["is_active"] is True

    # Authentification réussie
    principal = authenticate_api_key(db_file, raw_secret)
    assert principal is not None
    assert principal["role"] == "operator"

    # Révocation immédiate
    revoke_api_key(db_file, str(created["key_id"]))
    assert authenticate_api_key(db_file, raw_secret) is None

    with pytest.raises(ValueError, match="Rôle invalide"):
        create_api_key(db_file, name="bad", role="superuser")


def test_api_rbac_enforces_401_403_and_200(tmp_path: Path) -> None:
    db_file = tmp_path / "sec_api.sqlite"
    admin_key = create_api_key(db_file, name="root-admin", role="admin")["api_key"]
    op_key = create_api_key(db_file, name="etl-op", role="operator")["api_key"]
    view_key = create_api_key(db_file, name="bi-viewer", role="viewer")["api_key"]

    app = create_app(
        db_path=str(db_file),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
    )
    client = TestClient(app)

    # 1. Les sondes infrastructure restent publiques
    assert client.get("/health/live").status_code == 200
    assert client.get("/metrics").status_code == 200

    # 2. Sans clé -> 401 Unauthorized
    assert client.get("/api/runs").status_code == 401
    assert client.get("/api/runs", headers={"X-API-Key": "dp_live_invalid"}).status_code == 401

    # 3. Viewer : lecture autorisée (200), écriture refusée (403)
    assert client.get("/api/runs", headers={"X-API-Key": str(view_key)}).status_code == 200
    with open("data/reference/ventes_clean.csv", "rb") as f:
        forbidden_upload = client.post(
            "/api/runs/upload",
            headers={"X-API-Key": str(view_key)},
            files={"file": ("ventes_clean.csv", f, "text/csv")},
            data={"domain": "commerce"},
        )
    assert forbidden_upload.status_code == 403

    # 4. Operator : exécution pipeline autorisée (200 via Authorization Bearer), admin refusé (403)
    with open("data/reference/ventes_clean.csv", "rb") as f:
        ok_upload = client.post(
            "/api/runs/upload",
            headers={"Authorization": f"Bearer {op_key}"},
            files={"file": ("ventes_clean.csv", f, "text/csv")},
            data={"domain": "commerce"},
        )
    assert ok_upload.status_code == 200
    assert client.get("/api/security/keys", headers={"X-API-Key": str(op_key)}).status_code == 403

    # 5. Admin : gestion des clés et consultation du journal d'audit (200)
    create_resp = client.post(
        "/api/security/keys",
        headers={"X-API-Key": str(admin_key)},
        json={"name": "audit-bot", "role": "viewer"},
    )
    assert create_resp.status_code == 200
    new_key_id = create_resp.json()["key_id"]

    del_resp = client.delete(
        f"/api/security/keys/{new_key_id}",
        headers={"X-API-Key": str(admin_key)},
    )
    assert del_resp.status_code == 200

    audit_resp = client.get("/api/security/audit", headers={"X-API-Key": str(admin_key)})
    assert audit_resp.status_code == 200
    outcomes = {ev["outcome"] for ev in audit_resp.json()["events"]}
    assert "ALLOWED" in outcomes
    assert "DENIED" in outcomes


def test_env_bootstrap_api_key_enforces_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = tmp_path / "env_sec.sqlite"
    monkeypatch.setenv("DATAPULSE_API_KEY", "dp_live_bootstrap_secret_1234567890")

    app = create_app(db_path=str(db_file))
    client = TestClient(app)

    assert client.get("/api/domains").status_code == 401
    ok = client.get(
        "/api/domains",
        headers={"X-API-Key": "dp_live_bootstrap_secret_1234567890"},
    )
    assert ok.status_code == 200


def test_cli_security_key_and_audit_commands(tmp_path: Path) -> None:
    db_file = str(tmp_path / "cli_sec.sqlite")
    assert key_list_cmd(db_path=db_file) == 0
    assert audit_log_cmd(db_path=db_file) == 0

    assert key_create_cmd(name="cli-admin", role="admin", db_path=db_file) == 0
    assert key_list_cmd(db_path=db_file) == 0

    keys = list_api_keys(db_file)
    assert len(keys) == 1
    assert key_revoke_cmd(key_id=str(keys[0]["key_id"]), db_path=db_file) == 0
    assert audit_log_cmd(db_path=db_file) == 0

    events = list_security_audit(db_file)
    assert len(events) >= 2
