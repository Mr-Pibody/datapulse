from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from datapulse.api import create_app
from datapulse.catalog import (
    delete_glossary_term,
    get_asset,
    get_catalog_summary,
    list_assets,
    register_asset,
    search_catalog,
    sync_catalog_from_configs,
    upsert_glossary_term,
)
from datapulse.cli import main as cli_main
from datapulse.security import create_api_key


def test_catalog_sync_and_asset_registration(tmp_path: Path) -> None:
    db_file = tmp_path / "catalog_sync.sqlite"
    sync_res = sync_catalog_from_configs(db_file, config_dir="config")
    assert sync_res["synced_count"] >= 3
    assert "commerce" in sync_res["domains"]

    custom = register_asset(
        db_file,
        domain="finance",
        display_name="Transactions Financieres Core",
        owner="finops@datapulse.local",
        classification="RESTRICTED",
        description="Flux transactionnel critique",
        tags=["pci-dss", "finance"],
        sla_max_age_hours=12.0,
        schema_columns=[
            {"name": "tx_id", "dtype": "string", "pii": False},
            {"name": "iban", "dtype": "string", "pii": True},
        ],
    )
    assert custom["classification"] == "RESTRICTED"
    assert custom["pii_column_count"] == 1
    fetched = get_asset(db_file, "finance")
    assert fetched is not None
    assert fetched["owner"] == "finops@datapulse.local"


def test_catalog_sla_freshness_healthy_and_breached(tmp_path: Path) -> None:
    db_file = tmp_path / "catalog_sla.sqlite"
    register_asset(db_file, domain="commerce", sla_max_age_hours=6.0)
    register_asset(db_file, domain="finance", sla_max_age_hours=6.0)

    with sqlite3.connect(str(db_file)) as conn:
        conn.execute(
            "CREATE TABLE runs "
            "(run_id TEXT PRIMARY KEY, domain TEXT, created_at TEXT, quality_score REAL)"
        )
        fresh_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        stale_ts = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
        conn.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?)",
            ("r-fresh", "commerce", fresh_ts, 96.5),
        )
        conn.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?)",
            ("r-stale", "finance", stale_ts, 88.0),
        )
        conn.commit()

    commerce = get_asset(db_file, "commerce")
    finance = get_asset(db_file, "finance")
    assert commerce is not None and commerce["sla_status"] == "HEALTHY"
    assert finance is not None and finance["sla_status"] == "BREACHED"

    summary = get_catalog_summary(db_file)
    assert summary["healthy_assets"] == 1
    assert summary["breached_assets"] == 1
    assert summary["sla_compliance_pct"] == 50.0


def test_catalog_glossary_and_search(tmp_path: Path) -> None:
    db_file = tmp_path / "catalog_glossary.sqlite"
    register_asset(
        db_file,
        domain="commerce",
        description="Ventes e-commerce omnicanal",
        tags=["revenu"],
    )
    term = upsert_glossary_term(
        db_file,
        term="Panier Moyen",
        definition="Chiffre d'affaires total divise par le nombre de commandes validees",
        domain="commerce",
        formula="SUM(montant) / COUNT(DISTINCT commande_id)",
        synonyms=["AOV", "Average Order Value"],
        related_columns=["montant", "commande_id"],
    )
    assert term["term"] == "Panier Moyen"

    results = search_catalog(db_file, "Panier")
    assert results["glossary_count"] == 1
    assert delete_glossary_term(db_file, "Panier Moyen") is True


def test_cli_catalog_commands(tmp_path: Path) -> None:
    db_file = tmp_path / "cli_catalog.sqlite"
    rc_sync = cli_main(["catalog", "sync", "--config-dir", "config", "--db", str(db_file)])
    assert rc_sync == 0

    rc_reg = cli_main(
        [
            "catalog",
            "register",
            "--domain",
            "rh_paie",
            "--owner",
            "rh@datapulse.local",
            "--classification",
            "CONFIDENTIAL",
            "--sla-hours",
            "48",
            "--db",
            str(db_file),
        ]
    )
    assert rc_reg == 0
    assert any(a["domain"] == "rh_paie" for a in list_assets(db_file))


def test_api_catalog_endpoints_rbac_and_v17_dashboard(tmp_path: Path) -> None:
    db_file = tmp_path / "api_catalog.sqlite"
    view_key = str(create_api_key(db_file, name="cat-viewer", role="viewer")["api_key"])
    op_key = str(create_api_key(db_file, name="cat-op", role="operator")["api_key"])

    app = create_app(
        db_path=str(db_file),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
    )
    client = TestClient(app)

    dash = client.get("/")
    assert dash.status_code == 200
    assert "DataPulse" in dash.text and "v20.0.0" in dash.text
    assert "Data Catalog &amp; SLA" in dash.text

    # Un viewer ne peut pas synchroniser ni modifier le catalogue
    deny = client.post("/api/catalog/sync", headers={"X-API-Key": view_key}, json={})
    assert deny.status_code == 403

    # Un operator peut synchroniser et enrichir le glossaire
    sync_resp = client.post(
        "/api/catalog/sync",
        headers={"X-API-Key": op_key},
        json={"config_dir": "config"},
    )
    assert sync_resp.status_code == 200
    assert sync_resp.json()["synced_count"] >= 3

    glo_resp = client.post(
        "/api/catalog/glossary",
        headers={"X-API-Key": op_key},
        json={
            "term": "Taux de Conversion",
            "definition": "Pourcentage de visiteurs finalisant un achat",
            "domain": "commerce",
        },
    )
    assert glo_resp.status_code == 200

    # Le viewer peut consulter le resume et la recherche
    sum_resp = client.get("/api/catalog/summary", headers={"X-API-Key": view_key})
    assert sum_resp.status_code == 200
    assert sum_resp.json()["total_assets"] >= 3
