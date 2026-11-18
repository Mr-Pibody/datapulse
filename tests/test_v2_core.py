"""Tests unitaires et d'intégration pour les fonctionnalités DataPulse V2."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import pytest

from datapulse.cli import main, run_pipeline
from datapulse.exceptions import (
    ConfigError,
    ConfigValidationError,
    DataPulseError,
    ExitCode,
    StorageTransactionError,
)
from datapulse.observability import PipelineTracker, get_structured_logger
from datapulse.storage import DataPulseStorage


def test_exception_hierarchy_and_context() -> None:
    err = ConfigValidationError("Schéma YAML invalide", context={"domain": "finance", "line": 12})
    assert isinstance(err, DataPulseError)
    assert issubclass(ConfigError, ConfigValidationError)
    assert err.exit_code == ExitCode.EXECUTION_ERROR
    assert "domain='finance'" in str(err)
    assert "line=12" in str(err)


def test_pipeline_tracker_records_steps_and_throughput(capsys: pytest.CaptureFixture[str]) -> None:
    logger = get_structured_logger("datapulse.test.obs", enabled=True, level=logging.INFO)
    tracker = PipelineTracker("test_pipeline", run_id="run-test-001", logger=logger)

    with tracker.track_step("ingestion", rows_in=500) as step:
        step.rows_out = 495

    telemetry = tracker.finalize()
    assert telemetry.run_id == "run-test-001"
    assert len(telemetry.steps) == 1
    assert telemetry.steps[0].rows_in == 500
    assert telemetry.steps[0].rows_out == 495
    assert telemetry.steps[0].status == "ok"

    captured = capsys.readouterr()
    log_payload = json.loads(captured.err.strip())
    assert log_payload["run_id"] == "run-test-001"
    assert log_payload["step"] == "ingestion"
    assert log_payload["status"] == "ok"


def test_pipeline_tracker_marks_error_on_exception() -> None:
    tracker = PipelineTracker("test_err")
    with pytest.raises(RuntimeError, match="boom"), tracker.track_step("validation", rows_in=10):
        raise RuntimeError("boom")

    telemetry = tracker.finalize()
    assert telemetry.steps[0].status == "error"
    assert telemetry.steps[0].details["error_type"] == "RuntimeError"


def test_storage_atomic_rollback_on_foreign_key_error(tmp_path: Path) -> None:
    db_file = tmp_path / "rollback_test.sqlite"
    storage = DataPulseStorage(db_path=str(db_file))

    with pytest.raises(StorageTransactionError):
        storage.save_benchmark(
            "non-existent-run-id",
            {
                "true_positives": 1,
                "false_positives": 0,
                "false_negatives": 0,
                "precision": 1.0,
                "recall": 1.0,
                "f1_score": 1.0,
            },
        )

    assert storage.get_history() == []


def test_storage_persists_step_metrics_and_returns_details(tmp_path: Path) -> None:
    db_file = tmp_path / "steps_test.sqlite"
    storage = DataPulseStorage(db_path=str(db_file))

    run_meta = {
        "run_id": "abcd1234-5678-90ef",
        "timestamp": "2026-11-18T10:00:00+00:00",
        "domain": "commerce",
        "source_file": "data/raw/sample.csv",
        "file_hash_sha256": "deadbeef",
        "config_version": "2.0",
        "row_count": 100,
        "duration_ms": 12.5,
        "peak_memory_kb": 64.0,
        "status": "SUCCESS",
    }
    steps = [
        {
            "step": "ingestion",
            "status": "ok",
            "duration_ms": 4.2,
            "rows_in": 100,
            "rows_out": 100,
            "throughput_rows_per_sec": 23809.52,
        }
    ]
    storage.save_run(run_meta, {"columns": []}, [], steps=steps)

    details = storage.get_run_details("abcd1234")
    assert details is not None
    assert details["run_id"] == "abcd1234-5678-90ef"
    assert len(details["steps"]) == 1
    assert details["steps"][0]["step_name"] == "ingestion"


def test_storage_wraps_corrupted_database_error(tmp_path: Path) -> None:
    bad_db = tmp_path / "corrupt.sqlite"
    bad_db.write_bytes(b"not a valid sqlite database header")

    with pytest.raises(StorageTransactionError):
        DataPulseStorage(db_path=str(bad_db))


def test_cli_strict_mode_returns_quality_alert_when_findings_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample_csv = Path("data/raw/ventes.csv")
    db_file = tmp_path / "strict.sqlite"

    code = run_pipeline(
        file_path=str(sample_csv),
        domain="commerce",
        db_path=str(db_file),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
        strict=True,
    )
    assert code in (int(ExitCode.SUCCESS), int(ExitCode.QUALITY_ALERT))


def test_cli_inspect_missing_run_returns_execution_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "empty.sqlite"
    DataPulseStorage(db_path=str(db_file))

    from datapulse.cli import inspect_run

    code = inspect_run("unknown-id", db_path=str(db_file))
    assert code == int(ExitCode.EXECUTION_ERROR)
    assert "Aucun run trouvé" in capsys.readouterr().err


def test_cli_main_invalid_domain_returns_exit_code_2(capsys: pytest.CaptureFixture[str]) -> None:
    sample_csv = Path("data/raw/ventes.csv")
    code = main(["run-all", "--file", str(sample_csv), "--domain", "domaine_inexistant_xyz"])
    assert code == int(ExitCode.EXECUTION_ERROR)
    assert "[ERREUR]" in capsys.readouterr().err


def test_sqlite_wal_mode_enabled(tmp_path: Path) -> None:
    db_file = tmp_path / "wal.sqlite"
    DataPulseStorage(db_path=str(db_file))
    conn = sqlite3.connect(db_file)
    try:
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert str(mode).lower() == "wal"
    finally:
        conn.close()


def test_step_metric_throughput_edge_cases() -> None:
    from datapulse.observability import StepMetric

    zero_duration = StepMetric(step="empty", duration_ms=0.0, rows_in=100)
    zero_rows = StepMetric(step="empty", duration_ms=10.0, rows_in=0)
    assert zero_duration.throughput_rows_per_sec == 0.0
    assert zero_rows.throughput_rows_per_sec == 0.0


def test_cli_inspect_existing_run_displays_steps(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from datapulse.cli import inspect_run

    sample_csv = Path("data/raw/ventes.csv")
    db_file = tmp_path / "inspect_ok.sqlite"

    run_pipeline(
        file_path=str(sample_csv),
        domain="commerce",
        db_path=str(db_file),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
    )
    capsys.readouterr()

    latest_run = DataPulseStorage(db_path=str(db_file)).get_history(limit=1)[0]
    code = inspect_run(latest_run["run_id"][:8], db_path=str(db_file))
    output = capsys.readouterr().out

    assert code == int(ExitCode.SUCCESS)
    assert "Télémétrie par étape" in output
    assert "ingestion" in output
    assert "reconciliation" in output


def test_cli_run_all_with_json_logs_emits_valid_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sample_csv = Path("data/raw/ventes.csv")
    db_file = tmp_path / "json_logs.sqlite"

    code = run_pipeline(
        file_path=str(sample_csv),
        domain="commerce",
        db_path=str(db_file),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
        json_logs=True,
    )
    captured = capsys.readouterr()
    log_lines = [line for line in captured.err.strip().splitlines() if line.strip()]

    assert code == int(ExitCode.SUCCESS)
    assert len(log_lines) >= 6
    parsed = [json.loads(line) for line in log_lines]
    assert any(entry.get("step") == "reconciliation" for entry in parsed)
