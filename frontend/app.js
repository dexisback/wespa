/* Memory Engine frontend — one-screen answer experience */
const API = window.location.origin.startsWith("http") ? window.location.origin : "";
let currentMode = "hybrid";
let lastQuestion = "";
let graphNetwork = null;
let statusTimer = null;

const STAGES = {
  hybrid: ["Extracting entities...", "Searching graph...", "Searching semantic memory...", "Merging evidence...", "Generating grounded answer..."],
  graph: ["Extracting entities...", "Tracing evidence path...", "Generating grounded answer..."],
  vector: ["Searching semantic memory...", "Generating grounded answer..."],
};

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
  $("btn-eval").onclick = openEval;
  $("btn-ingest").onclick = toggleFixtureMenu;
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".ingest-wrap")) $("fixture-menu").classList.add("hidden");
  });
}

function setMode(mode) {
  currentMode = mode;
  document.querySelectorAll("#mode-toggle button").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  if (lastQuestion) ask(lastQuestion, true);
}

function startStatus(mode) {
  const stages = STAGES[mode] || STAGES.hybrid;
  let i = 0;
  $("status").classList.remove("hidden", "error");
  const render = () => {
    $("status").innerHTML = `<span class="spinner"></span>${stages[i % stages.length]}`;
    i += 1;
  };
  render();
  statusTimer = setInterval(render, 950);
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

async function ask(question, isModeSwitch) {
  if (!question) return;
  lastQuestion = question;
  $("question").value = isModeSwitch ? question : question;
  $("btn-ask").disabled = true;
  startStatus(currentMode);
  if (isModeSwitch) { $("result").classList.remove("hidden"); $("empty").classList.add("hidden"); }
  try {
    const resp = await fetch(`${API}/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, retrieval_mode: currentMode }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || "No reliable evidence found.");
    }
    const data = await resp.json();
    renderResult(data);
    stopStatus();
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

  const conflictsEl = $("conflicts");
  if (data.conflicts && data.conflicts.length) {
    conflictsEl.classList.remove("hidden");
    conflictsEl.innerHTML = `⚠ The sources disagree on: ${data.conflicts.join(" · ")}. Both versions are retained in memory; confidence reduced.`;
  } else conflictsEl.classList.add("hidden");

  renderSources(data);
  renderFacts(data);
  renderGraph(data);
}

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
      <div class="name">${esc(c.source_name)}</div>
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
    el.title = "Show fact history";
    el.onclick = () => openFactHistory(f.fact_id);
    wrap.appendChild(el);
  });
}

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
  const pathEdges = new Set();
  const pathNodes = new Set();
  (gp.paths || []).forEach((p) => {
    p.forEach((nid) => pathNodes.add(nid));
  });
  (gp.edges || []).forEach((e) => {
    const inPath = (gp.paths || []).some((p) => p.includes(e.source) && p.includes(e.target) && Math.abs(p.indexOf(e.target) - p.indexOf(e.source)) === 1);
    if (inPath) pathEdges.add(e.id);
  });

  const TYPE_COLORS = {
    Person: "#c084fc", Organization: "#7aa2ff", Product: "#4ade80", Money: "#fbbf24",
  };

  const nodes = new vis.DataSet(gp.nodes.map((n) => ({
    id: n.id,
    label: n.label,
    shape: n.type === "Person" ? "dot" : "box",
    color: {
      background: pathNodes.has(n.id) ? "#1e2c52" : "#1a1e26",
      border: pathNodes.has(n.id) ? "#7aa2ff" : "#2a303b",
      highlight: { background: "#25408f", border: "#9ab6ff" },
    },
    font: { color: pathNodes.has(n.id) ? "#dbe6ff" : "#c7cdd6", size: 12, face: "Inter, sans-serif" },
    size: n.type === "Person" ? 14 : undefined,
    borderWidth: pathNodes.has(n.id) ? 2 : 1,
    title: `${n.label} · ${n.type}`,
  })));

  const edges = new vis.DataSet(gp.edges.map((e) => {
    const hot = pathEdges.has(e.id);
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
    };
  }));

  graphNetwork = new vis.Network(container, { nodes, edges }, {
    layout: { improvedLayout: true },
    physics: { solver: "forceAtlas2Based", stabilization: { iterations: 220, fit: true } },
    interaction: { hover: true, zoomView: true, panSpeed: 0.8 },
    nodes: { shape: "box", margin: 10, borderWidth: 1 },
    edges: { selectionWidth: 2 },
  });

  // animate: edges light up in two beats
  const hotEdges = edges.get().filter((e) => pathEdges.has(e.id));
  const restEdges = edges.get().filter((e) => !pathEdges.has(e.id));
  restEdges.forEach((e) => edges.update({ id: e.id, hidden: false }));
  setTimeout(() => {
    hotEdges.forEach((e) => edges.update({ id: e.id, color: { color: "#9ab6ff" }, width: 3 }));
  }, 350);

  $("graph-note").textContent = `Subset of memory used for this answer — ${gp.nodes.length} nodes, ${gp.edges.length} edges${(gp.paths || []).length ? " · highlighted route = traversal path" : ""}`;
}

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
    const cls = v.valid_to ? "historical" : "";
    const tag = v.valid_to
      ? `<span class="status-tag superseded">superseded</span>`
      : `<span class="status-tag active">current belief</span>`;
    if (v.conflict) tag += `<span class="status-tag conflict">conflict</span>`;
    const confLabel = confLabelOf(v.confidence);
    return `
      <div class="timeline-item ${cls}">
        <div class="when">${fmtDate(v.valid_from)}${v.valid_to ? " → " + fmtDate(v.valid_to) : " → present"} ${tag}</div>
        <div class="what"><b>${esc(v.subject_name)}</b> <span class="rel">${esc(v.relation)}</span> ${esc(v.object_name)}</div>
        <div class="src">Source: ${esc(v.source_name || v.source_id)}</div>
        <div class="confline">confidence ${v.confidence} (${confLabel}) · extraction ${v.extraction_confidence}</div>
      </div>`;
  });
  $("drawer-body").innerHTML = `
    <p style="color:var(--text-dim);font-size:12.5px;margin-bottom:16px">${esc(title)}<br>Old versions are preserved, never overwritten.</p>
    ${items.join("")}
    ${data.audit && data.audit.length ? `<div class="sources-label">Audit trail</div>${data.audit.map((a) => `<div class="confline">${esc(a.timestamp.slice(0, 16).replace("T", " "))} · ${esc(a.action)} · ${esc(a.detail || "")}</div>`).join("")}` : ""}`;
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

async function openEval() {
  $("eval-panel").classList.remove("hidden");
  $("eval-body").innerHTML = `<div class="status" style="color:var(--accent)"><span class="spinner"></span>Loading evaluation...</div>`;
  try {
    let data = await (await fetch(`${API}/eval/results`)).json();
    if (data.status !== "ok") {
      $("eval-body").innerHTML = `<p style="color:var(--text-dim);font-size:13px">No measured results yet. Running the full evaluation now (all retrieval modes + grounded answers) — this takes a minute or two.</p><button id="btn-run-eval" class="btn primary" style="margin-top:12px">Run evaluation</button>`;
      $("btn-run-eval").onclick = async () => {
        $("eval-body").innerHTML = `<div class="status" style="color:var(--accent)"><span class="spinner"></span>Evaluating: Hit@5, Recall@5, multi-hop accuracy, temporal correctness, confidence weighting...</div>`;
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
  ];
  $("eval-body").innerHTML = rows.map(([k, v, sub]) => {
    const isPass = v === "PASS" || v === "FAIL";
    const cls = isPass ? (v === "PASS" ? "pass" : "fail") : "";
    return `<div class="metric-row"><div><div class="k">${esc(k)}</div>${sub ? `<div class="metric-sub">${esc(sub)}</div>` : ""}</div><div class="v ${cls}">${esc(String(v ?? "—"))}</div></div>`;
  }).join("");
}

async function toggleFixtureMenu() {
  const menu = $("fixture-menu");
  if (!menu.classList.contains("hidden")) { menu.classList.add("hidden"); return; }
  menu.classList.remove("hidden");
  menu.innerHTML = `<div class="fx">loading…</div>`;
  try {
    const data = await (await fetch(`${API}/ingest/fixtures`)).json();
    menu.innerHTML = data.fixtures.map((f) => `<div class="fx" data-f="${esc(f.file)}">${esc(f.title)}</div>`).join("") || `<div class="fx">no fixtures</div>`;
    menu.querySelectorAll(".fx[data-f]").forEach((el) => {
      el.onclick = () => { menu.classList.add("hidden"); runIngest(el.dataset.f); };
    });
  } catch {
    menu.innerHTML = `<div class="fx">failed to load fixtures</div>`;
  }
}

async function runIngest(fixture) {
  $("btn-ingest").disabled = true;
  $("btn-ingest").textContent = "Ingesting...";
  try {
    const resp = await fetch(`${API}/ingest`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "fixture", fixture }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Ingestion failed");
    const lines = [
      `Source ingested`,
      `+${data.entities_added} entities`,
      `+${data.relationships_added} relationships`,
      `${data.facts_superseded} fact${data.facts_superseded === 1 ? "" : "s"} updated (previous version preserved)`,
      `${data.facts_deleted} facts deleted`,
    ];
    toast(lines.join("<br>"));
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
  t._timer = setTimeout(() => t.classList.add("hidden"), 6500);
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
