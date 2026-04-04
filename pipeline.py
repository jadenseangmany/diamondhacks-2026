"""
Usability testing pipeline.

Steps:
  1. summarize_site(url)            — fetch HTML, ask Claude to summarise purpose & key flows
  2. generate_tasks(summary)        — ask Claude to return 5 usability tasks
  3. execute_tasks(tasks, url)      — run each task through browser (default persona)
  3b. execute_tasks_multi_persona   — run all tasks × 3 personas in parallel
  4. analyze_confusion(traces)      — ask Claude to identify friction points & severity
  4b. analyze_multi_persona         — combine persona results into one unified friction report
  5. suggest_fixes(analysis)        — ask Claude for specific HTML/CSS fixes
  6. generate_html_report(results)  — write dark-theme report.html and open in browser

Live log system:
  - report.html is opened immediately (skeleton with JS polling)
  - log.json is written in real time as persona threads execute actions
  - The HTML page fetches log.json every 2s and appends new entries live
  - When pipeline finishes, report_data.json is written, triggering a page reload
    to the final static report
"""

import asyncio
import http.server
import json
import os
import re
import socket
import subprocess
import threading
import webbrowser
from datetime import datetime
from html import escape
from pathlib import Path

import anthropic
import requests
from bs4 import BeautifulSoup

import fake_browser_use as _fbu
from fake_browser_use import (
    PERSONA_PROFILES,
    format_trace,
    run_task,
    run_tasks_multi_persona,
)

# ---------------------------------------------------------------------------
# WebSocket broadcaster (Chrome extension live feed on port 7655)
# ---------------------------------------------------------------------------

try:
    import websockets  # type: ignore
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False


class WebSocketBroadcaster:
    """
    Runs an asyncio WebSocket server in a background daemon thread.
    Sync code calls broadcast(msg_dict) which is thread-safe.
    """

    PORT = 7655

    def __init__(self) -> None:
        self._clients: set = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        if _WS_AVAILABLE:
            t = threading.Thread(target=self._run_server, daemon=True, name="ws-broadcaster")
            t.start()
            self._ready.wait(timeout=3)

    def _run_server(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        async def handler(ws):
            self._clients.add(ws)
            self._ready.set()
            try:
                await ws.wait_closed()
            finally:
                self._clients.discard(ws)

        try:
            async with websockets.serve(handler, "localhost", self.PORT):
                self._ready.set()
                await asyncio.Future()  # run forever
        except OSError:
            # Port already in use — continue without WS
            self._ready.set()

    def broadcast(self, msg: dict) -> None:
        """Thread-safe: enqueue a JSON broadcast to all connected extension clients."""
        if not _WS_AVAILABLE or not self._loop or not self._clients:
            return
        data = json.dumps(msg)

        async def _send_all():
            dead = set()
            for ws in list(self._clients):
                try:
                    await ws.send(data)
                except Exception:
                    dead.add(ws)
            self._clients -= dead

        asyncio.run_coroutine_threadsafe(_send_all(), self._loop)


_ws_broadcaster: WebSocketBroadcaster | None = None


def _ws_broadcast(msg: dict) -> None:
    if _ws_broadcaster is not None:
        _ws_broadcaster.broadcast(msg)


# ---------------------------------------------------------------------------
# Claude client
# ---------------------------------------------------------------------------

_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
_MODEL = "claude-opus-4-6"


def _ask(system: str, user: str, *, label: str = "") -> str:
    """Single Claude call with adaptive thinking. Returns text response."""
    response = _client.messages.create(
        model=_MODEL,
        max_tokens=4096,
        thinking={"type": "adaptive"},
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = next((b.text for b in response.content if b.type == "text"), "")
    return text.strip()


# ---------------------------------------------------------------------------
# Live log writer
# ---------------------------------------------------------------------------

class LogWriter:
    """Thread-safe appender to log.json, read by the live report page."""

    def __init__(self, path: str) -> None:
        self.path = Path(path).resolve()
        self._lock = threading.Lock()
        self._entries: list[dict] = []
        self._flush()

    def append(self, persona: str, task_num: int, msg: str) -> None:
        entry = {
            "ts":       datetime.now().strftime("%H:%M:%S.%f")[:11],
            "persona":  persona,
            "task_num": task_num,
            "msg":      msg,
        }
        with self._lock:
            self._entries.append(entry)
            self._flush()

    def entries(self) -> list[dict]:
        with self._lock:
            return list(self._entries)

    def _flush(self) -> None:
        try:
            self.path.write_text(json.dumps(self._entries), encoding="utf-8")
        except OSError:
            pass


_log_writer: LogWriter | None = None


def _pipeline_log(msg: str, task_num: int = 0) -> None:
    """Write a pipeline-level (non-persona) entry to log.json."""
    if _log_writer is not None:
        _log_writer.append("Pipeline", task_num, msg)


def init_live_log(log_path: str) -> LogWriter:
    """Create the LogWriter and wire it into fake_browser_use._live_log."""
    global _log_writer, _ws_broadcaster
    _log_writer = LogWriter(log_path)
    if _ws_broadcaster is None:
        _ws_broadcaster = WebSocketBroadcaster()

    def _cb(label: str, task_num: int, msg: str) -> None:
        _log_writer.append(label, task_num, msg)
        _ws_broadcast({
            "type": "log",
            "ts": datetime.now().strftime("%H:%M:%S"),
            "persona": label,
            "task_num": task_num,
            "msg": msg,
        })

    _fbu.set_log_callback(_cb)
    return _log_writer


# ---------------------------------------------------------------------------
# Background HTTP server (needed for fetch() to work from file:// pages)
# ---------------------------------------------------------------------------

def _start_report_server(directory: str, preferred_port: int = 7654) -> int:
    """
    Serve `directory` over HTTP in a daemon thread.
    Returns the port actually used.
    """
    dir_path = str(Path(directory).resolve())

    class _Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=dir_path, **kwargs)
        def log_message(self, *_args):
            pass  # silence request logs

    for port in range(preferred_port, preferred_port + 20):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("", port))
            server = http.server.HTTPServer(("", port), _Handler)
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            return port
        except OSError:
            continue
    raise RuntimeError("No available port in range for report HTTP server")


