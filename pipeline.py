"""
Usability testing pipeline.

Steps:
  1. summarize_site(url)          — fetch HTML, ask Gemini to summarise purpose & key flows
  2. generate_tasks(summary)      — ask Gemini to return 1 usability task
  3. execute_tasks_multi_persona  — run task × 2 personas (Elderly, First-Time) in parallel
  4. analyze_multi_persona        — unified friction report across both personas
  5. generate_visual_fixes        — ask Gemini for HTML/CSS fixes, capture before/after screenshots

All results are broadcast over WebSocket (port 7655) to the Chrome extension.
The extension side panel IS the report — no HTML file or HTTP server needed.
"""

import asyncio
import base64
import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

from google import genai
from google.genai import types as genai_types
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
# Gemini client
# ---------------------------------------------------------------------------

_MODEL = "gemini-3-flash-preview"
_GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
_genai_client: genai.Client | None = (
    genai.Client(api_key=_GOOGLE_API_KEY) if _GOOGLE_API_KEY else None
)


def _ask(system: str, user: str, *, label: str = "") -> str:
    """Single Gemini call. Returns text response."""
    if _genai_client is None:
        raise EnvironmentError(
            "GOOGLE_API_KEY is not set. Export it before running:\n"
            "  export GOOGLE_API_KEY=your_key_here"
        )
    response = _genai_client.models.generate_content(
        model=_MODEL,
        contents=user,
        config=genai_types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=4096,
        ),
    )
    return response.text.strip()


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
    """Create the LogWriter and wire log + screenshot callbacks into fake_browser_use."""
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

    def _screenshot_cb(label: str, task_num: int, b64_png: str, path: str) -> None:
        """Broadcast a screenshot as base64 to the extension."""
        _ws_broadcast({
            "type": "screenshot",
            "ts": datetime.now().strftime("%H:%M:%S"),
            "persona": label,
            "task_num": task_num,
            "path": path,
            "data": b64_png,   # base64-encoded PNG
        })

    _fbu.set_log_callback(_cb)
    _fbu.set_screenshot_callback(_screenshot_cb)
    return _log_writer


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
        "Return ONLY a JSON array of exactly 1 task string. "
        "The task should be the single most important action a real user would try to accomplish "
        "on the site (e.g. 'Find the price of the MacBook Pro 14-inch'). "
        "No markdown, no commentary — pure JSON array."
    )
    user = (
        f"Site: {summary['url']}\n"
        f"Purpose: {summary.get('purpose', '')}\n"
        f"Key flows: {json.dumps(summary.get('key_flows', []))}\n"
        f"Target audience: {summary.get('target_audience', '')}\n\n"
        "Generate 1 usability test task."
    )

    raw = _ask(system, user, label="generate_tasks")

    try:
        tasks = json.loads(raw)
        if isinstance(tasks, list):
            tasks = [str(t) for t in tasks[:1]]
    except json.JSONDecodeError:
        lines = re.split(r"\n|\d+[.)]\s*", raw)
        tasks = [l.strip().strip('"') for l in lines if len(l.strip()) > 10][:1]

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
# Step 5 — Visual redesign recommendations with before/after screenshots
# ---------------------------------------------------------------------------

