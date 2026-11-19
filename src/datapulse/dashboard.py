"""Dashboard web autonome pour l'observatoire DataPulse V14."""

from __future__ import annotations


def render_dashboard_html() -> str:
    """Retourne la page HTML/CSS/JS du tableau de bord DataPulse."""
    return """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>DataPulse V14 — Observatoire Zero-Trust, Lineage, Prometheus &amp; RBAC</title>
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
      flex-wrap: wrap;
      gap: 1rem;
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
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
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
    input[type="file"], input[type="text"], input[type="password"], select {
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
    .feedback { margin-top: 0.75rem; font-size: 0.9rem; color: var(--muted); }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div>
        <h1>DataPulse</h1>
        <p style="color: var(--muted); font-size: 0.9rem;">
          Observatoire Zero-Trust de qualité, Data Lineage, Prometheus et gouvernance RBAC
        </p>
      </div>
      <div style="display: flex; gap: 0.75rem; align-items: center; flex-wrap: wrap;">
        <input
          type="password"
          id="api-key-input"
          placeholder="X-API-Key (optionnel si ouvert)"
          style="width: 220px;"
        />
        <button type="button" id="save-key-btn">Appliquer Clé</button>
        <a href="/metrics" style="color: var(--ok); text-decoration: none;">Prometheus /metrics</a>
        <a href="/docs" style="color: var(--accent); text-decoration: none;">OpenAPI /docs</a>
        <span class="badge">v14.0.0</span>
      </div>
    </header>

    <section class="grid-kpi">
      <div class="card">
        <div class="kpi-label">Score Santé (Dernier)</div>
        <div class="kpi-value" id="kpi-score">-</div>
      </div>
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
      <h2>Exécuter un pipeline sur un fichier</h2>
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
      <div id="upload-feedback" class="feedback"></div>
    </section>

    <section class="panel">
      <h2>Data Lineage &amp; Certificat d'Audit Cryptographique</h2>
      <form class="upload-bar" id="lineage-form">
        <input type="text" id="lineage-run-id" placeholder="Run ID (ex: 1a5c9d82)" required />
        <button type="submit">Inspecter Lineage &amp; Sceau SHA-256</button>
      </form>
      <div id="lineage-feedback" class="feedback"></div>
    </section>

    <section class="panel">
      <h2>Auditer une base de données SQL</h2>
      <form class="upload-bar" id="sql-form">
        <input
          type="text"
          id="sql-uri"
          placeholder="sqlite:///db/source.sqlite"
          style="min-width: 240px;"
          required
        />
        <input type="text" id="sql-table" placeholder="Nom de table (ex: ventes)" required />
        <select id="sql-domain-select">
          <option value="auto">auto (Détection automatique)</option>
        </select>
        <label style="display: flex; align-items: center; gap: 0.4rem; font-size: 0.9rem;">
          <input type="checkbox" id="sql-quarantine-check" checked />
          Isoler en quarantaine
        </label>
        <button type="submit">Auditer SQL</button>
      </form>
      <div id="sql-feedback" class="feedback"></div>
    </section>

    <section class="panel">
      <h2>Orchestrateur Hot-Folder (Scan de Répertoire)</h2>
      <form class="upload-bar" id="watch-form">
        <input
          type="text"
          id="watch-dir"
          placeholder="data/inbox"
          style="min-width: 240px;"
          required
        />
        <select id="watch-domain-select">
          <option value="auto">auto (Détection automatique)</option>
        </select>
        <button type="submit">Scanner le répertoire</button>
      </form>
      <div id="watch-feedback" class="feedback"></div>
    </section>

    <section class="panel">
      <h2>Comparateur Inter-Runs (Schema Drift &amp; Data Drift KS / PSI)</h2>
      <form class="upload-bar" id="drift-form">
        <input type="text" id="drift-domain" placeholder="Domaine (ex: commerce)" required />
        <button type="submit">Comparer les 2 derniers runs</button>
      </form>
      <div id="drift-feedback" class="feedback"></div>
    </section>

    <section class="panel">
      <h2>Notifications &amp; Webhooks Temps Réel (Slack / Discord / JSON)</h2>
      <form class="upload-bar" id="alert-form">
        <select id="alert-channel">
          <option value="webhook">Webhook JSON</option>
          <option value="slack">Slack Webhook</option>
          <option value="discord">Discord Webhook</option>
        </select>
        <input
          type="text"
          id="alert-url"
          placeholder="https://hooks.slack.com/services/..."
          style="min-width: 300px;"
          required
        />
        <button type="submit">Tester la notification</button>
      </form>
      <div id="alert-feedback" class="feedback"></div>
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
    function authHeaders(extra = {}) {
      const key = localStorage.getItem('datapulse_api_key') || '';
      return key ? { ...extra, 'X-API-Key': key } : { ...extra };
    }

    const keyInput = document.getElementById('api-key-input');
    keyInput.value = localStorage.getItem('datapulse_api_key') || '';
    document.getElementById('save-key-btn').addEventListener('click', async () => {
      const val = keyInput.value.trim();
      if (val) localStorage.setItem('datapulse_api_key', val);
      else localStorage.removeItem('datapulse_api_key');
      await loadDomains();
      await refreshDashboard();
    });

    async function loadDomains() {
      const res = await fetch('/api/domains', { headers: authHeaders() });
      if (!res.ok) return;
      const data = await res.json();
      const items = data.domains || [];
      if (items.length > 0) {
        const optionsHtml = items.map(d => {
          const label = d.domain === 'auto'
            ? 'auto — Détection / Inférence automatique'
            : `${d.domain} — ${d.description}`;
          return `<option value="${d.domain}">${label}</option>`;
        }).join('');
        document.getElementById('domain-select').innerHTML = optionsHtml;
        document.getElementById('sql-domain-select').innerHTML = optionsHtml;
        document.getElementById('watch-domain-select').innerHTML = optionsHtml;
      }
    }

    async function refreshDashboard() {
      const [runsRes, quarRes] = await Promise.all([
        fetch('/api/runs?limit=20', { headers: authHeaders() }),
        fetch('/api/quarantine?status=PENDING&limit=100', { headers: authHeaders() })
      ]);
      if (!runsRes.ok || !quarRes.ok) return;
      const runsData = await runsRes.json();
      const quarData = await quarRes.json();
      const runs = runsData.runs || [];

      document.getElementById('kpi-runs').textContent = runs.length;
      document.getElementById('kpi-rows').textContent =
        runs.reduce((acc, r) => acc + (r.row_count || 0), 0).toLocaleString();
      document.getElementById('kpi-findings').textContent =
        runs.reduce((acc, r) => acc + (r.findings_count || 0), 0).toLocaleString();
      document.getElementById('kpi-quarantine').textContent = (quarData.items || []).length;

      if (runs.length > 0) {
        const linRes = await fetch(
          `/api/runs/${runs[0].run_id}/lineage`,
          { headers: authHeaders() }
        );
        if (linRes.ok) {
          const lin = await linRes.json();
          document.getElementById('kpi-score').textContent =
            `${lin.quality_score} (${lin.quality_grade})`;
        }
      }

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
      const resp = await fetch('/api/runs/upload', {
        method: 'POST',
        headers: authHeaders(),
        body: formData,
      });
      const data = await resp.json();
      if (resp.ok) {
        const rid = data.run.run_id.slice(0, 8);
        const cnt = data.run.findings_count;
        feedback.style.color = 'var(--ok)';
        feedback.textContent = `Run ${rid} (${data.run.domain}) terminé : ${cnt} incident(s).`;
        await refreshDashboard();
      } else {
        feedback.style.color = 'var(--err)';
        feedback.textContent = `Erreur : ${data.detail || 'Échec du pipeline'}`;
      }
    });

    document.getElementById('lineage-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const feedback = document.getElementById('lineage-feedback');
      const rid = document.getElementById('lineage-run-id').value.trim();
      const resp = await fetch(
        `/api/runs/${encodeURIComponent(rid)}/lineage`,
        { headers: authHeaders() }
      );
      const data = await resp.json();
      if (resp.ok) {
        feedback.style.color = 'var(--ok)';
        feedback.textContent =
          `Score ${data.quality_score}/100 (Grade ${data.quality_grade}) | ` +
          `Flux: ${data.input_rows} -> ${data.clean_rows} saines ` +
          `(${data.quarantined_rows} quar.) | ` +
          `Sceau SHA256: ${data.certificate_sha256.slice(0, 20)}...`;
      } else {
        feedback.style.color = 'var(--err)';
        feedback.textContent = `Erreur : ${data.detail || 'Lineage introuvable'}`;
      }
    });

    document.getElementById('sql-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const feedback = document.getElementById('sql-feedback');
      const payload = {
        uri: document.getElementById('sql-uri').value,
        table: document.getElementById('sql-table').value,
        domain: document.getElementById('sql-domain-select').value,
        quarantine: document.getElementById('sql-quarantine-check').checked,
      };
      feedback.style.color = 'var(--muted)';
      feedback.textContent = 'Extraction et audit SQL en cours...';
      const resp = await fetch('/api/runs/sql', {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(payload),
      });
      const data = await resp.json();
      if (resp.ok) {
        const rid = data.run.run_id.slice(0, 8);
        const cnt = data.run.findings_count;
        feedback.style.color = 'var(--ok)';
        feedback.textContent =
          `Audit SQL ${rid} (${data.run.domain}) terminé : ${cnt} incident(s).`;
        await refreshDashboard();
      } else {
        feedback.style.color = 'var(--err)';
        feedback.textContent = `Erreur SQL : ${data.detail || 'Échec de connexion'}`;
      }
    });

    document.getElementById('watch-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const feedback = document.getElementById('watch-feedback');
      const payload = {
        watch_dir: document.getElementById('watch-dir').value.trim(),
        domain: document.getElementById('watch-domain-select').value,
        quarantine: true,
      };
      feedback.style.color = 'var(--muted)';
      feedback.textContent = 'Scan du répertoire en cours...';
      const resp = await fetch('/api/watcher/scan', {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(payload),
      });
      const data = await resp.json();
      if (resp.ok) {
        feedback.style.color = 'var(--ok)';
        feedback.textContent = `Scan terminé : ${data.processed_count} fichier(s) traité(s).`;
        await refreshDashboard();
      } else {
        feedback.style.color = 'var(--err)';
        feedback.textContent = `Erreur : ${data.detail || 'Répertoire introuvable'}`;
      }
    });

    document.getElementById('drift-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const feedback = document.getElementById('drift-feedback');
      const domain = document.getElementById('drift-domain').value.trim();
      feedback.style.color = 'var(--muted)';
      feedback.textContent = 'Calcul KS / PSI / Jensen-Shannon en cours...';
      const resp = await fetch(
        `/api/diff?domain=${encodeURIComponent(domain)}`,
        { headers: authHeaders() }
      );
      const data = await resp.json();
      if (resp.ok) {
        const sCount = (data.schema_drifts || []).length;
        const dList = data.data_drifts || [];
        if (dList.length === 0 && sCount === 0) {
          feedback.style.color = 'var(--ok)';
          feedback.textContent = `Aucune dérive détectée sur '${domain}'.`;
        } else {
          feedback.style.color = 'var(--warn)';
          const details = dList.map(d => `[${d.severity}] ${d.explanation}`).join(' | ');
          feedback.textContent = `Schema drift: ${sCount} | Data drift: ${dList.length} ${details}`;
        }
      } else {
        feedback.style.color = 'var(--err)';
        feedback.textContent = `Erreur : ${data.detail || 'Impossible de comparer les runs'}`;
      }
    });

    document.getElementById('alert-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const feedback = document.getElementById('alert-feedback');
      const channel = document.getElementById('alert-channel').value;
      const url = document.getElementById('alert-url').value.trim();
      const body = { domain: 'dashboard_test' };
      if (channel === 'slack') body.slack_webhook_url = url;
      else if (channel === 'discord') body.discord_webhook_url = url;
      else body.webhook_url = url;

      feedback.style.color = 'var(--muted)';
      feedback.textContent = 'Envoi de la notification de test...';
      const resp = await fetch('/api/alerts/test', {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(body),
      });
      const data = await resp.json();
      if (resp.ok) {
        const r = (data.results || [])[0] || {};
        feedback.style.color = r.status === 'SENT' ? 'var(--ok)' : 'var(--err)';
        feedback.textContent = `Canal ${r.channel} : ${r.status} (${r.detail})`;
      } else {
        feedback.style.color = 'var(--err)';
        feedback.textContent = `Erreur : ${data.detail || 'Échec de notification'}`;
      }
    });

    loadDomains();
    refreshDashboard();
  </script>
</body>
</html>
"""