# ---------------------------------------------------------------------------
# Step 1 — Summarise the site
# ---------------------------------------------------------------------------

def summarize_site(url: str) -> dict:
    _pipeline_log(f"Step 1: Fetching and analyzing {url}")
    print(f"[1/5] Fetching {url} ...")
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "UsabilityBot/1.0"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for tag in soup(["script", "style", "noscript", "svg", "path"]):
            tag.decompose()

        parts = []
        if soup.title:
            parts.append(f"<title>{soup.title.get_text(strip=True)}</title>")
        for tag in soup.find_all(["h1", "h2", "h3", "nav", "main", "header", "footer", "section"]):
            text = tag.get_text(" ", strip=True)
            if text:
                parts.append(f"<{tag.name}>{text[:300]}</{tag.name}>")

        html_summary = "\n".join(parts[:80])
    except Exception as exc:
        html_summary = f"[Could not fetch page: {exc}]"

    system = (
        "You are a UX researcher. Analyse the provided HTML snippet and return ONLY "
        "a JSON object with keys: purpose (string), key_flows (array of up to 5 short "
        "strings describing the main things a user can do), target_audience (string). "
        "No markdown fences, no commentary — pure JSON."
    )
    user = f"URL: {url}\n\nHTML:\n{html_summary}"
    raw = _ask(system, user, label="summarize_site")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(match.group()) if match else {"purpose": raw, "key_flows": [], "target_audience": ""}

    data["url"] = url
    _pipeline_log(f"Site purpose: {data.get('purpose','')[:80]}")
    return data


# ---------------------------------------------------------------------------
# Step 2 — Generate usability tasks
# ---------------------------------------------------------------------------

def generate_tasks(summary: dict) -> list[str]:
    _pipeline_log("Step 2: Generating usability tasks...")
    print("[2/5] Generating usability tasks ...")

    system = (
        "You are a UX researcher designing a usability test. "
        "Return ONLY a JSON array of exactly 5 task strings. "
        "Each task should be a concrete action a real user would try to accomplish "
        "on the site (e.g. 'Find the price of the MacBook Pro 14-inch'). "
        "Tasks must be realistic, varied, and cover different user goals. "
        "No markdown, no commentary — pure JSON array."
    )
    user = (
        f"Site: {summary['url']}\n"
        f"Purpose: {summary.get('purpose', '')}\n"
        f"Key flows: {json.dumps(summary.get('key_flows', []))}\n"
        f"Target audience: {summary.get('target_audience', '')}\n\n"
        "Generate 5 usability test tasks."
    )

    raw = _ask(system, user, label="generate_tasks")

    try:
        tasks = json.loads(raw)
        if isinstance(tasks, list):
            tasks = [str(t) for t in tasks[:5]]
    except json.JSONDecodeError:
        lines = re.split(r"\n|\d+[.)]\s*", raw)
        tasks = [l.strip().strip('"') for l in lines if len(l.strip()) > 10][:5]

    for i, t in enumerate(tasks, 1):
        _pipeline_log(f"Task {i}: {t[:70]}", task_num=i)
    _ws_broadcast({"type": "task_list", "tasks": tasks})
    return tasks


# ---------------------------------------------------------------------------
# Step 3 — Execute tasks (default / real API persona)
# ---------------------------------------------------------------------------

def execute_tasks(tasks: list[str], url: str) -> list[dict]:
    _pipeline_log(f"Step 3: Executing {len(tasks)} tasks via browser...")
    print(f"[3/5] Executing {len(tasks)} tasks via browser ...")
    traces = []
    for i, task in enumerate(tasks, 1):
        print(f"       Task {i}/{len(tasks)}: {task[:60]}")
        trace = run_task(task, url, persona_label=f"Task {i}")
        traces.append(format_trace(trace))
    return traces


# ---------------------------------------------------------------------------
# Step 3b — Execute tasks × 3 personas in parallel
# ---------------------------------------------------------------------------

def execute_tasks_multi_persona(tasks: list[str], url: str) -> dict[str, list[dict]]:
    personas = list(PERSONA_PROFILES.keys())
    _pipeline_log(f"Step 3b: Running {len(tasks)} tasks × {len(personas)} personas in parallel...")
    print(f"[3b] Running {len(tasks)} tasks × {len(personas)} personas in parallel ...")
    for p in personas:
        print(f"       Persona: {PERSONA_PROFILES[p]['label']} — {PERSONA_PROFILES[p]['description']}")

    results = run_tasks_multi_persona(tasks, url)

    for persona, traces in results.items():
        passed = sum(1 for t in traces if t and t.get("success"))
        _pipeline_log(f"{PERSONA_PROFILES[persona]['label']}: {passed}/{len(tasks)} passed")
        print(f"       {PERSONA_PROFILES[persona]['label']}: {passed}/{len(tasks)} passed")
        _ws_broadcast({
            "type": "persona_update",
            "persona_key": persona,
            "persona": PERSONA_PROFILES[persona]["label"],
            "passed": passed,
            "total": len(tasks),
        })

    return results


# ---------------------------------------------------------------------------
# Step 4 — Analyse confusion / friction
# ---------------------------------------------------------------------------

