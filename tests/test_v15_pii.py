"""Tests pour DataPulse V15 : détection PII, anonymisation RGPD et interface Web v15."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from datapulse.api import create_app
from datapulse.cli import pii_mask_cmd, pii_scan_cmd
from datapulse.pii import mask_dataframe_pii, scan_dataframe_pii
from datapulse.security import create_api_key


def _sample_pii_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "client_id": [101, 102, 103],
            "email": ["alice@example.com", "bob@corp.org", "carol@mail.bj"],
            "telephone": ["+22997001122", "+33612345678", "+22961223344"],
            "iban": [
                "FR7630006000011234567890189",
                "BJ66BJ0610100100144390000769",
                "DE89370400440532013000",
            ],
            "montant": [150.0, 299.9, 45.5],
        }
    )


def test_pii_scanner_identifies_sensitive_columns_without_false_positives() -> None:
    df = _sample_pii_df()
    report = scan_dataframe_pii(df)

    assert report["pii_columns_count"] == 3
    detected = {c["column"]: c["pii_type"] for c in report["columns"]}
    assert detected == {
        "email": "EMAIL",
        "telephone": "PHONE",
        "iban": "IBAN",
    }
    assert "montant" not in detected
    assert "client_id" not in detected


def test_pii_masking_strategies_partial_redact_and_hash() -> None:
    df = _sample_pii_df()

    partial_df, summary_p = mask_dataframe_pii(df, strategy="partial")
    assert summary_p["masked_columns_count"] == 3
    assert partial_df.loc[0, "email"] == "a***@example.com"
    assert partial_df.loc[0, "montant"] == 150.0

    redact_df, _ = mask_dataframe_pii(df, strategy="redact", columns=["email"])
    assert redact_df.loc[1, "email"] == "***REDACTED***"
    assert redact_df.loc[1, "telephone"] == "+33612345678"

    hash_df, _ = mask_dataframe_pii(df, strategy="hash", columns=["email"])
    assert str(hash_df.loc[0, "email"]).startswith("pii_")
    assert hash_df.loc[0, "email"] != hash_df.loc[1, "email"]

    with pytest.raises(ValueError, match="Stratégie d'anonymisation invalide"):
        mask_dataframe_pii(df, strategy="invalid_mode")


def test_cli_pii_scan_and_mask_commands(tmp_path: Path) -> None:
    src_csv = tmp_path / "clients.csv"
    out_csv = tmp_path / "clients_anon.csv"
    _sample_pii_df().to_csv(src_csv, index=False)

    assert pii_scan_cmd(str(src_csv)) == 0
    assert (
        pii_mask_cmd(
            file_path=str(src_csv),
            strategy="partial",
            output_path=str(out_csv),
        )
        == 0
    )
    assert out_csv.exists()
    masked_out = pd.read_csv(out_csv)
    assert masked_out.loc[0, "email"] == "a***@example.com"


def test_api_pii_endpoints_rbac_and_v15_dashboard(tmp_path: Path) -> None:
    db_file = tmp_path / "v15_pii.sqlite"
    view_key = str(create_api_key(db_file, name="rgpd-viewer", role="viewer")["api_key"])
    op_key = str(create_api_key(db_file, name="rgpd-op", role="operator")["api_key"])

    src_csv = tmp_path / "users.csv"
    _sample_pii_df().to_csv(src_csv, index=False)

    app = create_app(
        db_path=str(db_file),
        reports_dir=str(tmp_path / "reports"),
        processed_dir=str(tmp_path / "processed"),
    )
    client = TestClient(app)

    # Dashboard v15 multi-onglets
    dash = client.get("/")
    assert dash.status_code == 200
    assert "DataPulse" in dash.text and "v19.0.0" in dash.text
    assert "RGPD / PII" in dash.text

    # Scan autorisé pour viewer
    with open(src_csv, "rb") as f:
        scan_res = client.post(
            "/api/pii/scan",
            headers={"X-API-Key": view_key},
            files={"file": ("users.csv", f, "text/csv")},
        )
    assert scan_res.status_code == 200
    assert scan_res.json()["pii_columns_count"] == 3

    # Masquage interdit pour viewer (403), autorisé pour operator (200)
    with open(src_csv, "rb") as f:
        denied = client.post(
            "/api/pii/mask",
            headers={"X-API-Key": view_key},
            files={"file": ("users.csv", f, "text/csv")},
            data={"strategy": "redact"},
        )
    assert denied.status_code == 403

    with open(src_csv, "rb") as f:
        allowed = client.post(
            "/api/pii/mask",
            headers={"X-API-Key": op_key},
            files={"file": ("users.csv", f, "text/csv")},
            data={"strategy": "redact"},
        )
    assert allowed.status_code == 200
    assert allowed.json()["preview_rows"][0]["email"] == "***REDACTED***"
