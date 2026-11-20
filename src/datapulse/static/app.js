(() => {
  const LS_KEY = "datapulse_api_key";
  let cachedRuns = [];
  let cachedDomains = [];
  let activeModalRunId = "";
  let autoBootTried = false;

  const TAB_TITLES = {
    overview: ["Cockpit Exécutif & Santé des Données", "Surveillance temps réel des contrats, dérives statistiques, conformité RGPD et certificats HMAC-SHA256."],
    pipelines: ["Studio d'Audit Multi-Sources (CSV, JSONL, SQL, Hot-Folder)", "Exécutez le moteur d'analyse sur vos fichiers locaux, tables SQLite/SQL ou datasets embarqués."],
    quarantine: ["Quarantaine Active & Replay Transactionnel", "Arbitrez les lignes isolées par les règles métier et réintégrez les lignes validées."],
    catalog: ["Enterprise Data Catalog, SLA & Glossaire Métier", "Cartographie des contrats YAML, classification PII et dictionnaire des indicateurs."],
    pii: ["Conformité RGPD & Pseudonymisation PII", "Détection automatique des données personnelles sensibles et export anonymisé."],
    chaos: ["Chaos Engineering & Générateur Synthétique", "Testez la résilience du pipeline par injection contrôlée d'anomalies."],
    governance: ["Certificats HMAC-SHA256, Drift Statistique & Sécurité RBAC", "Vérification cryptographique des preuves d'audit, dérive PSI/Wasserstein et clés API."]
  };

  const $ = (id) => document.getElementById(id);
  const getKey = () => localStorage.getItem(LS_KEY) || "";
  const setKey = (k) => {
    if (k) localStorage.setItem(LS_KEY, k.trim());
    else localStorage.removeItem(LS_KEY);
  };
  const headers = (extra = {}) => {
    const k = getKey();
    return k ? { ...extra, "X-API-Key": k } : { ...extra };
  };

  function showToast(msg) {
    const t = $("toast-box");
    if (!t) return;
    t.textContent = msg;
    t.classList.remove("hidden");
    clearTimeout(window._toastTimer);
    window._toastTimer = setTimeout(() => t.classList.add("hidden"), 3200);
  }

  async function api(path, opts = {}) {
    const resp = await fetch(path, { ...opts, headers: headers(opts.headers || {}) });
    const text = await resp.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
    if (!resp.ok) throw new Error(data.detail || `Erreur HTTP ${resp.status}`);
    return data;
  }

  function setFeedback(elId, msg, isErr = false) {
    const el = $(elId);
    if (!el) return;
    el.textContent = msg;
    el.className = "feedback " + (isErr ? "err" : "ok");
  }

  function switchTab(tabId) {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === tabId));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${tabId}`));
    if (TAB_TITLES[tabId]) {
      $("page-title").textContent = TAB_TITLES[tabId][0];
      $("page-subtitle").textContent = TAB_TITLES[tabId][1];
    }
  }

  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  // ==================== COMMAND PALETTE (CTRL + K) ====================
  const COMMANDS = [
    { label: "◈ Aller au Cockpit Exécutif", tag: "Navigation", action: () => switchTab("overview") },
    { label: "⚡ Ouvrir le Studio d'Audit (CSV & SQL)", tag: "Navigation", action: () => switchTab("pipelines") },
    { label: "🛡️ Ouvrir la Quarantaine & Replay", tag: "Navigation", action: () => switchTab("quarantine") },
    { label: "📚 Ouvrir le Data Catalog & SLA", tag: "Navigation", action: () => switchTab("catalog") },
    { label: "🔒 Ouvrir Conformité RGPD / PII", tag: "Navigation", action: () => switchTab("pii") },
    { label: "🧪 Ouvrir le Chaos Lab", tag: "Navigation", action: () => switchTab("chaos") },
    { label: "🔏 Vérifier les Certificats HMAC-SHA256", tag: "Navigation", action: () => switchTab("governance") },
    { label: "↻ Actualiser toutes les métriques", tag: "Action", action: () => refreshAll() },
  ];

  function openCmdPalette() {
    $("cmd-modal").classList.remove("hidden");
    $("cmd-input").value = "";
    renderCmdList("");
    $("cmd-input").focus();
  }
  function renderCmdList(q) {
    const query = q.trim().toLowerCase();
    const domItems = cachedDomains.map((d) => {
      const name = typeof d === "string" ? d : d.domain || d.name;
      return { label: `Filtrer le Cockpit sur le domaine « ${name} »`, tag: "Domaine", action: () => { switchTab("overview"); window.filterByDomainStory(name); } };
    });
    const all = [...COMMANDS, ...domItems].filter((c) => !query || c.label.toLowerCase().includes(query));
    $("cmd-list").innerHTML = all.map((c, idx) =>
      `<div class="cmd-item" data-idx="${idx}"><span>${c.label}</span><span class="tag tag-info">${c.tag}</span></div>`
    ).join("");
    $("cmd-list").querySelectorAll(".cmd-item").forEach((el, idx) => {
      el.addEventListener("click", () => { $("cmd-modal").classList.add("hidden"); all[idx].action(); });
    });
  }
  $("btn-cmd-palette").addEventListener("click", openCmdPalette);
  $("cmd-close-bg").addEventListener("click", () => $("cmd-modal").classList.add("hidden"));
  $("cmd-input").addEventListener("input", (e) => renderCmdList(e.target.value));
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openCmdPalette(); }
    if (e.key === "Escape") { $("cmd-modal").classList.add("hidden"); $("run-modal").classList.add("hidden"); }
  });

  // ==================== SESSION EVALUATEUR SANS FRICTION ====================
  async function activateEvaluatorSession(silent = false) {
    try {
      const res = await api("/api/demo/bootstrap", { method: "POST" });
      if (res.api_key) {
        setKey(res.api_key);
        $("api-key-input").value = res.api_key;
      }
      if (!silent) showToast("✅ Session Évaluateur activée");
      await refreshAll();
    } catch (err) {
      $("auth-pill").textContent = "Clé API requise";
      $("auth-dot").classList.remove("ok");
    }
  }

  $("btn-apply-key").addEventListener("click", async () => {
    setKey($("api-key-input").value);
    await refreshAll();
  });
  $("btn-evaluator-session").addEventListener("click", () => activateEvaluatorSession(false));

  // ==================== CHARGEMENT COCKPIT AGREGÉ ====================
  async function refreshAll() {
    $("api-key-input").value = getKey();
    try {
      const [cockpit, domData, runsData, assetsData] = await Promise.all([
        api("/api/analytics/cockpit"),
        api("/api/domains"),
        api("/api/runs?limit=40"),
        api("/api/catalog/assets"),
      ]);

      $("auth-pill").textContent = "Zero-Trust RBAC Actif";
      $("auth-dot").classList.add("ok");

      cachedDomains = domData.domains || [];
      cachedRuns = runsData.runs || [];

      const k = cockpit.kpis || {};
      $("kpi-runs").textContent = k.total_runs ?? cachedRuns.length;
      $("kpi-rows").textContent = Number(k.total_rows ?? 0).toLocaleString("fr-FR");
      $("kpi-findings").textContent = Number(k.total_findings ?? 0).toLocaleString("fr-FR");
      $("kpi-quarantine").textContent = k.pending_quarantine ?? 0;
      $("nav-quar-badge").textContent = k.pending_quarantine ?? 0;
      $("kpi-sla").textContent = `${k.sla_compliance_pct ?? 100}%`;
      $("kpi-assets").textContent = `${k.total_assets ?? 0} actifs (${k.sla_breached ?? 0} alerte)`;
      $("throughput-pill").textContent = `Débit moyen : ${Number(k.avg_throughput_rows_sec || 0).toLocaleString("fr-FR")} lignes/s`;

      renderRadialScore(cockpit.latest_score || {});
      renderScoreTrendSVG(cockpit.score_series || []);
      renderDomainStories(assetsData.assets || []);
      populateDomainSelects(cachedDomains);
      populateRunSelect(cachedRuns);
      renderRunsTable();

      await Promise.all([
        loadDemoDatasets(),
        loadCatalogAssets(),
        loadGlossary(),
        loadQuarantine(),
        loadKeysAndAudit(),
      ]);
    } catch {
      if (!autoBootTried) {
        autoBootTried = true;
        await activateEvaluatorSession(true);
        return;
      }
      $("auth-pill").textContent = "Clé API requise";
      $("auth-dot").classList.remove("ok");
    }
  }

  function renderRadialScore(info) {
    const score = Math.max(0, Math.min(100, Number(info.score ?? 100)));
    const grade = info.grade || "A";
    const circ = 414.69;
    const offset = circ - (score / 100) * circ;
    const ring = $("radial-progress");
    if (ring) {
      ring.style.strokeDashoffset = String(offset);
      ring.style.stroke = score >= 85 ? "#10b981" : score >= 65 ? "#f59e0b" : "#ef4444";
    }
    $("hero-score-val").textContent = score.toFixed(1);
    $("hero-grade-badge").textContent = `GRADE ${grade}`;
    $("hero-score-domain").textContent = info.domain
      ? `Domaine « ${info.domain} » · Run ${String(info.run_id || "").slice(0, 8)}`
      : "Score composite certifié";

    const b = info.breakdown || {};
    $("score-pillars-grid").innerHTML = `
      <div class="pillar-box"><span>Complétude</span><strong>${Number(b.completeness ?? 100).toFixed(0)}%</strong></div>
      <div class="pillar-box"><span>Validité</span><strong>${Number(b.validity ?? 100).toFixed(0)}%</strong></div>
      <div class="pillar-box"><span>Stabilité Stat.</span><strong>${Number(b.statistical_stability ?? 100).toFixed(0)}%</strong></div>
      <div class="pillar-box"><span>Stabilité Schéma</span><strong>${Number(b.schema_stability ?? 100).toFixed(0)}%</strong></div>
    `;
  }

  function renderScoreTrendSVG(series) {
    const box = $("score-trend-chart");
    if (!box) return;
    if (!series.length) {
      box.innerHTML = `<p class="muted">Exécutez un premier audit pour afficher la courbe d'évolution.</p>`;
      return;
    }
    const w = 720, h = 210, padL = 38, padR = 20, padT = 20, padB = 32;
    const plotW = w - padL - padR;
    const plotH = h - padT - padB;
    const n = series.length;
    const pts = series.map((item, idx) => {
      const x = padL + (n === 1 ? plotW / 2 : (idx / (n - 1)) * plotW);
      const val = Math.max(0, Math.min(100, Number(item.quality_score || 0)));
      const y = padT + plotH - (val / 100) * plotH;
      return { x, y, val, domain: item.domain, id: String(item.run_id || "").slice(0, 6) };
    });

    const linePath = pts.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" ");
    const areaPath = `${linePath} L ${pts[pts.length - 1].x.toFixed(1)} ${(padT + plotH).toFixed(1)} L ${pts[0].x.toFixed(1)} ${(padT + plotH).toFixed(1)} Z`;

    const gridLines = [0, 25, 50, 75, 100].map((v) => {
      const y = padT + plotH - (v / 100) * plotH;
      return `<line x1="${padL}" y1="${y}" x2="${w - padR}" y2="${y}" stroke="rgba(148,163,184,0.12)" stroke-dasharray="4 4" />
              <text x="${padL - 8}" y="${y + 4}" fill="#64748b" font-size="10" text-anchor="end">${v}</text>`;
    }).join("");

    const circles = pts.map((p) => `
      <circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="4.5" fill="#10b981" stroke="#070b14" stroke-width="2">
        <title>${p.domain} (${p.id}) : ${p.val.toFixed(1)}/100</title>
      </circle>
      <text x="${p.x.toFixed(1)}" y="${h - 10}" fill="#94a3b8" font-size="9" text-anchor="middle">${p.domain.slice(0, 6)}</text>
    `).join("");

    box.innerHTML = `
      <svg viewBox="0 0 ${w} ${h}" style="width:100%;height:auto;max-height:225px">
        <defs>
          <linearGradient id="dpAreaGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#10b981" stop-opacity="0.34" />
            <stop offset="100%" stop-color="#10b981" stop-opacity="0.0" />
          </linearGradient>
        </defs>
        ${gridLines}
        <path d="${areaPath}" fill="url(#dpAreaGrad)" />
        <path d="${linePath}" fill="none" stroke="#10b981" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" />
        ${circles}
      </svg>
    `;
  }

  function renderDomainStories(assets) {
    const bar = $("domain-stories-bar");
    if (!bar || !assets.length) return;
    const allChip = `
      <div class="story-item" onclick="window.filterByDomainStory('')">
        <div class="story-ring ok"><div class="story-inner">ALL</div></div>
        <span class="story-name">Tous</span>
        <span class="story-status">${cachedRuns.length} runs</span>
      </div>`;
    bar.innerHTML = allChip + assets.map((a) => {
      const healthy = a.sla_status === "HEALTHY";
      return `
        <div class="story-item" onclick="window.filterByDomainStory('${a.domain}')">
          <div class="story-ring ${healthy ? "ok" : "warn"}">
            <div class="story-inner">${String(a.domain || "DP").slice(0, 3)}</div>
          </div>
          <span class="story-name">${a.domain}</span>
          <span class="story-status">${healthy ? "SLA OK" : "SLA Alerte"}</span>
        </div>`;
    }).join("");
  }

  window.filterByDomainStory = (domain) => {
    $("filter-domain-select").value = domain;
    renderRunsTable();
    showToast(domain ? `Filtre actif : ${domain}` : "Tous les domaines affichés");
  };

  function populateDomainSelects(domains) {
    const names = domains.map((d) => (typeof d === "string" ? d : d.domain || d.name)).filter(Boolean);
    ["demo-dataset-domain", "upload-domain-select", "sql-domain-select", "watch-domain-select"].forEach((id) => {
      const sel = $(id);
      if (!sel) return;
      const cur = sel.value;
      sel.innerHTML = `<option value="auto">auto — Détection / Inférence automatique</option>` +
        names.map((n) => `<option value="${n}">${n}</option>`).join("");
      if ([...sel.options].some((o) => o.value === cur)) sel.value = cur;
    });
    ["filter-domain-select", "quar-domain-filter"].forEach((id) => {
      const sel = $(id);
      if (!sel) return;
      const cur = sel.value;
      sel.innerHTML = `<option value="">Tous les domaines</option>` +
        names.map((n) => `<option value="${n}">${n}</option>`).join("");
      if ([...sel.options].some((o) => o.value === cur)) sel.value = cur;
    });
    ["chaos-domain", "drift-domain-select"].forEach((id) => {
      const sel = $(id);
      if (!sel) return;
      const cur = sel.value;
      sel.innerHTML = names.map((n) => `<option value="${n}">${n}</option>`).join("");
      if ([...sel.options].some((o) => o.value === cur)) sel.value = cur;
    });
    const glSel = $("gl-domain");
    if (glSel) {
      glSel.innerHTML = `<option value="global">global — Transverse entreprise</option>` +
        names.map((n) => `<option value="${n}">${n}</option>`).join("");
    }
  }

  function populateRunSelect(runs) {
    const sel = $("lineage-run-select");
    if (!sel) return;
    sel.innerHTML = runs.map((r) =>
      `<option value="${r.run_id}">${r.run_id.slice(0, 8)} — ${r.domain} (${r.row_count} lignes, ${r.findings_count} incidents)</option>`
    ).join("");
  }

  function renderRunsTable() {
    const filter = ($("filter-domain-select").value || "").trim().toLowerCase();
    const rows = cachedRuns.filter((r) => !filter || String(r.domain).toLowerCase() === filter);
    const tbody = $("runs-tbody");
    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="8" class="empty-cell">Aucune exécution pour ce filtre.</td></tr>`;
      return;
    }
    tbody.innerHTML = rows.map((r) => {
      const durSec = Math.max(Number(r.duration_ms || 0) / 1000, 0.001);
      const tput = Math.round((Number(r.row_count) || 0) / durSec);
      return `
        <tr>
          <td><code>${r.run_id.slice(0, 8)}</code></td>
          <td><span class="tag tag-info">${r.domain}</span></td>
          <td>${(r.source_file || "").split(/[\\/]/).pop()}</td>
          <td>${r.row_count}</td>
          <td>${r.findings_count > 0 ? `<span class="tag tag-warn">${r.findings_count}</span>` : `<span class="tag tag-emerald">0</span>`}</td>
          <td><small>${tput.toLocaleString("fr-FR")} l/s</small></td>
          <td><span class="tag tag-emerald">${r.status}</span></td>
          <td><button class="btn btn-secondary btn-xs" onclick="window.openRunModal('${r.run_id}')">Inspecter</button></td>
        </tr>`;
    }).join("");
  }

  $("filter-domain-select").addEventListener("change", renderRunsTable);
  $("btn-refresh-all").addEventListener("click", refreshAll);

  // ==================== MODALE RUN & TELECHARGEMENTS ====================
  window.openRunModal = async (runId) => {
    activeModalRunId = runId;
    $("run-modal").classList.remove("hidden");
    $("modal-run-title").textContent = `Exécution ${runId.slice(0, 8)}`;
    $("modal-run-sub").textContent = "Chargement des détails...";
    $("modal-run-body").innerHTML = "";
    try {
      const det = await api(`/api/runs/${runId}`);
      const meta = det.run || det;
      const findings = det.findings || [];
      $("modal-run-title").textContent = `Exécution ${runId.slice(0, 8)} — Domaine « ${meta.domain} »`;
      $("modal-run-sub").textContent = `Source : ${meta.source_file} | Lignes : ${meta.row_count} | Empreinte : ${(meta.file_hash_sha256 || "").slice(0, 20)}...`;
      let html = `<h3>Règles &amp; Anomalies Détectées (${findings.length})</h3>`;
      if (!findings.length) {
        html += `<p class="feedback ok">✅ 100 % des enregistrements sont conformes au contrat YAML.</p>`;
      } else {
        html += `<div class="table-wrap"><table><thead><tr><th>Règle</th><th>Colonne</th><th>Sévérité</th><th>Détail</th></tr></thead><tbody>` +
          findings.slice(0, 50).map((f) => `
            <tr>
              <td><code>${f.rule_id || f.category || "RULE"}</code></td>
              <td>${f.column_name || "—"}</td>
              <td><span class="tag tag-warn">${f.severity || "MEDIUM"}</span></td>
              <td>${f.explanation || f.message || ""}</td>
            </tr>`).join("") + `</tbody></table></div>`;
      }
      $("modal-run-body").innerHTML = html;
    } catch (err) {
      $("modal-run-body").innerHTML = `<p class="feedback err">${err.message}</p>`;
    }
  };

  async function downloadRunArtifact(kind, filename) {
    if (!activeModalRunId) return;
    try {
      const resp = await fetch(`/api/runs/${activeModalRunId}/artifacts/${kind}`, { headers: headers() });
      if (!resp.ok) throw new Error(await resp.text());
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      if (kind === "report") window.open(url, "_blank");
      else {
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        a.click();
      }
      showToast(`✅ Artefact prêt : ${filename}`);
    } catch (err) {
      showToast("⚠️ " + err.message);
    }
  }

  $("dl-report-btn").addEventListener("click", () => downloadRunArtifact("report", `rapport_${activeModalRunId.slice(0, 8)}.html`));
  $("dl-clean-btn").addEventListener("click", () => downloadRunArtifact("clean", `${activeModalRunId.slice(0, 8)}_clean.csv`));
  $("dl-annotated-btn").addEventListener("click", () => downloadRunArtifact("annotated", `${activeModalRunId.slice(0, 8)}_annotated.csv`));
  $("btn-modal-close").addEventListener("click", () => $("run-modal").classList.add("hidden"));
  $("modal-close-bg").addEventListener("click", () => $("run-modal").classList.add("hidden"));

  // ==================== STUDIO D'AUDIT ====================
  async function loadDemoDatasets() {
    try {
      const data = await api("/api/demo/datasets");
      const sel = $("demo-dataset-select");
      sel.innerHTML = (data.datasets || []).map((d) =>
        `<option value="${d.filename}" data-domain="${d.suggested_domain}">${d.filename} — ${d.label} (${d.size_kb} Ko)</option>`
      ).join("");
      if (sel.options.length > 0) $("demo-dataset-domain").value = sel.options[0].dataset.domain || "auto";
    } catch {}
  }

  $("demo-dataset-select").addEventListener("change", (e) => {
    const opt = e.target.selectedOptions[0];
    if (opt && opt.dataset.domain) $("demo-dataset-domain").value = opt.dataset.domain;
  });

  $("demo-dataset-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    setFeedback("demo-dataset-feedback", "Audit en cours...");
    try {
      const res = await api("/api/demo/run-dataset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename: $("demo-dataset-select").value,
          domain: $("demo-dataset-domain").value,
          quarantine: $("demo-dataset-quarantine").checked,
        }),
      });
      const r = res.run || {};
      setFeedback("demo-dataset-feedback", `Audit ${r.run_id?.slice(0, 8)} terminé : ${r.findings_count} incident(s).`);
      await refreshAll();
      if (r.run_id) window.openRunModal(r.run_id);
    } catch (err) {
      setFeedback("demo-dataset-feedback", err.message, true);
    }
  });

  $("upload-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const file = $("upload-file").files[0];
    if (!file) return;
    setFeedback("upload-feedback", "Analyse du fichier...");
    const fd = new FormData();
    fd.append("file", file);
    fd.append("domain", $("upload-domain-select").value);
    fd.append("quarantine", $("upload-quarantine").checked);
    try {
      const res = await api("/api/runs/upload", { method: "POST", body: fd });
      const r = res.run || {};
      setFeedback("upload-feedback", `Run ${r.run_id?.slice(0, 8)} terminé : ${r.findings_count} incident(s).`);
      await refreshAll();
      if (r.run_id) window.openRunModal(r.run_id);
    } catch (err) {
      setFeedback("upload-feedback", err.message, true);
    }
  });

  $("btn-sql-tables").addEventListener("click", async () => {
    try {
      const res = await api(`/api/sql/tables?uri=${encodeURIComponent($("sql-uri").value.trim())}`);
      const tables = res.tables || [];
      if (tables.length) $("sql-table").value = tables[0];
      setFeedback("sql-feedback", `Tables détectées : ${tables.join(", ") || "aucune"}`);
    } catch (err) {
      setFeedback("sql-feedback", err.message, true);
    }
  });

  $("sql-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    setFeedback("sql-feedback", "Extraction SQL...");
    try {
      const res = await api("/api/runs/sql", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          uri: $("sql-uri").value.trim(),
          table: $("sql-table").value.trim(),
          domain: $("sql-domain-select").value,
          quarantine: $("sql-quarantine").checked,
        }),
      });
      const r = res.run || {};
      setFeedback("sql-feedback", `Audit SQL ${r.run_id?.slice(0, 8)} terminé : ${r.findings_count} incident(s).`);
      await refreshAll();
    } catch (err) {
      setFeedback("sql-feedback", err.message, true);
    }
  });

  $("watch-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const res = await api("/api/watcher/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ watch_dir: $("watch-dir").value.trim(), domain: $("watch-domain-select").value }),
      });
      setFeedback("watch-feedback", `Scan terminé : ${res.processed_count ?? 0} fichier(s) traité(s).`);
      await refreshAll();
    } catch (err) {
      setFeedback("watch-feedback", err.message, true);
    }
  });

  // ==================== QUARANTAINE & REPLAY ====================
  async function loadQuarantine() {
    try {
      const dom = $("quar-domain-filter").value;
      const st = $("quar-status-filter").value;
      const qs = new URLSearchParams({ status: st, limit: "60" });
      if (dom) qs.set("domain", dom);
      const data = await api(`/api/quarantine?${qs.toString()}`);
      const items = data.items || [];
      const grid = $("quarantine-cards-grid");
      if (!items.length) {
        grid.innerHTML = `<div class="empty-cell">Aucune ligne en quarantaine pour le statut ${st}.</div>`;
        return;
      }
      grid.innerHTML = items.map((it) => {
        const qid = it.quarantine_id ?? it.id;
        let rowObj = {};
        try { rowObj = typeof it.row_payload_json === "string" ? JSON.parse(it.row_payload_json) : (it.row_payload_json || {}); } catch {}
        let issuesArr = [];
        try { issuesArr = typeof it.issues_json === "string" ? JSON.parse(it.issues_json) : (it.issues_json || []); } catch {}
        const issueText = Array.isArray(issuesArr)
          ? issuesArr.map((x) => (typeof x === "string" ? x : (x.explanation || x.message || x.rule_id || JSON.stringify(x)))).join(" • ")
          : String(issuesArr);
        const chips = Object.entries(rowObj).map(([k, v]) =>
          `<span class="field-chip"><strong>${k}:</strong> ${v === null || v === "" ? "<em>NULL</em>" : v}</span>`
        ).join("");
        return `
          <div class="quar-card">
            <div class="quar-card-head">
              <div><strong>Ligne #${qid}</strong> · <span class="tag tag-info">${it.domain}</span></div>
              <span class="tag tag-warn">${it.status}</span>
            </div>
            <div class="quar-issues">⚠️ ${issueText || "Violation contrat"}</div>
            <div class="quar-fields">${chips}</div>
            <div class="quar-actions">
              <button class="btn btn-emerald btn-sm" onclick="window.resolveQuarantine(${qid}, 'APPROVED')">✓ Approuver</button>
              <button class="btn btn-danger btn-sm" onclick="window.resolveQuarantine(${qid}, 'REJECTED')">✕ Rejeter</button>
            </div>
          </div>`;
      }).join("");
    } catch {}
  }

  window.resolveQuarantine = async (qid, status) => {
    try {
      await api("/api/quarantine/resolve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ quarantine_id: qid, status }),
      });
      showToast(`Ligne #${qid} -> ${status}`);
      await loadQuarantine();
    } catch (err) {
      setFeedback("quarantine-feedback", err.message, true);
    }
  };

  $("btn-replay-quarantine").addEventListener("click", async () => {
    try {
      const res = await api("/api/quarantine/replay", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ domain: $("quar-domain-filter").value || null }),
      });
      if (!res.replayed_count) setFeedback("quarantine-feedback", "Aucune ligne APPROVED à rejouer.", true);
      else {
        setFeedback("quarantine-feedback", `✅ ${res.replayed_count} ligne(s) réintégrée(s) dans ${res.artifact_path}`);
        showToast(`🔄 ${res.replayed_count} ligne(s) rejouée(s)`);
        await loadQuarantine();
      }
    } catch (err) {
      setFeedback("quarantine-feedback", err.message, true);
    }
  });
  $("btn-load-quarantine").addEventListener("click", loadQuarantine);
  $("quar-domain-filter").addEventListener("change", loadQuarantine);
  $("quar-status-filter").addEventListener("change", loadQuarantine);

  // ==================== CATALOG & GLOSSAIRE ====================
  async function loadCatalogAssets() {
    try {
      const data = await api(`/api/catalog/assets?q=${encodeURIComponent($("cat-search-input").value.trim())}`);
      $("cat-assets-tbody").innerHTML = (data.assets || []).map((a) => {
        const cols = a.schema_columns || [];
        const piiCols = cols.filter((c) => c.is_pii).map((c) => c.name);
        const slaOk = a.sla_status === "HEALTHY";
        return `
          <tr>
            <td><strong>${a.domain}</strong><br><small class="muted">${a.description || ""}</small></td>
            <td><span class="tag ${a.classification === "CONFIDENTIAL" ? "tag-warn" : "tag-info"}">${a.classification}</span></td>
            <td>${a.owner}</td>
            <td><strong>${cols.length}</strong> col. ${piiCols.length ? `<span class="tag tag-err">${piiCols.length} PII (${piiCols.join(", ")})</span>` : `<span class="tag tag-emerald">0 PII</span>`}</td>
            <td><span class="tag ${slaOk ? "tag-emerald" : "tag-err"}">${a.sla_status}</span></td>
            <td><code>${(a.last_run_id || "—").slice(0, 8)}</code></td>
          </tr>`;
      }).join("");
    } catch {}
  }

  $("btn-cat-filter").addEventListener("click", loadCatalogAssets);
  $("btn-cat-sync").addEventListener("click", async () => {
    try {
      const res = await api("/api/catalog/sync", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ config_dir: "configs" }) });
      setFeedback("cat-feedback", `Catalogue YAML synchronisé (${res.synced_count} actifs).`);
      await refreshAll();
    } catch (err) {
      setFeedback("cat-feedback", err.message, true);
    }
  });

  async function loadGlossary() {
    try {
      const data = await api("/api/catalog/glossary");
      const terms = data.terms || [];
      $("glossary-tbody").innerHTML = terms.length ? terms.map((t) => `
        <tr>
          <td><strong>${t.term}</strong></td>
          <td><span class="tag tag-info">${t.domain}</span></td>
          <td>${t.definition}${t.formula ? `<br><code>${t.formula}</code>` : ""}</td>
          <td><button class="btn btn-danger btn-xs" onclick="window.deleteGlossaryTerm('${encodeURIComponent(t.term)}')">Supprimer</button></td>
        </tr>`).join("") : `<tr><td colspan="4" class="empty-cell">Aucun terme défini.</td></tr>`;
    } catch {}
  }

  window.deleteGlossaryTerm = async (encodedTerm) => {
    try { await api(`/api/catalog/glossary/${encodedTerm}`, { method: "DELETE" }); await loadGlossary(); } catch {}
  };

  $("glossary-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/catalog/glossary", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ term: $("gl-term").value.trim(), domain: $("gl-domain").value, definition: $("gl-def").value.trim(), formula: $("gl-formula").value.trim() }),
      });
      $("gl-term").value = ""; $("gl-def").value = ""; $("gl-formula").value = "";
      setFeedback("glossary-feedback", "Terme enregistré.");
      await loadGlossary();
    } catch (err) { setFeedback("glossary-feedback", err.message, true); }
  });
  $("btn-load-glossary").addEventListener("click", loadGlossary);

  // ==================== PII & RGPD (RENDU VISUEL SANS JSON BRUT) ====================
  $("btn-pii-scan").addEventListener("click", async () => {
    const file = $("pii-file").files[0];
    if (!file) return setFeedback("pii-feedback", "Sélectionnez un fichier CSV.", true);
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await api("/api/pii/scan", { method: "POST", body: fd });
      setFeedback("pii-feedback", `${res.pii_columns_count} colonne(s) PII détectée(s).`);
      const findings = res.columns || res.findings || [];
      $("pii-visual-results").innerHTML = `
        <div class="cert-banner">
          <strong>Diagnostic RGPD : ${res.pii_columns_count ?? 0} colonne(s) sensible(s) · ${res.total_pii_cells ?? 0} cellule(s) exposée(s)</strong>
        </div>
        ${Array.isArray(findings) ? findings.map((c) => `
          <div class="metric-bar-row">
            <div class="metric-bar-head">
              <span>Colonne <code>${c.column || c.name}</code> (${c.pii_type || c.type || "PII"})</span>
              <span class="tag tag-err">${c.match_count || c.count || 0} occurrences</span>
            </div>
          </div>`).join("") : ""}`;
    } catch (err) { setFeedback("pii-feedback", err.message, true); }
  });

  $("pii-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const file = $("pii-file").files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    fd.append("strategy", $("pii-strategy").value);
    fd.append("columns", $("pii-columns").value.trim());
    try {
      const resp = await fetch("/api/pii/mask", { method: "POST", headers: headers(), body: fd });
      if (!resp.ok) throw new Error(await resp.text());
      const blob = await resp.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `anonymise_${file.name}`;
      a.click();
      setFeedback("pii-feedback", "CSV anonymisé téléchargé.");
    } catch (err) { setFeedback("pii-feedback", err.message, true); }
  });

  // ==================== CHAOS LAB (RENDU VISUEL SANS JSON BRUT) ====================
  $("chaos-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    setFeedback("chaos-feedback", "Simulation Chaos en cours...");
    try {
      const res = await api("/api/synth/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          domain: $("chaos-domain").value,
          rows: Number($("chaos-rows").value),
          chaos_rate: Number($("chaos-rate").value),
          auto_audit: $("chaos-auto-audit").checked,
        }),
      });
      setFeedback("chaos-feedback", "Expérience Chaos terminée.");
      $("chaos-visual-results").innerHTML = `
        <div class="cert-banner">
          <strong>Simulation Chaos — Domaine « ${res.domain} »</strong>
          <p class="muted" style="margin:6px 0 0">Fichier généré : <code>${res.output_path || ""}</code></p>
        </div>
        <div class="pillars-grid">
          <div class="pillar-box"><span>Lignes Générées</span><strong>${res.rows_generated}</strong></div>
          <div class="pillar-box"><span>Anomalies Injectées</span><strong>${res.injected_faults_count}</strong></div>
        </div>`;
      await refreshAll();
    } catch (err) { setFeedback("chaos-feedback", err.message, true); }
  });

  // ==================== CERTIFICAT HMAC-SHA256 & DRIFT VISUEL ====================
  $("lineage-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const runId = $("lineage-run-select").value;
    if (!runId) return;
    try {
      const [lin, cert] = await Promise.all([
        api(`/api/runs/${runId}/lineage`),
        api(`/api/runs/${runId}/certificate`),
      ]);
      const sig = cert.certificate_sha256 || lin.certificate_sha256 || "—";
      $("lineage-visual-results").innerHTML = `
        <div class="cert-banner">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <strong>✅ Certificat Authentique (HMAC-SHA256)</strong>
            <span class="tag tag-emerald">Score ${lin.quality_score ?? "—"}/100 (${lin.quality_grade ?? "A"})</span>
          </div>
          <code class="cert-hash">${sig}</code>
          <div style="display:flex;justify-content:space-between;align-items:center;font-size:12px">
            <span>Contrat v${lin.contract_version || "1.0"} · ${lin.clean_rows ?? 0}/${lin.input_rows ?? 0} lignes saines</span>
            <button type="button" class="btn btn-secondary btn-xs" onclick="navigator.clipboard.writeText('${sig}');window.showToastMsg('Signature copiée !')">Copier le sceau</button>
          </div>
        </div>`;
    } catch (err) {
      $("lineage-visual-results").innerHTML = `<p class="feedback err">${err.message}</p>`;
    }
  });
  window.showToastMsg = showToast;

  $("drift-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const dom = $("drift-domain-select").value;
    try {
      const res = await api(`/api/diff?domain=${encodeURIComponent(dom)}`);
      const drifts = res.data_drifts || res.column_diffs || [];
      if (!drifts.length) {
        $("drift-visual-results").innerHTML = `<div class="cert-banner"><strong>✅ Aucune dérive statistique significative détectée sur « ${dom} ».</strong></div>`;
        return;
      }
      $("drift-visual-results").innerHTML = drifts.map((d) => {
        const psi = Number(d.psi || 0);
        const pct = Math.min(100, Math.round((psi / 1.0) * 100));
        const isWarn = psi >= 0.2 || String(d.severity).toUpperCase() === "CRITICAL";
        return `
          <div class="metric-bar-row">
            <div class="metric-bar-head">
              <span>Colonne <code>${d.column || d.column_name}</code></span>
              <span>PSI (Laplace) : <strong>${psi.toFixed(3)}</strong> · KS : ${Number(d.ks_stat || d.ks_d || 0).toFixed(3)}</span>
            </div>
            <div class="bar-track"><div class="bar-fill ${isWarn ? "warn" : ""}" style="width:${pct}%"></div></div>
          </div>`;
      }).join("");
    } catch (err) {
      $("drift-visual-results").innerHTML = `<p class="feedback err">${err.message}</p>`;
    }
  });

  // ==================== CLES API & AUDIT ====================
  async function loadKeysAndAudit() {
    try {
      const [kData, aData] = await Promise.all([api("/api/security/keys"), api("/api/security/audit?limit=25")]);
      $("keys-tbody").innerHTML = (kData.keys || []).map((k) => `
        <tr>
          <td><code>${k.key_id}</code></td>
          <td>${k.name}</td>
          <td><span class="tag tag-info">${k.role}</span></td>
          <td><span class="tag ${k.is_active ? "tag-emerald" : "tag-err"}">${k.is_active ? "ACTIVE" : "RÉVOQUÉE"}</span></td>
          <td>${k.is_active ? `<button class="btn btn-danger btn-xs" onclick="window.revokeKey('${k.key_id}')">Révoquer</button>` : "—"}</td>
        </tr>`).join("");
      $("audit-tbody").innerHTML = (aData.events || aData.audit || []).map((ev) => `
        <tr>
          <td><small>${ev.timestamp || ""}</small></td>
          <td>${ev.actor_name || ""}</td>
          <td><code>${ev.action || ""}</code></td>
          <td><small>${ev.resource || ""}</small></td>
          <td><span class="tag ${ev.outcome === "ALLOWED" ? "tag-emerald" : "tag-err"}">${ev.outcome || ""}</span></td>
        </tr>`).join("");
    } catch {}
  }

  window.revokeKey = async (keyId) => {
    try { await api(`/api/security/keys/${keyId}`, { method: "DELETE" }); await loadKeysAndAudit(); } catch {}
  };

  $("key-create-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const res = await api("/api/security/keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: $("new-key-name").value.trim(), role: $("new-key-role").value }),
      });
      $("new-key-name").value = "";
      setFeedback("key-feedback", `Clé générée : ${res.api_key}`);
      await loadKeysAndAudit();
    } catch (err) { setFeedback("key-feedback", err.message, true); }
  });
  $("btn-load-audit").addEventListener("click", loadKeysAndAudit);

  refreshAll();
})();
