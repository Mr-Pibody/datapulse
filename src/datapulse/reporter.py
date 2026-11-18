from collections import Counter
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd


def generate_reports(
    run_meta: dict[str, Any],
    profile: dict[str, Any],
    findings: list[dict[str, Any]],
    warnings: list[str],
    df: pd.DataFrame,
    benchmark: dict[str, Any] | None = None,
    reports_dir: str = "reports",
) -> dict[str, str]:
    plt.switch_backend("Agg")
    out_dir = Path(reports_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    run_id = run_meta["run_id"]
    domain = run_meta["domain"]
    base_name = f"{domain}_{run_id[:8]}"

    # 1. Graphique matplotlib
    chart_path = out_dir / f"{base_name}_chart.png"
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))

    cat_counts = Counter(f["category"] for f in findings)
    labels = ["RULE_VIOLATION", "STAT_ANOMALY", "BOTH"]
    values = [cat_counts.get(lbl, 0) for lbl in labels]
    colors = ["#e74c3c", "#f39c12", "#8e44ad"]
    axes[0].bar(labels, values, color=colors)
    axes[0].set_title("Incidents par catégorie (sans double comptage)")
    axes[0].set_ylabel("Nombre de lignes")

    num_cols = [c["column_name"] for c in profile["columns"] if c["mean"] is not None]
    if num_cols:
        col0 = num_cols[0]
        series = pd.to_numeric(df[col0], errors="coerce").dropna()
        axes[1].boxplot(series, orientation="vertical")
        axes[1].set_title(f"Distribution & outliers : {col0}")
        axes[1].set_ylabel(col0)
    else:
        axes[1].text(0.5, 0.5, "Aucune colonne numérique", ha="center", va="center")

    plt.tight_layout()
    plt.savefig(chart_path, dpi=120)
    plt.close(fig)

    # 2. Rapport Markdown
    md_path = out_dir / f"{base_name}.md"
    sha_short = run_meta["file_hash_sha256"][:12]
    md_lines = [
        f"# Rapport DataPulse — Domaine : {domain.capitalize()}",
        "",
        f"- **Run ID :** `{run_id}`",
        f"- **Date d'exécution :** {run_meta['timestamp']}",
        f"- **Fichier source :** `{run_meta['source_file']}` (SHA-256 : `{sha_short}...`)",
        f"- **Volume :** {profile['row_count']} lignes, {profile['column_count']} colonnes "
        f"(Complétude : {profile['completeness_pct']}%)",
        f"- **Performance :** {run_meta['duration_ms']} ms | "
        f"Pic mémoire : {run_meta['peak_memory_kb']} Ko",
        "",
        "## 1. Synthèse des problèmes détectés",
        f"**Total unique : {len(findings)}** "
        f"(Règles métier : {cat_counts.get('RULE_VIOLATION', 0)} | "
        f"Anomalies stats : {cat_counts.get('STAT_ANOMALY', 0)} | "
        f"Les deux : {cat_counts.get('BOTH', 0)})",
        "",
        f"![Graphique d'analyse]({chart_path.name})",
        "",
        "## 2. Profilage des colonnes",
        "| Colonne | Type | Manquants | Moyenne | Médiane | Écart-type | Min | Max |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]

    for c in profile["columns"]:
        md_lines.append(
            f"| {c['column_name']} | {c['dtype']} | {c['null_count']} | "
            f"{c['mean'] if c['mean'] is not None else '-'} | "
            f"{c['median'] if c['median'] is not None else '-'} | "
            f"{c['std'] if c['std'] is not None else '-'} | "
            f"{c['min'] if c['min'] is not None else '-'} | "
            f"{c['max'] if c['max'] is not None else '-'} |"
        )

    md_lines.extend(
        [
            "",
            "## 3. Détail explicable des incidents",
            "| Ligne | Colonne | Valeur | Catégorie | Gravité | Explication |",
            "|---:|---|---|---|---|---|",
        ]
    )

    for f in findings:
        md_lines.append(
            f"| {f['row_index']} | {f['column_name']} | `{f['raw_value']}` | "
            f"**{f['category']}** | {f['severity']} | {f['explanation']} |"
        )

    if warnings:
        md_lines.extend(["", "## 4. Limites statistiques signalées"])
        for w in warnings:
            md_lines.append(f"- {w}")

    if benchmark:
        md_lines.extend(
            [
                "",
                "## 5. Évaluation Benchmark (vs Vérité Terrain)",
                f"- **Précision :** {benchmark['precision'] * 100:.2f}%",
                f"- **Rappel :** {benchmark['recall'] * 100:.2f}%",
                f"- **Score F1 :** {benchmark['f1_score'] * 100:.2f}%",
                f"- **Détails :** TP={benchmark['true_positives']}, "
                f"FP={benchmark['false_positives']}, FN={benchmark['false_negatives']}",
            ]
        )

    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    # 3. Rapport HTML autonome
    html_path = out_dir / f"{base_name}.html"
    rows_html = "\n".join(
        f"<tr><td>{f['row_index']}</td><td>{f['column_name']}</td>"
        f"<td><code>{f['raw_value']}</code></td><td><strong>{f['category']}</strong></td>"
        f"<td>{f['severity']}</td><td>{f['explanation']}</td></tr>"
        for f in findings
    )
    bench_html = ""
    if benchmark:
        bench_html = (
            "<h2>Évaluation Benchmark</h2>"
            f"<p><strong>Précision :</strong> {benchmark['precision'] * 100:.2f}% | "
            f"<strong>Rappel :</strong> {benchmark['recall'] * 100:.2f}% | "
            f"<strong>F1 :</strong> {benchmark['f1_score'] * 100:.2f}% "
            f"(TP={benchmark['true_positives']}, FP={benchmark['false_positives']}, "
            f"FN={benchmark['false_negatives']})</p>"
        )

    html_content = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>DataPulse — {domain}</title>
<style>
  body {{
    font-family: system-ui, -apple-system, sans-serif;
    margin: 2rem auto; max-width: 1050px; color: #1e293b; line-height: 1.5;
  }}
  h1, h2 {{ color: #0f172a; border-bottom: 1px solid #e2e8f0; padding-bottom: .3rem; }}
  .meta {{ background: #f8fafc; padding: 1rem; border-radius: 8px; border: 1px solid #e2e8f0; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 1rem; font-size: 0.92rem; }}
  th, td {{ border: 1px solid #cbd5e1; padding: 8px 10px; text-align: left; }}
  th {{ background: #0f172a; color: #fff; }}
  tr:nth-child(even) {{ background: #f8fafc; }}
  img {{ max-width: 100%; margin: 1rem 0; border: 1px solid #e2e8f0; border-radius: 6px; }}
</style>
</head>
<body>
  <h1>Rapport DataPulse — {domain.capitalize()}</h1>
  <div class="meta">
    <p><strong>Run ID :</strong> <code>{run_id}</code> |
       <strong>Date :</strong> {run_meta["timestamp"]}</p>
    <p><strong>Source :</strong> <code>{run_meta["source_file"]}</code> |
       <strong>Complétude :</strong> {profile["completeness_pct"]}%
       ({profile["row_count"]} lignes)</p>
    <p><strong>Performance :</strong> {run_meta["duration_ms"]} ms |
       <strong>Pic mémoire :</strong> {run_meta["peak_memory_kb"]} Ko</p>
  </div>
  <h2>Synthèse visuelle</h2>
  <img src="{chart_path.name}" alt="Graphiques DataPulse">
  <h2>Incidents détectés ({len(findings)})</h2>
  <table>
    <thead>
      <tr>
        <th>Ligne</th><th>Colonne</th><th>Valeur</th>
        <th>Catégorie</th><th>Gravité</th><th>Explication</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
  {bench_html}
</body>
</html>"""
    html_path.write_text(html_content, encoding="utf-8")

    return {"markdown": str(md_path), "html": str(html_path), "chart": str(chart_path)}
