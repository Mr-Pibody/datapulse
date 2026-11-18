"""Tests pour DataPulse V3 : streaming par chunks, JSONL, schema drift et datapulse diff."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from datapulse.cli import diff_runs, run_pipeline
from datapulse.config_loader import ConfigValidationError, load_domain_config
from datapulse.exceptions import ExitCode, IngestionError
from datapulse.ingestion import ingest_file, iter_file_chunks
from datapulse.profiler import StreamingProfiler, detect_schema_drift, profile_dataframe
from datapulse.storage import DataPulseStorage


def test_ingest_jsonl_and_chunks(tmp_path: Path) -> None:
    jsonl_file = tmp_path / "events.jsonl"
    df_src = pd.DataFrame({"id": range(1, 26), "amount": [10.5 * i for i in range(1, 26)]})
    df_src.to_json(jsonl_file, orient="records", lines=True)

    res = ingest_file(str(jsonl_file), raw_dir=str(tmp_path / "raw"))
    assert res.row_count == 25
    assert res.column_count == 2

    _, _, chunk_iter = iter_file_chunks(
        str(jsonl_file), chunk_size=10, raw_dir=str(tmp_path / "raw2")
    )
    chunks = list(chunk_iter)
    assert [offset for offset, _ in chunks] == [0, 10, 20]
    assert sum(len(c) for _, c in chunks) == 25


def test_iter_file_chunks_rejects_non_positive_size(tmp_path: Path) -> None:
    csv_file = tmp_path / "tiny.csv"
    csv_file.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(IngestionError):
        iter_file_chunks(str(csv_file), chunk_size=0, raw_dir=str(tmp_path / "raw"))


def test_unsupported_extension_raises_ingestion_error(tmp_path: Path) -> None:
    bad_file = tmp_path / "archive.zip"
    bad_file.write_bytes(b"PK\x03\x04")
    with pytest.raises(IngestionError):
        ingest_file(str(bad_file), raw_dir=str(tmp_path / "raw"))


def test_streaming_profiler_matches_batch_profiler() -> None:
    rng = np.random.default_rng(123)
    df = pd.DataFrame(
        {
            "montant": rng.normal(loc=500.0, scale=75.0, size=500),
            "categorie": ["A", "B", None, "C", "A"] * 100,
        }
    )
    df.loc[0:9, "montant"] = np.nan

    batch_prof = profile_dataframe(df)

    s_prof = StreamingProfiler()
    for start in range(0, len(df), 120):
        s_prof.update(df.iloc[start : start + 120])
    stream_prof = s_prof.finalize()

    assert stream_prof["row_count"] == batch_prof["row_count"]
    assert stream_prof["total_nulls"] == batch_prof["total_nulls"]
    assert stream_prof["completeness_pct"] == batch_prof["completeness_pct"]

    b_num = batch_prof["columns"][0]
    s_num = stream_prof["columns"][0]
    assert s_num["null_count"] == b_num["null_count"]
    assert pytest.approx(s_num["mean"], rel=1e-4) == b_num["mean"]
    assert pytest.approx(s_num["std"], rel=1e-4) == b_num["std"]
    assert s_num["min"] == b_num["min"]
    assert s_num["max"] == b_num["max"]


def test_streaming_1m_rows_benchmark_and_constant_memory(tmp_path: Path) -> None:
    """Valide le traitement de 1 000 000 lignes en streaming par chunks."""
    big_csv = tmp_path / "million_rows.csv"
    n_rows = 1_000_000
    chunk = pd.DataFrame(
        {
            "id": np.arange(100_000, dtype=np.int64),
            "valeur": np.linspace(10.0, 999.0, 100_000),
        }
    )
    for idx in range(n_rows // 100_000):
        chunk.to_csv(big_csv, mode="w" if idx == 0 else "a", header=(idx == 0), index=False)

    _, _, chunk_iter = iter_file_chunks(
        str(big_csv), chunk_size=100_000, raw_dir=str(tmp_path / "raw")
    )
    profiler = StreamingProfiler()
    for _, df_chunk in chunk_iter:
        profiler.update(df_chunk)

    result = profiler.finalize()
    assert result["row_count"] == 1_000_000
    assert result["completeness_pct"] == 100.0


def test_detect_schema_drift_all_cases() -> None:
    baseline = [
        {"column_name": "id", "dtype": "int64", "null_count": 0},
        {"column_name": "amount", "dtype": "float64", "null_count": 0},
        {"column_name": "legacy_code", "dtype": "object", "null_count": 2},
    ]
    current = {
        "columns": [
            {"column_name": "id", "dtype": "object", "null_count": 0},
            {"column_name": "amount", "dtype": "float64", "null_count": 5},
            {"column_name": "currency", "dtype": "object", "null_count": 0},
        ]
    }
    drifts = detect_schema_drift(current, baseline)
    drift_types = {d["column_name"]: d["drift_type"] for d in drifts}

    assert drift_types == {
        "currency": "ADDED_COLUMN",
        "legacy_code": "REMOVED_COLUMN",
        "id": "TYPE_CHANGED",
        "amount": "NULLABILITY_SPIKE",
    }


def test_run_pipeline_streaming_mode_and_diff_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sample_csv = Path("data/raw/ventes.csv")
    db_file = tmp_path / "v3_diff.sqlite"

    # Run 1 : mode batch
    code1 = run_pipeline(
        file_path=str(sample_csv),
        domain="commerce",
        db_path=str(db_file),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
    )
    # Run 2 : mode streaming par chunks de 20 lignes
    code2 = run_pipeline(
        file_path=str(sample_csv),
        domain="commerce",
        db_path=str(db_file),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
        chunk_size=20,
    )
    capsys.readouterr()

    assert code1 == int(ExitCode.SUCCESS)
    assert code2 == int(ExitCode.SUCCESS)

    diff_code = diff_runs(domain="commerce", db_path=str(db_file))
    output = capsys.readouterr().out
    assert diff_code == int(ExitCode.SUCCESS)
    assert "Diff DataPulse" in output
    assert "Aucune dérive structurelle détectée" in output


def test_diff_cli_fails_when_fewer_than_two_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "single.sqlite"
    DataPulseStorage(db_path=str(db_file))

    code = diff_runs(domain="commerce", db_path=str(db_file))
    assert code == int(ExitCode.EXECUTION_ERROR)
    assert "Au moins 2 exécutions" in capsys.readouterr().err


def test_config_loader_validates_types(tmp_path: Path) -> None:
    bad_yaml = tmp_path / "broken.yaml"
    bad_yaml.write_text(
        "domain: broken\nversion: '1.0'\nrules: not_a_list\nanomaly_detection: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigValidationError, match="rules"):
        load_domain_config("broken", config_dir=str(tmp_path))
