"""Orchestrateur autonome (Hot-Folder Watcher & Planificateur) — DataPulse V10."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

SUPPORTED_WATCH_EXTENSIONS = {".csv", ".xlsx", ".xls", ".json", ".jsonl", ".parquet"}


def _compute_file_fingerprint(path: Path) -> tuple[int, int, str]:
    """Retourne (mtime_ns, size_bytes, sha256_hex) d'un fichier."""
    stat = path.stat()
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return int(stat.st_mtime_ns), int(stat.st_size), hasher.hexdigest()


def _ensure_watcher_tables(db_path: str | Path) -> None:
    """Initialise les tables SQLite de suivi du watcher et du planificateur."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS watcher_files ("
            "file_path TEXT PRIMARY KEY, "
            "file_hash TEXT NOT NULL, "
            "mtime_ns INTEGER NOT NULL, "
            "size_bytes INTEGER NOT NULL, "
            "domain TEXT NOT NULL, "
            "exit_code INTEGER NOT NULL, "
            "processed_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS scheduler_jobs ("
            "job_name TEXT PRIMARY KEY, "
            "job_type TEXT NOT NULL, "
            "target TEXT NOT NULL, "
            "domain TEXT NOT NULL, "
            "interval_sec REAL NOT NULL, "
            "last_run_epoch REAL NOT NULL, "
            "last_run_at TEXT NOT NULL, "
            "last_exit_code INTEGER NOT NULL, "
            "run_count INTEGER NOT NULL)"
        )


def _is_already_processed(
    db_path: str | Path,
    resolved_file: str,
    mtime_ns: int,
    size_bytes: int,
    file_hash: str,
) -> bool:
    """Vérifie si cette version exacte du fichier a déjà été traitée."""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT file_hash, mtime_ns, size_bytes FROM watcher_files WHERE file_path = ?",
            (resolved_file,),
        ).fetchone()
    if row is None:
        return False
    prev_hash, prev_mtime, prev_size = str(row[0]), int(row[1]), int(row[2])
    return prev_hash == file_hash and prev_mtime == mtime_ns and prev_size == size_bytes


def _record_processed_file(
    db_path: str | Path,
    resolved_file: str,
    file_hash: str,
    mtime_ns: int,
    size_bytes: int,
    domain: str,
    exit_code: int,
) -> None:
    """Enregistre ou met à jour l'état d'un fichier traité par le watcher."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO watcher_files "
            "(file_path, file_hash, mtime_ns, size_bytes, domain, exit_code, processed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (resolved_file, file_hash, mtime_ns, size_bytes, domain, exit_code, now_iso),
        )


def _archive_file(src_path: Path, archive_dir: str | Path) -> Path:
    """Déplace un fichier traité vers le répertoire d'archives sans écrasement."""
    dest_root = Path(archive_dir)
    dest_root.mkdir(parents=True, exist_ok=True)
    target = dest_root / src_path.name
    if target.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        target = dest_root / f"{src_path.stem}_{stamp}{src_path.suffix}"
    shutil.move(str(src_path), str(target))
    return target


