"""Dashboard web interactif multi-onglets pour l'observatoire DataPulse V16."""

from __future__ import annotations


def render_dashboard_html() -> str:
    """Retourne la page HTML/CSS/JS du tableau de bord DataPulse."""
    return """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>DataPulse V16 — Observatoire Zero-Trust, RGPD/PII &amp; Chaos Engineering</title>
  <style>
    :root {
      --bg: #080d1a;
      --surface: #0f172a;
      --card: #1e293b;
      --border: #334155;
      --text: #f8fafc;
      --muted: #94a3b8;
      --accent: #38bdf8;
      --accent-2: #818cf8;
      --ok: #10b981;
      --warn: #f59e0b;
      --err: #ef4444;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      background: radial-gradient(circle at top right, #13203c 0%, var(--bg) 55%);
      color: var(--text);
      line-height: 1.5;
      padding: 1.75rem;
      min-height: 100vh;
    }
    .container { max-width: 1260px; margin: 0 auto; }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 1rem;
      margin-bottom: 1.5rem;
      padding: 1.1rem 1.4rem;
      background: rgba(15, 23, 42, 0.85);
      border: 1px solid var(--border);
      border-radius: 0.9rem;
      backdrop-filter: blur(8px);
    }
    .brand { display: flex; align-items: center; gap: 0.85rem; }
    .logo-dot {
      width: 12px; height: 12px; border-radius: 50%;
      background: var(--ok); box-shadow: 0 0 12px var(--ok);
    }
    .badge {
      background: linear-gradient(135deg, rgba(56, 189, 248, 0.2), rgba(129, 140, 248, 0.2));
      color: var(--accent);
      border: 1px solid rgba(56, 189, 248, 0.45);
      padding: 0.25rem 0.75rem;
      border-radius: 999px;
      font-size: 0.82rem;
      font-weight: 700;
    }
    .pill {
      display: inline-block;
      padding: 0.15rem 0.55rem;
      border-radius: 999px;
      font-size: 0.78rem;
      font-weight: 600;
      background: rgba(56, 189, 248, 0.14);
      color: var(--accent);
      border: 1px solid rgba(56, 189, 248, 0.3);
    }
    .pill-warn {
      background: rgba(245, 158, 11, 0.15);
      color: var(--warn);
      border-color: rgba(245, 158, 11, 0.35);
    }
    .tabs {
      display: flex;
      gap: 0.5rem;
      flex-wrap: wrap;
      margin-bottom: 1.5rem;
      border-bottom: 1px solid var(--border);
      padding-bottom: 0.75rem;
    }
    .tab-btn {
      background: var(--surface);
      color: var(--muted);
      border: 1px solid var(--border);
      padding: 0.55rem 1rem;
      border-radius: 0.6rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s ease;
    }
    .tab-btn:hover { color: var(--text); border-color: var(--accent); }
    .tab-btn.active {
      background: var(--accent);
      color: #080d1a;
      border-color: var(--accent);
    }
    .tab-pane { display: none; }
    .tab-pane.active { display: block; }
    .grid-kpi {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
      gap: 1rem;
      margin-bottom: 1.5rem;
    }
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 0.8rem;
      padding: 1.2rem;
    }
    .kpi-label {
      color: var(--muted);
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .kpi-value { font-size: 1.85rem; font-weight: 700; margin-top: 0.3rem; }
    .panel {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 0.85rem;
      padding: 1.4rem;
      margin-bottom: 1.35rem;
    }
    h2 { font-size: 1.1rem; margin-bottom: 0.9rem; color: var(--accent); }
    form.upload-bar {
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
      align-items: center;
    }
    input[type="file"], input[type="text"], input[type="password"], input[type="number"], select {
      background: var(--card);
      color: var(--text);
      border: 1px solid var(--border);
      padding: 0.52rem 0.75rem;
      border-radius: 0.5rem;
    }
    button.primary {
      background: var(--accent);
      color: #080d1a;
      border: none;
      padding: 0.55rem 1.1rem;
      border-radius: 0.5rem;
      font-weight: 700;
      cursor: pointer;
    }
    button.secondary {
      background: var(--card);
      color: var(--text);
      border: 1px solid var(--border);
      padding: 0.52rem 1rem;
      border-radius: 0.5rem;
      font-weight: 600;
      cursor: pointer;
    }
    button.primary:hover, button.secondary:hover { opacity: 0.92; }
    table { width: 100%; border-collapse: collapse; font-size: 0.88rem; margin-top: 0.75rem; }
    th, td {
      text-align: left;
      padding: 0.65rem 0.75rem;
      border-bottom: 1px solid var(--border);
    }
    th { color: var(--muted); font-weight: 600; }
    .status-ok { color: var(--ok); font-weight: 600; }
    .status-alert { color: var(--warn); font-weight: 600; }
    .feedback { margin-top: 0.75rem; font-size: 0.9rem; color: var(--muted); }
    .toolbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.75rem;
      margin-bottom: 0.75rem;
    }
    .chaos-chips {
      display: flex;
      gap: 0.75rem;
      flex-wrap: wrap;
      margin-top: 0.75rem;
      font-size: 0.86rem;
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="brand">
        <span class="logo-dot" id="conn-dot"></span>
        <div>
          <h1 style="font-size: 1.35rem;">DataPulse Observatoire</h1>
          <p style="color: var(--muted); font-size: 0.85rem;">
            Gouvernance Qualité, RGPD/PII, Chaos Engineering, Data Lineage &amp; Zero-Trust
          </p>
        </div>
      </div>
      <div style="display: flex; gap: 0.65rem; align-items: center; flex-wrap: wrap;">
        <span class="pill" id="auth-status-pill">Mode Local</span>
        <input
          type="password"
          id="api-key-input"
          placeholder="X-API-Key (dp_live_...)"
          style="width: 210px;"
        />
        <button type="button" class="secondary" id="save-key-btn">Appliquer Clé</button>
        <a href="/metrics" style="color: var(--ok); text-decoration: none; font-size: 0.9rem;">
          /metrics
        </a>
        <a href="/docs" style="color: var(--accent); text-decoration: none; font-size: 0.9rem;">
          /docs
        </a>
        <span class="badge">v17.0.0</span>
      </div>
    </header>

    <nav class="tabs" role="tablist">
      <button type="button" class="tab-btn active" data-tab="overview">Vue d'ensemble</button>
      <button type="button" class="tab-btn" data-tab="pipelines">Pipelines &amp; SQL</button>
      <button type="button" class="tab-btn" data-tab="synth">Labo Chaos &amp; Synthèse</button>
      <button type="button" class="tab-btn" data-tab="pii">Gouvernance PII &amp; RGPD</button>
      <button type="button" class="tab-btn" data-tab="drift">Drift, Lineage &amp; Alertes</button>
      <button type="button" class="tab-btn" data-tab="security">Sécurité &amp; Audit RBAC</button>
    </nav>

    <!-- ONGLET 1 : VUE D'ENSEMBLE -->
    <div class="tab-pane active" id="tab-overview">
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
        <div class="toolbar">
          <h2 style="margin-bottom: 0;">Historique récent des exécutions</h2>
          <div style="display: flex; gap: 0.5rem;">
            <input type="text" id="filter-domain" placeholder="Filtrer par domaine..." />
            <button type="button" class="secondary" id="refresh-btn">Actualiser</button>
          </div>
        </div>
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

    <!-- ONGLET 2 : PIPELINES & SQL -->
    <div class="tab-pane" id="tab-pipelines">
      <section class="panel">
        <h2>Exécuter un pipeline qualité sur un fichier</h2>
        <form class="upload-bar" id="run-form">
          <input type="file" id="file-input" required />
          <select id="domain-select">
            <option value="auto">auto (Détection automatique)</option>
          </select>
          <label style="display: flex; align-items: center; gap: 0.4rem; font-size: 0.9rem;">
            <input type="checkbox" id="quarantine-check" checked />
            Isoler en quarantaine
          </label>
          <button type="submit" class="primary">Lancer l'analyse</button>
        </form>
        <div id="upload-feedback" class="feedback"></div>
      </section>

      <section class="panel">
        <h2>Auditer une base de données SQL (Lecture seule)</h2>
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
          <button type="submit" class="primary">Auditer SQL</button>
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
          <button type="submit" class="primary">Scanner le répertoire</button>
        </form>
        <div id="watch-feedback" class="feedback"></div>
      </section>
    </div>

    <!-- ONGLET 3 : LABO CHAOS & SYNTHÈSE -->
    <div class="tab-pane" id="tab-synth">
      <section class="panel">
        <h2>Générateur de Données Synthétiques &amp; Chaos Engineering</h2>
        <p style="color: var(--muted); font-size: 0.88rem; margin-bottom: 1rem;">
          Génère des jeux de données réalistes depuis un contrat YAML et injecte des anomalies
          contrôlées pour valider la résilience des pipelines.
        </p>
        <form class="upload-bar" id="synth-form">
          <select id="synth-domain-select">
            <option value="commerce">commerce</option>
            <option value="finance">finance</option>
            <option value="administration">administration</option>
          </select>
          <input
            type="number"
            id="synth-rows"
            value="200"
            min="10"
            max="50000"
            style="width: 115px;"
            title="Nombre de lignes"
          />
          <select id="synth-chaos-rate">
            <option value="0">0% Chaos (Données saines)</option>
            <option value="0.10">10% Chaos</option>
            <option value="0.25" selected>25% Chaos</option>
            <option value="0.50">50% Chaos</option>
          </select>
          <label style="display: flex; align-items: center; gap: 0.4rem; font-size: 0.9rem;">
            <input type="checkbox" id="synth-auto-audit" checked />
            Auditer immédiatement
          </label>
          <button type="submit" class="primary">Générer le Dataset</button>
        </form>
        <div class="chaos-chips">
          <label><input type="checkbox" class="chaos-mode" value="nulls" checked /> Nulls</label>
          <label>
            <input type="checkbox" class="chaos-mode" value="outliers" checked /> Outliers MAD
          </label>
          <label>
            <input type="checkbox" class="chaos-mode" value="negatives" checked /> Négatifs
          </label>
          <label>
            <input type="checkbox" class="chaos-mode" value="invalid_category" /> Catégorie invalide
          </label>
          <label><input type="checkbox" class="chaos-mode" value="duplicates" /> Doublons</label>
          <label>
            <input type="checkbox" class="chaos-mode" value="schema_drift" /> Schema Drift
          </label>
        </div>
        <div id="synth-feedback" class="feedback"></div>
        <div id="synth-preview-box" style="margin-top: 1rem;"></div>
      </section>
    </div>

    <!-- ONGLET 4 : GOUVERNANCE PII & RGPD -->
    <div class="tab-pane" id="tab-pii">
      <section class="panel">
        <h2>Détection &amp; Anonymisation RGPD des Données Personnelles (PII)</h2>
        <p style="color: var(--muted); font-size: 0.88rem; margin-bottom: 1rem;">
          Identifie automatiquement emails, numéros de téléphone, IBAN, cartes bancaires (Luhn)
          et adresses IP, puis génère un export anonymisé.
        </p>
        <form class="upload-bar" id="pii-form">
          <input type="file" id="pii-file-input" required />
          <select id="pii-strategy">
            <option value="partial">Masquage partiel (j***@domaine.com)</option>
            <option value="redact">Caviardage complet (***REDACTED***)</option>
            <option value="hash">Pseudonymisation SHA-256 (pii_...)</option>
          </select>
          <input
            type="text"
            id="pii-columns"
            placeholder="Colonnes (optionnel, ex: email,tel)"
            style="min-width: 230px;"
          />
          <button type="button" class="secondary" id="pii-scan-btn">Scanner PII</button>
          <button type="submit" class="primary">Anonymiser &amp; Exporter</button>
        </form>
        <div id="pii-feedback" class="feedback"></div>
        <div id="pii-results-box" style="margin-top: 1rem;"></div>
      </section>
    </div>

    <!-- ONGLET 5 : DRIFT, LINEAGE & ALERTES -->
    <div class="tab-pane" id="tab-drift">
      <section class="panel">
        <h2>Data Lineage &amp; Certificat d'Audit Cryptographique</h2>
        <form class="upload-bar" id="lineage-form">
          <input type="text" id="lineage-run-id" placeholder="Run ID (ex: 1a5c9d82)" required />
          <button type="submit" class="primary">Inspecter Lineage &amp; Sceau SHA-256</button>
        </form>
        <div id="lineage-feedback" class="feedback"></div>
      </section>

      <section class="panel">
        <h2>Comparateur Inter-Runs (Schema Drift &amp; Data Drift KS / PSI)</h2>
        <form class="upload-bar" id="drift-form">
          <input type="text" id="drift-domain" placeholder="Domaine (ex: commerce)" required />
          <button type="submit" class="primary">Comparer les 2 derniers runs</button>
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
          <button type="submit" class="primary">Tester la notification</button>
        </form>
        <div id="alert-feedback" class="feedback"></div>
      </section>
    </div>

    <!-- ONGLET 6 : SÉCURITÉ & AUDIT RBAC -->
    <div class="tab-pane" id="tab-security">
      <section class="panel">
        <div class="toolbar">
          <h2 style="margin-bottom: 0;">Journal d'Audit de Sécurité Zero-Trust (Rôle Admin)</h2>
          <button type="button" class="secondary" id="load-audit-btn">Charger le journal</button>
        </div>
        <div id="security-feedback" class="feedback"></div>
        <table>
          <thead>
            <tr>
              <th>Horodatage</th>
              <th>Acteur</th>
              <th>Rôle</th>
              <th>Action</th>
              <th>Ressource</th>
              <th>Résultat</th>
            </tr>
          </thead>
          <tbody id="audit-tbody"></tbody>
        </table>
      </section>
    </div>
  </div>

  <script>
    let cachedRuns = [];

    function authHeaders(extra = {}) {
      const key = localStorage.getItem('datapulse_api_key') ||
            localStorage.getItem('datapulse_api_key') || '';
      return key ? { ...extra, 'X-API-Key': key } : { ...extra };
    }

    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById(`tab-${btn.dataset.tab}`).classList.add('active');
      });
    });

    const keyInput = document.getElementById('api-key-input');
    keyInput.value = localStorage.getItem('datapulse_api_key') ||
            localStorage.getItem('datapulse_api_key') || '';
    document.getElementById('save-key-btn').addEventListener('click', async () => {
      const val = keyInput.value.trim();
      if (val) localStorage.setItem('datapulse_api_key', val);
      else localStorage.removeItem('datapulse_api_key');
      await loadHealthStatus();
      await loadDomains();
      await refreshDashboard();
    });

    async function loadHealthStatus() {
      const res = await fetch('/api/health');
      if (!res.ok) return;
      const data = await res.json();
      const pill = document.getElementById('auth-status-pill');
      if (data.auth_enforced) {
        pill.textContent = 'Zero-Trust RBAC Actif';
        pill.className = 'pill pill-warn';
      } else {
        pill.textContent = 'Mode Ouvert Local';
        pill.className = 'pill';
      }
    }

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
        const concrete = items.filter(d => d.domain !== 'auto');
        if (concrete.length > 0) {
          document.getElementById('synth-domain-select').innerHTML = concrete
            .map(d => `<option value="${d.domain}">${d.domain}</option>`)
            .join('');
        }
      }
    }

    function renderRunsTable() {
      const filter = (document.getElementById('filter-domain').value || '').trim().toLowerCase();
      const filtered = filter
        ? cachedRuns.filter(r => String(r.domain).toLowerCase().includes(filter))
        : cachedRuns;
      const tbody = document.getElementById('runs-tbody');
      tbody.innerHTML = filtered.map(r => {
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

    document.getElementById('filter-domain').addEventListener('input', renderRunsTable);
    document.getElementById('refresh-btn').addEventListener('click', refreshDashboard);

    async function refreshDashboard() {
      const [runsRes, quarRes] = await Promise.all([
        fetch('/api/runs?limit=30', { headers: authHeaders() }),
        fetch('/api/quarantine?status=PENDING&limit=100', { headers: authHeaders() })
      ]);
      if (!runsRes.ok || !quarRes.ok) return;
      const runsData = await runsRes.json();
      const quarData = await quarRes.json();
      cachedRuns = runsData.runs || [];

      document.getElementById('kpi-runs').textContent = cachedRuns.length;
      document.getElementById('kpi-rows').textContent =
        cachedRuns.reduce((acc, r) => acc + (r.row_count || 0), 0).toLocaleString();
      document.getElementById('kpi-findings').textContent =
        cachedRuns.reduce((acc, r) => acc + (r.findings_count || 0), 0).toLocaleString();
      document.getElementById('kpi-quarantine').textContent = (quarData.items || []).length;

      if (cachedRuns.length > 0) {
        const linRes = await fetch(
          `/api/runs/${cachedRuns[0].run_id}/lineage`,
          { headers: authHeaders() }
        );
        if (linRes.ok) {
          const lin = await linRes.json();
          document.getElementById('kpi-score').textContent =
            `${lin.quality_score} (${lin.quality_grade})`;
        }
      }
      renderRunsTable();
    }

    document.getElementById('synth-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const feedback = document.getElementById('synth-feedback');
      const box = document.getElementById('synth-preview-box');
      const modes = Array.from(document.querySelectorAll('.chaos-mode:checked')).map(c => c.value);
      const payload = {
        domain: document.getElementById('synth-domain-select').value,
        rows: Number(document.getElementById('synth-rows').value || 200),
        chaos_rate: Number(document.getElementById('synth-chaos-rate').value || 0),
        chaos_modes: modes,
        auto_audit: document.getElementById('synth-auto-audit').checked,
        quarantine: true,
      };
      feedback.style.color = 'var(--muted)';
      feedback.textContent = 'Génération synthétique & injection de chaos en cours...';
      const resp = await fetch('/api/synth/generate', {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(payload),
      });
      const data = await resp.json();
      if (resp.ok) {
        feedback.style.color = 'var(--ok)';
        const auditMsg = data.audit && data.audit.run
          ? ` | Audit Run ${data.audit.run.run_id.slice(0, 8)} : ` +
            `${data.audit.run.findings_count} incident(s) capturé(s)`
          : '';
        feedback.textContent =
          `${data.rows_generated} lignes générées (${data.output_path}) | ` +
          `Fautes injectées : ${data.injected_faults_count}${auditMsg}`;
        const preview = data.preview_rows || [];
        if (preview.length > 0) {
          const headers = Object.keys(preview[0]);
          const thead = headers.map(h => `<th>${h}</th>`).join('');
          const tbody = preview.map(row =>
            `<tr>${headers.map(h => `<td>${row[h]}</td>`).join('')}</tr>`
          ).join('');
          box.innerHTML =
            `<table><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table>`;
        }
        if (data.audit) await refreshDashboard();
      } else {
        feedback.style.color = 'var(--err)';
        feedback.textContent = `Erreur : ${data.detail || 'Échec de génération'}`;
      }
    });

    document.getElementById('pii-scan-btn').addEventListener('click', async () => {
      const fileInput = document.getElementById('pii-file-input');
      const feedback = document.getElementById('pii-feedback');
      const box = document.getElementById('pii-results-box');
      if (!fileInput.files.length) return;

      const formData = new FormData();
      formData.append('file', fileInput.files[0]);
      feedback.style.color = 'var(--muted)';
      feedback.textContent = 'Analyse RGPD / PII en cours...';
      const resp = await fetch('/api/pii/scan', {
        method: 'POST',
        headers: authHeaders(),
        body: formData,
      });
      const data = await resp.json();
      if (resp.ok) {
        feedback.style.color = 'var(--ok)';
        feedback.textContent =
          `Scan terminé : ${data.pii_columns_count} colonne(s) sensible(s), ` +
          `${data.total_pii_cells} cellule(s).`;
        const cols = data.columns || [];
        box.innerHTML = cols.length === 0
          ? '<p style="color: var(--ok);">Aucune donnée personnelle détectée.</p>'
          : '<table><thead><tr><th>Colonne</th><th>Type PII</th>' +
            '<th>Taux</th><th>Aperçu masqué</th></tr></thead><tbody>' +
            cols.map(c =>
              `<tr><td>${c.column}</td>` +
              `<td><span class="pill pill-warn">${c.pii_type}</span></td>` +
              `<td>${c.match_rate_pct}%</td>` +
              `<td><code>${c.sample_masked}</code></td></tr>`
            ).join('') +
            '</tbody></table>';
      } else {
        feedback.style.color = 'var(--err)';
        feedback.textContent = `Erreur : ${data.detail || 'Échec du scan PII'}`;
      }
    });

    document.getElementById('pii-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const fileInput = document.getElementById('pii-file-input');
      const feedback = document.getElementById('pii-feedback');
      const box = document.getElementById('pii-results-box');
      if (!fileInput.files.length) return;

      const formData = new FormData();
      formData.append('file', fileInput.files[0]);
      formData.append('strategy', document.getElementById('pii-strategy').value);
      formData.append('columns', document.getElementById('pii-columns').value);

      feedback.style.color = 'var(--muted)';
      feedback.textContent = 'Anonymisation RGPD en cours...';
      const resp = await fetch('/api/pii/mask', {
        method: 'POST',
        headers: authHeaders(),
        body: formData,
      });
      const data = await resp.json();
      if (resp.ok) {
        feedback.style.color = 'var(--ok)';
        feedback.textContent =
          `Export RGPD généré (${data.output_path}) : ` +
          `${data.masked_cells_total} cellule(s) anonymisée(s).`;
        const preview = data.preview_rows || [];
        if (preview.length > 0) {
          const headers = Object.keys(preview[0]);
          const thead = headers.map(h => `<th>${h}</th>`).join('');
          const tbody = preview.map(row =>
            `<tr>${headers.map(h => `<td>${row[h]}</td>`).join('')}</tr>`
          ).join('');
          box.innerHTML =
            `<table><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table>`;
        }
      } else {
        feedback.style.color = 'var(--err)';
        feedback.textContent = `Erreur : ${data.detail || 'Échec anonymisation'}`;
      }
    });

    document.getElementById('load-audit-btn').addEventListener('click', async () => {
      const feedback = document.getElementById('security-feedback');
      const tbody = document.getElementById('audit-tbody');
      const resp = await fetch('/api/security/audit?limit=30', { headers: authHeaders() });
      const data = await resp.json();
      if (resp.ok) {
        feedback.style.color = 'var(--ok)';
        feedback.textContent = `${data.count} événement(s) de sécurité chargé(s).`;
        tbody.innerHTML = (data.events || []).map(ev => {
          const cls = ev.outcome === 'ALLOWED' ? 'status-ok' : 'status-alert';
          return `<tr>
            <td>${String(ev.timestamp).slice(0, 19)}</td>
            <td>${ev.actor_name}</td>
            <td>${ev.actor_role}</td>
            <td><code>${ev.action}</code></td>
            <td>${ev.resource}</td>
            <td class="${cls}">${ev.outcome}</td>
          </tr>`;
        }).join('');
      } else {
        feedback.style.color = 'var(--err)';
        feedback.textContent = `Accès refusé : ${data.detail || 'Rôle admin requis'}`;
      }
    });

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

    loadHealthStatus();
    loadDomains();
    refreshDashboard();
  </script>

    <!-- v17-catalog-block -->
    <section class="card" style="margin-top: 1.5rem;">
      <div style="display:flex;justify-content:space-between;align-items:center;
                  flex-wrap:wrap;gap:0.75rem;margin-bottom:1rem;">
        <div>
          <h2 style="font-size:1.15rem;color:var(--accent);">
            Data Catalog &amp; SLA Freshness Explorer (v17)
          </h2>
          <p style="color:var(--muted);font-size:0.88rem;">
            Gouvernance des actifs, classification RGPD, SLA et glossaire metier.
          </p>
        </div>
        <div style="display:flex;gap:0.5rem;flex-wrap:wrap;">
          <input id="cat-search-input" type="text" placeholder="Rechercher..."
                 style="padding:0.45rem 0.7rem;border-radius:6px;
                        border:1px solid var(--border);
                        background:var(--surface);color:var(--text);" />
          <button type="button" onclick="loadCatalogData()"
                  style="padding:0.45rem 0.85rem;border-radius:6px;
                         border:1px solid var(--border);
                         background:var(--card);color:var(--text);cursor:pointer;">
            Filtrer
          </button>
          <button type="button" onclick="syncCatalogConfigs()"
                  style="padding:0.45rem 0.85rem;border-radius:6px;border:none;
                         background:var(--accent);color:#080d1a;
                         font-weight:600;cursor:pointer;">
            Sync YAML
          </button>
        </div>
      </div>
      <div id="cat-kpis"
           style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
                  gap:0.75rem;margin-bottom:1rem;"></div>
      <div style="overflow-x:auto;">
        <table style="width:100%;border-collapse:collapse;font-size:0.88rem;">
          <thead>
            <tr style="border-bottom:1px solid var(--border);text-align:left;
                       color:var(--muted);">
              <th style="padding:0.5rem;">Domaine</th>
              <th style="padding:0.5rem;">Classification</th>
              <th style="padding:0.5rem;">Owner</th>
              <th style="padding:0.5rem;">Colonnes / PII</th>
              <th style="padding:0.5rem;">SLA Fraicheur</th>
            </tr>
          </thead>
          <tbody id="cat-assets-tbody"></tbody>
        </table>
      </div>
      <p id="cat-feedback"
         style="margin-top:0.6rem;font-size:0.85rem;color:var(--muted);"></p>
    </section>
    <script>
      (function () {
        function getSavedKey() {
          const inp = document.querySelector(
            'input[placeholder*="dp_"], input[id*="api" i], input[type="password"]'
          );
          const typed = inp ? inp.value.trim() : '';
          if (typed) {
            localStorage.setItem('datapulse_api_key', typed);
            localStorage.setItem('datapulse_api_key', typed);
            localStorage.setItem('apiKey', typed);
            return typed;
          }
          return (
            localStorage.getItem('datapulse_api_key') ||
            localStorage.getItem('datapulse_api_key') ||
            localStorage.getItem('apiKey') ||
            ''
          );
        }

        function catHeaders(extra) {
          const k = getSavedKey();
          const h = Object.assign({}, extra || {});
          if (k) h['X-API-Key'] = k;
          return h;
        }

        window.loadCatalogData = async function () {
          const qEl = document.getElementById('cat-search-input');
          const q = qEl ? qEl.value.trim() : '';
          const kpiBox = document.getElementById('cat-kpis');
          const tbody = document.getElementById('cat-assets-tbody');
          const fb = document.getElementById('cat-feedback');
          if (!kpiBox || !tbody) return;
          try {
            const url = '/api/catalog/assets' + (q ? '?q=' + encodeURIComponent(q) : '');
            const [sumR, astR] = await Promise.all([
              fetch('/api/catalog/summary', { headers: catHeaders() }),
              fetch(url, { headers: catHeaders() })
            ]);
            if (sumR.status === 401 || astR.status === 401) {
              if (fb) {
                fb.textContent = 'Collez votre cle API dp_live_... en haut '
                  + 'puis cliquez sur Appliquer Cle.';
              }
              return;
            }
            if (!sumR.ok || !astR.ok) return;
            const sum = await sumR.json();
            const ast = await astR.json();
            const gloCount = sum.glossary_terms ?? sum.glossary_count ?? 0;
            const st = 'padding:0.65rem;background:var(--surface);'
              + 'border:1px solid var(--border);border-radius:8px;';
            kpiBox.innerHTML =
              '<div style="' + st + '"><div style="color:var(--muted);font-size:0.78rem;">'
              + 'Actifs</div><div style="font-size:1.25rem;font-weight:700;">'
              + (sum.total_assets ?? 0) + '</div></div>'
              + '<div style="' + st + '"><div style="color:var(--muted);font-size:0.78rem;">'
              + 'Conformite SLA</div><div style="font-size:1.25rem;font-weight:700;'
              + 'color:var(--ok);">' + (sum.sla_compliance_pct ?? 100) + '%</div></div>'
              + '<div style="' + st + '"><div style="color:var(--muted);font-size:0.78rem;">'
              + 'SLA Breached</div><div style="font-size:1.25rem;font-weight:700;'
              + 'color:var(--err);">' + (sum.breached_assets ?? 0) + '</div></div>'
              + '<div style="' + st + '"><div style="color:var(--muted);font-size:0.78rem;">'
              + 'Glossaire</div><div style="font-size:1.25rem;font-weight:700;'
              + 'color:var(--accent-2);">' + gloCount + '</div></div>';
            tbody.innerHTML = (ast.assets || []).map(function (a) {
              const col = a.sla_status === 'BREACHED' ? 'var(--err)' : 'var(--ok)';
              return '<tr style="border-bottom:1px solid rgba(51,65,85,0.5);">'
                + '<td style="padding:0.5rem;font-weight:600;">' + a.domain + '</td>'
                + '<td style="padding:0.5rem;">' + a.classification + '</td>'
                + '<td style="padding:0.5rem;color:var(--muted);">' + a.owner + '</td>'
                + '<td style="padding:0.5rem;">' + (a.column_count ?? 0)
                + ' (' + (a.pii_column_count ?? 0) + ' PII)</td>'
                + '<td style="padding:0.5rem;font-weight:600;color:' + col + ';">'
                + a.sla_status + '</td></tr>';
            }).join('');
            if (fb) fb.textContent = 'Catalogue charge (' + (ast.count ?? 0) + ' actifs).';
          } catch (err) {
            if (fb) fb.textContent = 'Erreur reseau : ' + err;
          }
        };

        window.syncCatalogConfigs = async function () {
          const fb = document.getElementById('cat-feedback');
          const r = await fetch('/api/catalog/sync', {
            method: 'POST',
            headers: catHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ config_dir: 'configs' })
          });
          const d = await r.json();
          if (fb) {
            fb.textContent = r.ok
              ? 'Catalogue synchronise (' + d.synced_count + ' domaines).'
              : 'Erreur : ' + (d.detail || 'Acces refuse');
          }
          if (r.ok) window.loadCatalogData();
        };

        document.querySelectorAll('button').forEach(function (btn) {
          if (btn.textContent && btn.textContent.indexOf('Appliquer') !== -1) {
            btn.addEventListener('click', function () {
              getSavedKey();
              setTimeout(window.loadCatalogData, 100);
            });
          }
        });

        window.loadCatalogData();
      })();
    </script>
    <!-- /v17-catalog-block -->

</body>
</html>
"""


