"""API REST FastAPI et endpoints d'observabilité DataPulse V5."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse

from datapulse.cli import run_pipeline
from datapulse.config_loader import ConfigError
from datapulse.dashboard import render_dashboard_html
from datapulse.exceptions import DataPulseError, StorageTransactionError
from datapulse.storage import DataPulseStorage


def create_app(
    db_path: str = "db/datapulse.sqlite",
    reports_dir: str = "reports",
    processed_dir: str = "data/processed",
) -> FastAPI:
    """Construit l'instance FastAPI configurée pour une base SQLite donnée."""
    app = FastAPI(
        title="DataPulse API",
        version="5.0.0",
        description="API REST de gouvernance, qualité et observabilité des données.",
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
            "version": "5.0.0",
            "db_path": str(storage.db_path),
        }

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
            return storage.compare_runs(run_a, run_b)
        except StorageTransactionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/quarantine")
    def get_quarantine(
        domain: str | None = Query(default=None),
        status: str = Query(default="PENDING"),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        items = _storage().list_quarantine(domain=domain, status=status, limit=limit)
        return {"count": len(items), "items": items}

    @app.post("/api/runs/upload")
    async def upload_and_run(
        file: UploadFile = File(...),  # noqa: B008
        domain: str = Form(...),
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
                    strict=strict,
                    quarantine=quarantine,
                )
            except (DataPulseError, ConfigError, FileNotFoundError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        latest = _storage().get_history(domain=domain, limit=1)
        return {
            "exit_code": exit_code,
            "run": latest[0] if latest else None,
        }

    return app


app = create_app()