def analyze_confusion(traces: list[dict]) -> dict:
    _pipeline_log("Step 4: Analyzing confusion points...")
    print("[4/5] Analysing confusion points ...")

    system = (
        "You are a senior UX researcher. You receive JSON action traces from a usability test. "
        "Return ONLY a JSON object with:\n"
        "  friction_points: array of objects, each with fields:\n"
        "    - task (which task)\n"
        "    - element (CSS selector or UI element)\n"
        "    - description (what went wrong)\n"
        "    - severity (critical | high | medium | low)\n"
        "    - evidence (list of relevant actions or confusion_points from the trace)\n"
        "  summary: one-paragraph overall assessment\n"
        "  severity_map: object mapping severity level to count\n"
        "No markdown, pure JSON."
    )
    user = f"Usability test traces:\n{json.dumps(traces, indent=2)}"
    raw = _ask(system, user, label="analyze_confusion")

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return {"friction_points": [], "summary": raw, "severity_map": {}}


# ---------------------------------------------------------------------------
# Step 4b — Analyse combined multi-persona results
# ---------------------------------------------------------------------------

def analyze_multi_persona(persona_traces: dict[str, list[dict]]) -> dict:
    _pipeline_log("Step 4b: Running multi-persona friction analysis...")
    print("[4b] Analysing multi-persona friction ...")

    all_traces_by_persona = {
        PERSONA_PROFILES[p]["label"]: traces
        for p, traces in persona_traces.items()
    }

    system = (
        "You are a senior UX researcher. You receive usability test traces from three distinct user "
        "personas. Analyse each persona's experience and the differences between them.\n"
        "Return ONLY a JSON object with:\n"
        "  friction_points: array of objects, each with:\n"
        "    - task (string)\n"
        "    - element (CSS selector or UI element)\n"
        "    - description (what went wrong)\n"
        "    - severity (critical | high | medium | low)\n"
        "    - affected_personas (array of persona labels with issues)\n"
        "    - evidence (list of supporting observations)\n"
        "  persona_summary: object keyed by persona label, each value is a short paragraph\n"
        "  summary: overall paragraph covering all three personas\n"
        "  severity_map: object mapping severity to count\n"
        "  persona_stats: object keyed by persona label with pass_rate (0-1) and avg_time_seconds\n"
        "No markdown, pure JSON."
    )
    user = f"Multi-persona usability traces:\n{json.dumps(all_traces_by_persona, indent=2)}"
    raw = _ask(system, user, label="analyze_multi_persona")

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
            except json.JSONDecodeError:
                result = {}
        else:
            result = {}

    if not result:
        result = {
            "friction_points": [],
            "persona_summary": {},
            "summary": raw,
            "severity_map": {},
            "persona_stats": {},
        }

    fp_count = len(result.get("friction_points", []))
    _pipeline_log(f"Friction analysis: {fp_count} friction points found")
    latest_fp = ""
    fps = result.get("friction_points", [])
    if fps:
        fp0 = fps[0]
        latest_fp = f"[{fp0.get('severity','?').upper()}] {fp0.get('element','?')} — {fp0.get('description','')[:60]}"
    _ws_broadcast({"type": "friction_found", "count": fp_count, "latest": latest_fp})
    return result


# ---------------------------------------------------------------------------
# Step 5 — Suggest HTML/CSS fixes
# ---------------------------------------------------------------------------

def _parse_fix_blocks(text: str) -> list[dict]:
    fixes = []
    blocks = re.split(r"FIX\s+\d+", text, flags=re.IGNORECASE)
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        def _field(name: str, b: str = block) -> str:
            m = re.search(rf"^{name}:\s*(.+)$", b, re.MULTILINE | re.IGNORECASE)
            return m.group(1).strip() if m else ""

        code_match = re.search(r"Code:\s*\n(.*?)(?:\nEND|\Z)", block, re.DOTALL | re.IGNORECASE)
        code = code_match.group(1).strip() if code_match else ""

        priority_raw = _field("Priority")
        try:
            priority = int(priority_raw.split()[0])
        except (ValueError, IndexError):
            priority = 99

        fix = {
            "priority": priority,
            "severity": _field("Severity") or "medium",
            "element":  _field("Element"),
            "problem":  _field("Problem"),
            "fix":      _field("Fix"),
            "code":     code,
        }
        if fix["element"] or fix["problem"]:
            fixes.append(fix)

    return sorted(fixes, key=lambda f: f["priority"])


def suggest_fixes(analysis: dict) -> list[dict]:
    _pipeline_log("Step 5: Generating fix recommendations...")
    print("[5/5] Generating fix recommendations ...")

    friction_points = analysis.get("friction_points", [])
    if not friction_points:
        print("       No friction points found — skipping fix generation.")
        _pipeline_log("No friction points — skipping fixes")
        return []

    fp_list = "\n".join(
        f"{i+1}. [{fp.get('severity','?').upper()}] {fp.get('element','?')} — {fp.get('description','')}"
        for i, fp in enumerate(friction_points)
    )

    system = (
        "You are a senior front-end engineer and UX specialist.\n"
        "For each numbered friction point below, write a fix using this exact format "
        "(repeat the block for every point, separated by a blank line):\n\n"
        "FIX <number>\n"
        "Priority: <1=highest … 5=lowest>\n"
        "Severity: <critical|high|medium|low>\n"
        "Element: <CSS selector or component name>\n"
        "Problem: <one sentence>\n"
        "Fix: <one or two sentences explaining what to change and why>\n"
        "Code:\n"
        "<the exact HTML/CSS/ARIA snippet to apply>\n"
        "END\n\n"
        "Rules:\n"
        "- Output ONLY the FIX blocks — no intro, no summary, no markdown fences.\n"
        "- Every friction point must have exactly one FIX block.\n"
        "- Code must be a real, copy-pasteable snippet (not pseudocode).\n"
        "- Priority 1 = highest user impact."
    )
    user = (
        f"Friction points ({len(friction_points)} total):\n{fp_list}\n\n"
        f"Full analysis:\n{json.dumps(analysis, indent=2)}"
    )

    raw = _ask(system, user, label="suggest_fixes")
    fixes = _parse_fix_blocks(raw)
    _pipeline_log(f"Generated {len(fixes)} fix recommendations")
    return fixes


