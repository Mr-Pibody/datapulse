"""Détection automatique de données personnelles (PII) et anonymisation RGPD — DataPulse V15."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import pandas as pd

from datapulse.ingestion import ingest_file

VALID_MASK_STRATEGIES: tuple[str, ...] = ("partial", "redact", "hash")

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_PHONE_RE = re.compile(r"^(?:\+|00)[1-9]\d{6,14}$|^0[1-9](?:[\s.-]?\d{2}){4}$")
_IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$")
_IPV4_RE = re.compile(r"^(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)$")
_CARD_RE = re.compile(r"^\d{13,19}$")

_COL_HINTS: dict[str, tuple[str, ...]] = {
    "EMAIL": ("email", "mail", "courriel"),
    "PHONE": ("phone", "tel", "telephone", "mobile", "gsm", "whatsapp"),
    "IBAN": ("iban", "rib", "compte_bancaire", "bank_account"),
    "CREDIT_CARD": ("card", "carte", "cc_num", "pan"),
    "IPV4": ("ip", "ip_address", "adresse_ip", "client_ip"),
    "NATIONAL_ID": ("ssn", "cni", "npi", "passport", "passeport", "nin", "secu"),
}


def _passes_luhn(digits: str) -> bool:
    """Vérifie la somme de contrôle de Luhn pour un numéro de carte bancaire."""
    if not _CARD_RE.match(digits):
        return False
    total = 0
    reverse_digits = digits[::-1]
    for idx, ch in enumerate(reverse_digits):
        n = int(ch)
        if idx % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def classify_pii_value(value: Any, column_hint: str | None = None) -> str | None:
    """Identifie le type de donnée personnelle d'une cellule, ou None si non sensible."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    raw = str(value).strip()
    if not raw or raw.lower() in {"nan", "none", "null", "na"}:
        return None

    if _EMAIL_RE.match(raw):
        return "EMAIL"

    compact = re.sub(r"[\s-]+", "", raw).upper()
    if _IBAN_RE.match(compact) and len(compact) >= 15:
        return "IBAN"

    digits_only = re.sub(r"[\s-]+", "", raw)
    if _passes_luhn(digits_only):
        return "CREDIT_CARD"

    if _IPV4_RE.match(raw):
        return "IPV4"

    if _PHONE_RE.match(raw):
        return "PHONE"

    if column_hint == "PHONE" and re.match(r"^\+?\d{8,15}$", digits_only):
        return "PHONE"

    if column_hint == "NATIONAL_ID" and len(compact) >= 6:
        return "NATIONAL_ID"

    return None


def _infer_column_hint(col_name: str) -> str | None:
    lower = col_name.strip().lower()
    for pii_type, keywords in _COL_HINTS.items():
        if any(kw in lower for kw in keywords):
            return pii_type
    return None


