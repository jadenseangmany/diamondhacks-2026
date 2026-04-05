/* sidepanel.js — connects to Python WS server on port 7655, renders 3-tab UI */

const WS_URL = "ws://localhost:7655";

const PERSONA_COLORS = {
  "Elderly User":    "#ffd700",
  "First-Time User": "#aaffaa",
  "Pipeline":        "#a0a8c0",
};

const PERSONA_LABEL = {
  elderly_user:    "Elderly User",
  first_time_user: "First-Time User",
};

const SEV_COLOR = {
  critical: "#ff4444",
  high:     "#ff8800",
  medium:   "#ffcc00",
  low:      "#44dd88",
};

const SEV_BG = {
  critical: "rgba(255,68,68,.13)",
  high:     "rgba(255,136,0,.12)",
  medium:   "rgba(255,204,0,.10)",
  low:      "rgba(68,221,136,.10)",
};

// ── State ──────────────────────────────────────────────────────────────────────
let taskCount = 0;
let completedRuns = {};   // persona_key → pass count
let frictionCount = 0;
let ws = null;
let visualFixes = [];     // array of visual_fix messages in arrival order
let fixState = {};        // id → 'accepted' | 'rejected' | null

// ── DOM refs ───────────────────────────────────────────────────────────────────
const wsDot      = document.getElementById("ws-dot");
const wsHint     = document.getElementById("ws-hint");
const hdrUrl     = document.getElementById("hdr-url");
const progLabel  = document.getElementById("prog-label");
const progPct    = document.getElementById("prog-pct");
const progFill   = document.getElementById("prog-fill");
const logBody    = document.getElementById("log-body");
const logEmpty   = document.getElementById("log-empty");
const resultsBody= document.getElementById("results-body");
const fixesBody  = document.getElementById("fixes-body");
const badgeResults = document.getElementById("badge-results");
const badgeFixes   = document.getElementById("badge-fixes");
const shotStrip  = document.getElementById("shot-strip");
const lightbox   = document.getElementById("lightbox");
const lbImg      = document.getElementById("lb-img");
const lbCaption  = document.getElementById("lb-caption");

document.getElementById("lb-close").addEventListener("click", () => lightbox.classList.remove("open"));
lightbox.addEventListener("click", e => { if (e.target === lightbox) lightbox.classList.remove("open"); });

// ── Tabs ───────────────────────────────────────────────────────────────────────
document.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById("panel-" + tab.dataset.tab).classList.add("active");
  });
});

document.getElementById("clear-btn").addEventListener("click", () => {
  logBody.innerHTML = "";
});

// ── WebSocket ──────────────────────────────────────────────────────────────────
function connect() {
  if (ws) { try { ws.close(); } catch(_) {} }
  ws = new WebSocket(WS_URL);
  ws.onopen  = () => setWs("ok");
  ws.onclose = () => { setWs("off"); setTimeout(connect, 3000); };
  ws.onerror = () => setWs("err");
  ws.onmessage = e => {
    let m; try { m = JSON.parse(e.data); } catch(_) { return; }
    dispatch(m);
  };
}

function setWs(state) {
  wsDot.className = "ws-dot";
  if (state === "ok")  { wsDot.classList.add("ok");  wsHint.textContent = "Connected"; }
  else if (state === "err") { wsDot.classList.add("err"); wsHint.textContent = "Error — retrying…"; }
  else                 { wsHint.textContent = "Disconnected — reconnecting…"; }
}

// ── Dispatch ───────────────────────────────────────────────────────────────────
function dispatch(m) {
  switch (m.type) {
    case "pipeline_start":  onStart(m);      break;
    case "task_list":       onTaskList(m);   break;
    case "log":             onLog(m);        break;
    case "screenshot":      onScreenshot(m); break;
    case "persona_update":  onPersonaUpdate(m); break;
    case "friction_found":  onFrictionFound(m); break;
    case "visual_fix":      onVisualFix(m);  break;
    case "pipeline_done":   onDone(m);       break;
  }
}

