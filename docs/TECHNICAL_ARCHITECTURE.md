# DataPulse v20.0.0 - Architecture Technique & Guide d'Ingenierie

## 1. But & Problematique Resolue
DataPulse est une plateforme d'observabilite, de gouvernance et de remediation des donnees. Elle empeche la degradation silencieuse des pipelines (valeurs nulles, derives statistiques, fuites de donnees personnelles RGPD, retards SLA) en validant chaque lot entrant avant sa consommation.

## 2. Architecture Generale
- Interface Studio Web (src/datapulse/static/) : HTML5, CSS3 (mode Clair/Sombre) et JavaScript ES6+ avec graphiques SVG natifs.
- API REST & Securite (src/datapulse/api.py) : FastAPI avec controle d'acces Zero-Trust RBAC (viewer, operator, admin), metriques Prometheus (/metrics) et documentation OpenAPI (/docs).
- Moteur d'Audit & Contrats (configs/*.yaml) : validation de schema, regles metier et calcul du score composite sur 6 dimensions (Accuracy, Completeness, Consistency, Timeliness, Validity, Uniqueness).
- Persistance (src/datapulse/storage.py) : base SQLite stockant les executions, anomalies, lignes en quarantaine, catalogue de donnees, cles API et journal d'audit immuable.

## 3. Fonctionnement d'un Pipeline d'Audit
1. Ingestion & Empreinte : lecture CSV, JSONL, SQL (lecture seule) ou Hot-Folder et calcul du hash SHA-256.
2. Controle YAML & Statistiques : verification des contraintes metier, detection d'outliers (MAD / corridor IQR) et calcul de derive (PSI de Laplace, distance de Wasserstein-1, test KS).
3. Quarantaine & Artefacts : isolation des lignes invalides (PENDING -> APPROVED -> REPLAYED) et generation des fichiers _clean.csv, _annotated.csv et rapport HTML.
4. Certification & RGPD : signature cryptographique HMAC-SHA256 de chaque run et detection/anonymisation des colonnes sensibles PII (partial, hash, redact).

## 4. Demarrage & Validation
- Linter : ruff check src/datapulse tests
- Tests : pytest -q
- Serveur : datapulse serve --host 127.0.0.1 --port 8000 --db db/datapulse.sqlite
