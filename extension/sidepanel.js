/* sidepanel.js — connects directly to the Python WebSocket server on port 7655 */

const WS_URL = "ws://localhost:7655";
const PERSONA_COLORS = {
  "Elderly User":       "#ffd700",
  "ADHD User":          "#ff88aa",
  "Non-Native English": "#88ccff",
  "Pipeline":           "#a0a8c0",
};
const PERSONA_KEY_MAP = {
  "elderly_user":       "elderly_user",
  "adhd_user":          "adhd_user",
  "non_native_english": "non_native_english",
};

let ws = null;
let reconnectTimer = null;
let taskCount = 0;
let completedTasks = {};   // persona_key -> count passed
let totalTasks = {};       // persona_key -> total
let frictionCount = 0;

// ── DOM refs ──────────────────────────────────────────────────────────────────
const wsDot       = document.getElementById("ws-dot");
const wsLabel     = document.getElementById("ws-label");
const urlDisplay  = document.getElementById("url-display");
const progressBar = document.getElementById("progress-bar");
const progressLabel = document.getElementById("progress-label-text");
const progressFraction = document.getElementById("progress-fraction");
const frictionCountEl = document.getElementById("friction-count");
const frictionLatest  = document.getElementById("friction-latest");
const logContainer = document.getElementById("log-container");
const emptyState   = document.getElementById("empty-state");
const doneBanner   = document.getElementById("done-banner");
const reportLink   = document.getElementById("report-link");
const clearBtn     = document.getElementById("clear-btn");

clearBtn.addEventListener("click", () => {
  logContainer.innerHTML = "";
});

// ── WebSocket ─────────────────────────────────────────────────────────────────
function connect() {
  if (ws) {
    try { ws.close(); } catch (_) {}
  }
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    setWsState("connected");
  };

  ws.onclose = () => {
    setWsState("disconnected");
    reconnectTimer = setTimeout(connect, 3000);
  };

  ws.onerror = () => {
    setWsState("error");
  };

  ws.onmessage = (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch (_) { return; }
    handleMessage(msg);
  };
}

function setWsState(state) {
  wsDot.className = "ws-status";
  if (state === "connected") {
    wsDot.classList.add("connected");
    wsLabel.textContent = "Connected to pipeline";
  } else if (state === "error") {
    wsDot.classList.add("error");
    wsLabel.textContent = "Connection error — retrying…";
  } else {
    wsLabel.textContent = "Disconnected — reconnecting…";
  }
}

// ── Message handlers ──────────────────────────────────────────────────────────
function handleMessage(msg) {
  switch (msg.type) {
    case "pipeline_start":
      onPipelineStart(msg);
      break;
    case "task_list":
      onTaskList(msg);
      break;
    case "log":
      onLog(msg);
      break;
    case "persona_update":
      onPersonaUpdate(msg);
      break;
    case "friction_found":
      onFrictionFound(msg);
      break;
    case "pipeline_done":
      onPipelineDone(msg);
      break;
  }
}

function onPipelineStart(msg) {
  urlDisplay.textContent = msg.url || "…";
  frictionCount = 0;
  taskCount = 0;
  completedTasks = {};
  totalTasks = {};
  frictionCountEl.textContent = "0";
  frictionLatest.textContent = "none yet";
  progressBar.style.width = "0%";
  progressLabel.textContent = "Pipeline started";
  progressFraction.textContent = "";
  doneBanner.style.display = "none";
  // reset persona cards
  ["elderly_user", "adhd_user", "non_native_english"].forEach(k => {
    document.getElementById(`score-${k}`).textContent = "—";
    document.getElementById(`status-${k}`).textContent = "";
    document.getElementById(`card-${k}`).classList.remove("active");
  });
  appendLog({ ts: now(), persona: "Pipeline", task_num: 0, msg: `▶ Starting pipeline: ${msg.url}` });
}