function onStart(m) {
  hdrUrl.textContent = m.url || "…";
  taskCount = 0; completedRuns = {}; frictionCount = 0;
  visualFixes = []; fixState = {};
  progFill.style.width = "0%";
  progLabel.innerHTML = "Pipeline started";
  progPct.textContent = "";
  badgeResults.style.display = "none";
  badgeFixes.style.display = "none";
  ["elderly_user", "first_time_user"].forEach(k => {
    document.getElementById(`ps-${k}`).textContent = "—";
    document.getElementById(`pst-${k}`).textContent = "";
    document.getElementById(`pc-${k}`).classList.remove("active");
  });
  resultsBody.innerHTML = '<div class="empty-hint">Results appear here after the pipeline finishes.</div>';
  fixesBody.innerHTML   = '<div class="empty-hint">Fix recommendations appear here after analysis completes.</div>';
  document.getElementById("fixes-footer").style.display = "none";
  document.getElementById("fixes-counter").textContent = "0 accepted · 0 rejected";
  const applyBtn = document.getElementById("apply-all-btn");
  applyBtn.disabled = true;
  applyBtn.textContent = "Apply All Accepted";
  shotStrip.innerHTML   = "";
  appendLog({ ts: now(), persona: "Pipeline", task_num: 0, msg: `▶ ${m.url}` });
}

function onTaskList(m) {
  taskCount = (m.tasks || []).length;
  progLabel.textContent = `0 / ${taskCount * 2} task runs`;
  appendLog({ ts: now(), persona: "Pipeline", task_num: 0, msg: `📋 ${taskCount} task(s)` });
}

function onLog(m) {
  appendLog(m);
  if (m.msg && (m.msg.includes("✓") || m.msg.includes("PASS"))) {
    const k = keyFromLabel(m.persona);
    if (k) { completedRuns[k] = (completedRuns[k] || 0) + 1; updateProgress(); }
  }
}

function onPersonaUpdate(m) {
  const key = m.persona_key || keyFromLabel(m.persona);
  if (!key) return;
  const passed = m.passed ?? 0, total = m.total ?? taskCount;
  document.getElementById(`ps-${key}`).textContent = `${passed}/${total}`;
  const pct = total > 0 ? Math.round(passed / total * 100) : 0;
  const el = document.getElementById(`pst-${key}`);
  el.textContent = `${pct}%`;
  el.style.color = pct >= 70 ? "#44dd88" : pct >= 40 ? "#ffcc00" : "#ff4444";
  document.getElementById(`pc-${key}`).classList.add("active");
  updateProgress();
}

function onScreenshot(m) {
  if (!m.data) return;
  const src = "data:image/png;base64," + m.data;
  const persona = m.persona || "";
  const color   = PERSONA_COLORS[persona] || "#888";
  const caption = `T${m.task_num} · ${persona}`;

  const thumb = document.createElement("div");
  thumb.className = "shot-thumb";
  thumb.innerHTML =
    `<img src="${src}" alt="${esc(caption)}">` +
    `<div class="shot-label" style="border-top:2px solid ${color}">${esc(caption)}</div>`;
  thumb.addEventListener("click", () => {
    lbImg.src = src;
    lbCaption.textContent = caption;
    lightbox.classList.add("open");
  });

  shotStrip.appendChild(thumb);
  shotStrip.scrollLeft = shotStrip.scrollWidth;

  // Log it too
  appendLog({ ts: m.ts || now(), persona, task_num: m.task_num,
               msg: `📸 screenshot → ${m.path || ""}` });
}

function onFrictionFound(m) {
  frictionCount = m.count ?? frictionCount + 1;
}

function switchTab(name) {
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
  document.querySelector(`[data-tab="${name}"]`).classList.add("active");
  document.getElementById("panel-" + name).classList.add("active");
}

