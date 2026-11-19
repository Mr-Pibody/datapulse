from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

VALID_CLASSIFICATIONS: tuple[str, ...] = (
    "PUBLIC",
    "INTERNAL",
    "CONFIDENTIAL",
    "RESTRICTED",
)

PII_KEYWORDS: tuple[str, ...] = (
    "email",
    "mail",
    "phone",
    "tel",
    "iban",
    "ssn",
    "nir",
    "carte",
    "card",
    "nom",
    "prenom",
    "name",
    "adresse",
    "address",
    "ip",
    "passport",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_catalog_tables(db_path: str | Path) -> None:
    """Initialise les tables SQLite du Data Catalog et du Glossaire Metier."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS catalog_assets (
                domain TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                owner TEXT NOT NULL DEFAULT 'data-engineering@datapulse.local',
                steward TEXT NOT NULL DEFAULT 'governance@datapulse.local',
                classification TEXT NOT NULL DEFAULT 'INTERNAL',
                description TEXT NOT NULL DEFAULT '',
                tags_json TEXT NOT NULL DEFAULT '[]',
                sla_max_age_hours REAL NOT NULL DEFAULT 24.0,
                schema_columns_json TEXT NOT NULL DEFAULT '[]',
                source_uri TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS catalog_glossary (
                term TEXT PRIMARY KEY,
                definition TEXT NOT NULL,
                domain TEXT NOT NULL DEFAULT 'global',
                owner TEXT NOT NULL DEFAULT 'business-steward@datapulse.local',
                formula TEXT NOT NULL DEFAULT '',
                synonyms_json TEXT NOT NULL DEFAULT '[]',
                related_columns_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def _compute_sla_metrics(
    conn: sqlite3.Connection,
    domain: str,
    sla_max_age_hours: float,
) -> dict[str, Any]:
    """Calcule la fraicheur et le statut SLA d'un domaine depuis la table runs."""
    table_check = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='runs'"
    ).fetchone()
    if table_check is None:
        return {
            "sla_status": "NO_DATA",
            "last_run_at": None,
            "age_hours": None,
            "total_runs": 0,
            "avg_quality_score": None,
        }

    cols_info = conn.execute("PRAGMA table_info(runs)").fetchall()
    col_names = {str(r["name"]) for r in cols_info}
    ts_col = next(
        (c for c in ("created_at", "started_at", "timestamp") if c in col_names),
        None,
    )
    if ts_col is None or "domain" not in col_names:
        return {
            "sla_status": "NO_DATA",
            "last_run_at": None,
            "age_hours": None,
            "total_runs": 0,
            "avg_quality_score": None,
        }

    score_expr = (
        "AVG(quality_score) AS avg_score" if "quality_score" in col_names else "NULL AS avg_score"
    )
    row = conn.execute(
        f"SELECT MAX({ts_col}) AS last_ts, COUNT(*) AS cnt, {score_expr} "
        "FROM runs WHERE domain = ?",
        (domain,),
    ).fetchone()

    total_runs = int(row["cnt"]) if row and row["cnt"] is not None else 0
    last_ts_raw = str(row["last_ts"]) if row and row["last_ts"] else None
    avg_score = round(float(row["avg_score"]), 2) if row and row["avg_score"] is not None else None

    if total_runs == 0 or not last_ts_raw:
        return {
            "sla_status": "NO_DATA",
            "last_run_at": None,
            "age_hours": None,
            "total_runs": 0,
            "avg_quality_score": avg_score,
        }

    try:
        parsed_ts = datetime.fromisoformat(last_ts_raw.replace("Z", "+00:00"))
        if parsed_ts.tzinfo is None:
            parsed_ts = parsed_ts.replace(tzinfo=timezone.utc)
        age_hours = max(0.0, round((_utc_now() - parsed_ts).total_seconds() / 3600.0, 2))
    except ValueError:
        age_hours = 0.0

    if age_hours > sla_max_age_hours:
        sla_status = "BREACHED"
    elif age_hours >= 0.8 * sla_max_age_hours:
        sla_status = "WARNING"
    else:
        sla_status = "HEALTHY"

    return {
        "sla_status": sla_status,
        "last_run_at": last_ts_raw,
        "age_hours": age_hours,
        "total_runs": total_runs,
        "avg_quality_score": avg_score,
    }