def mask_single_value(
    value: Any,
    *,
    strategy: str = "partial",
    pii_type: str | None = None,
    salt: str = "datapulse-rgpd",
) -> Any:
    """Anonymise une valeur individuelle selon la stratégie choisie."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return value
    raw = str(value).strip()
    if not raw:
        return value

    clean_strategy = strategy.strip().lower()
    if clean_strategy not in VALID_MASK_STRATEGIES:
        raise ValueError(
            f"Stratégie d'anonymisation invalide '{strategy}'. "
            f"Choix : {', '.join(VALID_MASK_STRATEGIES)}."
        )

    if clean_strategy == "redact":
        return "***REDACTED***"

    if clean_strategy == "hash":
        digest = hashlib.sha256(f"{salt}:{raw}".encode()).hexdigest()[:16]
        return f"pii_{digest}"

    # Stratégie "partial" adaptée au type de donnée
    detected = pii_type or classify_pii_value(raw)
    if detected == "EMAIL" and "@" in raw:
        local, domain = raw.split("@", 1)
        prefix = local[0] if local else "*"
        return f"{prefix}***@{domain}"
    if detected in {"PHONE", "IBAN", "CREDIT_CARD"}:
        if len(raw) <= 6:
            return "*" * len(raw)
        return f"{raw[:4]}{'*' * (len(raw) - 6)}{raw[-2:]}"
    if detected == "IPV4":
        parts = raw.split(".")
        if len(parts) == 4:
            return f"{parts[0]}.{parts[1]}.***.***"

    if len(raw) <= 4:
        return "*" * len(raw)
    return f"{raw[:2]}{'*' * (len(raw) - 4)}{raw[-2:]}"


def scan_dataframe_pii(df: pd.DataFrame, sample_limit: int = 1000) -> dict[str, Any]:
    """Analyse un DataFrame et détecte les colonnes contenant des données personnelles."""
    if df.empty:
        return {
            "row_count": 0,
            "pii_columns_count": 0,
            "total_pii_cells": 0,
            "columns": [],
        }

    sample = df.head(sample_limit)
    detected_columns: list[dict[str, Any]] = []
    total_pii_cells = 0

    for col in sample.columns:
        col_str = str(col)
        hint = _infer_column_hint(col_str)
        series = sample[col].dropna()
        non_null_count = len(series)
        if non_null_count == 0:
            continue

        type_counts: dict[str, int] = {}
        first_sample: str | None = None
        for val in series:
            ptype = classify_pii_value(val, column_hint=hint)
            if ptype is not None:
                type_counts[ptype] = type_counts.get(ptype, 0) + 1
                if first_sample is None:
                    first_sample = str(val).strip()

        if not type_counts:
            continue

        dominant_type = max(type_counts.items(), key=lambda kv: kv[1])[0]
        matched_count = sum(type_counts.values())
        match_rate = round((matched_count / non_null_count) * 100.0, 2)

        # Exige au moins 20% de correspondance pour éviter le bruit accidentel
        if match_rate < 20.0:
            continue

        total_pii_cells += matched_count
        detected_columns.append(
            {
                "column": col_str,
                "pii_type": dominant_type,
                "match_count": matched_count,
                "non_null_count": non_null_count,
                "match_rate_pct": match_rate,
                "sample_masked": mask_single_value(
                    first_sample, strategy="partial", pii_type=dominant_type
                ),
            }
        )

    return {
        "row_count": len(df),
        "pii_columns_count": len(detected_columns),
        "total_pii_cells": total_pii_cells,
        "columns": detected_columns,
    }


def mask_dataframe_pii(
    df: pd.DataFrame,
    *,
    strategy: str = "partial",
    columns: list[str] | None = None,
    salt: str = "datapulse-rgpd",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Anonymise les colonnes sensibles détectées ou explicitement ciblées."""
    clean_strategy = strategy.strip().lower()
    if clean_strategy not in VALID_MASK_STRATEGIES:
        raise ValueError(
            f"Stratégie d'anonymisation invalide '{strategy}'. "
            f"Choix : {', '.join(VALID_MASK_STRATEGIES)}."
        )

    scan_report = scan_dataframe_pii(df)
    detected_map = {c["column"]: c["pii_type"] for c in scan_report["columns"]}

    if columns:
        target_cols = [c.strip() for c in columns if c.strip() in df.columns]
    else:
        target_cols = list(detected_map.keys())

    masked_df = df.copy()
    masked_cells_total = 0
    for col in target_cols:
        ptype = detected_map.get(col) or _infer_column_hint(col)
        non_null_mask = masked_df[col].notna()
        masked_cells_total += int(non_null_mask.sum())
        masked_df[col] = masked_df[col].apply(
            lambda v, pt=ptype: mask_single_value(
                v, strategy=clean_strategy, pii_type=pt, salt=salt
            )
        )

    summary = {
        "strategy": clean_strategy,
        "row_count": len(masked_df),
        "masked_columns": target_cols,
        "masked_columns_count": len(target_cols),
        "masked_cells_total": masked_cells_total,
        "detected_pii": scan_report["columns"],
    }
    return masked_df, summary


def mask_file_pii(
    file_path: str,
    *,
    output_path: str | None = None,
    strategy: str = "partial",
    columns: list[str] | None = None,
    processed_dir: str = "data/processed",
) -> dict[str, Any]:
    """Ingère un fichier, anonymise ses colonnes PII et écrit l'export RGPD."""
    ingestion = ingest_file(file_path)
    masked_df, summary = mask_dataframe_pii(
        ingestion.dataframe,
        strategy=strategy,
        columns=columns,
    )

    if output_path:
        dest = Path(output_path)
    else:
        out_dir = Path(processed_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(file_path).stem
        dest = out_dir / f"{stem}_masked.csv"

    dest.parent.mkdir(parents=True, exist_ok=True)
    masked_df.to_csv(dest, index=False)
    summary["output_path"] = str(dest)
    summary["preview_rows"] = masked_df.head(5).fillna("").to_dict(orient="records")
    return summary
