import json
from pathlib import Path
from typing import Any

import pandas as pd


def evaluate_against_reference(
    raw_df: pd.DataFrame,
    ref_path: str,
    findings: list[dict[str, Any]],
    domain: str,
    manifest_path: str = "data/reference/injected_errors_manifest.json",
) -> dict[str, Any]:
    ref_file = Path(ref_path)
    if not ref_file.exists():
        raise FileNotFoundError(f"Fichier de référence introuvable : {ref_file}")

    ref_df = pd.read_csv(ref_file, keep_default_na=True)

    # Identifie les lignes réellement modifiées par rapport au fichier propre
    true_error_rows = set()
    min_len = min(len(raw_df), len(ref_df))
    for idx in range(min_len):
        raw_row = raw_df.iloc[idx].fillna("__NA__").astype(str)
        ref_row = ref_df.iloc[idx].fillna("__NA__").astype(str)
        if not raw_row.equals(ref_row):
            true_error_rows.add(int(idx))

    # Complète avec le manifeste s'il existe
    manifest_file = Path(manifest_path)
    if manifest_file.exists():
        with open(manifest_file, encoding="utf-8") as f:
            manifest = json.load(f)
        for item in manifest.get(domain, []):
            true_error_rows.add(int(item["row"]))

    detected_rows = {int(f["row_index"]) for f in findings}

    tp = len(detected_rows & true_error_rows)
    fp = len(detected_rows - true_error_rows)
    fn = len(true_error_rows - detected_rows)

    precision = round(tp / (tp + fp), 4) if (tp + fp) > 0 else 1.0
    recall = round(tp / (tp + fn), 4) if (tp + fn) > 0 else 1.0
    f1 = (
        round((2 * precision * recall) / (precision + recall), 4)
        if (precision + recall) > 0
        else 0.0
    )

    return {
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": precision,
        "recall": recall,
        "f1_score": f1,
        "expected_rows": sorted(true_error_rows),
        "detected_rows": sorted(detected_rows),
    }
