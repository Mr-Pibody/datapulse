from typing import Any

import pandas as pd


def profile_dataframe(df: pd.DataFrame) -> dict[str, Any]:
    total_rows = len(df)
    total_cells = total_rows * len(df.columns) if total_rows > 0 else 0
    total_nulls = int(df.isna().sum().sum())
    completeness = (
        round(((total_cells - total_nulls) / total_cells) * 100, 2) if total_cells > 0 else 100.0
    )

    columns_profile: list[dict[str, Any]] = []

    for col in df.columns:
        series = df[col]
        null_count = int(series.isna().sum())
        col_info: dict[str, Any] = {
            "column_name": col,
            "dtype": str(series.dtype),
            "null_count": null_count,
            "null_pct": round((null_count / total_rows) * 100, 2) if total_rows > 0 else 0.0,
            "mean": None,
            "median": None,
            "std": None,
            "min": None,
            "max": None,
        }

        if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
            clean_s = series.dropna()
            if not clean_s.empty:
                col_info["mean"] = round(float(clean_s.mean()), 4)
                col_info["median"] = round(float(clean_s.median()), 4)
                col_info["std"] = round(float(clean_s.std(ddof=1)), 4) if len(clean_s) > 1 else 0.0
                col_info["min"] = round(float(clean_s.min()), 4)
                col_info["max"] = round(float(clean_s.max()), 4)

        columns_profile.append(col_info)

    return {
        "row_count": total_rows,
        "column_count": len(df.columns),
        "total_nulls": total_nulls,
        "completeness_pct": completeness,
        "columns": columns_profile,
    }