async def _capture_fix_pair(
    url: str,
    selector: str,
    css: str,
    js: str,
) -> tuple[str, str]:
    """
    Open a fresh tab in the existing Chrome, scroll to `selector`, capture a
    'before' screenshot, inject `css`/`js`, then capture an 'after' screenshot.
    Returns (before_b64_jpeg, after_b64_jpeg).  Both are empty strings on failure.
    """
    from browser_use import Browser
    from browser_use.browser.profile import BrowserProfile

    profile = BrowserProfile(
        cdp_url=_fbu.CDP_URL,
        keep_alive=True,
        minimum_wait_page_load_time=1.2,
    )
    browser = Browser(browser_profile=profile)
    before_b64 = after_b64 = ""

    try:
        await browser.start()
        await browser.navigate_to(url, new_tab=True)
        page = await browser.get_current_page()
        # bring_to_front() does not exist on browser-use 0.12+ actor Page;
        # use CDP Target.activateTarget instead (purely cosmetic, ignored on error)
        try:
            await page._client.send.Target.activateTarget({"targetId": page._target_id})
        except Exception:
            pass
        await asyncio.sleep(1.2)

        # Scroll element into view so it's centered in the viewport
        if selector:
            try:
                await page.evaluate(
                    f"() => {{ const el = document.querySelector({json.dumps(selector)}); "
                    f"if (el) el.scrollIntoView({{behavior:'instant',block:'center'}}); }}"
                )
                await asyncio.sleep(0.3)
            except Exception:
                pass

        raw_before = await browser.take_screenshot(
            full_page=False, format="jpeg", quality=45
        )
        if raw_before:
            before_b64 = base64.b64encode(raw_before).decode()

        # Inject the CSS fix
        if css:
            await page.evaluate(f"""() => {{
                const s = document.createElement('style');
                s.id = '__ux_fix__';
                s.textContent = {json.dumps(css)};
                document.head.appendChild(s);
            }}""")
        # Inject optional JS fix (LLM output — wrap in arrow fn if needed)
        if js:
            try:
                js_expr = js.strip()
                if not (js_expr.startswith("(") and "=>" in js_expr):
                    js_expr = f"() => {{ {js_expr} }}"
                await page.evaluate(js_expr)
            except Exception:
                pass

        await asyncio.sleep(0.6)   # let repaint settle

        raw_after = await browser.take_screenshot(
            full_page=False, format="jpeg", quality=45
        )
        if raw_after:
            after_b64 = base64.b64encode(raw_after).decode()

    except Exception as exc:
        _pipeline_log(f"visual fix screenshot error: {exc}")
    finally:
        try:
            pg = await browser.get_current_page()
            await browser.close_page(pg)
        except Exception:
            pass
        try:
            await browser.stop()
        except Exception:
            pass

    return before_b64, after_b64


def generate_visual_fixes(friction_points: list[dict], url: str) -> list[dict]:
    """
    Step 5 — For each friction point:
      1. Ask Claude for a plain-English description + minimal CSS/JS fix.
      2. Capture before/after screenshots via CDP.
      3. Broadcast each fix incrementally as a `visual_fix` WS message.

    Returns list of visual fix dicts (screenshots as base64 JPEG).
    """
    _pipeline_log("Step 5: Generating visual redesign recommendations...")
    print("[5/5] Generating visual fix recommendations ...")

    if not friction_points:
        _pipeline_log("No friction points — skipping visual fixes")
        return []

    # ── Ask Claude for plain-English descriptions + CSS ──────────────────
    fp_list = "\n".join(
        f"{i+1}. [{fp.get('severity','?').upper()}] "
        f"{fp.get('element','?')} — {fp.get('description','')}"
        for i, fp in enumerate(friction_points)
    )
    system = (
        "You are a UX designer reviewing a website. For each friction point, "
        "create a fix. Return ONLY a JSON array — no markdown, no commentary. "
        "Each item must have exactly these keys:\n"
        '  "id": integer (0-based index),\n'
        '  "element": CSS selector for the problem element (e.g. \".nav-btn\", \"h1\", \"form\"),\n'
        '  "description": one plain-English sentence a non-developer can understand '
        '(e.g. "Make the donate button stand out with a brighter background colour"),\n'
        '  "severity": one of critical | high | medium | low,\n'
        '  "css": complete CSS rule(s) to inject — every property MUST end with !important '
        "so it overrides existing site styles. Target the exact selector. Keep it minimal.\n"
        '  "js": optional JS expression to run after CSS injection, or empty string.\n'
        "Plain-English description examples:\n"
        "  ✓ 'Move the search bar to the top of the page so it is immediately visible'\n"
        "  ✓ 'Add a visible label to every form field'\n"
        "  ✗ 'Apply display:flex to .nav-container' (too technical)"
    )
    raw = _ask(system, fp_list, label="visual_fixes")

    try:
        fixes_plan: list[dict] = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        try:
            fixes_plan = json.loads(m.group()) if m else []
        except Exception:
            fixes_plan = []

    if not isinstance(fixes_plan, list):
        fixes_plan = []

    # ── Force !important on every CSS declaration so injected styles win ──
    def _force_important(css: str) -> str:
        """Append !important to every CSS property value that doesn't already have it."""
        import re as _re
        def _add(m):
            prop = m.group(0)
            if "!important" in prop:
                return prop
            return _re.sub(r'\s*;', ' !important;', prop, count=1)
        return _re.sub(r'[^{}:]+:[^;{}]+;', _add, css)

    # ── Capture before/after screenshots for each fix ────────────────────
    cdp_ok = asyncio.run(_fbu._check_cdp())
    visual_fixes: list[dict] = []

    for fix in fixes_plan:
        fix_id    = int(fix.get("id", len(visual_fixes)))
        selector  = fix.get("element", "body")
        css       = _force_important(fix.get("css", ""))
        js        = fix.get("js", "")
        desc      = fix.get("description", "")
        severity  = fix.get("severity", "medium").lower()

        before_b64 = after_b64 = ""
        if cdp_ok and (css or js):
            try:
                before_b64, after_b64 = asyncio.run(
                    _capture_fix_pair(url, selector, css, js)
                )
            except Exception as e:
                _pipeline_log(f"screenshot pair failed for fix {fix_id}: {e}")

        vf = {
            "id":          fix_id,
            "element":     selector,
            "description": desc,
            "severity":    severity,
            "css":         css,
            "js":          js,
            "before":      before_b64,
            "after":       after_b64,
        }
        visual_fixes.append(vf)

        # Broadcast immediately so the extension shows cards as they arrive
        _ws_broadcast({"type": "visual_fix", **vf})
        _pipeline_log(
            f"Fix {fix_id + 1}/{len(fixes_plan)}: [{severity.upper()}] {desc[:70]}"
        )
        print(
            f"       Fix {fix_id + 1}/{len(fixes_plan)}: "
            f"[{severity.upper()}] {desc[:60]}"
        )

    return visual_fixes


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

