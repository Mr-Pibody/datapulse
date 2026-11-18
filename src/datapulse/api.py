"""API REST FastAPI et endpoints d'observabilité DataPulse V9."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from datapulse.alerts import list_alert_history, send_test_alert
from datapulse.cli import run_pipeline, run_sql_pipeline
from datapulse.config_loader import ConfigError
from datapulse.dashboard import render_dashboard_html
from datapulse.drift import detect_data_drift, load_run_column_profiles
from datapulse.exceptions import DataPulseError, StorageTransactionError
from datapulse.schema_inference import list_available_domains
from datapulse.sql_connector import list_sql_tables
from datapulse.storage import DataPulseStorage


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


def create_app(
    db_path: str = "db/datapulse.sqlite",
    reports_dir: str = "reports",
    processed_dir: str = "data/processed",
    configs_dir: str = "configs",
) -> FastAPI:
    """Construit l'instance FastAPI configurée pour une base SQLite donnée."""
    app = FastAPI(
        title="DataPulse API",
        version="9.0.0",
        description="API REST de gouvernance, qualité, Data Drift et alertes temps réel.",
    )

    def _storage() -> DataPulseStorage:
        return DataPulseStorage(db_path=db_path)

    @app.get("/", response_class=HTMLResponse)
    def dashboard_home() -> HTMLResponse:
        return HTMLResponse(content=render_dashboard_html())

    @app.get("/api/health")
    def healthcheck() -> dict[str, Any]:
        storage = _storage()
        return {
            "status": "ok",
            "version": "9.0.0",
            "db_path": str(storage.db_path),
        }

    @app.get("/api/domains")
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

    @app.get("/api/sql/tables")
    def inspect_sql_tables(uri: str = Query(..., min_length=3)) -> dict[str, Any]:
        try:
            tables = list_sql_tables(uri)
        except DataPulseError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"count": len(tables), "tables": tables}

    @app.get("/api/runs")
    def list_runs(
        domain: str | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=200),
    ) -> dict[str, Any]:
        runs = _storage().get_history(domain=domain, limit=limit)
        return {"count": len(runs), "runs": runs}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        details = _storage().get_run_details(run_id)
        if details is None:
            raise HTTPException(status_code=404, detail=f"Run introuvable : {run_id}")
        return details

    @app.get("/api/diff")
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

    @app.get("/api/quarantine")
    def get_quarantine(
        domain: str | None = Query(default=None),
        status: str = Query(default="PENDING"),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        items = _storage().list_quarantine(domain=domain, status=status, limit=limit)
        return {"count": len(items), "items": items}

    @app.get("/api/alerts")
    def get_alerts(
        domain: str | None = Query(default=None),
        limit: int = Query(default=30, ge=1, le=200),
    ) -> dict[str, Any]:
        items = list_alert_history(db_path=db_path, domain=domain, limit=limit)
        return {"count": len(items), "alerts": items}

    @app.post("/api/alerts/test")
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

    @app.post("/api/runs/upload")
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

    @app.post("/api/runs/sql")
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
