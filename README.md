# DataPulse v20.0.0

**DataPulse** est un moteur d'observabilité, de gouvernance et de contrôle qualité des données orienté contrats (*Data Contracts*), conçu pour auditer des flux **fichiers** (CSV, Excel, JSON, JSONL, Parquet) et **bases de données SQL** en mode batch ou streaming à mémoire constante.

---

## Architecture & Capacités Clés

1. **Contrats de Données Déclaratifs & Auto-Discovery (`v1` – `v6`)**
   - Règles YAML versionnées (`not_null`, `unique`, `gt`, `lt`, `between`, `regex`, `in_set`, `cross_column`).
   - Détection automatique de domaine (`--domain auto`) et inférence de contrats (`datapulse init-config`).
2. **Détection Statistique Robuste & Data Drift (`v4`, `v8`)**
   - Outliers univariés (Z-score robuste basé sur la médiane / MAD) et multivariés (distance de Mahalanobis).
   - Détection de dérives inter-runs : **Schema Drift** et **Data Drift** (test de Kolmogorov-Smirnov à 2 échantillons, *Population Stability Index* PSI, divergence de Jensen-Shannon).
3. **Quarantaine Active & Replay (`v4`)**
   - Séparation automatique des lignes saines et des enregistrements rejetés, export CSV éditable et réinjection contrôlée (`datapulse replay`).
4. **Connecteur SQL Universel en Lecture Seule (`v7`)**
   - Audit direct de tables ou requêtes `SELECT` (SQLite, PostgreSQL, MySQL/MariaDB via SQLAlchemy) avec garde-fou bloquant toute requête destructive (`DROP`, `DELETE`, `UPDATE`, `INSERT`).
5. **Orchestration Autonome & Alerting Temps Réel (`v9` – `v10`)**
   - Surveillance de répertoire *hot-folder* avec déduplication SHA-256 (`datapulse watch`) et planificateur multi-tâches YAML.
   - Notifications temps réel vers **Slack**, **Discord**, **Webhooks JSON signés HMAC-SHA256** et **SMTP**.
6. **Score de Santé Qualité (0–100), Data Lineage & Certificats Signés (`v11`)**
   - Calcul d'un score composite (Grades `A` à `F`), traçabilité cryptographique SHA-256 de bout en bout et génération/vérification de certificats d'audit (`datapulse audit-cert`).
7. **Observabilité Prometheus, Sécurité Zero-Trust RBAC & Docker (`v12` – `v14`)**
   - Exposition native `/metrics` (format OpenMetrics / Prometheus) et sondes Kubernetes `/health/live` & `/health/ready`.
   - Authentification par clés API (`SHA-256` + comparaison temps constant), contrôle d'accès par rôles (`viewer`, `operator`, `admin`) et journal d'audit immuable.

---

## Installation Rapide

```bash
python -m venv .venv
source .venv/bin/activate  # Sous Windows PowerShell : .\.venv\Scripts\Activate.ps1
pip install -e ".[dev,api]"
```

## Utilisation CLI

```bash
datapulse run-all --file data/raw/ventes.csv --domain commerce --quarantine --strict
datapulse init-config --file data/raw/ventes.csv --domain ventes_custom
datapulse run-sql --uri sqlite:///db/source.sqlite --table commandes --domain auto --chunk-size 10000
datapulse lineage --run-id <RUN_ID>
datapulse audit-cert --run-id <RUN_ID> --output reports/certificat.json
datapulse metrics --format prometheus
datapulse key-create --name ci-pipeline --role operator
datapulse serve --host 127.0.0.1 --port 8000
```

## Deploiement Production Docker

```bash
docker compose up -d --build
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
curl http://localhost:8000/metrics
```

## Qualite Logicielle & Tests

```bash
ruff format --check src tests
ruff check src tests
mypy src
pytest --cov=datapulse --cov-report=term-missing
```


## Nouveautes v15.0 - v18.2 (Enterprise & Zero-Terminal)
- **RGPD / PII (v15)** : Detection et anonymisation automatique (partial, hash, redact).
- **Chaos Lab (v16)** : Generateur synthetique et injection controlee d anomalies.
- **Enterprise Data Catalog & SLA (v17)** : Cartographie YAML, extraction PII et glossaire metier.
- **Observatoire Web Zero-Terminal & Durcissement Jury (v18.0 - v18.2)** : Interface 7 onglets, Stories Sante, Replay de Quarantaine, signatures HMAC-SHA256, PSI lisse de Laplace et distance de Wasserstein-1 (95/95 tests).

## Auteur & Licence

- **Auteur & Architecte :** Nugbognon Elfried ZANNOU (`Mr-Pibody`)
- **Licence :** Apache License 2.0 — toute réutilisation ou distribution doit conserver la mention de copyright originale (`LICENSE`).
