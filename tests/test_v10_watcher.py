"""Tests pour DataPulse V10 : orchestrateur hot-folder et planificateur périodique."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from datapulse.api import create_app
from datapulse.cli import show_watch_status, watch_cmd
from datapulse.exceptions import ExitCode
from datapulse.watcher import (
    get_watcher_status,
    run_declarative_schedule_cycle,
    scan_directory_once,
)


def test_hot_folder_deduplicates_unchanged_and_reruns_modified_files(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    db_file = tmp_path / "watch.sqlite"

    # Fichiers ignorés (cachés, temporaires, extension non supportée)
    (inbox / ".hidden.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (inbox / "ongoing.csv.tmp").write_text("a,b\n1,2\n", encoding="utf-8")
    (inbox / "readme.txt").write_text("ignore me", encoding="utf-8")

    # Fichier valide
    target_csv = inbox / "lot_01.csv"
    pd.DataFrame({"id_cmd": ["C1", "C2"], "montant": [100.0, 250.0]}).to_csv(
        target_csv, index=False
    )

    # Cycle 1 : 1 fichier traité
    ev1 = scan_directory_once(
        inbox,
        domain="auto",
        db_path=db_file,
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
    )
    assert len(ev1) == 1
    assert ev1[0]["file"] == "lot_01.csv"

    # Cycle 2 sans modification : 0 fichier retraité (idempotence)
    ev2 = scan_directory_once(
        inbox,
        domain="auto",
        db_path=db_file,
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
    )
    assert ev2 == []

    # Modification du fichier -> ré-exécution automatique
    time.sleep(0.02)
    pd.DataFrame({"id_cmd": ["C1", "C2", "C3"], "montant": [100.0, 250.0, 400.0]}).to_csv(
        target_csv, index=False
    )
    ev3 = scan_directory_once(
        inbox,
        domain="auto",
        db_path=db_file,
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
    )
    assert len(ev3) == 1


def test_hot_folder_archives_processed_files_and_isolates_quarantine(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    archive = tmp_path / "archive"
    inbox.mkdir()
    db_file = tmp_path / "watch_arch.sqlite"

    df_clean = pd.read_csv("data/reference/ventes_clean.csv").head(5).copy()
    df_clean.loc[0, "quantite"] = -99
    drop_file = inbox / "ventes_drop.csv"
    df_clean.to_csv(drop_file, index=False)

    events = scan_directory_once(
        inbox,
        domain="commerce",
        db_path=db_file,
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
        archive_dir=archive,
        quarantine=True,
    )
    assert len(events) == 1
    assert not drop_file.exists()
    assert (archive / "ventes_drop.csv").exists()

    status = get_watcher_status(db_path=db_file)
    assert status["files_count"] == 1


def test_declarative_yaml_scheduler_runs_folder_and_sql_jobs(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    pd.DataFrame({"col_a": [10, 20, 30]}).to_csv(inbox / "drop.csv", index=False)

    src_sqlite = tmp_path / "source.sqlite"
    with sqlite3.connect(src_sqlite) as conn:
        conn.execute("CREATE TABLE mesures (id INTEGER, valeur REAL)")
        conn.execute("INSERT INTO mesures VALUES (1, 42.5), (2, 44.0)")

    db_file = tmp_path / "sched.sqlite"
    watch_yaml = tmp_path / "watch.yaml"
    watch_yaml.write_text(
        f"""
jobs:
  - name: dossier_auto
    type: folder
    path: "{inbox.as_posix()}"
    domain: auto
    interval_sec: 30
  - name: audit_sql_mesures
    type: sql
    uri: "sqlite:///{src_sqlite.as_posix()}"
    table: mesures
    domain: auto
    interval_sec: 60
""".strip(),
        encoding="utf-8",
    )

    # t=1000 : les 2 tâches s'exécutent
    res1 = run_declarative_schedule_cycle(
        watch_yaml,
        db_path=db_file,
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
        now_epoch=1000.0,
    )
    assert len(res1) == 2

    # t=1015 : interval_sec non écoulé -> aucune tâche déclenchée
    res2 = run_declarative_schedule_cycle(
        watch_yaml,
        db_path=db_file,
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
        now_epoch=1015.0,
    )
    assert res2 == []

    # t=1040 : seule dossier_auto (interval=30s) est due
    res3 = run_declarative_schedule_cycle(
        watch_yaml,
        db_path=db_file,
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
        now_epoch=1040.0,
    )
    assert len(res3) == 1
    assert res3[0]["job_name"] == "dossier_auto"


def test_cli_watch_and_watch_status_commands(tmp_path: Path) -> None:
    inbox = tmp_path / "cli_inbox"
    inbox.mkdir()
    pd.DataFrame({"k": ["A", "B"], "v": [1.0, 2.0]}).to_csv(inbox / "sample.csv", index=False)
    db_file = tmp_path / "cli_watch.sqlite"

    code = watch_cmd(
        watch_dir=str(inbox),
        domain="auto",
        interval=0.05,
        max_cycles=1,
        db_path=str(db_file),
    )
    assert code == int(ExitCode.SUCCESS)
    assert show_watch_status(db_path=str(db_file)) == int(ExitCode.SUCCESS)


def test_api_watcher_scan_and_status_endpoints(tmp_path: Path) -> None:
    inbox = tmp_path / "api_inbox"
    inbox.mkdir()
    pd.DataFrame({"x": [1, 2, 3], "y": [4.0, 5.0, 6.0]}).to_csv(inbox / "api_drop.csv", index=False)

    db_file = tmp_path / "api_watch.sqlite"
    app = create_app(
        db_path=str(db_file),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
    )
    client = TestClient(app)

    scan_resp = client.post(
        "/api/watcher/scan",
        json={"watch_dir": str(inbox), "domain": "auto"},
    )
    assert scan_resp.status_code == 200
    assert scan_resp.json()["processed_count"] == 1

    stat_resp = client.get("/api/watcher/status")
    assert stat_resp.status_code == 200
    assert stat_resp.json()["files_count"] == 1