def scan_directory_once(
    watch_dir: str | Path,
    *,
    domain: str = "auto",
    db_path: str | Path = "db/datapulse.sqlite",
    reports_dir: str = "reports",
    processed_dir: str = "data/processed",
    configs_dir: str = "configs",
    archive_dir: str | Path | None = None,
    strict: bool = False,
    quarantine: bool = True,
    chunk_size: int | None = None,
    pipeline_runner: Callable[..., int] | None = None,
) -> list[dict[str, Any]]:
    """Scanne un répertoire une fois et exécute DataPulse sur les fichiers nouveaux/modifiés."""
    from datapulse.cli import run_pipeline

    runner = pipeline_runner or run_pipeline
    folder = Path(watch_dir)
    if not folder.exists() or not folder.is_dir():
        raise FileNotFoundError(f"Répertoire surveillé introuvable : '{watch_dir}'")

    _ensure_watcher_tables(db_path)
    events: list[dict[str, Any]] = []

    candidates = sorted(p for p in folder.iterdir() if p.is_file())
    for item in candidates:
        name = item.name
        if name.startswith(".") or name.lower().endswith((".tmp", ".part", ".crdownload")):
            continue
        if item.suffix.lower() not in SUPPORTED_WATCH_EXTENSIONS:
            continue

        resolved = str(item.resolve())
        mtime_ns, size_bytes, file_hash = _compute_file_fingerprint(item)
        if _is_already_processed(db_path, resolved, mtime_ns, size_bytes, file_hash):
            continue

        exit_code = runner(
            file_path=str(item),
            domain=domain,
            db_path=str(db_path),
            reports_dir=reports_dir,
            processed_dir=processed_dir,
            configs_dir=configs_dir,
            strict=strict,
            chunk_size=chunk_size,
            quarantine=quarantine,
        )
        _record_processed_file(
            db_path, resolved, file_hash, mtime_ns, size_bytes, domain, int(exit_code)
        )

        archived_to: str | None = None
        if archive_dir is not None and item.exists():
            archived_to = str(_archive_file(item, archive_dir))

        events.append(
            {
                "file": name,
                "domain": domain,
                "exit_code": int(exit_code),
                "archived_to": archived_to,
            }
        )

    return events


def run_declarative_schedule_cycle(
    config_path: str | Path,
    *,
    db_path: str | Path = "db/datapulse.sqlite",
    reports_dir: str = "reports",
    processed_dir: str = "data/processed",
    configs_dir: str = "configs",
    now_epoch: float | None = None,
    pipeline_runner: Callable[..., int] | None = None,
    sql_runner: Callable[..., int] | None = None,
) -> list[dict[str, Any]]:
    """Exécute un cycle de planification à partir d'un fichier YAML multi-tâches."""
    from datapulse.cli import run_pipeline, run_sql_pipeline

    cfg_file = Path(config_path)
    if not cfg_file.exists():
        raise FileNotFoundError(f"Configuration de planification introuvable : '{config_path}'")

    raw_cfg = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
    jobs = raw_cfg.get("jobs", [])
    if not isinstance(jobs, list):
        raise ValueError("La clé 'jobs' du fichier watch YAML doit être une liste.")

    _ensure_watcher_tables(db_path)
    current_ts = time.time() if now_epoch is None else float(now_epoch)
    f_runner = pipeline_runner or run_pipeline
    s_runner = sql_runner or run_sql_pipeline
    triggered: list[dict[str, Any]] = []

    for idx, job in enumerate(jobs):
        if not isinstance(job, dict):
            continue
        job_name = str(job.get("name") or f"job_{idx + 1}")
        job_type = str(job.get("type") or "folder").strip().lower()
        domain = str(job.get("domain") or "auto")
        interval_sec = float(job.get("interval_sec", 60.0))
        strict = bool(job.get("strict", False))
        quarantine = bool(job.get("quarantine", True))
        chunk_size = int(job["chunk_size"]) if job.get("chunk_size") is not None else None

        with sqlite3.connect(db_path) as conn:
            prev = conn.execute(
                "SELECT last_run_epoch, run_count FROM scheduler_jobs WHERE job_name = ?",
                (job_name,),
            ).fetchone()

        last_epoch = float(prev[0]) if prev else 0.0
        prev_count = int(prev[1]) if prev else 0
        if prev is not None and (current_ts - last_epoch) < interval_sec:
            continue

        if job_type == "folder":
            folder_path = str(job.get("path") or "")
            if not folder_path:
                raise ValueError(f"Tâche '{job_name}' (folder) : paramètre 'path' requis.")
            archive_dir = job.get("archive_dir")
            file_events = scan_directory_once(
                folder_path,
                domain=domain,
                db_path=db_path,
                reports_dir=reports_dir,
                processed_dir=processed_dir,
                configs_dir=configs_dir,
                archive_dir=str(archive_dir) if archive_dir else None,
                strict=strict,
                quarantine=quarantine,
                chunk_size=chunk_size,
                pipeline_runner=f_runner,
            )
            exit_code = max((int(e["exit_code"]) for e in file_events), default=0)
            target_label = folder_path
            detail_count = len(file_events)
        elif job_type == "sql":
            uri = str(job.get("uri") or "")
            if not uri:
                raise ValueError(f"Tâche '{job_name}' (sql) : paramètre 'uri' requis.")
            table = str(job["table"]) if job.get("table") else None
            query = str(job["query"]) if job.get("query") else None
            exit_code = s_runner(
                uri=uri,
                domain=domain,
                table=table,
                query=query,
                db_path=str(db_path),
                reports_dir=reports_dir,
                processed_dir=processed_dir,
                configs_dir=configs_dir,
                strict=strict,
                chunk_size=chunk_size,
                quarantine=quarantine,
            )
            target_label = f"{uri} ({table or 'query'})"
            detail_count = 1
        else:
            raise ValueError(f"Type de tâche inconnu pour '{job_name}' : '{job_type}'")

        now_iso = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO scheduler_jobs "
                "(job_name, job_type, target, domain, interval_sec, "
                "last_run_epoch, last_run_at, last_exit_code, run_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_name,
                    job_type,
                    target_label,
                    domain,
                    interval_sec,
                    current_ts,
                    now_iso,
                    int(exit_code),
                    prev_count + 1,
                ),
            )

        triggered.append(
            {
                "job_name": job_name,
                "job_type": job_type,
                "domain": domain,
                "exit_code": int(exit_code),
                "items_processed": detail_count,
            }
        )

    return triggered


