"""Dashboard web autonome pour l'observatoire DataPulse V6."""

from __future__ import annotations


def render_dashboard_html() -> str:
    """Retourne la page HTML/CSS/JS du tableau de bord DataPulse."""
    return """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>DataPulse V6 — Observatoire Qualité &amp; Anomalies</title>
  <style>
    :root {
      --bg: #0b1120;
      --surface: #111827;
      --card: #1e293b;
      --border: #334155;
      --text: #f1f5f9;
      --muted: #94a3b8;
      --accent: #38bdf8;
      --ok: #10b981;
      --warn: #f59e0b;
      --err: #ef4444;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      padding: 2rem;
    }
    .container { max-width: 1200px; margin: 0 auto; }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 2rem;
      padding-bottom: 1rem;
      border-bottom: 1px solid var(--border);
    }
    .badge {
      background: rgba(56, 189, 248, 0.15);
      color: var(--accent);
      border: 1px solid rgba(56, 189, 248, 0.4);
      padding: 0.25rem 0.75rem;
      border-radius: 999px;
      font-size: 0.85rem;
      font-weight: 600;
    }
    .grid-kpi {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 1rem;
      margin-bottom: 2rem;
    }
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 0.75rem;
      padding: 1.25rem;
    }
    .kpi-label { color: var(--muted); font-size: 0.85rem; text-transform: uppercase; }
    .kpi-value { font-size: 1.85rem; font-weight: 700; margin-top: 0.35rem; }
    .panel {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 0.75rem;
      padding: 1.5rem;
      margin-bottom: 1.5rem;
    }
    h2 { font-size: 1.15rem; margin-bottom: 1rem; color: var(--accent); }
    form.upload-bar {
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
      align-items: center;
    }
    input[type="file"], select {
      background: var(--card);
      color: var(--text);
      border: 1px solid var(--border);
      padding: 0.5rem 0.75rem;
      border-radius: 0.5rem;
    }
    button {
      background: var(--accent);
      color: #0f172a;
      border: none;
      padding: 0.55rem 1.1rem;
      border-radius: 0.5rem;
      font-weight: 600;
      cursor: pointer;
    }
    button:hover { opacity: 0.9; }
    table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
    th, td {
      text-align: left;
      padding: 0.65rem 0.75rem;
      border-bottom: 1px solid var(--border);
    }
    th { color: var(--muted); font-weight: 600; }
    .status-ok { color: var(--ok); font-weight: 600; }
    .status-alert { color: var(--warn); font-weight: 600; }
    #upload-feedback { margin-top: 0.75rem; font-size: 0.9rem; color: var(--muted); }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div>
        <h1>DataPulse</h1>
        <p style="color: var(--muted); font-size: 0.9rem;">
          Observatoire de qualité, réconciliation et gouvernance des données
        </p>
      </div>
      <div style="display: flex; gap: 0.75rem; align-items: center;">
        <a href="/docs" style="color: var(--accent); text-decoration: none;">OpenAPI /docs</a>
        <span class="badge">v6.0.0</span>
      </div>
    </header>

    <section class="grid-kpi">
      <div class="card">
        <div class="kpi-label">Exécutions totales</div>
        <div class="kpi-value" id="kpi-runs">-</div>
      </div>
      <div class="card">
        <div class="kpi-label">Lignes analysées</div>
        <div class="kpi-value" id="kpi-rows">-</div>
      </div>
      <div class="card">
        <div class="kpi-label">Incidents détectés</div>
        <div class="kpi-value" id="kpi-findings">-</div>
      </div>
      <div class="card">
        <div class="kpi-label">Quarantaine active</div>
        <div class="kpi-value" id="kpi-quarantine">-</div>
      </div>
    </section>

    <section class="panel">
      <h2>Exécuter un pipeline d'analyse</h2>
      <form class="upload-bar" id="run-form">
        <input type="file" id="file-input" required />
        <select id="domain-select">
          <option value="auto">auto (Détection automatique)</option>
        </select>
        <label style="display: flex; align-items: center; gap: 0.4rem; font-size: 0.9rem;">
          <input type="checkbox" id="quarantine-check" checked />
          Isoler en quarantaine
        </label>
        <button type="submit">Lancer l'analyse</button>
      </form>
      <div id="upload-feedback"></div>
    </section>

    <section class="panel">
      <h2>Historique récent des exécutions</h2>
      <table>
        <thead>
          <tr>
            <th>Run ID</th>
            <th>Domaine</th>
            <th>Lignes</th>
            <th>Incidents</th>
            <th>Durée (ms)</th>
            <th>Statut</th>
          </tr>
        </thead>
        <tbody id="runs-tbody"></tbody>
      </table>
    </section>
  </div>

  <script>
    async function loadDomains() {
      const res = await fetch('/api/domains');
      const data = await res.json();
      const select = document.getElementById('domain-select');
      const items = data.domains || [];
      if (items.length > 0) {
        select.innerHTML = items.map(d => {
          const label = d.domain === 'auto'
            ? 'auto — Détection / Inférence automatique'
            : `${d.domain} — ${d.description}`;
          return `<option value="${d.domain}">${label}</option>`;
        }).join('');
      }
    }

    async function refreshDashboard() {
      const [runsRes, quarRes] = await Promise.all([
        fetch('/api/runs?limit=20'),
        fetch('/api/quarantine?status=PENDING&limit=100')
      ]);
      const runsData = await runsRes.json();
      const quarData = await quarRes.json();
      const runs = runsData.runs || [];

      document.getElementById('kpi-runs').textContent = runs.length;
      document.getElementById('kpi-rows').textContent =
        runs.reduce((acc, r) => acc + (r.row_count || 0), 0).toLocaleString();
      document.getElementById('kpi-findings').textContent =
        runs.reduce((acc, r) => acc + (r.findings_count || 0), 0).toLocaleString();
      document.getElementById('kpi-quarantine').textContent = (quarData.items || []).length;

      const tbody = document.getElementById('runs-tbody');
      tbody.innerHTML = runs.map(r => {
        const cls = r.status === 'SUCCESS' ? 'status-ok' : 'status-alert';
        return `<tr>
          <td><code>${r.run_id.slice(0, 8)}</code></td>
          <td>${r.domain}</td>
          <td>${r.row_count}</td>
          <td>${r.findings_count}</td>
          <td>${Number(r.duration_ms).toFixed(2)}</td>
          <td class="${cls}">${r.status}</td>
        </tr>`;
      }).join('');
    }

    document.getElementById('run-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const fileInput = document.getElementById('file-input');
      const feedback = document.getElementById('upload-feedback');
      if (!fileInput.files.length) return;

      const formData = new FormData();
      formData.append('file', fileInput.files[0]);
      formData.append('domain', document.getElementById('domain-select').value);
      formData.append('quarantine', document.getElementById('quarantine-check').checked);

      feedback.style.color = 'var(--muted)';
      feedback.textContent = 'Exécution du pipeline en cours...';
      const resp = await fetch('/api/runs/upload', { method: 'POST', body: formData });
      const data = await resp.json();
      if (resp.ok) {
        feedback.style.color = 'var(--ok)';
        feedback.textContent =
          `Run ${data.run.run_id.slice(0, 8)} (${data.run.domain}) terminé : ${data.run.findings_count} incident(s).`;
        await refreshDashboard();
      } else {
        feedback.style.color = 'var(--err)';
        feedback.textContent = `Erreur : ${data.detail || 'Échec du pipeline'}`;
      }
    });

    loadDomains();
    refreshDashboard();
  </script>
</body>
</html>
"""
