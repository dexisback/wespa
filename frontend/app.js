/* Memory Engine frontend — one-screen answer experience */
const API = window.location.origin.startsWith("http") ? window.location.origin : "";
let currentMode = "hybrid";
let lastQuestion = "";
let graphNetwork = null;
let statusTimer = null;
let lastResult = null;

const $ = (id) => document.getElementById(id);

const EXAMPLES = [
  "Which companies did people who left OpenAI go on to found?",
  "Which company acquired the startup that David Luan founded after leaving OpenAI?",
  "How did Google end up re-hiring Noam Shazeer?",
  "What is Safe Superintelligence valued at?",
];

function init() {
  const chips = $("chips");
  EXAMPLES.forEach((q) => {
    const el = document.createElement("button");
    el.className = "chip";
    el.textContent = q;
    el.onclick = () => ask(q);
    chips.appendChild(el);
  });

  $("btn-ask").onclick = () => ask($("question").value.trim());
  $("question").addEventListener("keydown", (e) => { if (e.key === "Enter") ask($("question").value.trim()); });

  document.querySelectorAll("#mode-toggle button").forEach((b) => {
    b.onclick = () => setMode(b.dataset.mode);
  });

  $("drawer-close").onclick = closeDrawer;
  $("eval-close").onclick = () => $("eval-panel").classList.add("hidden");
  $("compare-close").onclick = () => $("compare-panel").classList.add("hidden");
  $("impact-close").onclick = () => $("impact-panel").classList.add("hidden");
  $("btn-eval").onclick = openEval;
  $("btn-compare").onclick = openCompare;
  $("btn-impact").onclick = openImpact;
  $("btn-ingest").onclick = toggleFixtureMenu;
  $("btn-ingest-url").onclick = ingestUrl;
  $("opt-asof-clear").onclick = () => { $("opt-asof").value = ""; };
  $("btn-why").onclick = toggleWhy;
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".ingest-wrap")) $("fixture-menu").classList.add("hidden");
  });
  refreshMemStatus();
  setInterval(refreshMemStatus, 30000);
}

/* ---------- memory status bar ---------- */
async function refreshMemStatus() {
  try {
    const s = await (await fetch(`${API}/stats`)).json();
    $("mem-status").classList.remove("hidden");
    $("ms-entities").innerHTML = `<b>${s.entities}</b> entities`;
    $("ms-facts").innerHTML = `<b>${s.facts}</b> facts <span class="dim">(${s.active_facts} active · ${s.historical_facts} historical${s.conflicts ? " · " + s.conflicts + " conflicts" : ""})</span>`;
    $("ms-sources").innerHTML = `<b>${s.sources}</b> sources`;
    $("ms-breakdown").textContent = `${s.corroborated_facts} corroborated`;
    const t = s.last_ingestion?.completed_at || s.last_document_at;
    $("ms-updated").textContent = t ? `last updated ${timeAgo(t)}` : "";
  } catch { /* status bar is cosmetic; ignore */ }
}

