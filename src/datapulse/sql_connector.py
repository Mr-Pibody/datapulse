"""Connecteur SQL lecture seule (SQLite natif & SQLAlchemy) pour DataPulse V7."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pandas as pd

from datapulse.exceptions import DataPulseError

_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_FORBIDDEN_SQL_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|REPLACE|ATTACH|DETACH|PRAGMA|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


class SqlConnectorError(DataPulseError):
    """Erreur levée lors de la connexion, validation ou lecture d'une source SQL."""


def build_select_sql(table: str | None = None, query: str | None = None) -> tuple[str, str]:
    """Valide la cible SQL (table ou requête SELECT) et retourne (requête_sql, libellé_source)."""
    if bool(table) == bool(query):
        raise SqlConnectorError(
            "Spécifiez exactement un paramètre parmi '--table' ou '--query'.",
            context={"table": table, "query": query},
        )

    if table is not None:
        clean_table = table.strip()
        if not _TABLE_NAME_RE.match(clean_table):
            raise SqlConnectorError(
                f"Nom de table SQL invalide ou non sécurisé : '{table}'",
                context={"table": table},
            )
        return f"SELECT * FROM {clean_table}", f"sql:table:{clean_table}"

    assert query is not None
    clean_query = query.strip().rstrip(";").strip()
    if not clean_query:
        raise SqlConnectorError("La requête SQL ne peut pas être vide.")

    upper_start = clean_query.lstrip("(").upper()
    if not (upper_start.startswith("SELECT") or upper_start.startswith("WITH")):
        raise SqlConnectorError(
            "Seules les requêtes en lecture seule (SELECT / WITH) sont autorisées.",
            context={"query": clean_query[:120]},
        )

    if ";" in clean_query or _FORBIDDEN_SQL_RE.search(clean_query):
        raise SqlConnectorError(
            "Instruction SQL interdite détectée (écriture, DDL ou multi-requêtes).",
            context={"query": clean_query[:120]},
        )

    digest = hashlib.sha256(clean_query.encode("utf-8")).hexdigest()[:8]
    return clean_query, f"sql:query:{digest}"


def compute_sql_fingerprint(uri: str, sql: str, row_count: int, columns: list[str]) -> str:
    """Calcule une empreinte SHA-256 déterministe pour un résultat d'extraction SQL."""
    safe_uri = uri.split("@")[-1] if "@" in uri else uri
    payload = f"{safe_uri}|{sql}|{row_count}|{','.join(columns)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _resolve_sqlite_path(uri: str) -> str | None:
    """Retourne le chemin fichier/mémoire si l'URI cible SQLite, sinon None."""
    stripped = uri.strip()
    if stripped.startswith("sqlite:///"):
        return stripped[len("sqlite:///") :]
    if stripped.startswith("sqlite://"):
        rest = stripped[len("sqlite://") :]
        return rest if rest else ":memory:"
    if stripped == ":memory:" or stripped.endswith((".sqlite", ".sqlite3", ".db")):
        return stripped
    return None


def list_sql_tables(uri: str) -> list[str]:
    """Liste les tables utilisateur disponibles dans la base SQL cible."""
    sqlite_target = _resolve_sqlite_path(uri)
    if sqlite_target is not None:
        if sqlite_target != ":memory:" and not Path(sqlite_target).exists():
            raise SqlConnectorError(
                f"Base SQLite introuvable : '{sqlite_target}'",
                context={"uri": uri},
            )
        try:
            with sqlite3.connect(sqlite_target) as conn:
                cursor = conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
                return [str(row[0]) for row in cursor.fetchall()]
        except sqlite3.Error as exc:
            raise SqlConnectorError(f"Erreur SQLite : {exc}", context={"uri": uri}) from exc

    try:
        import sqlalchemy as sa
    except ImportError as exc:
        raise SqlConnectorError(
            "Le pilote SQLAlchemy est requis pour les bases externes (PostgreSQL/MySQL). "
            "Installez : pip install -e '.[sql]'",
            context={"uri": uri},
        ) from exc

    try:
        engine = sa.create_engine(uri)
        try:
            inspector = sa.inspect(engine)
            return sorted(str(t) for t in inspector.get_table_names())
        finally:
            engine.dispose()
    except Exception as exc:
        raise SqlConnectorError(
            f"Impossible d'inspecter la base SQL : {exc}",
            context={"uri": uri},
        ) from exc