def run_pipeline(url: str) -> dict:
    print(f"\n{'='*60}")
    print(f"  Usability Pipeline — {url}")
    print(f"{'='*60}\n")

    # ── Live log ─────────────────────────────────────────────────────────────
    log_path = Path(".").resolve() / "log.json"
    init_live_log(str(log_path))

    # Broadcast pipeline start so the extension can show it immediately
    _ws_broadcast({"type": "pipeline_start", "url": url})
    _pipeline_log(f"Pipeline started: {url}")

    # ── Pipeline steps ────────────────────────────────────────────────────────
    summary        = summarize_site(url)
    tasks          = generate_tasks(summary)
    persona_traces = execute_tasks_multi_persona(tasks, url)
    multi_analysis = analyze_multi_persona(persona_traces)

    friction_points = multi_analysis.get("friction_points", [])
    visual_fixes    = generate_visual_fixes(friction_points, url)

    results = {
        "url":            url,
        "summary":        summary,
        "tasks":          tasks,
        "persona_traces": persona_traces,
        "multi_analysis": multi_analysis,
        "visual_fixes":   visual_fixes,
    }

    # ── Broadcast full results to extension ───────────────────────────────────
    _pipeline_log("Pipeline complete — broadcasting results to extension...")

    # Build a compact per-task, per-persona result table for the extension
    personas = list(PERSONA_PROFILES.keys())
    task_results = []
    for i, task in enumerate(tasks):
        row = {"task": task, "personas": {}}
        for p in personas:
            p_traces = persona_traces.get(p, [])
            t = p_traces[i] if i < len(p_traces) and p_traces[i] else {}
            row["personas"][p] = {
                "success": t.get("success", False),
                "time": round(t.get("total_time_seconds", 0), 1),
                "completion_rate": round(t.get("completion_rate", 0), 2),
            }
        task_results.append(row)

    _ws_broadcast({
        "type":            "pipeline_done",
        "url":             url,
        "summary":         summary,
        "task_results":    task_results,
        "friction_points": friction_points,
        "severity_map":    multi_analysis.get("severity_map", {}),
        "overall_summary": multi_analysis.get("summary", ""),
        "persona_stats":   multi_analysis.get("persona_stats", {}),
        "persona_summary": multi_analysis.get("persona_summary", {}),
        "fix_count":       len(visual_fixes),
    })

    return results
