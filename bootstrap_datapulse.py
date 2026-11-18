import os
import json
import random
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
import numpy as np

# Reproductibilité stricte
SEED = 42
random.seed(SEED)
np.random.seed(SEED)

BASE_DIR = Path(".")

DIRS = [
    "configs",
    "data/reference",
    "data/raw",
    "data/processed",
    "db",
    "reports",
    "src/datapulse",
    "tests",
]

GEMINI_MD = """# Contexte Projet : DataPulse V1

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
"""

CONFIGS = {
    "commerce.yaml": """domain: commerce
version: "1.0"
primary_key: ["date", "produit"]

schema:
  date: { type: date, required: true }
  produit: { type: string, required: true, not_empty: true }
  quantite: { type: int, required: true }
  prix_unitaire: { type: float, required: true }

rules:
  - id: R01
    column: quantite
    check: gt
    value: 0
    severity: HIGH
    message: "La quantité doit être strictement supérieure à 0"
  - id: R02
    column: prix_unitaire
    check: gt
    value: 0
    severity: HIGH
    message: "Le prix unitaire doit être strictement supérieur à 0"
  - id: R03
    column: produit
    check: not_null_or_empty
    severity: HIGH
    message: "Le nom du produit ne peut pas être vide"
  - id: R04
    columns: ["date", "produit"]
    check: unique_combination
    severity: MEDIUM
    message: "Doublon détecté pour le couple (date, produit)"

anomaly_detection:
  columns: ["quantite", "prix_unitaire"]
  zscore_threshold: 3.0
  iqr_multiplier: 1.5
  min_sample_size: 30
""",
    "finance.yaml": """domain: finance
version: "1.0"
primary_key: ["transaction_id"]

schema:
  transaction_id: { type: string, required: true }
  date: { type: date, required: true }
  type: { type: string, required: true }
  montant: { type: float, required: true }

rules:
  - id: F01
    column: montant
    check: gt
    value: 0
    severity: HIGH
    message: "Le montant de la transaction doit être strictement positif"
  - id: F02
    column: date
    check: not_future
    severity: HIGH
    message: "La date de transaction ne peut pas être dans le futur"
  - id: F03
    column: type
    check: in_set
    values: ["credit", "debit", "virement"]
    severity: HIGH
    message: "Type invalide : doit appartenir à {credit, debit, virement}"
  - id: F04
    columns: ["transaction_id"]
    check: unique_combination
    severity: MEDIUM
    message: "Identifiant de transaction en doublon"

anomaly_detection:
  columns: ["montant"]
  zscore_threshold: 3.0
  iqr_multiplier: 1.5
  min_sample_size: 30
""",
    "administration.yaml": """domain: administration
version: "1.0"
primary_key: ["id_demande"]

schema:
  id_demande: { type: string, required: true }
  service: { type: string, required: true }
  delai_jours: { type: int, required: true }
  statut: { type: string, required: true }

rules:
  - id: A01
    column: delai_jours
    check: gte
    value: 0
    severity: HIGH
    message: "Le délai de traitement ne peut pas être négatif"
  - id: A02
    column: statut
    check: in_set
    values: ["en attente", "traite", "rejete"]
    severity: HIGH
    message: "Statut inconnu : doit appartenir à {en attente, traite, rejete}"
  - id: A03
    columns: ["id_demande"]
    check: unique_combination
    severity: HIGH
    message: "Doublon détecté sur l'identifiant de demande"

anomaly_detection:
  columns: ["delai_jours"]
  zscore_threshold: 3.0
  iqr_multiplier: 1.5
  min_sample_size: 30
""",
}

REQUIREMENTS = """pandas>=2.0.0
numpy>=1.24.0
PyYAML>=6.0
matplotlib>=3.7.0
pytest>=7.4.0
"""