function onDone(m) {
  progFill.style.width = "100%";
  progLabel.innerHTML =
    `Pipeline complete — <a href="#" style="color:var(--accent2);text-decoration:none"
       onclick="switchTab('results');return false">View Full Report →</a>`;
  progPct.textContent = "✓";
  appendLogLink(now(), "✓ Pipeline complete — View Full Report →", () => switchTab("results"));

  badgeResults.style.display = "inline-block";
  renderResults(m);

  // Fixes arrive as incremental visual_fix messages; update badge with final count
  if (visualFixes.length > 0) {
    badgeFixes.style.display = "inline-block";
    badgeFixes.textContent = String(visualFixes.length);
  }

  // Auto-switch to RESULTS tab
  switchTab("results");
}

// ── Progress ───────────────────────────────────────────────────────────────────
function updateProgress() {
  if (!taskCount) return;
  const total = taskCount * 2;
  const done  = Object.values(completedRuns).reduce((s, n) => s + n, 0);
  const pct   = Math.min(100, Math.round(done / total * 100));
  progFill.style.width = pct + "%";
  progLabel.textContent = `${done} / ${total} task runs`;
  progPct.textContent = pct + "%";
}

// ── Log rendering ──────────────────────────────────────────────────────────────
function appendLogLink(ts, msg, onClick) {
  if (logEmpty) { logEmpty.remove(); }
  const div = document.createElement("div");
  div.className = "log-entry";
  const link = document.createElement("a");
  link.href = "#";
  link.style.cssText = "color:var(--accent2);text-decoration:none";
  link.textContent = msg;
  link.addEventListener("click", e => { e.preventDefault(); onClick(); });
  div.innerHTML =
    `<span class="le-ts">${esc(ts)}</span>` +
    `<span class="le-who" style="color:#a0a8c0">[Pipeline]</span>`;
  div.appendChild(link);
  logBody.appendChild(div);
  logBody.scrollTop = logBody.scrollHeight;
}

