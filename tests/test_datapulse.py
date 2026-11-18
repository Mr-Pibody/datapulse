import time

import numpy as np
import pandas as pd
import pytest

from datapulse.cli import run_pipeline
from datapulse.config_loader import ConfigError, load_domain_config
from datapulse.detector import detect_anomalies
from datapulse.ingestion import compute_sha256
from datapulse.reconciler import reconcile_findings
from datapulse.storage import DataPulseStorage
from datapulse.validator import validate_dataframe


def test_load_invalid_domain_raises_config_error():
    with pytest.raises(ConfigError):
        load_domain_config("domaine_inexistant")


def test_reconciler_avoids_double_counting(commerce_sample_df):
    cfg = load_domain_config("commerce")
    violations = validate_dataframe(commerce_sample_df, cfg)
    anomalies, _ = detect_anomalies(commerce_sample_df, cfg)
    findings, annotated = reconcile_findings(commerce_sample_df, violations, anomalies)

    row0 = [f for f in findings if f["row_index"] == 0 and f["column_name"] == "quantite"]
    assert len(row0) == 1
    assert row0[0]["category"] == "BOTH"
    assert annotated.loc[0, "_datapulse_status"] == "BOTH"


def test_detector_respects_min_sample_size():
    small_df = pd.DataFrame({"quantite": [10, 12, 11, 999]})
    cfg = load_domain_config("commerce")
    anomalies, warnings = detect_anomalies(small_df, cfg)

    assert anomalies == []
    assert any("échantillon trop faible" in w for w in warnings)


@pytest.mark.parametrize(
    "domain,raw_file,ref_file",
    [
        ("commerce", "data/raw/ventes.csv", "data/reference/ventes_clean.csv"),
        ("finance", "data/raw/transactions.csv", "data/reference/transactions_clean.csv"),
        (
            "administration",
            "data/raw/services_publics.csv",
            "data/reference/services_publics_clean.csv",
        ),
    ],
)
def test_end_to_end_three_domains(tmp_path, tmp_db_path, domain, raw_file, ref_file):
    from pathlib import Path

    before_hash = compute_sha256(Path(raw_file))
    exit_code = run_pipeline(
        file_path=raw_file,
        domain=domain,
        ref_path=ref_file,
        db_path=tmp_db_path,
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
    )
    after_hash = compute_sha256(Path(raw_file))

    assert exit_code == 0
    # Vérifie l'immuabilité stricte du fichier brut
    assert before_hash == after_hash

    storage = DataPulseStorage(db_path=tmp_db_path)
    history = storage.get_history(domain=domain)
    assert len(history) == 1
    assert history[0]["findings_count"] >= 4


def test_performance_100k_rows_under_60s():
    rng = np.random.default_rng(42)
    n = 100_000
    df = pd.DataFrame(
        {
            "date": ["2026-03-01"] * n,
            "produit": [f"Prod_{i}" for i in range(n)],
            "quantite": rng.integers(1, 50, size=n),
            "prix_unitaire": rng.uniform(5.0, 200.0, size=n),
        }
    )
    df.loc[500, "quantite"] = -10

    cfg = load_domain_config("commerce")
    t0 = time.perf_counter()
    violations = validate_dataframe(df, cfg)
    anomalies, _ = detect_anomalies(df, cfg)
    findings, _ = reconcile_findings(df, violations, anomalies)
    elapsed = time.perf_counter() - t0

    assert elapsed < 60.0
    assert any(f["row_index"] == 500 for f in findings)