function onTaskList(msg) {
  taskCount = (msg.tasks || []).length;
  progressLabel.textContent = `0 / ${taskCount} tasks`;
  appendLog({ ts: now(), persona: "Pipeline", task_num: 0, msg: `📋 ${taskCount} tasks generated` });
  (msg.tasks || []).forEach((t, i) => {
    appendLog({ ts: now(), persona: "Pipeline", task_num: i + 1, msg: `  ${i + 1}. ${t}` });
  });
}

function onLog(msg) {
  appendLog(msg);
  // Update progress: count distinct "✓" / "✗" entries per persona
  if (msg.msg && (msg.msg.includes("✓") || msg.msg.includes("PASS"))) {
    const k = personaKeyFromLabel(msg.persona);
    if (k) {
      completedTasks[k] = (completedTasks[k] || 0) + 1;
      updateProgress();
      const card = document.getElementById(`card-${k}`);
      if (card) card.classList.add("active");
    }
  }
}

function onPersonaUpdate(msg) {
  const key = msg.persona_key || personaKeyFromLabel(msg.persona);
  if (!key) return;
  const passed = msg.passed ?? 0;
  const total  = msg.total ?? taskCount;
  totalTasks[key] = total;
  const scoreEl = document.getElementById(`score-${key}`);
  const statusEl = document.getElementById(`status-${key}`);
  if (scoreEl) scoreEl.textContent = `${passed}/${total}`;
  if (statusEl) {
    const pct = total > 0 ? Math.round(passed / total * 100) : 0;
    statusEl.textContent = `${pct}% pass`;
    statusEl.style.color = pct >= 70 ? "#44dd88" : (pct >= 40 ? "#ffcc00" : "#ff4444");
  }
  updateProgress();
}

function onFrictionFound(msg) {
  frictionCount = msg.count ?? frictionCount + 1;
  frictionCountEl.textContent = String(frictionCount);
  if (msg.latest) {
    frictionLatest.textContent = msg.latest;
  }
}

function onPipelineDone(msg) {
  doneBanner.style.display = "block";
  if (msg.report_url) {
    reportLink.href = msg.report_url;
  }
  progressBar.style.width = "100%";
  progressLabel.textContent = "Pipeline complete";
  appendLog({ ts: now(), persona: "Pipeline", task_num: 0, msg: "✓ Pipeline complete — report ready" });
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function appendLog(entry) {
  if (emptyState) emptyState.remove();

  const row = document.createElement("div");
  row.className = "log-entry";

  const msg = entry.msg || "";
  const isPass = msg.includes("✓") || msg.includes("PASS");
  const isFail = msg.includes("✗") || msg.includes("FAIL");
  const msgClass = isPass ? "log-msg log-pass" : (isFail ? "log-msg log-fail" : "log-msg");

  const persona = entry.persona || "";
  const color = PERSONA_COLORS[persona] || "#888899";
  const taskNum = entry.task_num ?? 0;

  row.innerHTML =
    `<span class="log-ts">${escHtml(entry.ts || "")}</span>` +
    `<span class="log-persona" style="color:${color};">[${escHtml(persona)}]</span>` +
    `<span class="log-ts">T${taskNum}</span>` +
    `<span class="${msgClass}">${escHtml(msg)}</span>`;

  logContainer.appendChild(row);
  logContainer.scrollTop = logContainer.scrollHeight;
}

function updateProgress() {
  if (!taskCount) return;
  const personas = ["elderly_user", "adhd_user", "non_native_english"];
  const total = taskCount * personas.length;
  let done = 0;
  personas.forEach(k => { done += completedTasks[k] || 0; });
  const pct = Math.min(100, Math.round(done / total * 100));
  progressBar.style.width = pct + "%";
  progressLabel.textContent = `${done} / ${total} task runs`;
  progressFraction.textContent = `${pct}%`;
}

function personaKeyFromLabel(label) {
  if (!label) return null;
  const lower = label.toLowerCase();
  if (lower.includes("elderly")) return "elderly_user";
  if (lower.includes("adhd"))    return "adhd_user";
  if (lower.includes("native"))  return "non_native_english";
  return null;
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function now() {
  const d = new Date();
  return d.toTimeString().slice(0, 8);
}

// ── Start ─────────────────────────────────────────────────────────────────────
connect();