# ---------------------------------------------------------------------------
# HTML report helpers
# ---------------------------------------------------------------------------

_SEVERITY_COLORS = {
    "critical": "#ff4444",
    "high":     "#ff8800",
    "medium":   "#ffcc00",
    "low":      "#44dd88",
}

_SEVERITY_BG = {
    "critical": "rgba(255,68,68,0.12)",
    "high":     "rgba(255,136,0,0.12)",
    "medium":   "rgba(255,204,0,0.10)",
    "low":      "rgba(68,221,136,0.10)",
}

_PERSONA_LOG_COLORS = {
    "Elderly User":       "#ffd700",
    "ADHD User":          "#ff88aa",
    "Non-Native English": "#88ccff",
    "Pipeline":           "#a0a8c0",
}


def _sev_badge(severity: str) -> str:
    sev = severity.lower()
    color = _SEVERITY_COLORS.get(sev, "#888")
    return (
        f'<span class="badge" style="background:{color};color:#000;'
        f'font-weight:700;padding:2px 8px;border-radius:4px;font-size:0.75rem;">'
        f'{escape(sev.upper())}</span>'
    )


def _persona_comparison_table(tasks: list[str], persona_traces: dict[str, list[dict]]) -> str:
    personas = list(PERSONA_PROFILES.keys())
    header_cells = "".join(
        f'<th>{escape(PERSONA_PROFILES[p]["label"])}</th>' for p in personas
    )
    rows = []
    for i, task in enumerate(tasks):
        cells = f'<td class="task-cell">{escape(task)}</td>'
        for p in personas:
            traces = persona_traces.get(p, [])
            trace = traces[i] if i < len(traces) and traces[i] else {}
            success = trace.get("success", False)
            t_sec = trace.get("total_time_seconds", 0)
            cr = trace.get("completion_rate", 0)
            icon = "✓" if success else "✗"
            icon_color = "#44dd88" if success else "#ff4444"
            cells += (
                f'<td style="text-align:center;">'
                f'<span style="color:{icon_color};font-weight:700;">{icon}</span>'
                f'<br><small>{t_sec:.1f}s · {int(cr*100)}%</small>'
                f'</td>'
            )
        rows.append(f"<tr>{cells}</tr>")

    return f"""
<table class="data-table">
  <thead>
    <tr><th>Task</th>{header_cells}</tr>
  </thead>
  <tbody>{"".join(rows)}</tbody>
</table>
"""


def _friction_heatmap(friction_points: list[dict]) -> str:
    if not friction_points:
        return "<p class='muted'>No friction points detected.</p>"
    items = []
    for fp in friction_points:
        sev = fp.get("severity", "low").lower()
        color = _SEVERITY_COLORS.get(sev, "#888")
        bg = _SEVERITY_BG.get(sev, "rgba(255,255,255,0.05)")
        affected = fp.get("affected_personas", [])
        persona_tags = "".join(
            f'<span class="persona-tag">{escape(a)}</span>' for a in affected
        )
        evidence = fp.get("evidence", [])
        evidence_html = "".join(f"<li>{escape(str(e))}</li>" for e in evidence[:3])
        items.append(f"""
<div class="friction-item" style="border-left:4px solid {color};background:{bg};">
  <div class="friction-header">
    {_sev_badge(sev)}
    <code class="element">{escape(fp.get('element','?'))}</code>
    {persona_tags}
  </div>
  <p class="friction-desc">{escape(fp.get('description',''))}</p>
  <p class="friction-task"><strong>Task:</strong> {escape(fp.get('task',''))}</p>
  {f'<ul class="evidence">{evidence_html}</ul>' if evidence_html else ''}
</div>""")
    return "\n".join(items)


def _screenshots_grid_html(persona_traces: dict[str, list[dict]]) -> str:
    """
    Render the last screenshot from each persona's last task as a grid.
    Screenshots are file paths relative to the report directory.
    """
    items = []
    for persona_key, traces in persona_traces.items():
        profile = PERSONA_PROFILES.get(persona_key, {})
        label = profile.get("label", persona_key)
        color = profile.get("indicator_color", "#888")
        # Find the last screenshot across all tasks for this persona
        last_shot = None
        for trace in reversed(traces):
            if trace and trace.get("screenshots"):
                last_shot = trace["screenshots"][-1]
                break
        if not last_shot:
            continue
        items.append(f"""
<div class="screenshot-card">
  <div class="screenshot-label" style="color:{color};">{escape(label)}</div>
  <img src="{escape(last_shot)}" alt="Last screenshot — {escape(label)}"
       class="screenshot-img" loading="lazy">
</div>""")
    if not items:
        return "<p class='muted'>No screenshots captured (browser_use not active).</p>"
    return '<div class="screenshot-grid">' + "\n".join(items) + "</div>"


def _fixes_html(fixes: list[dict]) -> str:
    if not fixes:
        return "<p class='muted'>No fixes generated.</p>"
    items = []
    for fix in fixes:
        sev = fix.get("severity", "medium").lower()
        code = fix.get("code", "")
        code_block = (
            f'<pre class="code-block"><code>{escape(code)}</code></pre>'
            if code else ""
        )
        items.append(f"""
<div class="fix-card">
  <div class="fix-header">
    <span class="priority-badge">P{fix.get('priority','?')}</span>
    {_sev_badge(sev)}
    <code class="element">{escape(fix.get('element','?'))}</code>
  </div>
  <p><strong>Problem:</strong> {escape(fix.get('problem',''))}</p>
  <p><strong>Fix:</strong> {escape(fix.get('fix',''))}</p>
  {code_block}
</div>""")
    return "\n".join(items)