// Clean raw Python repr out of step messages so the log shows plain English.
function cleanMsg(msg) {
  if (!msg) return msg;

  // "Step N: ..." — already cleaned by Python; just make it look nicer
  if (/^Step \d+:/i.test(msg)) return msg;

  // Legacy "step N: root=XxxActionModel(...)" — strip the repr
  const stepMatch = msg.match(/^step (\d+):\s*(.*)/i);
  if (stepMatch) {
    const n = stepMatch[1];
    const body = stepMatch[2];

    // Map raw action tokens → readable phrases
    const rules = [
      [/navigate.*?url[=:\s'"]+([^\s,)'"]+)/i,       m => `Navigating to ${m[1].slice(0,50)}`],
      [/click.*?index[=:\s]+(\d+)/i,                  m => `Clicking element #${m[1]}`],
      [/scroll.*?down[=:\s]+(True|true)/i,             ()  => "Scrolling down"],
      [/scroll.*?down[=:\s]+(False|false)/i,           ()  => "Scrolling up"],
      [/wait.*?seconds[=:\s]+([\d.]+)/i,              m => `Waiting ${m[1]}s`],
      [/done.*?success[=:\s]+(True|true)/i,            ()  => "✓ Task complete"],
      [/done.*?success[=:\s]+(False|false)/i,          ()  => "Task ended"],
      [/EvaluateAction|evaluate_Params/i,              ()  => "Checking page element"],
      [/ClickAction/i,                                 ()  => "Clicking element"],
      [/ScrollAction/i,                                ()  => "Scrolling page"],
      [/NavigateAction/i,                              ()  => "Navigating"],
      [/WaitAction/i,                                  ()  => "Waiting"],
      [/DoneAction/i,                                  ()  => "Finishing task"],
      [/TypeAction|InputText/i,                        ()  => "Typing text"],
      [/ExtractContent/i,                              ()  => "Reading page content"],
    ];

    for (const [pattern, fn] of rules) {
      const m = body.match(pattern);
      if (m) return `Step ${n}: ${fn(m)}`;
    }

    // Generic: strip ActionModel class names to just verbs
    const cleaned = body
      .replace(/root=\w+ActionModel\(/g, "")
      .replace(/\w+_Params\([^)]*\)/g, "")
      .replace(/[()='"]/g, " ")
      .replace(/\s{2,}/g, " ")
      .trim()
      .slice(0, 80);
    return `Step ${n}: ${cleaned || "…"}`;
  }

  return msg;
}

function appendLog(e) {
  if (logEmpty) { logEmpty.remove(); }
  const div = document.createElement("div");
  div.className = "log-entry";
  const msg = cleanMsg(e.msg || "");
  const isPass = msg.includes("✓") || msg.includes("PASS") || msg.includes("DONE");
  const isFail = msg.includes("✗") || msg.includes("FAIL") || msg.includes("ERROR");
  const msgCls = isPass ? "le-msg le-pass" : isFail ? "le-msg le-fail" : "le-msg";
  const persona = e.persona || "";
  const color = PERSONA_COLORS[persona] || "#888899";
  div.innerHTML =
    `<span class="le-ts">${esc(e.ts||"")}</span>` +
    `<span class="le-who" style="color:${color}">[${esc(persona)}]</span>` +
    `<span class="${msgCls}">${esc(msg)}</span>`;
  logBody.appendChild(div);
  logBody.scrollTop = logBody.scrollHeight;
}

// ── RESULTS tab rendering ──────────────────────────────────────────────────────
function renderResults(m) {
  const summary  = m.summary || {};
  const taskRows = m.task_results || [];
  const fps      = m.friction_points || [];
  const sevMap   = m.severity_map || {};
  const pStats   = m.persona_stats || {};
  const pSummary = m.persona_summary || {};
  const overall  = m.overall_summary || "";

  let html = "";

  // ── Site summary ──
  html += `<div class="section-head">Site Summary</div>`;
  html += `<div class="summary-card">
    <div class="label">Purpose</div>
    <div class="val">${esc(summary.purpose || "")}</div>
  </div>`;
  html += `<div class="summary-card">
    <div class="label">Audience</div>
    <div class="val">${esc(summary.target_audience || "")}</div>
  </div>`;
  const flows = summary.key_flows || [];
  if (flows.length) {
    html += `<div class="summary-card">
      <div class="label">Key Flows</div>
      ${flows.map(f => `<div class="flow-item">${esc(f)}</div>`).join("")}
    </div>`;
  }

  // ── Persona stat cards ──
  const personaKeys   = ["elderly_user", "first_time_user"];
  const personaIcons  = { elderly_user: "👴", first_time_user: "🆕" };
  const personaColors = { elderly_user: "var(--elderly)", first_time_user: "var(--firsttime)" };

  html += `<div class="section-head">Persona Results</div>`;
  html += `<div class="stat-row">`;
  for (const key of personaKeys) {
    const label = PERSONA_LABEL[key];
    const stats = pStats[label] || {};
    const pct   = Math.round((stats.pass_rate ?? 0) * 100);
    const avgT  = (stats.avg_time_seconds ?? 0).toFixed(1);
    const col   = pct >= 70 ? "#44dd88" : pct >= 40 ? "#ffcc00" : "#ff4444";
    html += `<div class="stat-card">
      <div class="sn" style="color:${personaColors[key]}">${personaIcons[key]} ${esc(label)}</div>
      <div class="sv" style="color:${col}">${pct}%</div>
      <div class="sl">pass rate</div>
      <div class="st">${avgT}s avg</div>
    </div>`;
  }
  html += `</div>`;

  // ── Task × persona table ──
  if (taskRows.length) {
    html += `<div class="section-head">Tasks × Personas</div>`;
    html += `<table class="task-table"><thead><tr>
      <th>Task</th>
      <th>👴 Elderly</th><th>🆕 First-Time</th>
    </tr></thead><tbody>`;
    for (let i = 0; i < taskRows.length; i++) {
      const row = taskRows[i];
      html += `<tr><td><span class="task-num">${i+1}.</span> ${esc(row.task)}</td>`;
      for (const key of personaKeys) {
        const p = (row.personas || {})[key] || {};
        const ok = p.success;
        html += `<td style="text-align:center">
          <span class="${ok ? "cell-ok" : "cell-fail"}">${ok ? "✓" : "✗"}</span>
          <div class="cell-sub">${p.time ?? 0}s</div>
        </td>`;
      }
      html += `</tr>`;
    }
    html += `</tbody></table>`;
  }

  // ── Persona insights ──
  const psKeys = Object.keys(pSummary);
  if (psKeys.length) {
    html += `<div class="section-head">Persona Insights</div>`;
    for (const k of psKeys) {
      html += `<div class="persona-insight">
        <div class="pi-name">${esc(k)}</div>
        <div class="pi-text">${esc(pSummary[k])}</div>
      </div>`;
    }
  }

  // ── Friction points ──
  const sevOrder = { critical: 0, high: 1, medium: 2, low: 3 };
  const sorted = [...fps].sort((a, b) => (sevOrder[a.severity] ?? 3) - (sevOrder[b.severity] ?? 3));

  html += `<div class="section-head">Friction Points (${sorted.length})</div>`;
  if (overall) {
    html += `<div class="summary-card" style="margin-bottom:8px;border-left:3px solid var(--accent)">
      <div class="val" style="font-size:0.78rem;line-height:1.55;color:var(--muted)">${esc(overall)}</div>
    </div>`;
  }
  if (sorted.length === 0) {
    html += `<div class="waiting">No friction points detected.</div>`;
  }
  for (const fp of sorted) {
    const sev   = (fp.severity || "low").toLowerCase();
    const color = SEV_COLOR[sev] || "#888";
    const bg    = SEV_BG[sev] || "rgba(255,255,255,.05)";
    const affected = (fp.affected_personas || []).map(a => `<span class="ptag">${esc(a)}</span>`).join(" ");
    html += `<div class="fp-item" style="border-color:${color};background:${bg}">
      <div class="fp-header">
        <span class="sev-badge" style="background:${color}">${sev.toUpperCase()}</span>
        <span class="fp-el">${esc(fp.element || "?")}</span>
        ${affected}
      </div>
      <div class="fp-desc">${esc(fp.description || "")}</div>
      <div class="fp-task">Task: ${esc(fp.task || "")}</div>
    </div>`;
  }

  resultsBody.innerHTML = html;
}

// ── FIXES tab — visual before/after cards ──────────────────────────────────────
function onVisualFix(m) {
  visualFixes.push(m);
  fixState[m.id] = null;

  // Clear placeholder on first fix
  const hint = fixesBody.querySelector(".empty-hint");
  if (hint) hint.remove();

  fixesBody.appendChild(buildFixCard(m));

  badgeFixes.style.display = "inline-block";
  badgeFixes.textContent = String(visualFixes.length);
  document.getElementById("fixes-footer").style.display = "flex";
  updateFixCounter();
}

function buildFixCard(fix) {
  const sev   = (fix.severity || "medium").toLowerCase();
  const color = SEV_COLOR[sev] || "#888";
  const beforeSrc = fix.before ? "data:image/jpeg;base64," + fix.before : "";
  const afterSrc  = fix.after  ? "data:image/jpeg;base64," + fix.after  : "";
  const id = CSS.escape(fix.id);

  const card = document.createElement("div");
  card.className = "fix-card";
  card.dataset.id = fix.id;

  card.innerHTML =
    `<div class="fix-head">
      <span class="sev-badge" style="background:${color}">${sev.toUpperCase()}</span>
      <span class="fix-el">${esc(fix.element || "?")}</span>
    </div>
    <div class="fix-description">${esc(fix.description || "")}</div>
    <div class="ss-pair">
      <div class="ss-col">
        <div class="ss-col-label before-label">BEFORE</div>
        ${beforeSrc
          ? `<img class="ss-img" src="${beforeSrc}" alt="Before" onclick="openLightbox(this.src,'Before: ${esc(fix.element||"")}')">`
          : `<div class="ss-placeholder">No screenshot</div>`}
      </div>
      <div class="ss-col">
        <div class="ss-col-label after-label">AFTER</div>
        ${afterSrc
          ? `<img class="ss-img" src="${afterSrc}" alt="After" onclick="openLightbox(this.src,'After: ${esc(fix.element||"")}')">`
          : `<div class="ss-placeholder">No screenshot</div>`}
      </div>
    </div>
    <div class="fix-actions">
      <button class="btn-accept" id="btn-accept-${id}" onclick="acceptFix('${fix.id}')">✓ Accept</button>
      <button class="btn-reject" id="btn-reject-${id}" onclick="rejectFix('${fix.id}')">✗ Reject</button>
    </div>`;

  return card;
}

function acceptFix(id) {
  fixState[id] = fixState[id] === "accepted" ? null : "accepted";
  syncFixCard(id);
  updateFixCounter();
}

function rejectFix(id) {
  fixState[id] = fixState[id] === "rejected" ? null : "rejected";
  syncFixCard(id);
  updateFixCounter();
}

function syncFixCard(id) {
  const card = fixesBody.querySelector(`[data-id="${id}"]`);
  if (!card) return;
  const state = fixState[id];
  card.classList.toggle("accepted", state === "accepted");
  card.classList.toggle("rejected", state === "rejected");
  const escId = CSS.escape(id);
  const aBtn = document.getElementById("btn-accept-" + escId);
  const rBtn = document.getElementById("btn-reject-" + escId);
  if (aBtn) aBtn.classList.toggle("active", state === "accepted");
  if (rBtn) rBtn.classList.toggle("active", state === "rejected");
}

function updateFixCounter() {
  const accepted = Object.values(fixState).filter(s => s === "accepted").length;
  const rejected = Object.values(fixState).filter(s => s === "rejected").length;
  document.getElementById("fixes-counter").textContent = `${accepted} accepted · ${rejected} rejected`;
  const applyBtn = document.getElementById("apply-all-btn");
  applyBtn.disabled = accepted === 0;
}

function openLightbox(src, caption) {
  lbImg.src = src;
  lbCaption.textContent = caption;
  lightbox.classList.add("open");
}

async function applyAccepted() {
  const accepted = visualFixes.filter(f => fixState[f.id] === "accepted");
  if (!accepted.length) return;

  const css    = accepted.map(f => f.css || "").filter(Boolean).join("\n");
  const jsCode = accepted.map(f => f.js  || "").filter(Boolean).join(";\n");

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) { alert("No active tab found."); return; }

    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: (css, jsCode) => {
        if (css) {
          const s = document.createElement("style");
          s.textContent = css;
          document.head.appendChild(s);
        }
        if (jsCode) {
          try { new Function(jsCode)(); } catch (e) { console.warn("Fix JS error:", e); }
        }
      },
      args: [css, jsCode],
    });

    const applyBtn = document.getElementById("apply-all-btn");
    applyBtn.textContent = "✓ Applied!";
    applyBtn.style.cssText = "background:#44dd88;color:#000";
    setTimeout(() => {
      applyBtn.textContent = "Apply All Accepted";
      applyBtn.style.cssText = "";
      updateFixCounter();
    }, 3000);
  } catch (err) {
    console.error("applyAccepted:", err);
    alert("Failed to apply: " + err.message);
  }
}

// ── Utilities ──────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function now() {
  return new Date().toTimeString().slice(0, 8);
}

function keyFromLabel(label) {
  if (!label) return null;
  const l = label.toLowerCase();
  if (l.includes("elderly"))    return "elderly_user";
  if (l.includes("first"))      return "first_time_user";
  if (l.includes("first-time")) return "first_time_user";
  return null;
}

// ── Boot ───────────────────────────────────────────────────────────────────────
connect();
