"""Tests pour DataPulse V7 : connecteurs SQL, sécurité anti-injection et API SQL."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from datapulse.api import create_app
from datapulse.cli import main, run_sql_pipeline
from datapulse.exceptions import ExitCode
from datapulse.sql_connector import (
    SqlConnectorError,
    build_select_sql,
    iter_sql_chunks,
    list_sql_tables,
)


def _create_sample_sqlite(db_file: Path) -> str:
    df = pd.read_csv("data/raw/ventes.csv")
    with sqlite3.connect(db_file) as conn:
        df.to_sql("ventes_sql", conn, index=False, if_exists="replace")
    return f"sqlite:///{db_file.as_posix()}"


def test_sql_read_only_guard_blocks_destructive_statements() -> None:
    with pytest.raises(SqlConnectorError):
        build_select_sql(table="ventes; DROP TABLE users")

    with pytest.raises(SqlConnectorError):
        build_select_sql(query="DELETE FROM ventes WHERE 1=1")

    with pytest.raises(SqlConnectorError):
        build_select_sql(query="SELECT * FROM ventes; DROP TABLE ventes")

    with pytest.raises(SqlConnectorError):
        build_select_sql(table="ventes", query="SELECT * FROM ventes")

    sql, label = build_select_sql(query="WITH cte AS (SELECT 1 AS x) SELECT * FROM cte")
    assert sql.startswith("WITH cte")
    assert label.startswith("sql:query:")


def test_sql_pipeline_batch_and_streaming_sqlite(tmp_path: Path) -> None:
    src_db = tmp_path / "source_commerce.sqlite"
    uri = _create_sample_sqlite(src_db)

    tables = list_sql_tables(uri)
    assert tables == ["ventes_sql"]

    # Mode batch avec --domain auto et quarantaine
    code_batch = run_sql_pipeline(
        uri=uri,
        table="ventes_sql",
        domain="auto",
        db_path=str(tmp_path / "dp_batch.sqlite"),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
        quarantine=True,
    )
    assert code_batch == int(ExitCode.SUCCESS)

    # Mode streaming par chunks sur une requête SELECT
    code_stream = run_sql_pipeline(
        uri=uri,
        query="SELECT date, produit, quantite, prix_unitaire FROM ventes_sql",
        domain="commerce",
        db_path=str(tmp_path / "dp_stream.sqlite"),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
        chunk_size=50,
    )
    assert code_stream == int(ExitCode.SUCCESS)


def test_cli_run_sql_command_and_strict_exit_code(tmp_path: Path) -> None:
    src_db = tmp_path / "cli_source.sqlite"
    uri = _create_sample_sqlite(src_db)

    code = main(["run-sql", "--uri", uri, "--table", "ventes_sql", "--domain", "auto"])
    assert code == int(ExitCode.SUCCESS)

    # Base inexistante -> code retour 2 (EXECUTION_ERROR)
    bad_code = main(["run-sql", "--uri", "sqlite:///inexistant_xyz.sqlite", "--table", "t"])
    assert bad_code == int(ExitCode.EXECUTION_ERROR)


def test_iter_sql_chunks_rejects_invalid_chunk_size(tmp_path: Path) -> None:
    src_db = tmp_path / "chunk_err.sqlite"
    uri = _create_sample_sqlite(src_db)
    with pytest.raises(SqlConnectorError):
        iter_sql_chunks(uri, table="ventes_sql", chunk_size=0)


def test_api_sql_tables_and_run_sql_endpoints(tmp_path: Path) -> None:
    src_db = tmp_path / "api_source.sqlite"
    uri = _create_sample_sqlite(src_db)

    app = create_app(
        db_path=str(tmp_path / "v7_api.sqlite"),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
    )
    client = TestClient(app)

    tables_resp = client.get("/api/sql/tables", params={"uri": uri})
    assert tables_resp.status_code == 200
    assert tables_resp.json()["tables"] == ["ventes_sql"]

    run_resp = client.post(
        "/api/runs/sql",
        json={"uri": uri, "table": "ventes_sql", "domain": "auto", "quarantine": True},
    )
    assert run_resp.status_code == 200
    body = run_resp.json()
    assert body["run"]["domain"] == "commerce"
    assert body["run"]["row_count"] == 200

    # Requête destructive rejetée en 400
    bad_resp = client.post(
        "/api/runs/sql",
        json={"uri": uri, "query": "DROP TABLE ventes_sql"},
    )
    assert bad_resp.status_code == 400