def _severity_summary_bars(severity_map: dict) -> str:
    levels = ["critical", "high", "medium", "low"]
    total = sum(severity_map.get(l, 0) for l in levels)
    if not total:
        return "<p class='muted'>No severity data.</p>"
    bars = []
    for level in levels:
        count = severity_map.get(level, 0)
        if not count:
            continue
        pct = int(count / total * 100)
        color = _SEVERITY_COLORS.get(level, "#888")
        bars.append(f"""
<div class="sev-row">
  <span class="sev-label">{level.upper()}</span>
  <div class="sev-bar-bg"><div class="sev-bar" style="width:{pct}%;background:{color};"></div></div>
  <span class="sev-count">{count}</span>
</div>""")
    return "\n".join(bars)


def _persona_stat_cards(persona_stats: dict) -> str:
    if not persona_stats:
        return ""
    cards = []
    for label, stats in persona_stats.items():
        pass_pct = int(stats.get("pass_rate", 0) * 100)
        avg_t = stats.get("avg_time_seconds", 0)
        color = "#44dd88" if pass_pct >= 70 else ("#ffcc00" if pass_pct >= 40 else "#ff4444")
        cards.append(f"""
<div class="stat-card">
  <h4>{escape(label)}</h4>
  <div class="stat-value" style="color:{color};">{pass_pct}%</div>
  <div class="stat-label">pass rate</div>
  <div class="stat-time">{avg_t:.1f}s avg</div>
</div>""")
    return '<div class="stat-cards">' + "".join(cards) + "</div>"


def _static_log_html(entries: list[dict]) -> str:
    """Render log entries as static HTML rows for the final report."""
    if not entries:
        return "<p class='muted'>No log entries.</p>"
    rows = []
    for e in entries:
        persona = e.get("persona", "")
        color = _PERSONA_LOG_COLORS.get(persona, "#888899")
        msg = e.get("msg", "")
        is_pass = "✓" in msg
        is_fail = "✗" in msg or "FAILED" in msg
        msg_color = "#44dd88" if is_pass else ("#ff4444" if is_fail else "#c0c8d8")
        rows.append(
            f'<div class="log-entry">'
            f'<span class="log-ts">{escape(e.get("ts",""))}</span>'
            f'<span class="log-persona" style="color:{color};">[{escape(persona)}]</span>'
            f'<span class="log-task-num">T{e.get("task_num",0)}</span>'
            f'<span class="log-msg" style="color:{msg_color};">{escape(msg)}</span>'
            f'</div>'
        )
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# CSS (shared between skeleton and final report)
# ---------------------------------------------------------------------------

