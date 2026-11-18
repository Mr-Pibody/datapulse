# DataPulse — Project Architecture & Engineering Contract (v5.0.0)

## Règles d'or & Invariants
1. **Zone brute immuable (`data/raw/`) :** Ne jamais modifier les fichiers sources en place. Chaque fichier ingéré reçoit une empreinte SHA-256 et toute sortie transformée va dans `data/processed/`.
2. **Séparation stricte & Réconciliation (`reconciler.py`) :** Distinguer `RULE_VIOLATION` (règles métier YAML) et `STAT_ANOMALY` (Z-Score, IQR, MAD univarié/multivarié), en fusionnant les collisions sur `(row_index, column_name)` en `BOTH` pour éviter le double comptage.
3. **Explicabilité & Quarantaine :** Chaque incident documente la ligne, la colonne, la règle ou le score statistique et la gravité. Les lignes rejetées peuvent être isolées en quarantaine (`quarantine_rows`) puis rejouées (`datapulse replay`).
4. **Persistance SQLite WAL atomique (`storage.py`) :** Toutes les écritures passent par `DataPulseStorage._connect()` (`PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, `commit()`/`rollback()`/`close()` explicites).
5. **Typage strict & Codes de sortie CLI :** `mypy --strict` et `ruff check` à 0 erreur sur `src/datapulse/`. Codes CLI : `0` = succès, `1` = alerte qualité/drift (`--strict`), `2` = erreur de configuration, d'ingestion ou d'exécution.

## Architecture validée
- `configs/` : Règles métier YAML (`commerce.yaml`, `finance.yaml`, `administration.yaml`)
- `data/reference/` : Jeux propres de référence + `injected_errors_manifest.json`
- `data/raw/` : Fichiers bruts immuables
- `data/processed/` : Exports nettoyés et fichiers de quarantaine
- `db/datapulse.sqlite` : Tables `runs`, `profiling_stats`, `findings`, `step_metrics`, `quarantine_rows`
- `reports/` : Rapports Markdown, HTML et graphiques PNG
- `src/datapulse/` : `cli.py`, `config_loader.py`, `ingestion.py`, `profiler.py`, `validator.py`, `detector.py`, `reconciler.py`, `quarantine.py`, `storage.py`, `observability.py`, `benchmark.py`, `reporter.py`, `api.py`, `dashboard.py`, `models.py`, `exceptions.py`
- `tests/` : Suite `pytest` (38 tests, couverture > 90 %)

## Reference Skills Applied
- `improve-codebase-architecture`, `codebase-design`, `code-review` (`mattpocock/skills`)
- `systematic-debugging` (`obra/superpowers`)
- `python-type-safety`, `python-error-handling`, `python-resource-management`, `python-observability`, `python-performance-optimization`, `python-testing-patterns` (`wshobson/agents`)
- `pytest-coverage`, `ruff-recursive-fix` (`github/awesome-copilot`)
- `modern-python` (`trailofbits/skills`)