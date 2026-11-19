(() => {
  const LS_KEY = "datapulse_api_key";
  let cachedRuns = [];
  let cachedDomains = [];
  let activeModalRunId = "";

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
    const resp = await fetch(path, {
      ...opts,
      headers: headers(opts.headers || {}),
    });
    const text = await resp.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
    if (!resp.ok) {
      throw new Error(data.detail || `Erreur HTTP ${resp.status}`);
    }
    return data;
  }

  function setFeedback(elId, msg, isErr = false) {
    const el = $(elId);
    if (!el) return;
    el.textContent = msg;
    el.className = "feedback " + (isErr ? "err" : "ok");
  }

  // ==================== NAVIGATION ONGLETS ====================
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      const panel = $(`tab-${btn.dataset.tab}`);
      if (panel) panel.classList.add("active");
    });
  });

  // ==================== AUTH & BOOTSTRAP DEMO 1-CLIC ====================
  async function activateDemoSession() {
    try {
      const res = await api("/api/demo/bootstrap", { method: "POST" });
      if (res.api_key) {
        setKey(res.api_key);
        $("api-key-input").value = res.api_key;
      }
      showToast("✅ Session Admin activée & catalogue synchronisé");
      await refreshAll();
    } catch (err) {
      $("auth-pill").textContent = "Erreur auth : " + err.message;
      $("auth-pill").className = "pill pill-warn";
    }
  }

  $("btn-apply-key").addEventListener("click", async () => {
    setKey($("api-key-input").value);
    await refreshAll();
  });
  $("btn-demo-bootstrap").addEventListener("click", activateDemoSession);
  if ($("btn-banner-bootstrap")) $("btn-banner-bootstrap").addEventListener("click", activateDemoSession);

  // ==================== CHARGEMENT GLOBAL ====================
  async function refreshAll() {
    $("api-key-input").value = getKey();
    try {
      await api("/api/health");
      const [domData, runsData, quarData, catSum, assetsData] = await Promise.all([
        api("/api/domains"),
        api("/api/runs?limit=40"),
        api("/api/quarantine?status=PENDING&limit=200"),
        api("/api/catalog/summary"),
        api("/api/catalog/assets"),
      ]);

      $("auth-pill").textContent = "Zero-Trust RBAC Actif";
      $("auth-pill").className = "pill pill-ok";
      if ($("onboarding-banner")) $("onboarding-banner").classList.add("hidden");

      cachedDomains = domData.domains || [];
      cachedRuns = runsData.runs || [];

      renderDomainStories(assetsData.assets || []);
      populateDomainSelects(cachedDomains);
      populateRunSelect(cachedRuns);
      renderRunsTable();

      $("kpi-runs").textContent = cachedRuns.length;
      $("kpi-rows").textContent = cachedRuns.reduce((s, r) => s + (Number(r.row_count) || 0), 0).toLocaleString("fr-FR");
      $("kpi-findings").textContent = cachedRuns.reduce((s, r) => s + (Number(r.findings_count) || 0), 0).toLocaleString("fr-FR");
      $("kpi-quarantine").textContent = (quarData.items || []).length;
      $("kpi-sla").textContent = `${catSum.sla_compliance_pct ?? 100}%`;
      $("kpi-assets").textContent = `${catSum.total_assets ?? 0} actifs (${catSum.sla_breached ?? 0} SLA alerte)`;

      if (cachedRuns.length > 0) {
        try {
          const det = await api(`/api/runs/${cachedRuns[0].run_id}`);
          const score = det.profile?.quality_score ?? det.quality_score;
          const grade = det.profile?.quality_grade ?? det.quality_grade ?? "";
          $("kpi-score").textContent = score !== undefined ? `${Number(score).toFixed(2)} (${grade})` : "OK";
          $("kpi-grade").textContent = `Dernier : ${cachedRuns[0].domain} (${cachedRuns[0].run_id.slice(0, 8)})`;
        } catch {
          $("kpi-score").textContent = "Actif";
        }
      }

      await Promise.all([
        loadDemoDatasets(),
        loadCatalogAssets(),
        loadGlossary(),
        loadQuarantine(),
        loadKeysAndAudit(),
      ]);
    } catch (err) {
      $("auth-pill").textContent = "Clé requise";
      $("auth-pill").className = "pill pill-warn";
      if ($("onboarding-banner")) $("onboarding-banner").classList.remove("hidden");
    }
  }

  function renderDomainStories(assets) {
    const bar = $("domain-stories-bar");
    if (!bar) return;
    if (!assets.length) {
      bar.innerHTML = "";
      return;
    }
    const allChip = `
      <div class="story-item" onclick="window.filterByDomainStory('')">
        <div class="story-ring ok"><div class="story-inner">ALL</div></div>
        <span class="story-name">Tous</span>
        <span class="story-status">${cachedRuns.length} runs</span>
      </div>`;
    bar.innerHTML = allChip + assets.map((a) => {
      const healthy = a.sla_status === "HEALTHY";
      const initials = String(a.domain || "DP").slice(0, 3);
      return `
        <div class="story-item" onclick="window.filterByDomainStory('${a.domain}')">
          <div class="story-ring ${healthy ? "ok" : "warn"}">
            <div class="story-inner">${initials}</div>
          </div>
          <span class="story-name">${a.domain}</span>
          <span class="story-status">${healthy ? "SLA OK" : "SLA Alerte"}</span>
        </div>
      `;
    }).join("");
  }

  window.filterByDomainStory = (domain) => {
    $("filter-domain-select").value = domain;
    renderRunsTable();
    showToast(domain ? `Filtre appliqué : domaine « ${domain} »` : "Affichage de tous les domaines");
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
      tbody.innerHTML = `<tr><td colspan="8" class="empty-cell">Aucune exécution trouvée.</td></tr>`;
      return;
    }
    tbody.innerHTML = rows.map((r) => `
      <tr>
        <td><code>${r.run_id.slice(0, 8)}</code></td>
        <td><span class="tag tag-info">${r.domain}</span></td>
        <td>${(r.source_file || "").split(/[\\/]/).pop()}</td>
        <td>${r.row_count}</td>
        <td>${r.findings_count > 0 ? `<span class="tag tag-warn">${r.findings_count}</span>` : `<span class="tag tag-ok">0</span>`}</td>
        <td>${Number(r.duration_ms || 0).toFixed(1)} ms</td>
        <td><span class="tag tag-ok">${r.status}</span></td>
        <td>
          <button class="btn btn-secondary btn-sm" onclick="window.openRunModal('${r.run_id}')">
            Inspecter &amp; Télécharger
          </button>
        </td>
      </tr>
    `).join("");
  }

  $("filter-domain-select").addEventListener("change", renderRunsTable);
  $("btn-refresh-all").addEventListener("click", refreshAll);

  // ==================== MODALE DETAIL D'UN RUN + TELECHARGEMENTS ====================
  window.openRunModal = async (runId) => {
    activeModalRunId = runId;
    $("run-modal").classList.remove("hidden");
    $("modal-run-title").textContent = `Exécution ${runId.slice(0, 8)}`;
    $("modal-run-sub").textContent = "Chargement des détails et des incidents...";
    $("modal-run-body").innerHTML = "";

    try {
      const det = await api(`/api/runs/${runId}`);
      const meta = det.run || det;
      const findings = det.findings || [];
      $("modal-run-title").textContent = `Exécution ${runId.slice(0, 8)} — Domaine « ${meta.domain} »`;
      $("modal-run-sub").textContent = `Source : ${meta.source_file} | Lignes : ${meta.row_count} | SHA-256 : ${(meta.file_hash_sha256 || "").slice(0, 16)}...`;

      let html = `<h3>Incidents &amp; Règles Déclenchées (${findings.length})</h3>`;
      if (!findings.length) {
        html += `<p class="feedback ok">✅ Aucun incident détecté : 100 % des lignes respectent le contrat de qualité.</p>`;
      } else {
        html += `<div class="table-wrap"><table><thead><tr><th>Règle / Type</th><th>Colonne</th><th>Sévérité</th><th>Occurrences</th><th>Message</th></tr></thead><tbody>` +
          findings.slice(0, 50).map((f) => `
            <tr>
              <td><code>${f.rule_id || f.finding_type || "RULE"}</code></td>
              <td>${f.column_name || f.column || "—"}</td>
              <td><span class="tag tag-warn">${f.severity || "MEDIUM"}</span></td>
              <td>${f.affected_rows || f.count || 1}</td>
              <td>${f.message || f.description || ""}</td>
            </tr>
          `).join("") + `</tbody></table></div>`;
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
      if (!resp.ok) {
        const txt = await resp.text();
        showToast("⚠️ Artefact non généré pour ce run");
        alert("Artefact indisponible : " + txt);
        return;
      }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      if (kind === "report") {
        window.open(url, "_blank");
      } else {
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        a.click();
      }
      showToast(`✅ Téléchargement prêt : ${filename}`);
    } catch (err) {
      alert(err.message);
    }
  }

  $("dl-report-btn").addEventListener("click", () => downloadRunArtifact("report", `rapport_${activeModalRunId.slice(0, 8)}.html`));
  $("dl-clean-btn").addEventListener("click", () => downloadRunArtifact("clean", `${activeModalRunId.slice(0, 8)}_clean.csv`));
  $("dl-annotated-btn").addEventListener("click", () => downloadRunArtifact("annotated", `${activeModalRunId.slice(0, 8)}_annotated.csv`));
  $("btn-modal-close").addEventListener("click", () => $("run-modal").classList.add("hidden"));
  $("modal-close-bg").addEventListener("click", () => $("run-modal").classList.add("hidden"));

  // ==================== ONGLET 2 : STUDIO D'AUDIT ====================
  async function loadDemoDatasets() {
    try {
      const data = await api("/api/demo/datasets");
      const sel = $("demo-dataset-select");
      sel.innerHTML = (data.datasets || []).map((d) =>
        `<option value="${d.filename}" data-domain="${d.suggested_domain}">${d.filename} — ${d.label} (${d.size_kb} Ko)</option>`
      ).join("");
      if (sel.options.length > 0) {
        $("demo-dataset-domain").value = sel.options[0].dataset.domain || "auto";
      }
    } catch {}
  }

  $("demo-dataset-select").addEventListener("change", (e) => {
    const opt = e.target.selectedOptions[0];
    if (opt && opt.dataset.domain) $("demo-dataset-domain").value = opt.dataset.domain;
  });

  $("demo-dataset-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    setFeedback("demo-dataset-feedback", "Exécution du pipeline en cours...");
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
      setFeedback("demo-dataset-feedback", `Run ${r.run_id?.slice(0, 8)} (${r.domain}) terminé : ${r.findings_count} incident(s).`);
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
    setFeedback("upload-feedback", "Analyse du fichier en cours...");
    const fd = new FormData();
    fd.append("file", file);
    fd.append("domain", $("upload-domain-select").value);
    fd.append("quarantine", $("upload-quarantine").checked);
    try {
      const res = await api("/api/runs/upload", { method: "POST", body: fd });
      const r = res.run || {};
      setFeedback("upload-feedback", `Run ${r.run_id?.slice(0, 8)} (${r.domain}) terminé : ${r.findings_count} incident(s).`);
      await refreshAll();
      if (r.run_id) window.openRunModal(r.run_id);
    } catch (err) {
      setFeedback("upload-feedback", err.message, true);
    }
  });

  $("btn-sql-tables").addEventListener("click", async () => {
    try {
      const uri = encodeURIComponent($("sql-uri").value.trim());
      const res = await api(`/api/sql/tables?uri=${uri}`);
      const tables = res.tables || [];
      if (tables.length) $("sql-table").value = tables[0];
      setFeedback("sql-feedback", `Tables détectées : ${tables.join(", ") || "aucune"}`);
    } catch (err) {
      setFeedback("sql-feedback", err.message, true);
    }
  });

  $("sql-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    setFeedback("sql-feedback", "Extraction et audit SQL en cours...");
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
      setFeedback("sql-feedback", `Audit SQL ${r.run_id?.slice(0, 8)} (${r.domain}) terminé : ${r.findings_count} incident(s).`);
      await refreshAll();
    } catch (err) {
      setFeedback("sql-feedback", err.message, true);
    }
  });

  $("watch-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    setFeedback("watch-feedback", "Scan du répertoire en cours...");
    try {
      const res = await api("/api/watcher/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          watch_dir: $("watch-dir").value.trim(),
          domain: $("watch-domain-select").value,
        }),
      });
      setFeedback("watch-feedback", `Scan terminé : ${res.processed_count ?? 0} fichier(s) traité(s).`);
      await refreshAll();
    } catch (err) {
      setFeedback("watch-feedback", err.message, true);
    }
  });

  // ==================== ONGLET 3 : QUARANTAINE (CARTES TACTILES) ====================
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
        grid.innerHTML = `<div class="empty-cell">Aucune ligne en quarantaine pour ce filtre (${st}).</div>`;
        return;
      }
      grid.innerHTML = items.map((it) => {
        const qid = it.quarantine_id ?? it.id;
        let rowObj = {};
        try {
          rowObj = typeof it.row_payload_json === "string" ? JSON.parse(it.row_payload_json) : (it.row_payload_json || {});
        } catch { rowObj = { brut: String(it.row_payload_json || "") }; }

        let issuesArr = [];
        try {
          issuesArr = typeof it.issues_json === "string" ? JSON.parse(it.issues_json) : (it.issues_json || []);
        } catch { issuesArr = [String(it.issues_json || "Violation contrat")]; }

        const issueText = Array.isArray(issuesArr)
          ? issuesArr.map((x) => (typeof x === "string" ? x : (x.message || x.rule_id || JSON.stringify(x)))).join(" • ")
          : String(issuesArr);

        const chips = Object.entries(rowObj).map(([k, v]) =>
          `<span class="field-chip"><strong>${k}:</strong> ${v === null || v === "" ? "<em>NULL</em>" : v}</span>`
        ).join("");

        return `
          <div class="quar-card">
            <div class="quar-card-head">
              <div>
                <strong>Ligne #${qid}</strong> · <span class="tag tag-info">${it.domain}</span>
                <small class="muted"> (Run ${String(it.run_id || "").slice(0, 8)})</small>
              </div>
              <span class="tag tag-warn">${it.status}</span>
            </div>
            <div class="quar-issues">⚠️ ${issueText || "Violation de règle métier"}</div>
            <div class="quar-fields">${chips}</div>
            <div class="quar-actions">
              <button class="btn btn-success btn-sm" onclick="window.resolveQuarantine(${qid}, 'APPROVED')">✓ Approuver</button>
              <button class="btn btn-danger btn-sm" onclick="window.resolveQuarantine(${qid}, 'REJECTED')">✕ Rejeter</button>
            </div>
          </div>
        `;
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
      setFeedback("quarantine-feedback", `Ligne #${qid} marquée ${status}.`);
      showToast(`Ligne #${qid} -> ${status}`);
      await loadQuarantine();
      const quarData = await api("/api/quarantine?status=PENDING&limit=200");
      $("kpi-quarantine").textContent = (quarData.items || []).length;
    } catch (err) {
      setFeedback("quarantine-feedback", err.message, true);
    }
  };

  $("btn-load-quarantine").addEventListener("click", loadQuarantine);
  $("quar-domain-filter").addEventListener("change", loadQuarantine);
  $("quar-status-filter").addEventListener("change", loadQuarantine);

  // ==================== ONGLET 4 : DATA CATALOG & GLOSSAIRE ====================
  async function loadCatalogAssets() {
    try {
      const q = encodeURIComponent($("cat-search-input").value.trim());
      const data = await api(`/api/catalog/assets?q=${q}`);
      const assets = data.assets || [];
      $("cat-assets-tbody").innerHTML = assets.map((a) => {
        const cols = a.schema_columns || [];
        const piiCols = cols.filter((c) => c.is_pii).map((c) => c.name);
        const slaOk = a.sla_status === "HEALTHY";
        return `
          <tr>
            <td><strong>${a.domain}</strong><br><small class="muted">${a.description || ""}</small></td>
            <td><span class="tag ${a.classification === "CONFIDENTIAL" ? "tag-warn" : "tag-info"}">${a.classification}</span></td>
            <td>${a.owner}</td>
            <td>
              <strong>${cols.length}</strong> col.
              ${piiCols.length ? `<span class="tag tag-err">${piiCols.length} PII (${piiCols.join(", ")})</span>` : `<span class="tag tag-ok">0 PII</span>`}
            </td>
            <td><span class="tag ${slaOk ? "tag-ok" : "tag-err"}">${a.sla_status}</span></td>
            <td><code>${(a.last_run_id || "—").slice(0, 8)}</code></td>
          </tr>
        `;
      }).join("");
    } catch {}
  }

  $("btn-cat-filter").addEventListener("click", loadCatalogAssets);
  $("btn-cat-sync").addEventListener("click", async () => {
    try {
      const res = await api("/api/catalog/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config_dir: "configs" }),
      });
      setFeedback("cat-feedback", `Synchronisation YAML réussie (${res.synced_count} actifs mis à jour).`);
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
          <td><button class="btn btn-danger btn-sm" onclick="window.deleteGlossaryTerm('${encodeURIComponent(t.term)}')">Supprimer</button></td>
        </tr>
      `).join("") : `<tr><td colspan="4" class="empty-cell">Aucun terme défini.</td></tr>`;
    } catch {}
  }

  window.deleteGlossaryTerm = async (encodedTerm) => {
    try {
      await api(`/api/catalog/glossary/${encodedTerm}`, { method: "DELETE" });
      await loadGlossary();
    } catch (err) {
      setFeedback("glossary-feedback", err.message, true);
    }
  };

  $("glossary-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/catalog/glossary", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          term: $("gl-term").value.trim(),
          domain: $("gl-domain").value,
          definition: $("gl-def").value.trim(),
          formula: $("gl-formula").value.trim(),
        }),
      });
      $("gl-term").value = "";
      $("gl-def").value = "";
      $("gl-formula").value = "";
      setFeedback("glossary-feedback", "Terme métier enregistré.");
      await loadGlossary();
    } catch (err) {
      setFeedback("glossary-feedback", err.message, true);
    }
  });
  $("btn-load-glossary").addEventListener("click", loadGlossary);

  // ==================== ONGLET 5 : PII & RGPD ====================
  $("btn-pii-scan").addEventListener("click", async () => {
    const file = $("pii-file").files[0];
    if (!file) return setFeedback("pii-feedback", "Sélectionnez un fichier CSV d'abord.", true);
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await api("/api/pii/scan", { method: "POST", body: fd });
      setFeedback("pii-feedback", `${res.pii_columns_count} colonne(s) sensible(s) détectée(s) (${res.total_pii_cells} cellules).`);
      $("pii-results").textContent = JSON.stringify(res, null, 2);
    } catch (err) {
      setFeedback("pii-feedback", err.message, true);
    }
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
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `anonymise_${file.name}`;
      a.click();
      setFeedback("pii-feedback", "Fichier CSV anonymisé généré et téléchargé.");
    } catch (err) {
      setFeedback("pii-feedback", err.message, true);
    }
  });

  // ==================== ONGLET 6 : CHAOS LAB ====================
  $("chaos-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    setFeedback("chaos-feedback", "Génération synthétique & injection Chaos en cours...");
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
      setFeedback("chaos-feedback", `${res.rows_generated} lignes générées, ${res.injected_faults_count} anomalies injectées.`);
      $("chaos-results").textContent = JSON.stringify(res, null, 2);
      await refreshAll();
    } catch (err) {
      setFeedback("chaos-feedback", err.message, true);
    }
  });

  // ==================== ONGLET 7 : DRIFT, LINEAGE & SECURITE ====================
  $("lineage-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const runId = $("lineage-run-select").value;
    if (!runId) return;
    try {
      const [lin, cert] = await Promise.all([
        api(`/api/runs/${runId}/lineage`),
        api(`/api/runs/${runId}/certificate`),
      ]);
      $("lineage-results").textContent = JSON.stringify({ lineage: lin, certificat_sha256: cert }, null, 2);
    } catch (err) {
      $("lineage-results").textContent = "Erreur : " + err.message;
    }
  });

  $("drift-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const dom = $("drift-domain-select").value;
    try {
      const res = await api(`/api/diff?domain=${encodeURIComponent(dom)}`);
      $("drift-results").textContent = JSON.stringify(res, null, 2);
    } catch (err) {
      $("drift-results").textContent = "Erreur : " + err.message;
    }
  });

  async function loadKeysAndAudit() {
    try {
      const [kData, aData] = await Promise.all([
        api("/api/security/keys"),
        api("/api/security/audit?limit=25"),
      ]);
      $("keys-tbody").innerHTML = (kData.keys || []).map((k) => `
        <tr>
          <td><code>${k.key_id}</code></td>
          <td>${k.name}</td>
          <td><span class="tag tag-info">${k.role}</span></td>
          <td><span class="tag ${k.is_active ? "tag-ok" : "tag-err"}">${k.is_active ? "ACTIVE" : "RÉVOQUÉE"}</span></td>
          <td>${k.is_active ? `<button class="btn btn-danger btn-sm" onclick="window.revokeKey('${k.key_id}')">Révoquer</button>` : "—"}</td>
        </tr>
      `).join("");

      $("audit-tbody").innerHTML = (aData.events || aData.audit || []).map((ev) => `
        <tr>
          <td><small>${ev.timestamp || ""}</small></td>
          <td>${ev.actor_name || ""}</td>
          <td><code>${ev.action || ""}</code></td>
          <td><small>${ev.resource || ""}</small></td>
          <td><span class="tag ${ev.outcome === "ALLOWED" ? "tag-ok" : "tag-err"}">${ev.outcome || ""}</span></td>
        </tr>
      `).join("");
    } catch {}
  }

  window.revokeKey = async (keyId) => {
    try {
      await api(`/api/security/keys/${keyId}`, { method: "DELETE" });
      await loadKeysAndAudit();
    } catch (err) {
      setFeedback("key-feedback", err.message, true);
    }
  };

  $("key-create-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const res = await api("/api/security/keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: $("new-key-name").value.trim(),
          role: $("new-key-role").value,
        }),
      });
      $("new-key-name").value = "";
      setFeedback("key-feedback", `Clé créée (copiez-la maintenant) : ${res.api_key}`);
      await loadKeysAndAudit();
    } catch (err) {
      setFeedback("key-feedback", err.message, true);
    }
  });

  $("btn-load-audit").addEventListener("click", loadKeysAndAudit);

  refreshAll();
})();
