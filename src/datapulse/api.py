"""API REST FastAPI, RBAC Zero-Trust et observabilité DataPulse V13."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from datapulse.alerts import list_alert_history, send_test_alert
from datapulse.cli import run_pipeline, run_sql_pipeline
from datapulse.config_loader import ConfigError
from datapulse.dashboard import render_dashboard_html
from datapulse.drift import detect_data_drift, load_run_column_profiles
from datapulse.exceptions import DataPulseError, StorageTransactionError
from datapulse.lineage import build_audit_certificate, get_run_lineage, verify_audit_certificate
from datapulse.prometheus_metrics import (
    check_liveness,
    check_readiness,
    render_prometheus_metrics,
)
from datapulse.schema_inference import list_available_domains
from datapulse.security import (
    authenticate_api_key,
    bootstrap_env_api_key,
    create_api_key,
    extract_bearer_or_api_key,
    has_active_api_keys,
    list_api_keys,
    list_security_audit,
    record_security_audit,
    revoke_api_key,
    role_satisfies,
)
from datapulse.sql_connector import list_sql_tables
from datapulse.storage import DataPulseStorage
from datapulse.watcher import get_watcher_status, scan_directory_once


class SqlRunRequest(BaseModel):
    """Charge utile JSON pour déclencher un audit qualité sur une source SQL."""

    uri: str
    table: str | None = None
    query: str | None = None
    domain: str = "auto"
    quarantine: bool = False
    strict: bool = False


class AlertTestRequest(BaseModel):
    """Charge utile JSON pour tester un canal de notification DataPulse."""

    domain: str = "test_domain"
    webhook_url: str = ""
    slack_webhook_url: str = ""
    discord_webhook_url: str = ""
    webhook_secret: str = ""


class WatcherScanRequest(BaseModel):
    """Charge utile JSON pour déclencher un scan immédiat d'un hot-folder."""

    watch_dir: str
    domain: str = "auto"
    archive_dir: str | None = None
    quarantine: bool = True
    strict: bool = False


class ApiKeyCreateRequest(BaseModel):
    """Charge utile JSON pour créer une nouvelle clé API RBAC."""

    name: str
    role: str = "viewer"