def watch_loop(
    *,
    watch_dir: str | Path | None = None,
    config_path: str | Path | None = None,
    domain: str = "auto",
    interval_sec: float = 5.0,
    max_cycles: int | None = None,
    db_path: str | Path = "db/datapulse.sqlite",
    reports_dir: str = "reports",
    processed_dir: str = "data/processed",
    configs_dir: str = "configs",
    archive_dir: str | Path | None = None,
    strict: bool = False,
    quarantine: bool = True,
    chunk_size: int | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Boucle d'orchestration autonome (hot-folder ou fichier YAML multi-tâches)."""
    if not watch_dir and not config_path:
        raise ValueError("Précisez --dir (répertoire à surveiller) ou --config (watch.yaml).")

    cycles = 0
    total_events: list[dict[str, Any]] = []

    try:
        while True:
            cycles += 1
            if config_path:
                events = run_declarative_schedule_cycle(
                    config_path,
                    db_path=db_path,
                    reports_dir=reports_dir,
                    processed_dir=processed_dir,
                    configs_dir=configs_dir,
                )
            else:
                assert watch_dir is not None
                events = scan_directory_once(
                    watch_dir,
                    domain=domain,
                    db_path=db_path,
                    reports_dir=reports_dir,
                    processed_dir=processed_dir,
                    configs_dir=configs_dir,
                    archive_dir=archive_dir,
                    strict=strict,
                    quarantine=quarantine,
                    chunk_size=chunk_size,
                )
            total_events.extend(events)

            if max_cycles is not None and cycles >= max_cycles:
                break
            sleep_fn(max(0.05, float(interval_sec)))
    except KeyboardInterrupt:
        pass

    return {
        "cycles": cycles,
        "processed_count": len(total_events),
        "events": total_events,
    }


def get_watcher_status(
    db_path: str | Path = "db/datapulse.sqlite",
    limit: int = 30,
) -> dict[str, Any]:
    """Retourne l'état des fichiers traités et des tâches planifiées depuis SQLite."""
    path = Path(db_path)
    if not path.exists():
        return {"files_count": 0, "recent_files": [], "jobs_count": 0, "jobs": []}

    _ensure_watcher_tables(path)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        file_rows = conn.execute(
            "SELECT * FROM watcher_files ORDER BY processed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        job_rows = conn.execute(
            "SELECT * FROM scheduler_jobs ORDER BY last_run_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    recent_files = [dict(r) for r in file_rows]
    jobs = [dict(r) for r in job_rows]
    return {
        "files_count": len(recent_files),
        "recent_files": recent_files,
        "jobs_count": len(jobs),
        "jobs": jobs,
    }
