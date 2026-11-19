from __future__ import annotations

from pathlib import Path


def render_dashboard_html() -> str:
    """Retourne le HTML complet de l'Observatoire Web DataPulse v18.0."""
    html_file = Path(__file__).resolve().parent / "static" / "index.html"
    if html_file.exists():
        return html_file.read_text("utf-8")
    return "<h1>DataPulse v18.0</h1>"


render_dashboard = render_dashboard_html
