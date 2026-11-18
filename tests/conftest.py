import pandas as pd
import pytest


@pytest.fixture
def tmp_db_path(tmp_path) -> str:
    return str(tmp_path / "test_datapulse.sqlite")


@pytest.fixture
def commerce_sample_df() -> pd.DataFrame:
    base_quantities = [12, 14, 15, 16, 18]
    rows = [
        {
            "date": f"2026-01-{(i % 28) + 1:02d}",
            "produit": f"Item_{i}",
            "quantite": base_quantities[i % len(base_quantities)],
            "prix_unitaire": 25.0,
        }
        for i in range(40)
    ]
    df = pd.DataFrame(rows)
    # Ligne 0 : violation règle R01 (<=0) + hors bornes IQR => BOTH (1 seul finding)
    df.loc[0, "quantite"] = -5
    # Ligne 1 : violation règle R02 (prix = 0) => RULE_VIOLATION
    df.loc[1, "prix_unitaire"] = 0.0
    # Ligne 2 : anomalie statistique pure => STAT_ANOMALY
    df.loc[2, "quantite"] = 500
    return df