CSS = """:root {
  --bg: #0d0d1a;
  --bg2: #13131f;
  --bg3: #1a1a2e;
  --border: #2a2a45;
  --text: #e0e0f0;
  --muted: #888899;
  --accent: #7c6fff;
  --accent2: #4fc3f7;
  --code-bg: #0a0a14;
  --radius: 8px;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.6;
  font-size: 15px;
}
a { color: var(--accent2); }
header {
  background: linear-gradient(135deg, #1a1040 0%, #0d0d1a 100%);
  border-bottom: 1px solid var(--border);
  padding: 2rem;
}
header h1 { font-size: 1.8rem; color: #fff; margin-bottom: 0.4rem; }
header .meta { color: var(--muted); font-size: 0.9rem; }
header .url-badge {
  display: inline-block;
  background: var(--bg3);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 2px 10px;
  font-family: monospace;
  color: var(--accent2);
  margin-top: 0.4rem;
}
.live-badge {
  font-size: 0.75rem;
  color: #ff4444;
  animation: pulse 1.2s ease-in-out infinite;
  margin-left: 0.5rem;
  vertical-align: middle;
}
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }
main { max-width: 1100px; margin: 0 auto; padding: 2rem 1.5rem; }
section { margin-bottom: 3rem; }
h2 {
  font-size: 1.25rem;
  color: #fff;
  border-bottom: 1px solid var(--border);
  padding-bottom: 0.5rem;
  margin-bottom: 1.25rem;
}
h3 { font-size: 1rem; color: var(--accent2); margin-bottom: 0.75rem; }
.card {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.25rem 1.5rem;
  margin-bottom: 1rem;
}
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
.muted { color: var(--muted); font-style: italic; }
.badge {
  display: inline-block;
  border-radius: 4px;
  font-size: 0.72rem;
  font-weight: 700;
  padding: 2px 8px;
  letter-spacing: 0.04em;
}
.element {
  font-family: monospace;
  font-size: 0.85rem;
  background: var(--code-bg);
  border: 1px solid var(--border);
  border-radius: 3px;
  padding: 1px 6px;
  color: #c0c0ff;
}
.persona-tag {
  display: inline-block;
  background: rgba(124,111,255,0.18);
  border: 1px solid rgba(124,111,255,0.35);
  border-radius: 4px;
  font-size: 0.72rem;
  padding: 1px 6px;
  color: #a090ff;
  margin-left: 4px;
}
/* Live log */
.log-container {
  background: var(--code-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 0.75rem 1rem;
  max-height: 480px;
  overflow-y: auto;
  font-family: "SFMono-Regular", Consolas, monospace;
  font-size: 0.8rem;
}
.log-entry {
  display: flex;
  gap: 0.6rem;
  padding: 0.18rem 0;
  border-bottom: 1px solid rgba(255,255,255,0.03);
  line-height: 1.4;
  flex-wrap: nowrap;
}
.log-ts { color: var(--muted); min-width: 84px; flex-shrink: 0; }
.log-persona { min-width: 152px; font-weight: 600; flex-shrink: 0; white-space: nowrap; }
.log-task-num { color: var(--muted); min-width: 24px; flex-shrink: 0; }
.log-msg { flex: 1; word-break: break-word; }
.loading-placeholder {
  color: var(--muted);
  font-style: italic;
  padding: 1rem 0;
}
/* Data table */
.data-table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
.data-table th {
  background: var(--bg3);
  color: var(--accent2);
  padding: 0.6rem 0.9rem;
  text-align: left;
  border-bottom: 2px solid var(--border);
  font-size: 0.8rem;
  letter-spacing: 0.05em;
}
.data-table td {
  padding: 0.6rem 0.9rem;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}
.data-table tr:hover td { background: var(--bg3); }
.task-cell { max-width: 320px; }
/* Friction heatmap */
.friction-item {
  padding: 1rem 1.25rem;
  border-radius: var(--radius);
  margin-bottom: 0.8rem;
}
.friction-header { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; margin-bottom: 0.5rem; }
.friction-desc { margin-bottom: 0.3rem; }
.friction-task { color: var(--muted); font-size: 0.85rem; margin-bottom: 0.3rem; }
.evidence { margin-top: 0.4rem; padding-left: 1.2rem; color: var(--muted); font-size: 0.85rem; }
/* Severity bars */
.sev-row { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.5rem; }
.sev-label { width: 70px; font-size: 0.75rem; font-weight: 700; letter-spacing: 0.06em; color: var(--muted); }
.sev-bar-bg { flex: 1; height: 12px; background: var(--bg3); border-radius: 6px; overflow: hidden; }
.sev-bar { height: 100%; border-radius: 6px; }
.sev-count { width: 24px; text-align: right; color: var(--muted); font-size: 0.85rem; }
/* Persona stat cards */
.stat-cards { display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem; }
.stat-card {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1rem 1.25rem;
  text-align: center;
  flex: 1;
  min-width: 140px;
}
.stat-card h4 { font-size: 0.82rem; color: var(--muted); margin-bottom: 0.5rem; }
.stat-value { font-size: 2rem; font-weight: 700; line-height: 1; }
.stat-label { font-size: 0.75rem; color: var(--muted); margin-top: 0.2rem; }
.stat-time { font-size: 0.85rem; margin-top: 0.4rem; }
/* Fix cards */
.fix-card {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.25rem 1.5rem;
  margin-bottom: 1rem;
}
.fix-header { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; margin-bottom: 0.75rem; }
.priority-badge {
  display: inline-block;
  background: var(--accent);
  color: #fff;
  font-weight: 700;
  font-size: 0.75rem;
  padding: 2px 8px;
  border-radius: 4px;
}
.code-block {
  background: var(--code-bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 1rem;
  margin-top: 0.75rem;
  overflow-x: auto;
  font-family: "SFMono-Regular", Consolas, monospace;
  font-size: 0.82rem;
  line-height: 1.55;
  color: #c0d0ff;
}
.fix-card p { margin-top: 0.4rem; }
.summary-text {
  background: var(--bg2);
  border-left: 3px solid var(--accent);
  padding: 1rem 1.25rem;
  border-radius: 0 var(--radius) var(--radius) 0;
  line-height: 1.7;
}
.flow-list { list-style: none; }
.flow-list li { padding: 0.4rem 0; border-bottom: 1px solid var(--border); }
.flow-list li::before { content: "→ "; color: var(--accent2); }
.task-list { list-style: none; counter-reset: task-counter; }
.task-list li {
  counter-increment: task-counter;
  padding: 0.5rem 0;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: flex-start;
  gap: 0.6rem;
}
.task-list li::before {
  content: counter(task-counter);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 22px;
  height: 22px;
  background: var(--accent);
  border-radius: 50%;
  font-size: 0.75rem;
  font-weight: 700;
  color: #fff;
  flex-shrink: 0;
  margin-top: 2px;
}
.persona-summary {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1rem 1.25rem;
  margin-bottom: 0.75rem;
}
.persona-summary h4 { color: var(--accent2); margin-bottom: 0.4rem; font-size: 0.92rem; }
/* Screenshots grid */
.screenshot-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 1rem;
  margin-top: 0.5rem;
}
.screenshot-card {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}
.screenshot-label {
  padding: 0.5rem 0.9rem;
  font-size: 0.82rem;
  font-weight: 700;
  border-bottom: 1px solid var(--border);
}
.screenshot-img {
  width: 100%;
  height: auto;
  display: block;
  max-height: 300px;
  object-fit: cover;
  object-position: top;
}
footer {
  text-align: center;
  color: var(--muted);
  font-size: 0.82rem;
  padding: 2rem;
  border-top: 1px solid var(--border);
}
"""

# JS injected into the skeleton for live polling (not needed in final static report)
_SKELETON_JS = """
<script>
const PERSONA_COLORS = {
  'Elderly User':       '#ffd700',
  'ADHD User':          '#ff88aa',
  'Non-Native English': '#88ccff',
  'Pipeline':           '#a0a8c0',
};

function escHtml(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Live log polling ──────────────────────────────────────────────────────
let lastLogCount = 0;
async function pollLog() {
  try {
    const r = await fetch('log.json?t=' + Date.now());
    if (!r.ok) return;
    const entries = await r.json();
    if (entries.length <= lastLogCount) return;
    const container = document.getElementById('log-entries');
    entries.slice(lastLogCount).forEach(e => {
      const div = document.createElement('div');
      div.className = 'log-entry';
      const color = PERSONA_COLORS[e.persona] || '#888899';
      const msg   = e.msg || '';
      const isPass = msg.includes('\\u2713');   // ✓
      const isFail = msg.includes('\\u2717') || msg.toUpperCase().includes('FAIL');
      const msgColor = isPass ? '#44dd88' : isFail ? '#ff4444' : '#c0c8d8';
      div.innerHTML =
        '<span class="log-ts">'        + escHtml(e.ts || '')       + '</span>' +
        '<span class="log-persona" style="color:' + color + '">[' + escHtml(e.persona || '') + ']</span>' +
        '<span class="log-task-num">T' + (e.task_num || 0)         + '</span>' +
        '<span class="log-msg" style="color:' + msgColor + '">'   + escHtml(msg) + '</span>';
      container.appendChild(div);
    });
    container.scrollTop = container.scrollHeight;
    lastLogCount = entries.length;
  } catch(_) {}
}
setInterval(pollLog, 2000);
pollLog();

// ── Completion polling — reload to final static report ────────────────────
let _doneTries = 0;
const _doneInterval = setInterval(async function() {
  try {
    const r = await fetch('report_data.json?t=' + Date.now());
    if (!r.ok) return;
    const data = await r.json();
    if (data.done) {
      clearInterval(_doneInterval);
      const el = document.getElementById('pipeline-status');
      if (el) el.textContent = '\\u2713 Pipeline complete — loading final report...';
      setTimeout(() => window.location.reload(), 1200);
    }
  } catch(_) {}
  if (++_doneTries > 400) clearInterval(_doneInterval);
}, 2500);
</script>
"""


