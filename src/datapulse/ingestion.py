import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass
class IngestionResult:
    source_path: Path
    raw_copy_path: Path
    file_hash_sha256: str
    dataframe: pd.DataFrame
    row_count: int
    column_count: int


def compute_sha256(file_path: Path) -> str:
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def ingest_file(file_path: str, raw_dir: str = "data/raw") -> IngestionResult:
    src = Path(file_path)
    if not src.exists():
        raise FileNotFoundError(f"Fichier source introuvable : {src}")

    raw_folder = Path(raw_dir)
    raw_folder.mkdir(parents=True, exist_ok=True)
    raw_target = raw_folder / src.name

    # Préservation immuable : on copie uniquement si le fichier n'est pas déjà dans data/raw/
    if src.resolve() != raw_target.resolve():
        shutil.copy2(src, raw_target)

    file_hash = compute_sha256(raw_target)
    suffix = raw_target.suffix.lower()

    if suffix == ".csv":
        df = pd.read_csv(raw_target, keep_default_na=True)
    elif suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(raw_target)
    elif suffix == ".json":
        df = pd.read_json(raw_target)
    else:
        raise ValueError(f"Format non supporté en V1 : {suffix} (attendu : .csv, .xlsx, .json)")

    return IngestionResult(
        source_path=src,
        raw_copy_path=raw_target,
        file_hash_sha256=file_hash,
        dataframe=df,
        row_count=len(df),
        column_count=len(df.columns),
    )