def _row_to_asset(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    domain = str(row["domain"])
    sla_max = float(row["sla_max_age_hours"])
    raw_tags = json.loads(str(row["tags_json"]))
    tags = [str(t) for t in raw_tags] if isinstance(raw_tags, list) else []
    raw_cols = json.loads(str(row["schema_columns_json"]))
    columns = list(raw_cols) if isinstance(raw_cols, list) else []
    sla_info = _compute_sla_metrics(conn, domain, sla_max)
    return {
        "domain": domain,
        "display_name": str(row["display_name"]),
        "owner": str(row["owner"]),
        "steward": str(row["steward"]),
        "classification": str(row["classification"]),
        "description": str(row["description"]),
        "tags": tags,
        "sla_max_age_hours": sla_max,
        "schema_columns": columns,
        "column_count": len(columns),
        "pii_column_count": sum(1 for c in columns if isinstance(c, dict) and c.get("pii")),
        "source_uri": str(row["source_uri"]),
        "updated_at": str(row["updated_at"]),
        **sla_info,
    }


def register_asset(
    db_path: str | Path,
    *,
    domain: str,
    display_name: str | None = None,
    owner: str = "data-engineering@datapulse.local",
    steward: str = "governance@datapulse.local",
    classification: str = "INTERNAL",
    description: str = "",
    tags: list[str] | None = None,
    sla_max_age_hours: float = 24.0,
    schema_columns: list[dict[str, Any]] | None = None,
    source_uri: str = "",
) -> dict[str, Any]:
    """Enregistre ou met a jour un actif de donnees dans le Data Catalog."""
    clean_domain = domain.strip()
    if not clean_domain:
        raise ValueError("Le nom du domaine ne peut pas etre vide.")
    norm_class = classification.strip().upper()
    if norm_class not in VALID_CLASSIFICATIONS:
        raise ValueError(
            f"Classification invalide '{classification}'. "
            f"Valeurs autorisees: {', '.join(VALID_CLASSIFICATIONS)}"
        )
    if sla_max_age_hours <= 0:
        raise ValueError("Le seuil SLA (sla_max_age_hours) doit etre strictement positif.")

    init_catalog_tables(db_path)
    title = (display_name or clean_domain.replace("_", " ").title()).strip()
    tags_payload = json.dumps(tags or [], ensure_ascii=False)
    cols_payload = json.dumps(schema_columns or [], ensure_ascii=False)
    now_iso = _utc_now_iso()

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO catalog_assets (
                domain, display_name, owner, steward, classification,
                description, tags_json, sla_max_age_hours,
                schema_columns_json, source_uri, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET
                display_name = excluded.display_name,
                owner = excluded.owner,
                steward = excluded.steward,
                classification = excluded.classification,
                description = excluded.description,
                tags_json = excluded.tags_json,
                sla_max_age_hours = excluded.sla_max_age_hours,
                schema_columns_json = excluded.schema_columns_json,
                source_uri = excluded.source_uri,
                updated_at = excluded.updated_at
            """,
            (
                clean_domain,
                title,
                owner.strip(),
                steward.strip(),
                norm_class,
                description.strip(),
                tags_payload,
                float(sla_max_age_hours),
                cols_payload,
                source_uri.strip(),
                now_iso,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM catalog_assets WHERE domain = ?",
            (clean_domain,),
        ).fetchone()
        assert row is not None
        return _row_to_asset(conn, row)


def get_asset(db_path: str | Path, domain: str) -> dict[str, Any] | None:
    """Recupere la fiche complete d'un actif du catalogue."""
    init_catalog_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM catalog_assets WHERE domain = ?",
            (domain.strip(),),
        ).fetchone()
        if row is None:
            return None
        return _row_to_asset(conn, row)


def list_assets(
    db_path: str | Path,
    *,
    classification: str | None = None,
    sla_status: str | None = None,
    query: str | None = None,
) -> list[dict[str, Any]]:
    """Liste les actifs du catalogue avec filtres optionnels."""
    init_catalog_tables(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM catalog_assets ORDER BY domain ASC").fetchall()
        assets = [_row_to_asset(conn, r) for r in rows]

    if classification:
        target_cls = classification.strip().upper()
        assets = [a for a in assets if a["classification"] == target_cls]
    if sla_status:
        target_sla = sla_status.strip().upper()
        assets = [a for a in assets if a["sla_status"] == target_sla]
    if query and query.strip():
        q = query.strip().lower()
        filtered: list[dict[str, Any]] = []
        for asset in assets:
            col_names = " ".join(
                str(c.get("name", "")) for c in asset["schema_columns"] if isinstance(c, dict)
            )
            haystack = (
                f"{asset['domain']} {asset['display_name']} {asset['owner']} "
                f"{asset['description']} {' '.join(asset['tags'])} {col_names}"
            ).lower()
            if q in haystack:
                filtered.append(asset)
        assets = filtered
    return assets


def _is_pii_column(col_name: str) -> bool:
    lowered = col_name.lower()
    return any(kw in lowered for kw in PII_KEYWORDS)


def sync_catalog_from_configs(
    db_path: str | Path,
    config_dir: str | Path = "config",
) -> dict[str, Any]:
    """Synchronise les contrats YAML de domaines vers le Data Catalog."""
    init_catalog_tables(db_path)
    cfg_root = Path(config_dir)
    repo_root = Path(__file__).resolve().parents[2]

    # Resoudre automatiquement le dossier de configs si 'config' n'existe pas a la racine
    search_roots: list[Path] = []
    if cfg_root.exists():
        search_roots.append(cfg_root)
    elif str(config_dir) in {"config", "configs"}:
        for candidate in (
            Path("configs"),
            repo_root / "config",
            repo_root / "configs",
            repo_root / "src" / "datapulse" / "config",
        ):
            if candidate.exists():
                search_roots.append(candidate)
                break

    ignored = {"alerts", "schedules", "watch", "settings", "config", "docker-compose"}
    yaml_files: list[Path] = []
    for root in search_roots:
        yaml_files.extend(
            p for p in root.rglob("*.y*ml") if p.is_file() and p.stem.lower() not in ignored
        )

    domain_specs: dict[str, dict[str, Any]] = {}
    for yaml_file in sorted(set(yaml_files)):
        raw = yaml.safe_load(yaml_file.read_text("utf-8"))
        if not isinstance(raw, dict):
            continue
        if isinstance(raw.get("domains"), dict):
            for dom_key, dom_cfg in raw["domains"].items():
                if isinstance(dom_cfg, dict):
                    domain_specs[str(dom_key).strip()] = dom_cfg
            continue
        dom_name = str(
            raw.get("domain") or raw.get("domain_name") or raw.get("name") or yaml_file.stem
        ).strip()
        if dom_name and dom_name.lower() not in ignored:
            domain_specs[dom_name] = raw

    # Fallback via config_loader si aucun fichier YAML n'a ete trouve dans le chemin passe
    if not domain_specs and str(config_dir) in {"config", "configs"}:
        try:
            from datapulse import config_loader as _cl

            loader = getattr(_cl, "load_all_domains", None) or getattr(_cl, "load_domains", None)
            if callable(loader):
                loaded = loader()
                if isinstance(loaded, dict):
                    for k, v in loaded.items():
                        domain_specs[str(k)] = v if isinstance(v, dict) else {}
        except Exception:
            pass

    synced_domains: list[str] = []
    for domain, spec in sorted(domain_specs.items()):
        desc = str(spec.get("description") or f"Contrat de donnees {domain}").strip()
        sla_hours = float(spec.get("sla_max_age_hours") or spec.get("sla_hours") or 24.0)
        owner = str(spec.get("owner") or "data-engineering@datapulse.local").strip()
        steward = str(spec.get("steward") or "governance@datapulse.local").strip()
        classification = str(spec.get("classification") or "INTERNAL").strip().upper()
        register_asset(
            db_path,
            domain=domain,
            display_name=str(spec.get("display_name") or domain.replace("_", " ").title()),
            owner=owner,
            steward=steward,
            classification=classification,
            description=desc,
            sla_max_age_hours=sla_hours,
        )
        synced_domains.append(domain)

    return {"synced_count": len(synced_domains), "domains": synced_domains}


def upsert_glossary_term(
    db_path: str | Path,
    *,
    term: str,
    definition: str,
    domain: str = "global",
    owner: str = "business-steward@datapulse.local",
    formula: str = "",
    synonyms: list[str] | None = None,
    related_columns: list[str] | None = None,
) -> dict[str, Any]:
    """Ajoute ou met a jour un terme dans le Glossaire Metier."""
    clean_term = term.strip()
    if not clean_term:
        raise ValueError("Le terme du glossaire ne peut pas etre vide.")
    if not definition.strip():
        raise ValueError("La definition du terme est obligatoire.")

    init_catalog_tables(db_path)
    now_iso = _utc_now_iso()
    syn_json = json.dumps(synonyms or [], ensure_ascii=False)
    rel_json = json.dumps(related_columns or [], ensure_ascii=False)

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO catalog_glossary (
                term, definition, domain, owner, formula,
                synonyms_json, related_columns_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(term) DO UPDATE SET
                definition = excluded.definition,
                domain = excluded.domain,
                owner = excluded.owner,
                formula = excluded.formula,
                synonyms_json = excluded.synonyms_json,
                related_columns_json = excluded.related_columns_json,
                updated_at = excluded.updated_at
            """,
            (
                clean_term,
                definition.strip(),
                domain.strip() or "global",
                owner.strip(),
                formula.strip(),
                syn_json,
                rel_json,
                now_iso,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM catalog_glossary WHERE term = ?",
            (clean_term,),
        ).fetchone()
        assert row is not None
        return _row_to_glossary(row)


def _row_to_glossary(row: sqlite3.Row) -> dict[str, Any]:
    raw_syn = json.loads(str(row["synonyms_json"]))
    raw_rel = json.loads(str(row["related_columns_json"]))
    return {
        "term": str(row["term"]),
        "definition": str(row["definition"]),
        "domain": str(row["domain"]),
        "owner": str(row["owner"]),
        "formula": str(row["formula"]),
        "synonyms": [str(s) for s in raw_syn] if isinstance(raw_syn, list) else [],
        "related_columns": [str(c) for c in raw_rel] if isinstance(raw_rel, list) else [],
        "updated_at": str(row["updated_at"]),
    }


def list_glossary_terms(
    db_path: str | Path,
    *,
    domain: str | None = None,
    query: str | None = None,
) -> list[dict[str, Any]]:
    """Liste les termes du Glossaire Metier avec filtrage optionnel."""
    init_catalog_tables(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM catalog_glossary ORDER BY term ASC").fetchall()
    terms = [_row_to_glossary(r) for r in rows]
    if domain and domain.strip():
        target_d = domain.strip().lower()
        terms = [t for t in terms if t["domain"].lower() == target_d]
    if query and query.strip():
        q = query.strip().lower()
        terms = [
            t
            for t in terms
            if q
            in (
                f"{t['term']} {t['definition']} {t['domain']} "
                f"{t['formula']} {' '.join(t['synonyms'])} {' '.join(t['related_columns'])}"
            ).lower()
        ]
    return terms


def delete_glossary_term(db_path: str | Path, term: str) -> bool:
    """Supprime un terme du Glossaire Metier."""
    init_catalog_tables(db_path)
    with _connect(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM catalog_glossary WHERE term = ?",
            (term.strip(),),
        )
        conn.commit()
        return cur.rowcount > 0


def search_catalog(db_path: str | Path, query: str) -> dict[str, Any]:
    """Recherche federee sur les actifs, colonnes et termes du glossaire."""
    assets = list_assets(db_path, query=query)
    terms = list_glossary_terms(db_path, query=query)
    return {
        "query": query,
        "assets_count": len(assets),
        "glossary_count": len(terms),
        "assets": assets,
        "glossary": terms,
    }


def get_catalog_summary(db_path: str | Path) -> dict[str, Any]:
    """Calcule les indicateurs globaux de gouvernance et de fraicheur SLA."""
    assets = list_assets(db_path)
    terms = list_glossary_terms(db_path)
    total = len(assets)
    healthy = sum(1 for a in assets if a["sla_status"] == "HEALTHY")
    warning = sum(1 for a in assets if a["sla_status"] == "WARNING")
    breached = sum(1 for a in assets if a["sla_status"] == "BREACHED")
    no_data = sum(1 for a in assets if a["sla_status"] == "NO_DATA")
    documented = sum(1 for a in assets if bool(a["description"]) and bool(a["owner"]))
    pii_assets = sum(1 for a in assets if int(a["pii_column_count"]) > 0)

    monitored = total - no_data
    sla_compliance_pct = (
        round(((healthy + warning) / monitored) * 100.0, 1) if monitored > 0 else 100.0
    )
    doc_coverage_pct = round((documented / total) * 100.0, 1) if total > 0 else 0.0
    glossary_bonus = min(15.0, len(terms) * 3.0)
    governance_score = (
        round(min(100.0, 0.5 * sla_compliance_pct + 0.35 * doc_coverage_pct + glossary_bonus), 1)
        if total > 0
        else 0.0
    )

    return {
        "total_assets": total,
        "healthy_assets": healthy,
        "warning_assets": warning,
        "breached_assets": breached,
        "no_data_assets": no_data,
        "documented_assets": documented,
        "pii_assets": pii_assets,
        "glossary_terms_count": len(terms),
        "sla_compliance_pct": sla_compliance_pct,
        "doc_coverage_pct": doc_coverage_pct,
        "governance_score": governance_score,
    }