def extract_sql_to_dataframe(
    uri: str,
    *,
    table: str | None = None,
    query: str | None = None,
) -> tuple[pd.DataFrame, str, str]:
    """Extrait une table ou requête SQL en mémoire et retourne (df, source_label, sha256)."""
    sql, source_label = build_select_sql(table=table, query=query)
    sqlite_target = _resolve_sqlite_path(uri)

    if sqlite_target is not None:
        if sqlite_target != ":memory:" and not Path(sqlite_target).exists():
            raise SqlConnectorError(
                f"Base SQLite introuvable : '{sqlite_target}'",
                context={"uri": uri},
            )
        try:
            with sqlite3.connect(sqlite_target) as conn:
                df = pd.read_sql_query(sql, conn)
        except (sqlite3.Error, pd.errors.DatabaseError) as exc:
            raise SqlConnectorError(
                f"Échec de lecture SQLite : {exc}",
                context={"source": source_label},
            ) from exc
    else:
        try:
            import sqlalchemy as sa
        except ImportError as exc:
            raise SqlConnectorError(
                "SQLAlchemy est requis pour cette URI. Installez : pip install -e '.[sql]'",
                context={"uri": uri},
            ) from exc

        try:
            engine = sa.create_engine(uri)
            try:
                with engine.connect() as conn:
                    df = pd.read_sql_query(sa.text(sql), conn)
            finally:
                engine.dispose()
        except Exception as exc:
            raise SqlConnectorError(
                f"Échec d'exécution SQL : {exc}",
                context={"source": source_label},
            ) from exc

    fingerprint = compute_sql_fingerprint(uri, sql, len(df), [str(c) for c in df.columns])
    return df, source_label, fingerprint


def iter_sql_chunks(
    uri: str,
    *,
    table: str | None = None,
    query: str | None = None,
    chunk_size: int = 50_000,
) -> tuple[str, Iterator[tuple[int, pd.DataFrame]]]:
    """Itère en streaming par blocs sur une source SQL."""
    if chunk_size <= 0:
        raise SqlConnectorError(
            f"chunk_size doit être strictement positif (reçu : {chunk_size}).",
            context={"chunk_size": chunk_size},
        )

    sql, source_label = build_select_sql(table=table, query=query)
    sqlite_target = _resolve_sqlite_path(uri)

    if sqlite_target is not None:
        if sqlite_target != ":memory:" and not Path(sqlite_target).exists():
            raise SqlConnectorError(
                f"Base SQLite introuvable : '{sqlite_target}'",
                context={"uri": uri},
            )

        def _sqlite_gen() -> Iterator[tuple[int, pd.DataFrame]]:
            offset = 0
            try:
                with sqlite3.connect(sqlite_target) as conn:
                    chunks: Iterator[pd.DataFrame] = pd.read_sql_query(
                        sql, conn, chunksize=chunk_size
                    )
                    for chunk in chunks:
                        yield offset, chunk
                        offset += len(chunk)
            except (sqlite3.Error, pd.errors.DatabaseError) as exc:
                raise SqlConnectorError(
                    f"Échec du streaming SQLite : {exc}",
                    context={"source": source_label},
                ) from exc

        return source_label, _sqlite_gen()

    try:
        import sqlalchemy as sa
    except ImportError as exc:
        raise SqlConnectorError(
            "SQLAlchemy est requis pour le streaming SQL externe. "
            "Installez : pip install -e '.[sql]'",
            context={"uri": uri},
        ) from exc

    def _sa_gen() -> Iterator[tuple[int, pd.DataFrame]]:
        offset = 0
        try:
            engine = sa.create_engine(uri)
            try:
                with engine.connect() as conn:
                    chunks: Iterator[pd.DataFrame] = pd.read_sql_query(
                        sa.text(sql), conn, chunksize=chunk_size
                    )
                    for chunk in chunks:
                        yield offset, chunk
                        offset += len(chunk)
            finally:
                engine.dispose()
        except Exception as exc:
            raise SqlConnectorError(
                f"Échec du streaming SQL : {exc}",
                context={"source": source_label},
            ) from exc

    return source_label, _sa_gen()
