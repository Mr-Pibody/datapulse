"""API REST FastAPI, RBAC Zero-Trust, RGPD/PII, Synthèse Chaos & Lineage DataPulse V17."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from datapulse.alerts import list_alert_history, send_test_alert
from datapulse.catalog import (
    delete_glossary_term,
    get_asset,
    get_catalog_summary,
    list_assets,
    list_glossary_terms,
    register_asset,
    search_catalog,
    sync_catalog_from_configs,
    upsert_glossary_term,
)
from datapulse.cli import run_pipeline, run_sql_pipeline
from datapulse.config_loader import ConfigError
from datapulse.dashboard import render_dashboard_html
from datapulse.drift import detect_data_drift, load_run_column_profiles
from datapulse.exceptions import DataPulseError, StorageTransactionError
from datapulse.ingestion import ingest_file
from datapulse.lineage import build_audit_certificate, get_run_lineage, verify_audit_certificate
from datapulse.pii import mask_file_pii, scan_dataframe_pii
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
    ensure_security_tables,
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
from datapulse.synth import generate_synthetic_file
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


class SynthGenerateRequest(BaseModel):
    """Charge utile JSON pour générer un dataset synthétique avec chaos optionnel."""

    domain: str = "commerce"
    rows: int = 200
    chaos_rate: float = 0.0
    chaos_modes: list[str] | None = None
    seed: int = 42
    auto_audit: bool = False
    quarantine: bool = True


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
        version="18.0.0",
        description="API REST Zero-Trust de gouvernance, RGPD/PII, Synthèse Chaos et Lineage.",
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
            "version": "17.0.0",
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

    @app.post("/api/synth/generate", dependencies=[Depends(_require_role("operator"))])
    def generate_synth_endpoint(payload: SynthGenerateRequest) -> dict[str, Any]:
        try:
            manifest = generate_synthetic_file(
                domain=payload.domain,
                rows=payload.rows,
                chaos_rate=payload.chaos_rate,
                chaos_modes=payload.chaos_modes,
                seed=payload.seed,
                processed_dir=processed_dir,
                configs_dir=configs_dir,
            )
            audit_summary: dict[str, Any] | None = None
            if payload.auto_audit:
                exit_code = run_pipeline(
                    file_path=str(manifest["output_path"]),
                    domain=str(manifest["domain"]),
                    db_path=db_path,
                    reports_dir=reports_dir,
                    processed_dir=processed_dir,
                    configs_dir=configs_dir,
                    quarantine=payload.quarantine,
                )
                latest = _storage().get_history(domain=str(manifest["domain"]), limit=1)
                audit_summary = {
                    "exit_code": exit_code,
                    "run": latest[0] if latest else None,
                }
            return {**manifest, "audit": audit_summary}
        except (DataPulseError, ConfigError, FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/pii/scan", dependencies=[Depends(_require_role("viewer"))])
    async def scan_pii_endpoint(file: UploadFile = File(...)) -> dict[str, Any]:  # noqa: B008
        filename = file.filename or "scan.csv"
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir) / filename
            with open(tmp_path, "wb") as out_f:
                shutil.copyfileobj(file.file, out_f)
            try:
                ingestion = ingest_file(str(tmp_path))
                return scan_dataframe_pii(ingestion.dataframe)
            except (DataPulseError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/pii/mask", dependencies=[Depends(_require_role("operator"))])
    async def mask_pii_endpoint(
        file: UploadFile = File(...),  # noqa: B008
        strategy: str = Form(default="partial"),
        columns: str = Form(default=""),
    ) -> dict[str, Any]:
        filename = file.filename or "mask.csv"
        col_list = [c.strip() for c in columns.split(",") if c.strip()] if columns else None
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir) / filename
            with open(tmp_path, "wb") as out_f:
                shutil.copyfileobj(file.file, out_f)
            try:
                return mask_file_pii(
                    str(tmp_path),
                    strategy=strategy,
                    columns=col_list,
                    processed_dir=processed_dir,
                )
            except (DataPulseError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

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

    @app.get("/api/catalog/summary")
    def api_catalog_summary() -> dict[str, Any]:
        return get_catalog_summary(db_path)

    @app.get("/api/catalog/assets")
    def api_catalog_assets(
        classification: str | None = None,
        sla_status: str | None = None,
        q: str | None = None,
    ) -> dict[str, Any]:
        items = list_assets(
            db_path,
            classification=classification,
            sla_status=sla_status,
            query=q,
        )
        return {"count": len(items), "assets": items}

    @app.get("/api/catalog/assets/{domain}")
    def api_catalog_asset_detail(
        domain: str,
    ) -> dict[str, Any]:
        asset = get_asset(db_path, domain)
        if asset is None:
            raise HTTPException(status_code=404, detail=f"Actif introuvable: {domain}")
        return asset

    @app.post("/api/catalog/assets", dependencies=[Depends(_require_role("operator"))])
    def api_catalog_register_asset(
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        domain = str(payload.get("domain", "")).strip()
        if not domain:
            raise HTTPException(status_code=400, detail="Le champ 'domain' est requis.")
        try:
            return register_asset(
                db_path,
                domain=domain,
                display_name=payload.get("display_name"),
                owner=str(payload.get("owner", "data-engineering@datapulse.local")),
                steward=str(payload.get("steward", "governance@datapulse.local")),
                classification=str(payload.get("classification", "INTERNAL")),
                description=str(payload.get("description", "")),
                tags=list(payload.get("tags") or []),
                sla_max_age_hours=float(payload.get("sla_max_age_hours", 24.0)),
                schema_columns=list(payload.get("schema_columns") or []),
                source_uri=str(payload.get("source_uri", "")),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/catalog/sync", dependencies=[Depends(_require_role("operator"))])
    def api_catalog_sync(
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cfg_dir = str((payload or {}).get("config_dir", "config"))
        return sync_catalog_from_configs(db_path, config_dir=cfg_dir)

    @app.get("/api/catalog/glossary")
    def api_catalog_glossary(
        domain: str | None = None,
        q: str | None = None,
    ) -> dict[str, Any]:
        terms = list_glossary_terms(db_path, domain=domain, query=q)
        return {"count": len(terms), "terms": terms}

    @app.post("/api/catalog/glossary", dependencies=[Depends(_require_role("operator"))])
    def api_catalog_upsert_glossary(
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return upsert_glossary_term(
                db_path,
                term=str(payload.get("term", "")),
                definition=str(payload.get("definition", "")),
                domain=str(payload.get("domain", "global")),
                owner=str(payload.get("owner", "business-steward@datapulse.local")),
                formula=str(payload.get("formula", "")),
                synonyms=list(payload.get("synonyms") or []),
                related_columns=list(payload.get("related_columns") or []),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/catalog/glossary/{term}")
    def api_catalog_delete_glossary(
        term: str,
    ) -> dict[str, Any]:
        deleted = delete_glossary_term(db_path, term)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Terme introuvable: {term}")
        return {"deleted": True, "term": term}

    @app.get("/api/catalog/search")
    def api_catalog_search(
        q: str = "",
    ) -> dict[str, Any]:
        return search_catalog(db_path, q)


    # ==================== ENDPOINTS v18.0 (ZERO TERMINAL) ====================
    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.post("/api/demo/bootstrap")
    def demo_bootstrap_session() -> dict[str, Any]:
        """Genere et active une cle admin pour la session Web sans passer par la CLI."""
        ensure_security_tables(db_path)
        key_info = create_api_key(db_path, name="web-demo-admin", role="admin")
        try:
            sync_catalog_from_configs(db_path, config_dir=configs_dir)
            if not list_glossary_terms(db_path):
                upsert_glossary_term(
                    db_path,
                    term="Taux de Conformité Qualité",
                    definition=(
                        "Pourcentage de lignes valides respectant l'ensemble des règles."
                    ),
                    domain="global",
                    formula="(total_rows - quarantined_rows) / total_rows * 100",
                )
        except Exception:
            pass
        return {
            "api_key": key_info["api_key"],
            "key_id": key_info["key_id"],
            "role": key_info["role"],
            "name": key_info["name"],
        }

    @app.get("/api/demo/datasets", dependencies=[Depends(_require_role("viewer"))])
    def list_demo_datasets() -> dict[str, Any]:
        """Liste les jeux de donnees reels disponibles dans data/raw pour audit en 1 clic."""
        raw_dir = Path("data/raw")
        curated = [
            ("ventes.csv", "commerce", "Ventes E-Commerce réelles (avec anomalies prix/doublons)"),
            ("transactions.csv", "finance", "Transactions financières (montants & devises)"),
            ("clients_pii_test.csv", "auto", "Fichier Clients RGPD (Emails, Téléphones, IBAN)"),
            ("services_publics.csv", "administration", "Indicateurs Services Publics"),
            ("diamonds.csv", "diamonds", "Catalogue Diamants haute volumétrie"),
        ]
        datasets: list[dict[str, Any]] = []
        seen: set[str] = set()
        for fname, dom, label in curated:
            fpath = raw_dir / fname
            if fpath.exists():
                seen.add(fname)
                datasets.append({
                    "filename": fname,
                    "suggested_domain": dom,
                    "label": label,
                    "size_kb": round(fpath.stat().st_size / 1024, 1),
                })
        if raw_dir.exists():
            for p in sorted(raw_dir.glob("*.csv")):
                if p.name not in seen and "_annotated" not in p.name and "_clean" not in p.name:
                    datasets.append({
                        "filename": p.name,
                        "suggested_domain": "auto",
                        "label": f"Dataset CSV ({p.name})",
                        "size_kb": round(p.stat().st_size / 1024, 1),
                    })
        return {"count": len(datasets), "datasets": datasets}

    @app.post("/api/demo/run-dataset", dependencies=[Depends(_require_role("operator"))])
    def run_demo_dataset(payload: dict[str, Any]) -> dict[str, Any]:
        """Execute le pipeline complet directement sur un fichier de data/raw."""
        filename = Path(str(payload.get("filename", ""))).name
        domain = str(payload.get("domain", "auto"))
        quarantine_flag = bool(payload.get("quarantine", True))
        target = Path("data/raw") / filename
        if not filename or not target.exists():
            raise HTTPException(status_code=404, detail=f"Dataset introuvable : {filename}")
        try:
            exit_code = run_pipeline(
                file_path=str(target),
                domain=domain,
                db_path=db_path,
                reports_dir=reports_dir,
                processed_dir=processed_dir,
                configs_dir=configs_dir,
                quarantine=quarantine_flag,
            )
        except (DataPulseError, ConfigError, FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        lookup_domain = None if domain.strip().lower() == "auto" else domain
        latest = _storage().get_history(domain=lookup_domain, limit=1)
        return {"exit_code": exit_code, "run": latest[0] if latest else None}

    @app.get("/api/runs/{run_id}/artifacts/{kind}", dependencies=[Depends(_require_role("viewer"))])
    def download_run_artifact(run_id: str, kind: str) -> FileResponse:
        """Telecharge le rapport HTML, le CSV nettoye ou le CSV annote d'une execution."""
        details = _storage().get_run_details(run_id)
        if details is None:
            raise HTTPException(status_code=404, detail=f"Run introuvable : {run_id}")
        meta = details.get("run", details)
        full_id = str(meta.get("run_id", run_id))
        prefix = full_id[:8]
        dom = str(meta.get("domain", ""))

        if kind == "report":
            candidates = sorted(Path(reports_dir).glob(f"*{prefix}*.html"))
            media_type = "text/html"
        elif kind == "clean":
            candidates = sorted(Path(processed_dir).glob(f"*{prefix}*_clean.csv"))
            media_type = "text/csv"
        elif kind == "annotated":
            candidates = sorted(Path(processed_dir).glob(f"*{prefix}*_annotated.csv"))
            media_type = "text/csv"
        else:
            raise HTTPException(
                status_code=400, detail="Type d'artefact invalide (report, clean, annotated)."
            )

        if not candidates:
            raise HTTPException(
                status_code=404,
                detail=f"Artefact '{kind}' introuvable pour {dom}_{prefix}.",
            )
        return FileResponse(
            path=str(candidates[0]), filename=candidates[0].name, media_type=media_type
        )

    @app.post("/api/quarantine/resolve", dependencies=[Depends(_require_role("operator"))])
    def resolve_quarantine_row(payload: dict[str, Any]) -> dict[str, Any]:
        """Met a jour le statut d'une ligne en quarantaine (APPROVED / REJECTED)."""
        row_id = int(payload.get("id", 0))
        new_status = str(payload.get("status", "")).strip().upper()
        if new_status not in {"APPROVED", "REJECTED", "PENDING"}:
            raise HTTPException(
                status_code=400, detail="Statut invalide (APPROVED, REJECTED ou PENDING)."
            )
        import sqlite3
        with sqlite3.connect(db_path) as conn:
            cur = conn.execute(
                "UPDATE quarantine SET status = ? WHERE id = ?", (new_status, row_id)
            )
            conn.commit()
            if cur.rowcount == 0:
                raise HTTPException(
                    status_code=404, detail=f"Ligne quarantaine #{row_id} introuvable."
                )
        return {"id": row_id, "status": new_status}

    return app



app = create_app()