# ---------------------------------------------------------------------------
# Step 6a — Generate live skeleton report (opened immediately)
# ---------------------------------------------------------------------------

def generate_report_skeleton(url: str, output_path: str) -> str:
    """
    Write an initial report.html with a live log section that polls log.json.
    Opens the file via HTTP (not file://) so fetch() works in Chrome.
    Returns the absolute path.
    """
    out = Path(output_path).resolve()
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    html = (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="UTF-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f"  <title>Usability Report — {escape(url)} (Live)</title>\n"
        f"  <style>{CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        "<header>\n"
        '  <h1>Usability Report <span class="live-badge">&#9679; LIVE</span></h1>\n'
        f'  <div class="url-badge">{escape(url)}</div>\n'
        f'  <div class="meta" id="pipeline-status">Pipeline started {generated_at} — running...</div>\n'
        "</header>\n"
        "<main>\n"
        '<section id="live-log">\n'
        "  <h2>Live Agent Log</h2>\n"
        '  <div class="log-container" id="log-entries"></div>\n'
        '  <p class="muted" style="margin-top:0.5rem;font-size:0.8rem;">Updates every 2s</p>\n'
        "</section>\n"
        '<section id="summary"><h2>Site Summary</h2>'
        '<div class="loading-placeholder">Analyzing site...</div></section>\n'
        '<section id="tasks"><h2>Usability Tasks</h2>'
        '<div class="loading-placeholder">Generating tasks...</div></section>\n'
        '<section id="persona-comparison"><h2>Persona Comparison</h2>'
        '<div class="loading-placeholder">Running persona sessions...</div></section>\n'
        '<section id="friction"><h2>Friction Heatmap</h2>'
        '<div class="loading-placeholder">Analyzing friction points...</div></section>\n'
        '<section id="fixes"><h2>Recommended Fixes</h2>'
        '<div class="loading-placeholder">Generating fixes...</div></section>\n'
        "</main>\n"
        "<footer>Claude Usability Pipeline &mdash; Live View</footer>\n"
        + _SKELETON_JS
        + "\n</body>\n</html>"
    )

    out.write_text(html, encoding="utf-8")
    return str(out)


# ---------------------------------------------------------------------------
# Step 6b — Generate final static report (replaces skeleton when done)
# ---------------------------------------------------------------------------