def create_app(
    db_path: str = "db/datapulse.sqlite",
    reports_dir: str = "reports",
    processed_dir: str = "data/processed",
    configs_dir: str = "configs",
    *,
    auth_enabled: bool | None = None,
) -> FastAPI:
    """Construit l'instance FastAPI configurée avec contrôle d'accès RBAC."""
    app = FastAPI(
        title="DataPulse API",
        version="13.0.0",
        description="API REST Zero-Trust de gouvernance, Data Lineage, RBAC et Prometheus.",
    )

    def _storage() -> DataPulseStorage:
        return DataPulseStorage(db_path=db_path)

    def _require_role(min_role: str) -> Callable[[Request], dict[str, Any]]:
        def _dependency(request: Request) -> dict[str, Any]:
            bootstrap_env_api_key(db_path)
            env_required = os.environ.get("DATAPULSE_AUTH_REQUIRED", "").strip().lower() in {
                "1",
                "true",
                "yes",
            }
            enforced = (
                auth_enabled
                if auth_enabled is not None
                else (env_required or has_active_api_keys(db_path))
            )
            resource = f"{request.method} {request.url.path}"
            raw_key = extract_bearer_or_api_key(
                x_api_key=request.headers.get("x-api-key"),
                authorization=request.headers.get("authorization"),
            )

            if not enforced and not raw_key:
                return {"key_id": "open-mode", "name": "local-open", "role": "admin"}

            if not raw_key:
                record_security_audit(
                    db_path,
                    actor_key_id="anonymous",
                    actor_name="anonymous",
                    actor_role="none",
                    action="AUTH_MISSING",
                    resource=resource,
                    outcome="DENIED",
                    detail="En-tête X-API-Key ou Authorization Bearer manquant",
                )
                raise HTTPException(
                    status_code=401,
                    detail="Authentification requise (en-tête X-API-Key ou Authorization: Bearer).",
                )

            principal = authenticate_api_key(db_path, raw_key)
            if principal is None:
                record_security_audit(
                    db_path,
                    actor_key_id="invalid",
                    actor_name="invalid",
                    actor_role="none",
                    action="AUTH_INVALID",
                    resource=resource,
                    outcome="DENIED",
                    detail="Clé API invalide ou révoquée",
                )
                raise HTTPException(status_code=401, detail="Clé API invalide ou révoquée.")

            if not role_satisfies(str(principal["role"]), min_role):
                record_security_audit(
                    db_path,
                    actor_key_id=str(principal["key_id"]),
                    actor_name=str(principal["name"]),
                    actor_role=str(principal["role"]),
                    action="RBAC_FORBIDDEN",
                    resource=resource,
                    outcome="DENIED",
                    detail=f"Rôle '{principal['role']}' insuffisant (requis: '{min_role}')",
                )
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"Accès refusé : rôle '{min_role}' requis (actuel : '{principal['role']}')."
                    ),
                )

            record_security_audit(
                db_path,
                actor_key_id=str(principal["key_id"]),
                actor_name=str(principal["name"]),
                actor_role=str(principal["role"]),
                action="API_ACCESS",
                resource=resource,
                outcome="ALLOWED",
                detail=f"Accès autorisé ({min_role})",
            )
            return principal

        return _dependency

    @app.get("/", response_class=HTMLResponse)
    def dashboard_home() -> HTMLResponse:
        return HTMLResponse(content=render_dashboard_html())

    @app.get("/api/health")
    def healthcheck() -> dict[str, Any]:
        storage = _storage()
        return {
            "status": "ok",
            "version": "13.0.0",
            "db_path": str(storage.db_path),
            "auth_enforced": (
                auth_enabled if auth_enabled is not None else has_active_api_keys(db_path)
            ),
        }

    @app.get("/health/live")
    def liveness_probe() -> dict[str, Any]:
        return check_liveness()

    @app.get("/health/ready")
    def readiness_probe() -> JSONResponse:
        ready, payload = check_readiness(db_path=db_path)
        return JSONResponse(status_code=200 if ready else 503, content=payload)

    @app.get("/metrics", response_class=PlainTextResponse)
    def prometheus_metrics() -> PlainTextResponse:
        body = render_prometheus_metrics(db_path=db_path)
        return PlainTextResponse(
            content=body,
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.get("/api/security/keys", dependencies=[Depends(_require_role("admin"))])
    def get_security_keys() -> dict[str, Any]:
        items = list_api_keys(db_path)
        return {"count": len(items), "keys": items}

    @app.post("/api/security/keys", dependencies=[Depends(_require_role("admin"))])
    def create_security_key_endpoint(payload: ApiKeyCreateRequest) -> dict[str, Any]:
        try:
            return create_api_key(db_path, name=payload.name, role=payload.role)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/security/keys/{key_id}", dependencies=[Depends(_require_role("admin"))])
    def revoke_security_key_endpoint(key_id: str) -> dict[str, Any]:
        try:
            return revoke_api_key(db_path, key_id=key_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/security/audit", dependencies=[Depends(_require_role("admin"))])
    def get_security_audit_endpoint(
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        events = list_security_audit(db_path, limit=limit)
        return {"count": len(events), "events": events}

    @app.get("/api/domains", dependencies=[Depends(_require_role("viewer"))])
    def get_domains() -> dict[str, Any]:
        configured = list_available_domains(configs_dir=configs_dir)
        items = [
            {
                "domain": "auto",
                "description": "Détection automatique du contrat ou inférence à la volée",
            },
            *[{"domain": d["domain"], "description": d["description"]} for d in configured],
        ]
        return {"count": len(items), "domains": items}

    @app.get("/api/sql/tables", dependencies=[Depends(_require_role("viewer"))])
    def inspect_sql_tables(uri: str = Query(..., min_length=3)) -> dict[str, Any]:
        try:
            tables = list_sql_tables(uri)
        except DataPulseError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"count": len(tables), "tables": tables}

    @app.get("/api/runs", dependencies=[Depends(_require_role("viewer"))])
    def list_runs(
        domain: str | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=200),
    ) -> dict[str, Any]:
        runs = _storage().get_history(domain=domain, limit=limit)
        return {"count": len(runs), "runs": runs}

    @app.get("/api/runs/{run_id}", dependencies=[Depends(_require_role("viewer"))])
    def get_run(run_id: str) -> dict[str, Any]:
        details = _storage().get_run_details(run_id)
        if details is None:
            raise HTTPException(status_code=404, detail=f"Run introuvable : {run_id}")
        return details

    @app.get("/api/runs/{run_id}/lineage", dependencies=[Depends(_require_role("viewer"))])
    def get_run_lineage_endpoint(run_id: str) -> dict[str, Any]:
        lineage = get_run_lineage(db_path, run_id)
        if lineage is None:
            raise HTTPException(status_code=404, detail=f"Lineage introuvable : {run_id}")
        return lineage

    @app.get("/api/runs/{run_id}/certificate", dependencies=[Depends(_require_role("viewer"))])
    def get_run_certificate_endpoint(run_id: str) -> dict[str, Any]:
        cert = build_audit_certificate(db_path, run_id)
        if cert is None:
            raise HTTPException(status_code=404, detail=f"Certificat introuvable : {run_id}")
        return cert

    @app.post("/api/certificates/verify", dependencies=[Depends(_require_role("viewer"))])
    def verify_certificate_endpoint(certificate: dict[str, Any]) -> dict[str, Any]:
        return verify_audit_certificate(certificate)

    @app.get("/api/diff", dependencies=[Depends(_require_role("viewer"))])
    def compare_runs_endpoint(
        run_a: str | None = Query(default=None),
        run_b: str | None = Query(default=None),
        domain: str | None = Query(default=None),
    ) -> dict[str, Any]:
        storage = _storage()
        if domain and (not run_a or not run_b):
            recent = storage.get_history(domain=domain, limit=2)
            if len(recent) < 2:
                raise HTTPException(
                    status_code=400,
                    detail=f"Au moins 2 exécutions sont requises pour '{domain}'.",
                )
            run_b = str(recent[0]["run_id"])
            run_a = str(recent[1]["run_id"])

        if not run_a or not run_b:
            raise HTTPException(
                status_code=400,
                detail="Fournissez run_a et run_b, ou le paramètre domain.",
            )

        try:
            comparison = storage.compare_runs(run_a, run_b)
        except StorageTransactionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        full_a = str(comparison["run_a"]["run_id"])
        full_b = str(comparison["run_b"]["run_id"])
        cols_a = load_run_column_profiles(db_path, full_a)
        cols_b = load_run_column_profiles(db_path, full_b)
        comparison["data_drifts"] = detect_data_drift(cols_b, cols_a)
        return comparison

    @app.get("/api/quarantine", dependencies=[Depends(_require_role("viewer"))])
    def get_quarantine(
        domain: str | None = Query(default=None),
        status: str = Query(default="PENDING"),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        items = _storage().list_quarantine(domain=domain, status=status, limit=limit)
        return {"count": len(items), "items": items}

    @app.get("/api/alerts", dependencies=[Depends(_require_role("viewer"))])
    def get_alerts(
        domain: str | None = Query(default=None),
        limit: int = Query(default=30, ge=1, le=200),
    ) -> dict[str, Any]:
        items = list_alert_history(db_path=db_path, domain=domain, limit=limit)
        return {"count": len(items), "alerts": items}

    @app.post("/api/alerts/test", dependencies=[Depends(_require_role("operator"))])
    def test_alerts_endpoint(payload: AlertTestRequest) -> dict[str, Any]:
        results = send_test_alert(
            db_path=db_path,
            domain=payload.domain,
            webhook_url=payload.webhook_url,
            slack_webhook_url=payload.slack_webhook_url,
            discord_webhook_url=payload.discord_webhook_url,
            webhook_secret=payload.webhook_secret,
        )
        if not results:
            raise HTTPException(
                status_code=400,
                detail="Aucun canal de notification fourni ou configuré.",
            )
        return {"count": len(results), "results": results}

    @app.get("/api/watcher/status", dependencies=[Depends(_require_role("viewer"))])
    def watcher_status_endpoint(limit: int = Query(default=30, ge=1, le=200)) -> dict[str, Any]:
        return get_watcher_status(db_path=db_path, limit=limit)

    @app.post("/api/watcher/scan", dependencies=[Depends(_require_role("operator"))])
    def watcher_scan_endpoint(payload: WatcherScanRequest) -> dict[str, Any]:
        try:
            events = scan_directory_once(
                payload.watch_dir,
                domain=payload.domain,
                db_path=db_path,
                reports_dir=reports_dir,
                processed_dir=processed_dir,
                configs_dir=configs_dir,
                archive_dir=payload.archive_dir,
                strict=payload.strict,
                quarantine=payload.quarantine,
            )
        except (DataPulseError, ConfigError, FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"processed_count": len(events), "events": events}

    @app.post("/api/runs/upload", dependencies=[Depends(_require_role("operator"))])
    async def upload_and_run(
        file: UploadFile = File(...),  # noqa: B008
        domain: str = Form(default="auto"),
        quarantine: bool = Form(default=False),
        strict: bool = Form(default=False),
    ) -> dict[str, Any]:
        filename = file.filename or "upload.csv"
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir) / filename
            with open(tmp_path, "wb") as out_f:
                shutil.copyfileobj(file.file, out_f)

            try:
                exit_code = run_pipeline(
                    file_path=str(tmp_path),
                    domain=domain,
                    db_path=db_path,
                    reports_dir=reports_dir,
                    processed_dir=processed_dir,
                    configs_dir=configs_dir,
                    strict=strict,
                    quarantine=quarantine,
                )
            except (DataPulseError, ConfigError, FileNotFoundError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        lookup_domain = None if domain.strip().lower() == "auto" else domain
        latest = _storage().get_history(domain=lookup_domain, limit=1)
        return {
            "exit_code": exit_code,
            "run": latest[0] if latest else None,
        }

    @app.post("/api/runs/sql", dependencies=[Depends(_require_role("operator"))])
    def run_sql_endpoint(payload: SqlRunRequest) -> dict[str, Any]:
        try:
            exit_code = run_sql_pipeline(
                uri=payload.uri,
                domain=payload.domain,
                table=payload.table,
                query=payload.query,
                db_path=db_path,
                reports_dir=reports_dir,
                processed_dir=processed_dir,
                configs_dir=configs_dir,
                strict=payload.strict,
                quarantine=payload.quarantine,
            )
        except (DataPulseError, ConfigError, FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        lookup_domain = None if payload.domain.strip().lower() == "auto" else payload.domain
        latest = _storage().get_history(domain=lookup_domain, limit=1)
        return {
            "exit_code": exit_code,
            "run": latest[0] if latest else None,
        }

    return app


app = create_app()