def generate_datasets():
    manifest = {}
    base_date = datetime(2026, 1, 1)

    # 1. COMMERCE (200 lignes)
    produits = ["Clavier Mécanique", "Souris Sans Fil", "Écran 27p", "Câble USB-C", "Casque Audio", "Webcam HD"]
    rows_c = []
    for i in range(200):
        d = (base_date + timedelta(days=i // 2)).strftime("%Y-%m-%d")
        p = produits[i % len(produits)]
        q = max(1, int(np.random.normal(15, 4)))
        prix = round(float(np.random.choice([19.99, 29.50, 79.00, 149.90, 299.00])), 2)
        rows_c.append({"date": d, "produit": p, "quantite": q, "prix_unitaire": prix})

    df_c_clean = pd.DataFrame(rows_c)
    df_c_clean.to_csv("data/reference/ventes_clean.csv", index=False)

    df_c_raw = df_c_clean.copy()
    df_c_raw.loc[5, "quantite"] = -3
    df_c_raw.loc[18, "prix_unitaire"] = 0.0
    df_c_raw.loc[42, "produit"] = ""
    df_c_raw.loc[75] = df_c_raw.loc[74]
    df_c_raw.loc[120, "quantite"] = 450
    df_c_raw.to_csv("data/raw/ventes.csv", index=False)

    manifest["commerce"] = [
        {"row": 5, "col": "quantite", "type": "BOTH", "detail": "quantite = -3 (règle R01 + outlier)"},
        {"row": 18, "col": "prix_unitaire", "type": "RULE_VIOLATION", "detail": "prix_unitaire = 0.0 (R02)"},
        {"row": 42, "col": "produit", "type": "RULE_VIOLATION", "detail": "produit vide (R03)"},
        {"row": 75, "col": "date,produit", "type": "RULE_VIOLATION", "detail": "doublon ligne 74 (R04)"},
        {"row": 120, "col": "quantite", "type": "STAT_ANOMALY", "detail": "pic quantite = 450"},
    ]

    # 2. FINANCE (200 lignes)
    rows_f = []
    for i in range(200):
        d = (base_date + timedelta(days=i % 180)).strftime("%Y-%m-%d")
        t = random.choice(["credit", "debit", "virement"])
        m = round(abs(float(np.random.normal(250, 60))), 2)
        rows_f.append({"transaction_id": f"TRX-{1000+i}", "date": d, "type": t, "montant": m})

    df_f_clean = pd.DataFrame(rows_f)
    df_f_clean.to_csv("data/reference/transactions_clean.csv", index=False)

    df_f_raw = df_f_clean.copy()
    df_f_raw.loc[10, "montant"] = -120.50
    df_f_raw.loc[33, "date"] = "2028-05-12"
    df_f_raw.loc[64, "type"] = "especes"
    df_f_raw.loc[99, "transaction_id"] = df_f_raw.loc[98, "transaction_id"]
    df_f_raw.loc[150, "montant"] = 9850.00
    df_f_raw.to_csv("data/raw/transactions.csv", index=False)

    manifest["finance"] = [
        {"row": 10, "col": "montant", "type": "BOTH", "detail": "montant = -120.50 (F01 + outlier)"},
        {"row": 33, "col": "date", "type": "RULE_VIOLATION", "detail": "date future 2028-05-12 (F02)"},
        {"row": 64, "col": "type", "type": "RULE_VIOLATION", "detail": "type = especes (F03)"},
        {"row": 99, "col": "transaction_id", "type": "RULE_VIOLATION", "detail": "doublon TRX (F04)"},
        {"row": 150, "col": "montant", "type": "STAT_ANOMALY", "detail": "montant = 9850.00"},
    ]

    # 3. ADMINISTRATION (200 lignes)
    services = ["État Civil", "Urbanisme", "Fiscalité", "Passeports"]
    rows_a = []
    for i in range(200):
        s = services[i % len(services)]
        delai = max(1, int(np.random.normal(12, 3)))
        statut = random.choice(["en attente", "traite", "rejete"])
        rows_a.append({"id_demande": f"DEM-{5000+i}", "service": s, "delai_jours": delai, "statut": statut})

    df_a_clean = pd.DataFrame(rows_a)
    df_a_clean.to_csv("data/reference/services_publics_clean.csv", index=False)

    df_a_raw = df_a_clean.copy()
    df_a_raw.loc[8, "delai_jours"] = -4
    df_a_raw.loc[51, "statut"] = "archive"
    df_a_raw.loc[88, "id_demande"] = df_a_raw.loc[87, "id_demande"]
    df_a_raw.loc[175, "delai_jours"] = 142
    df_a_raw.to_csv("data/raw/services_publics.csv", index=False)

    manifest["administration"] = [
        {"row": 8, "col": "delai_jours", "type": "BOTH", "detail": "delai_jours = -4 (A01 + outlier)"},
        {"row": 51, "col": "statut", "type": "RULE_VIOLATION", "detail": "statut = archive (A02)"},
        {"row": 88, "col": "id_demande", "type": "RULE_VIOLATION", "detail": "doublon id_demande (A03)"},
        {"row": 175, "col": "delai_jours", "type": "STAT_ANOMALY", "detail": "delai_jours = 142"},
    ]

    with open("data/reference/injected_errors_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)


def main():
    for d in DIRS:
        (BASE_DIR / d).mkdir(parents=True, exist_ok=True)

    (BASE_DIR / "GEMINI.md").write_text(GEMINI_MD, encoding="utf-8")
    (BASE_DIR / "requirements.txt").write_text(REQUIREMENTS, encoding="utf-8")
    (BASE_DIR / "src/datapulse/__init__.py").write_text('"""DataPulse V1 Engine."""\n', encoding="utf-8")

    for filename, content in CONFIGS.items():
        (BASE_DIR / "configs" / filename).write_text(content, encoding="utf-8")

    generate_datasets()
    print("✅ Tout est généré avec succès !")


if __name__ == "__main__":
    main()