function timeAgo(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const s = Math.round((Date.now() - d.getTime()) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

/* ---------- status stages ---------- */
function startStatus(mode) {
  const asof = $("opt-asof").value;
  const live = $("opt-live").checked;
  const stages = [];
  if (asof) stages.push("Time travel — reconstructing memory as of " + asof + "...");
  if (currentMode !== "vector") stages.push("Searching graph memory...");
  stages.push("Searching semantic memory...");
  if (currentMode === "hybrid") stages.push("Merging evidence...");
  stages.push("Generating grounded answer...");
  if (live) stages.push("Memory insufficient? Checking...");
  let i = 0;
  $("status").classList.remove("hidden", "error");
  const render = () => {
    $("status").innerHTML = `<span class="spinner"></span>${stages[i % stages.length]}`;
    i += 1;
  };
  render();
  statusTimer = setInterval(render, 1100);
}

function stopStatus(err) {
  clearInterval(statusTimer);
  statusTimer = null;
  if (err) {
    $("status").classList.add("error");
    $("status").textContent = err;
    setTimeout(() => $("status").classList.add("hidden"), 4000);
  } else {
    $("status").classList.add("hidden");
  }
}

/* ---------- ask ---------- */
async function ask(question, isModeSwitch) {
  if (!question) return;
  lastQuestion = question;
  $("btn-ask").disabled = true;
  startStatus(currentMode);
  if (isModeSwitch) { $("result").classList.remove("hidden"); $("empty").classList.add("hidden"); }
  const asof = $("opt-asof").value ? new Date($("opt-asof").value + "T12:00:00Z").toISOString() : null;
  try {
    const resp = await fetch(`${API}/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        retrieval_mode: currentMode,
        allow_live: $("opt-live").checked,
        as_of: asof,
      }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || "No reliable evidence found.");
    }
    const data = await resp.json();
    lastResult = data;
    renderResult(data);
    stopStatus();
    refreshMemStatus();
  } catch (e) {
    stopStatus(e.message || "Query failed.");
    toast(e.message || "Query failed.", true);
  } finally {
    $("btn-ask").disabled = false;
  }
}

function confClass(labelText) {
  const l = (labelText || "").toLowerCase();
  if (l.includes("very")) return "very-high";
  if (l.includes("high")) return "high";
  if (l.includes("medium")) return "medium";
  return "low";
}

function renderResult(data) {
  $("empty").classList.add("hidden");
  $("result").classList.remove("hidden");

  $("latency").innerHTML = data.latency_ms != null ? `<b>${Math.round(data.latency_ms)}ms</b>` : "";
  $("answer").textContent = data.answer;

  const badge = $("conf-badge");
  badge.className = `badge ${confClass(data.confidence_label)}`;
  const pct = Math.round((data.confidence || 0) * 100);
  badge.innerHTML = `Confidence&nbsp; ${data.confidence_label || "—"} <span class="bar"><i style="width:${pct}%;background:currentColor"></i></span> ${pct}%`;
  $("conf-detail").textContent = `0.5·source trust + 0.3·corroboration + 0.2·extraction`;

  const asofTag = $("as-of-tag");
  if (data.as_of) {
    asofTag.classList.remove("hidden");
    asofTag.innerHTML = `⏱ Reconstruction of memory <b>as of ${esc(data.as_of.slice(0, 10))}</b> — facts valid on that date only.`;
  } else asofTag.classList.add("hidden");

  const mu = $("memory-updated");
  if (data.live_retrieval_used && data.memory_updates) {
    const u = data.memory_updates;
    const bits = [`+${u.documents_added} source${u.documents_added === 1 ? "" : "s"}`, `+${u.entities_added} entities`, `+${u.relationships_added} facts`];
    if (u.facts_superseded) bits.push(`${u.facts_superseded} superseded`);
    if (u.facts_corroborated) bits.push(`${u.facts_corroborated} corroborated`);
    if (u.conflicts_flagged) bits.push(`${u.conflicts_flagged} conflicted`);
    if (u.documents_skipped_duplicate) bits.push(`${u.documents_skipped_duplicate} duplicate skipped`);
    mu.classList.remove("hidden");
    mu.innerHTML = `
      <div class="mu-title">⬆ MEMORY UPDATED — first answer: the system learned, now it knows</div>
      <div class="mu-bits">${bits.map((b) => `<span>${esc(b)}</span>`).join("")}</div>
      <div class="mu-grounded">✓ Answer grounded in updated memory${data.still_insufficient ? " · evidence still thin — treat with care" : ""}</div>`;
  } else mu.classList.add("hidden");

  const pl = $("pipeline");
  const stages = data.pipeline || [];
  if (stages.length) {
    pl.classList.remove("hidden");
    pl.innerHTML = stages.map((s) => {
      const live = /insufficient|updated|grounded|unavailable|fetch/i.test(s.name);
      return `<span class="stage${live ? " live" : ""}">${esc(s.name)}${s.detail ? `<i> · ${esc(s.detail)}</i>` : ""}</span>`;
    }).join(" → ");
  } else pl.classList.add("hidden");

  const conflictsEl = $("conflicts");
  if (data.conflicts && data.conflicts.length) {
    conflictsEl.classList.remove("hidden");
    conflictsEl.innerHTML = `⚠ The sources disagree on: ${data.conflicts.join(" · ")}. Both versions are retained in memory; confidence reduced.`;
  } else conflictsEl.classList.add("hidden");

  const wp = $("why-panel");
  wp.classList.add("hidden");
  wp.dataset.payload = JSON.stringify(data.confidence_breakdown || {});
  renderWhy(wp.dataset.payload);

  renderSources(data);
  renderFacts(data);
  renderGraph(data);
}

/* ---------- why this answer ---------- */
function toggleWhy() {
  $("why-panel").classList.toggle("hidden");
}

function renderWhy(bd) {
  const wp = $("why-panel");
  if (!bd || !bd.explanation) {
    wp.innerHTML = `<p style="color:var(--text-dim)">No breakdown available.</p>`;
    return;
  }
  const bar = (labelText, v, color) => `
    <div class="why-row"><span class="k">${esc(labelText)}</span>
      <span class="bar big"><i style="width:${Math.round(v * 100)}%;background:${color}"></i></span>
      <span class="v">${Math.round(v * 100)}%</span></div>`;
  wp.innerHTML = `
    <div class="why-title">WHY THIS CONFIDENCE</div>
    ${bar("Source reliability", bd.source_reliability, "#7aa2ff")}
    ${bar("Cross-source agreement", bd.cross_source_agreement, "#4ade80")}
    ${bar("Extraction confidence", bd.extraction_confidence, "#c084fc")}
    <div class="why-meta">Supporting sources: <b>${bd.supporting_sources}</b>${bd.conflicting_sources ? ` · <span style="color:var(--red)">Conflicting: ${bd.conflicting_sources}</span>` : " · Conflicting: 0"}</div>
    <div class="why-meta dim">${esc(bd.explanation)}</div>`;
}

/* ---------- evidence & facts ---------- */
function renderSources(data) {
  const wrap = $("source-cards");
  wrap.innerHTML = "";
  const cards = data.source_cards || [];
  if (!cards.length) {
    wrap.innerHTML = `<div class="graph-note">No reliable evidence found.</div>`;
    return;
  }
  cards.forEach((c) => {
    const el = document.createElement("div");
    el.className = "source-card";
    let bar = "";
    if (c.confidence != null) {
      const pct = Math.round(c.confidence * 100);
      bar = `<div class="cbar"><i style="width:${pct}%"></i></div>`;
    }
    const date = c.published_at ? fmtDate(c.published_at) : (c.retrieved_at ? fmtDate(c.retrieved_at) : "");
    el.innerHTML = `
      <div class="name">${esc(c.source_name)}${c.is_new ? '<span class="new-badge">NEW</span>' : ""}</div>
      ${c.title ? `<div class="title">${esc(c.title)}</div>` : ""}
      <div class="meta"><span>${date ? "Retrieved " + date : ""}</span><span>${c.url ? "article ↗" : ""}</span></div>
      ${bar}`;
    if (c.url) el.querySelector(".meta").onclick = () => window.open(c.url, "_blank");
    wrap.appendChild(el);
  });
}

function renderFacts(data) {
  const block = $("facts-block");
  const wrap = $("fact-chips");
  wrap.innerHTML = "";
  if (currentMode === "vector" || !(data.facts || []).length) {
    block.style.display = "none";
    return;
  }
  block.style.display = "";
  data.facts.forEach((f) => {
    const el = document.createElement("button");
    el.className = `fact-chip${f.active ? "" : " historical"}${f.conflict ? " conflict" : ""}`;
    el.innerHTML = `<b>${esc(f.subject_name)}</b><span class="rel">${esc(f.relation)}</span>${esc(f.object_name)}${f.active ? "" : " ⟂"}`;
    el.title = "Show fact history, impact & contradiction analysis";
    el.onclick = () => openFactHistory(f.fact_id);
    wrap.appendChild(el);
  });
}

/* ---------- graph with sequential path animation ---------- */
function renderGraph(data) {
  const container = $("graph");
  container.innerHTML = "";
  const gp = data.graph_path;
  if (!gp || !gp.nodes || gp.nodes.length === 0) {
    container.classList.add("empty");
    $("graph-note").textContent = currentMode === "vector" ? "No graph traversal used" : "";
    if (graphNetwork) { graphNetwork.destroy(); graphNetwork = null; }
    return;
  }
  container.classList.remove("empty");

  const primary = (gp.paths || [])[0] || [];
  const pathNodes = new Set();
  (gp.paths || []).forEach((p) => p.forEach((nid) => pathNodes.add(nid)));
  const primaryEdges = [];
  (gp.edges || []).forEach((e) => {
    const idx = primary.indexOf(e.source);
    if (idx !== -1 && primary[idx + 1] === e.target) primaryEdges.push(e.id);
  });
  const allPathEdges = new Set();
  (gp.edges || []).forEach((e) => {
    const inAny = (gp.paths || []).some((p) => p.includes(e.source) && p.includes(e.target) && Math.abs(p.indexOf(e.target) - p.indexOf(e.source)) === 1);
    if (inAny) allPathEdges.add(e.id);
  });

  const TYPE_COLORS = {
    Person: "#c084fc", Organization: "#7aa2ff", Product: "#4ade80", Money: "#fbbf24",
  };

  const nodes = new vis.DataSet(gp.nodes.map((n) => {
    const onPrimary = primary.includes(n.id);
    const inPath = pathNodes.has(n.id);
    return {
      id: n.id,
      label: n.label,
      shape: n.type === "Person" ? "dot" : "box",
      color: {
        background: onPrimary ? "#25408f" : inPath ? "#1e2c52" : "#1a1e26",
        border: onPrimary ? "#9ab6ff" : inPath ? "#7aa2ff" : "#2a303b",
        highlight: { background: "#25408f", border: "#9ab6ff" },
      },
      font: { color: onPrimary || inPath ? "#dbe6ff" : "#9aa1ad", size: onPrimary ? 13 : 12, face: "Inter, sans-serif" },
      size: n.type === "Person" ? (onPrimary ? 17 : 14) : undefined,
      borderWidth: onPrimary ? 2.5 : inPath ? 2 : 1,
      title: `${n.label} · ${n.type}${onPrimary ? " · on answer path" : inPath ? " · in traversal" : ""}`,
    };
  }));

  const edges = new vis.DataSet(gp.edges.map((e) => {
    const onPrimary = primaryEdges.includes(e.id);
    const hot = allPathEdges.has(e.id);
    return {
      id: e.id,
      from: e.source,
      to: e.target,
      label: e.label,
      arrows: "to",
      physics: false,
      color: { color: hot ? "#7aa2ff" : "#333a46", highlight: "#9ab6ff" },
      width: hot ? 2.5 : 1,
      font: { size: 9.5, color: hot ? "#9ab6ff" : "#5a6270", background: "none" },
      smooth: { type: "curvedCW", roundness: 0.12 },
      title: `${e.label} · confidence ${Math.round((e.confidence || 0) * 100)}%${e.active ? "" : " · historical"}`,
      _primary: onPrimary,
    };
  }));

  graphNetwork = new vis.Network(container, { nodes, edges }, {
    layout: { improvedLayout: true },
    physics: { solver: "forceAtlas2Based", stabilization: { iterations: 220, fit: true } },
    interaction: { hover: true, zoomView: true, panSpeed: 0.8 },
    nodes: { shape: "box", margin: 10, borderWidth: 1 },
    edges: { selectionWidth: 2 },
  });

  // sequential "light travels along the path" animation
  const anim = edges.get().filter((e) => e._primary);
  const baseWidth = 3;
  anim.forEach((e, i) => {
    setTimeout(() => {
      edges.update({ id: e.id, color: { color: "#e6efff" }, width: baseWidth + 1.5, font: { size: 10.5, color: "#e6efff", background: "none" } });
      setTimeout(() => {
        edges.update({ id: e.id, color: { color: "#9ab6ff" }, width: baseWidth });
      }, 380);
    }, 420 + i * 380);
  });

  const note = `Subset of memory used for this answer — ${gp.nodes.length} nodes, ${gp.edges.length} edges`;
  $("graph-note").textContent = primary.length
    ? `${note} · animated route = the chain behind this answer: ${primary.map((id) => (gp.nodes.find((n) => n.id === id) || {}).label || id).join(" → ")}`
    : note;
}

/* ---------- fact history drawer (timeline + contradiction resolution) ---------- */
async function openFactHistory(factId) {
  $("drawer").classList.remove("hidden");
  $("drawer-body").innerHTML = `<div class="status" style="color:var(--accent)"><span class="spinner"></span>Loading fact history...</div>`;
  try {
    const resp = await fetch(`${API}/facts/${factId}`);
    if (!resp.ok) throw new Error("Fact not found");
    const data = await resp.json();
    renderHistory(data);
  } catch (e) {
    $("drawer-body").innerHTML = `<p style="color:var(--red)">${esc(e.message)}</p>`;
  }
}

function renderHistory(data) {
  const versions = data.versions || [data.fact];
  const f = data.fact;
  const title = `${f.subject_name} ⟶ ${f.relation} ⟶ ${f.object_name}`;

  const items = versions.map((v) => {
    const current = !v.valid_to;
    const cls = v.valid_to ? "historical" : "";
    const tag = v.valid_to
      ? `<span class="status-tag superseded">superseded</span>`
      : `<span class="status-tag active">current belief</span>`;
    if (v.conflict) tag += `<span class="status-tag conflict">conflict</span>`;
    const confLabel = confLabelOf(v.confidence);
    return `
      <div class="tl-item ${cls}">
        <div class="tl-dot"></div>
        <div class="tl-body">
          <div class="when">${fmtDate(v.valid_from)}${v.valid_to ? " → " + fmtDate(v.valid_to) : " → present"} ${tag}</div>
          <div class="what"><b>${esc(v.subject_name)}</b> <span class="rel">${esc(v.relation)}</span> ${esc(v.object_name)}</div>
          <div class="src">Source: ${esc(v.source_name || v.source_id)}</div>
          <div class="confline">confidence ${v.confidence} (${confLabel}) · extraction ${v.extraction_confidence}</div>
        </div>
      </div>`;
  }).join("");

  let res = "";
  const r = data.resolution;
  if (r && r.competing_claims && r.competing_claims.length > 1) {
    const claims = r.competing_claims.map((c) => `
      <div class="claim ${c.active ? "winner" : ""}">
        <div class="what"><b>${esc(c.object)}</b> ${c.active ? '<span class="status-tag active">current belief</span>' : '<span class="status-tag superseded">superseded</span>'}</div>
        <div class="src">${esc(c.source)} · reliability ${c.source_reliability} · observed ${fmtDate(c.observed_at)} · corroborations ${c.corroborations}</div>
      </div>`).join("");
    const why = (r.why || []).map((w) => `<li>${esc(w)}</li>`).join("");
    res = `<div class="sources-label">Competing claims</div>${claims}
           <div class="sources-label">Why the current belief wins</div><ul class="why-list">${why || "<li>Strongest combined confidence</li>"}</ul>`;
  }

  let impactBtn = `<button class="btn ghost small" id="btn-impact-this">Inspect impact of changes to this fact</button>`;
  $("drawer-body").innerHTML = `
    <p style="color:var(--text-dim);font-size:12.5px;margin-bottom:16px">${esc(title)}<br>Old versions are preserved, never overwritten.</p>
    <div class="timeline">${items.join("")}</div>
    ${res}
    <div style="margin-top:14px">${impactBtn}</div>
    ${data.audit && data.audit.length ? `<div class="sources-label">Audit trail</div>${data.audit.map((a) => `<div class="confline">${esc(a.timestamp.slice(0, 16).replace("T", " "))} · ${esc(a.action)} · ${esc(a.detail || "")}</div>`).join("")}` : ""}`;
  const b = $("btn-impact-this");
  if (b) b.onclick = () => { closeDrawer(); openImpact(factId); };
}

function confLabelOf(c) {
  if (c >= 0.85) return "Very high";
  if (c >= 0.7) return "High";
  if (c >= 0.55) return "Medium";
  return "Low";
}

function closeDrawer() {
  $("drawer").classList.add("hidden");
}

/* ---------- mode comparison ---------- */
async function openCompare() {
  const q = lastQuestion || $("question").value.trim();
  $("compare-panel").classList.remove("hidden");
  if (!q) {
    $("compare-body").innerHTML = `<p style="color:var(--text-dim)">Ask a question first, then compare how each retrieval mode handles it.</p>`;
    return;
  }
  $("compare-body").innerHTML = `<div class="status" style="color:var(--accent)"><span class="spinner"></span>Running the same question through Vector, Graph and Hybrid...</div>`;
  try {
    const asof = $("opt-asof").value ? new Date($("opt-asof").value + "T12:00:00Z").toISOString() : null;
    const run = (mode) => fetch(`${API}/query`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q, retrieval_mode: mode, as_of: asof }),
    }).then((r) => r.json());
    const [v, g, h] = await Promise.all([run("vector"), run("graph"), run("hybrid")]);
    const col = (mode, d) => {
      if (!d || d.detail) return `<div class="compare-col"><div class="mode-name">${mode}</div><p style="color:var(--red)">Failed: ${esc((d && d.detail) || "?")}</p></div>`;
      const evCount = mode === "vector"
        ? `${(d.passages || []).length} passages`
        : `${(d.facts || []).length} facts + ${(d.passages || []).length} passages`;
      const pathNote = mode === "vector" ? "no graph traversal" : `path: ${(d.graph_path?.paths || [])[0]?.length || 0} nodes`;
      return `<div class="compare-col${mode === currentMode ? " active" : ""}">
        <div class="mode-name">${mode.toUpperCase()}${mode === currentMode ? " · current" : ""}</div>
        <div class="metric-sub">${evCount} · <b>${Math.round(d.latency_ms)}ms</b> · confidence ${d.confidence} ${esc(d.confidence_label)}<br>${pathNote}</div>
        <p class="answer-text" style="font-size:12.5px;max-height:180px;overflow:auto">${esc((d.answer || "").slice(0, 400))}…</p>
        <div class="confline">sources: ${esc((d.sources || []).slice(0, 4).join(", ")) || "—"}</div>
      </div>`;
    };
    $("compare-body").innerHTML = `
      <p style="color:var(--text-dim);font-size:12.5px;margin-bottom:12px">Question: <b>${esc(q)}</b><br>Different retrieval paths genuinely produce different evidence — no mode is universally best.</p>
      <div class="compare-grid">${col("vector", v)}${col("graph", g)}${col("hybrid", h)}</div>`;
  } catch (e) {
    $("compare-body").innerHTML = `<p style="color:var(--red)">Comparison failed: ${esc(e.message)}</p>`;
  }
}

/* ---------- knowledge impact ---------- */
async function openImpact(factId) {
  $("impact-panel").classList.remove("hidden");
  $("impact-body").innerHTML = `<div class="status" style="color:var(--accent)"><span class="spinner"></span>Computing impact of knowledge changes...</div>`;
  try {
    if (factId) {
      const data = await (await fetch(`${API}/impact/fact/${factId}`)).json();
      renderImpactFact(data, factId);
      return;
    }
    const data = await (await fetch(`${API}/impact/recent?limit=10`)).json();
    const events = data.events || [];
    if (!events.length) {
      $("impact-body").innerHTML = `<p style="color:var(--text-dim)">No knowledge changes recorded yet. Ingest a new source, then check here for what it affected.</p>`;
      return;
    }
    $("impact-body").innerHTML = `
      <p style="color:var(--text-dim);font-size:12.5px;margin-bottom:12px">Recent knowledge changes and what they affected. Click one for the full impact analysis.</p>
      ${events.map((e) => `
        <div class="impact-row" data-f="${esc(e.fact_id)}">
          <div>
            <div class="k"><b>${esc(e.label)}</b></div>
            <div class="metric-sub">${esc(e.action)} ${fmtDate(e.when)}${e.successor_object ? " → now: <b>" + esc(e.successor_object) + "</b>" : ""}</div>
          </div>
          <div class="v">${e.answers_stale ? `<span class="status-tag conflict">${e.answers_stale} answer${e.answers_stale === 1 ? "" : "s"} possibly stale</span>` : '<span class="status-tag active">no impact</span>'}</div>
        </div>`).join("")}`;
    $("impact-body").querySelectorAll(".impact-row").forEach((el) => {
      el.onclick = () => openImpact(el.dataset.f);
    });
  } catch (e) {
    $("impact-body").innerHTML = `<p style="color:var(--red)">Impact lookup failed: ${esc(e.message)}</p>`;
  }
}

function renderImpactFact(data, factId) {
  if (!data.ok) {
    $("impact-body").innerHTML = `<p style="color:var(--red)">${esc(data.error || "not found")}</p>`;
    return;
  }
  const STATUS_CLASS = { "CURRENT": "active", "POTENTIALLY STALE": "superseded", "INVALIDATED": "conflict" };
  const answers = (data.answers || []).map((a) => `
    <div class="impact-row static">
      <div>
        <div class="k">${esc(a.question)}</div>
        <div class="metric-sub">${fmtDate(a.answered_at)} · ${esc(a.retrieval_mode)} · answered ${timeAgo(a.answered_at)}</div>
        <div class="metric-sub dim">${esc(a.reason)}</div>
      </div>
      <div><span class="status-tag ${STATUS_CLASS[a.status] || ""}">${esc(a.status)}</span></div>
    </div>`).join("") || `<p style="color:var(--text-dim)">No recorded answers cited this fact yet.</p>`;
  const deps = (data.dependent_facts || []).map((f) =>
    `<div class="confline">${esc(f.subject_name)} <span class="rel">${esc(f.relation)}</span> ${esc(f.object_name)} · ${Math.round((f.confidence || 0) * 100)}%</div>`).join("");
  const s = data.successor;
  $("impact-body").innerHTML = `
    <button class="btn ghost small" id="impact-back">← All changes</button>
    <div class="fact-changed">
      <div class="why-title">FACT CHANGED</div>
      <div class="what"><b>${esc(data.fact.label)}</b></div>
      <div class="metric-sub">${esc(data.fact.source)} · valid ${fmtDate(data.fact.valid_from)}${data.fact.valid_to ? " → " + fmtDate(data.fact.valid_to) : " → present"}${data.fact.conflict ? " · <span style='color:var(--red)'>contradicted</span>" : ""}</div>
      ${s ? `<div class="metric-sub">Now: <b>${esc(s.object)}</b> (observed ${fmtDate(s.observed_at)}, confidence ${Math.round((s.confidence || 0) * 100)}%)</div>` : ""}
      <div class="impact-headline">${esc(data.impact.headline)}</div>
    </div>
    <div class="sources-label">Previous answers affected</div>
    ${answers}
    <div class="sources-label">Related facts that depend on this</div>
    ${deps || '<p style="color:var(--text-dim)">None.</p>'}`;
  $("impact-back").onclick = () => openImpact();
}

/* ---------- evaluation ---------- */
async function openEval() {
  $("eval-panel").classList.remove("hidden");
  $("eval-body").innerHTML = `<div class="status" style="color:var(--accent)"><span class="spinner"></span>Loading evaluation...</div>`;
  try {
    let data = await (await fetch(`${API}/eval/results`)).json();
    if (data.status !== "ok") {
      $("eval-body").innerHTML = `<p style="color:var(--text-dim);font-size:13px">No measured results yet. Running the full evaluation now (all retrieval modes + grounded answers) — this takes a minute or two.</p><button id="btn-run-eval" class="btn primary" style="margin-top:12px">Run evaluation</button>`;
      $("btn-run-eval").onclick = async () => {
        $("eval-body").innerHTML = `<div class="status" style="color:var(--accent)"><span class="spinner"></span>Evaluating: Hit@5, Recall@5, multi-hop accuracy, temporal correctness, confidence weighting, stale-answer detection, impact analysis...</div>`;
        try {
          const r = await fetch(`${API}/eval/run`, { method: "POST" });
          data = await r.json();
          renderEval(data);
        } catch (e) { $("eval-body").innerHTML = `<p style="color:var(--red)">Evaluation failed: ${esc(e.message)}</p>`; }
      };
      return;
    }
    renderEval(data);
  } catch (e) {
    $("eval-body").innerHTML = `<p style="color:var(--red)">${esc(e.message)}</p>`;
  }
}

function renderEval(data) {
  const rows = [
    ["Hit@5", data.hit_at_5?.hybrid ?? data.hit_at_5_overall, `vector ${data.hit_at_5?.vector ?? "—"} · graph ${data.hit_at_5?.graph ?? "—"} · hybrid ${data.hit_at_5?.hybrid ?? "—"}`],
    ["Recall@5", data.recall_at_5?.hybrid ?? data.recall_at_5_overall, `vector ${data.recall_at_5?.vector ?? "—"} · graph ${data.recall_at_5?.graph ?? "—"} · hybrid ${data.recall_at_5?.hybrid ?? "—"}`],
    ["Multi-hop accuracy", data.multi_hop_accuracy, `${data.multi_hop_questions} multi-hop questions`],
    ["Avg latency", `${data.avg_latency_ms} ms`, `${data.questions} questions × 3 modes`],
    ["Temporal correctness", data.temporal_correctness?.result, data.temporal_correctness?.detail],
    ["Confidence weighting", data.confidence_weighting?.result, data.confidence_weighting?.detail],
    ["Stale-answer detection", data.stale_answer_detection?.result, data.stale_answer_detection?.detail],
    ["Impact analysis", data.impact_analysis?.result, data.impact_analysis?.detail],
    ["Temporal reconstruction", data.temporal_reconstruction?.result, data.temporal_reconstruction?.detail],
  ];
  $("eval-body").innerHTML = rows.map(([k, v, sub]) => {
    const isPass = v === "PASS" || v === "FAIL";
    const cls = isPass ? (v === "PASS" ? "pass" : "fail") : "";
    return `<div class="metric-row"><div><div class="k">${esc(k)}</div>${sub ? `<div class="metric-sub">${esc(sub)}</div>` : ""}</div><div class="v ${cls}">${esc(String(v ?? "—"))}</div></div>`;
  }).join("");
}

/* ---------- ingestion ---------- */
async function toggleFixtureMenu() {
  const menu = $("fixture-menu");
  if (!menu.classList.contains("hidden")) { menu.classList.add("hidden"); return; }
  menu.classList.remove("hidden");
  $("fixture-list").innerHTML = `<div class="fx">loading…</div>`;
  try {
    const data = await (await fetch(`${API}/ingest/fixtures`)).json();
    $("fixture-list").innerHTML = data.fixtures.map((f) => `<div class="fx" data-f="${esc(f.file)}">${esc(f.title)}</div>`).join("") || `<div class="fx">no fixtures</div>`;
    $("fixture-list").querySelectorAll(".fx[data-f]").forEach((el) => {
      el.onclick = () => { menu.classList.add("hidden"); runIngest({ mode: "fixture", fixture: el.dataset.f }); };
    });
  } catch {
    $("fixture-list").innerHTML = `<div class="fx">failed to load fixtures</div>`;
  }
}

async function ingestUrl() {
  const url = $("ingest-url").value.trim();
  if (!url) { toast("Paste a URL first.", true); return; }
  $("btn-ingest-url").disabled = true;
  $("fixture-list").innerHTML = `<div class="status" style="color:var(--accent)"><span class="spinner"></span>Fetching article… cleaning… extracting…</div>`;
  try {
    const data = await runIngest({ mode: "url", url });
    $("fixture-menu").classList.add("hidden");
    $("ingest-url").value = "";
    toast(data.message || "Memory updated.");
  } catch (e) {
    toast(`Unable to ingest this URL. ${esc(e.message)}`, true);
    $("fixture-list").innerHTML = `<div class="fx">failed — try another URL</div>`;
  } finally {
    $("btn-ingest-url").disabled = false;
  }
}

async function runIngest(body) {
  $("btn-ingest").disabled = true;
  $("btn-ingest").textContent = "Ingesting...";
  try {
    const resp = await fetch(`${API}/ingest`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Ingestion failed");
    const lines = [
      `<b>MEMORY UPDATED</b>`,
      `+${data.entities_added} entities · +${data.relationships_added} relationships`,
      `${data.facts_superseded} fact${data.facts_superseded === 1 ? "" : "s"} superseded · ${data.facts_corroborated} corroborated${data.conflicts_flagged ? ` · ${data.conflicts_flagged} conflicted` : ""}`,
      `${data.documents_skipped_duplicate ? data.documents_skipped_duplicate + " duplicate(s) skipped · " : ""}historical versions preserved`,
    ];
    toast(lines.join("<br>"));
    refreshMemStatus();
    return data;
  } catch (e) {
    toast(`Unable to ingest this source. ${esc(e.message)}`, true);
  } finally {
    $("btn-ingest").disabled = false;
    $("btn-ingest").textContent = "Ingest new source";
  }
}

function toast(msg, isError) {
  const t = $("toast");
  t.className = isError ? "toast error" : "toast";
  t.innerHTML = msg;
  t.classList.remove("hidden");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.add("hidden"), 7500);
}

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return iso.slice(0, 10);
  return d.toLocaleDateString("en-US", { month: "short", day: "2-digit" });
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

document.addEventListener("DOMContentLoaded", init);
