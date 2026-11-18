# Contexte Projet : DataPulse V1

## Rôle & Règles de collaboration
Tu es le binôme d'ingénierie logicielle sur **DataPulse V1**.
- **Règle d'or :** Ne jamais modifier les fichiers dans `data/raw/`. Toute sortie transformée va dans `data/processed/`.
- **Distinction stricte :** Séparer les violations de règles métier (`RULE_VIOLATION`) des anomalies statistiques (`STAT_ANOMALY`), et fusionner les doublons sur un même `(row_index, column_name)` en `BOTH` dans `reconciler.py` pour éviter le double comptage.
- **Explicabilité & Reproductibilité :** Chaque finding doit expliquer la colonne, la ligne, la règle ou le score statistique (`ZSCORE` / `IQR`) et la gravité.

## Architecture validée
- `configs/` : Règles métier YAML (`commerce.yaml`, `finance.yaml`, `administration.yaml`)
- `data/reference/` : Jeux propres de référence + `injected_errors_manifest.json`
- `data/raw/` : Fichiers bruts immuables (`ventes.csv`, `transactions.csv`, `services_publics.csv`)
- `data/processed/` : Exports nettoyés
- `db/datapulse.sqlite` : Tables `runs`, `profiling_stats`, `findings`, `benchmark_metrics`
- `reports/` : Rapports Markdown, HTML et graphiques PNG
- `src/datapulse/` : `cli.py`, `config_loader.py`, `ingestion.py`, `profiler.py`, `validator.py`, `detector.py`, `reconciler.py`, `storage.py`, `benchmark.py`, `reporter.py`
- `tests/` : Tests unitaires `pytest`
