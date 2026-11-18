"""Ingestion immuable multi-formats et lecture en streaming par chunks."""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from datapulse.exceptions import IngestionError

SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".json", ".jsonl", ".ndjson", ".parquet"}


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


def prepare_raw_copy(file_path: str, raw_dir: str = "data/raw") -> tuple[Path, Path, str]:
    """Copie la source de façon immuable dans raw_dir et calcule son empreinte SHA-256."""
    src = Path(file_path)
    if not src.exists():
        raise FileNotFoundError(f"Fichier source introuvable : {src}")

    suffix = src.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise IngestionError(
            f"Format non supporté : {suffix}",
            context={"supported": sorted(SUPPORTED_EXTENSIONS)},
        )

    raw_folder = Path(raw_dir)
    raw_folder.mkdir(parents=True, exist_ok=True)
    raw_target = raw_folder / src.name

    if src.resolve() != raw_target.resolve():
        shutil.copy2(src, raw_target)

    return src, raw_target, compute_sha256(raw_target)


def _read_full_dataframe(target: Path) -> pd.DataFrame:
    suffix = target.suffix.lower()
    try:
        if suffix == ".csv":
            return pd.read_csv(target, keep_default_na=True)
        if suffix in {".xlsx", ".xls"}:
            return pd.read_excel(target)
        if suffix == ".json":
            return pd.read_json(target)
        if suffix in {".jsonl", ".ndjson"}:
            return pd.read_json(target, lines=True)
        if suffix == ".parquet":
            return pd.read_parquet(target)
    except ImportError as exc:
        raise IngestionError(
            f"Dépendance manquante pour lire le format {suffix}",
            context={"file": str(target), "details": str(exc)},
        ) from exc
    except Exception as exc:
        raise IngestionError(
            f"Impossible de décoder le fichier {target.name}",
            context={"file": str(target), "error": str(exc)},
        ) from exc

    raise IngestionError(f"Format non supporté : {suffix}")


def ingest_file(file_path: str, raw_dir: str = "data/raw") -> IngestionResult:
    src, raw_target, file_hash = prepare_raw_copy(file_path, raw_dir=raw_dir)
    df = _read_full_dataframe(raw_target)

    return IngestionResult(
        source_path=src,
        raw_copy_path=raw_target,
        file_hash_sha256=file_hash,
        dataframe=df,
        row_count=len(df),
        column_count=len(df.columns),
    )


def iter_file_chunks(
    file_path: str,
    *,
    chunk_size: int = 50_000,
    raw_dir: str = "data/raw",
) -> tuple[Path, str, Iterator[tuple[int, pd.DataFrame]]]:
    """Prépare le fichier brut et retourne un itérateur (offset_ligne, chunk_df)."""
    if chunk_size <= 0:
        raise IngestionError(
            "chunk_size doit être strictement positif", context={"chunk_size": chunk_size}
        )

    _, raw_target, file_hash = prepare_raw_copy(file_path, raw_dir=raw_dir)
    suffix = raw_target.suffix.lower()

    def _chunk_generator() -> Iterator[tuple[int, pd.DataFrame]]:
        offset = 0
        try:
            if suffix == ".csv":
                reader = pd.read_csv(raw_target, keep_default_na=True, chunksize=chunk_size)
                for chunk in reader:
                    yield offset, chunk
                    offset += len(chunk)
                return

            if suffix in {".jsonl", ".ndjson"}:
                json_reader = pd.read_json(raw_target, lines=True, chunksize=chunk_size)
                for chunk in json_reader:
                    yield offset, chunk
                    offset += len(chunk)
                return

            full_df = _read_full_dataframe(raw_target)
            for start in range(0, len(full_df), chunk_size):
                chunk = full_df.iloc[start : start + chunk_size].copy()
                yield start, chunk
        except IngestionError:
            raise
        except Exception as exc:
            raise IngestionError(
                f"Erreur lors de la lecture en streaming de {raw_target.name}",
                context={"offset": offset, "error": str(exc)},
            ) from exc

    return raw_target, file_hash, _chunk_generator()