def generate_html_report(results: dict, output_path: str = "report.html") -> str:
    """
    Generate the complete dark-themed static HTML report.
    Reads log entries from log.json if present.
    Overwrites report.html (the browser reloads via report_data.json signal).
    Returns the absolute file path.
    """
    url = results.get("url", "")
    summary = results.get("summary", {})
    tasks = results.get("tasks", [])
    traces = results.get("traces", [])
    fixes = results.get("fixes", [])
    persona_traces = results.get("persona_traces", {})
    multi_analysis = results.get("multi_analysis", {})
    analysis = results.get("analysis", {})

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── Fix 1: pass/fail counts from persona_traces ─────────────────────────
    # A task passes if at least one persona completed it successfully.
    if persona_traces and tasks:
        pass_count = 0
        for i in range(len(tasks)):
            for p_traces in persona_traces.values():
                if i < len(p_traces) and p_traces[i] and p_traces[i].get("success"):
                    pass_count += 1
                    break
        fail_count = len(tasks) - pass_count
    else:
        pass_count = sum(1 for t in traces if t.get("success"))
        fail_count = len(traces) - pass_count

    # ── Fix 2: correct friction source ──────────────────────────────────────
    # Only use multi_analysis if it actually has friction points.
    if multi_analysis.get("friction_points"):
        friction_source = multi_analysis
    elif analysis.get("friction_points"):
        friction_source = analysis
    else:
        friction_source = multi_analysis or analysis

    sev_map = friction_source.get("severity_map", {})
    friction_points = friction_source.get("friction_points", [])
    sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    friction_points_sorted = sorted(
        friction_points, key=lambda x: sev_order.get(x.get("severity", "low"), 3)
    )
    overall_summary = friction_source.get("summary", "")

    # ── Log entries ──────────────────────────────────────────────────────────
    log_entries: list[dict] = []
    if _log_writer is not None:
        log_entries = _log_writer.entries()
    else:
        log_path = Path(output_path).parent / "log.json"
        if log_path.exists():
            try:
                log_entries = json.loads(log_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass

    # ── Site summary section ─────────────────────────────────────────────────
    flows_html = "".join(f"<li>{escape(f)}</li>" for f in summary.get("key_flows", []))
    summary_section = f"""
<section id="summary">
  <h2>Site Summary</h2>
  <div class="grid-2">
    <div class="card">
      <h3>Purpose</h3><p>{escape(summary.get('purpose',''))}</p><br>
      <h3>Target Audience</h3><p>{escape(summary.get('target_audience',''))}</p>
    </div>
    <div class="card">
      <h3>Key Flows</h3>
      <ul class="flow-list">{flows_html}</ul>
    </div>
  </div>
</section>"""

    # ── Tasks section ────────────────────────────────────────────────────────
    task_items = "".join(f"<li>{escape(t)}</li>" for t in tasks)
    tasks_section = f"""
<section id="tasks">
  <h2>Usability Tasks ({len(tasks)} total &middot; {pass_count} passed &middot; {fail_count} failed)</h2>
  <ul class="task-list">{task_items}</ul>
</section>"""

    # ── Persona comparison section ───────────────────────────────────────────
    if persona_traces:
        stat_cards = _persona_stat_cards(multi_analysis.get("persona_stats", {}))
        comparison_table = _persona_comparison_table(tasks, persona_traces)
        persona_summaries = multi_analysis.get("persona_summary", {})
        ps_html = "".join(
            f'<div class="persona-summary"><h4>{escape(k)}</h4><p>{escape(v)}</p></div>'
            for k, v in persona_summaries.items()
        )
        persona_inner = f"""
{stat_cards}
{comparison_table}
<h3 style="margin-top:1.5rem;">Persona Insights</h3>
{ps_html}"""
    else:
        persona_inner = "<p class='muted'>Multi-persona analysis not available.</p>"

    persona_section = f"""
<section id="persona-comparison">
  <h2>Persona Comparison</h2>
  {persona_inner}
</section>"""

    # ── Friction heatmap section ─────────────────────────────────────────────
    friction_section = f"""
<section id="friction">
  <h2>Friction Heatmap ({len(friction_points)} issues)</h2>
  <div class="card" style="margin-bottom:1.5rem;">
    <h3>Overall Assessment</h3>
    <div class="summary-text">{escape(overall_summary)}</div>
    <div style="margin-top:1.25rem;">{_severity_summary_bars(sev_map)}</div>
  </div>
  {_friction_heatmap(friction_points_sorted)}
</section>"""

    # ── Fixes section ────────────────────────────────────────────────────────
    fixes_section = f"""
<section id="fixes">
  <h2>Recommended Fixes ({len(fixes)} total)</h2>
  {_fixes_html(fixes)}
</section>"""

    # ── Screenshots section ──────────────────────────────────────────────────
    screenshots_section = f"""
<section id="screenshots">
  <h2>Session Screenshots</h2>
  {_screenshots_grid_html(persona_traces)}
</section>"""

    # ── Live log section (static, from recorded entries) ─────────────────────
    live_log_section = f"""
<section id="live-log">
  <h2>Agent Execution Log ({len(log_entries)} entries)</h2>
  <div class="log-container">{_static_log_html(log_entries)}</div>
</section>"""

    html = (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="UTF-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f"  <title>Usability Report &mdash; {escape(url)}</title>\n"
        f"  <style>{CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        "<header>\n"
        "  <h1>Usability Report</h1>\n"
        f'  <div class="url-badge">{escape(url)}</div>\n'
        f'  <div class="meta" style="margin-top:0.5rem;">Generated {generated_at}</div>\n'
        "</header>\n"
        "<main>\n"
        + live_log_section
        + summary_section
        + tasks_section
        + persona_section
        + screenshots_section
        + friction_section
        + fixes_section
        + "\n</main>\n"
        f"<footer>Generated by Claude Usability Pipeline &middot; {generated_at}</footer>\n"
        "</body>\n</html>"
    )

    out = Path(output_path).resolve()
    out.write_text(html, encoding="utf-8")
    print(f"\n[Report] Final report saved → {out}")
    return str(out)


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

def run_pipeline(url: str) -> dict:
    print(f"\n{'='*60}")
    print(f"  Usability Pipeline — {url}")
    print(f"{'='*60}\n")

    report_dir = Path(".").resolve()
    log_path = report_dir / "log.json"
    report_path = report_dir / "report.html"
    done_path = report_dir / "report_data.json"

    # ── Live log + HTTP server ───────────────────────────────────────────────
    init_live_log(str(log_path))
    port = _start_report_server(str(report_dir))
    report_url = f"http://localhost:{port}/report.html"

    # Broadcast pipeline start (extension may already be open)
    _ws_broadcast({"type": "pipeline_start", "url": url})

    # ── Skeleton report → open in Chrome ────────────────────────────────────
    generate_report_skeleton(url, str(report_path))
    print(f"\n[Report] Live view → {report_url}")
    subprocess.Popen(
        ["open", "-a", "Google Chrome", report_url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _pipeline_log(f"Report live at {report_url}")

    # ── Pipeline steps ───────────────────────────────────────────────────────
    summary = summarize_site(url)
    tasks = generate_tasks(summary)
    traces = execute_tasks(tasks, url)
    persona_traces = execute_tasks_multi_persona(tasks, url)
    multi_analysis = analyze_multi_persona(persona_traces)
    analysis = analyze_confusion(traces)
    fixes = suggest_fixes(
        multi_analysis if multi_analysis.get("friction_points") else analysis
    )

    results = {
        "url":            url,
        "summary":        summary,
        "tasks":          tasks,
        "traces":         traces,
        "persona_traces": persona_traces,
        "analysis":       analysis,
        "multi_analysis": multi_analysis,
        "fixes":          fixes,
    }

    # ── Write final report, then signal done (triggers browser reload) ───────
    _pipeline_log("Pipeline complete — generating final report...")
    generate_html_report(results, str(report_path))
    done_path.write_text(json.dumps({"done": True}), encoding="utf-8")
    _ws_broadcast({"type": "pipeline_done", "report_url": report_url})

    return results
