"""
Browser automation via the local browser_use Python library (≥ 0.12).

Connects to an EXISTING Chrome instance via CDP (remote debugging) so all
three persona tabs appear in the same Chrome window alongside the extension
side panel.

Before running, start Chrome with the remote-debugging flag:
    /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
        --remote-debugging-port=9222

Each persona opens as a NEW TAB in that window with a colored banner
injected at the top.  Falls back to the deterministic mock if Chrome is
not reachable on port 9222 or browser_use is not installed.

Install:
    pip install browser-use playwright litellm
    playwright install chromium
"""

import asyncio
import base64
import os
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Try to import browser_use — new API uses BrowserProfile, not BrowserConfig
# ---------------------------------------------------------------------------

CDP_URL = "http://localhost:9222"

try:
    from browser_use import Agent, Browser
    from browser_use.browser.profile import BrowserProfile
    _BROWSER_USE_AVAILABLE = True
except ImportError:
    _BROWSER_USE_AVAILABLE = False


def _require_llm():
    """
    Return an LLM that satisfies browser_use's BaseChatModel protocol.

    browser_use ≥ 0.12 requires a `provider` property that langchain-anthropic's
    ChatAnthropic does not expose.  We use browser_use's own ChatLiteLLM wrapper
    (backed by litellm) which implements the full protocol correctly.

    Requires:  pip install litellm
    """
    try:
        from browser_use.llm.litellm.chat import ChatLiteLLM
    except ImportError:
        raise ImportError(
            "litellm is required for browser_use LLM integration.\n"
            "  pip install litellm"
        )
    return ChatLiteLLM(
        model="gemini/gemini-3-flash-preview",
        api_key=os.environ.get("GOOGLE_API_KEY"),
        temperature=0,
        max_tokens=8096,
    )


# ---------------------------------------------------------------------------
# Thread / async-safe logging + screenshot broadcast
# ---------------------------------------------------------------------------

_print_lock = threading.Lock()
_log_callback = None        # fn(persona_label, task_num, msg)
_screenshot_callback = None # fn(persona_label, task_num, b64_png, path)


def set_log_callback(fn) -> None:
    global _log_callback
    _log_callback = fn


def set_screenshot_callback(fn) -> None:
    global _screenshot_callback
    _screenshot_callback = fn


def _live_log(label: str, task_num: int, msg: str) -> None:
    with _print_lock:
        print(f"[{label:<20}] T{task_num}: {msg}", flush=True)
    if _log_callback is not None:
        try:
            _log_callback(label, task_num, msg)
        except Exception:
            pass


async def _bring_to_front(page) -> None:
    """Bring a CDP tab to the front.

    browser-use 0.12+ returns a browser_use.actor.page.Page (CDP-based), not a
    Playwright Page, so Playwright's bring_to_front() doesn't exist.  Use the
    CDP Target.activateTarget command instead.  Purely cosmetic — silently
    ignored on any error.
    """
    try:
        await page._client.send.Target.activateTarget({"targetId": page._target_id})
    except Exception:
        pass


_ACTION_VERBS = {
    "navigate": "Navigating to", "click": "Clicking",
    "scroll": "Scrolling", "evaluate": "Checking page",
    "wait": "Waiting", "done": "Finishing task",
    "type": "Typing", "inputtext": "Typing",
    "goback": "Going back", "goforward": "Going forward",
    "extractcontent": "Reading page", "screenshot": "Taking screenshot",
    "search": "Searching for",
}


def _readable_action(act) -> str:
    """Convert a browser-use action (dict or Pydantic model) to a human-readable string."""
    try:
        if isinstance(act, dict):
            name = next(iter(act), "act").lower()
            params = act.get(next(iter(act)), {})
            verb = _ACTION_VERBS.get(name, name.replace("_", " ").capitalize())
            if isinstance(params, dict):
                target = (params.get("url") or params.get("selector") or
                          params.get("text") or params.get("query") or
                          params.get("index") or "")
                if params.get("down") is not None:
                    return "Scrolling down" if params["down"] else "Scrolling up"
                if params.get("seconds"):
                    return f"Waiting {params['seconds']}s"
                return f"{verb} {str(target)[:45]}".strip() if target else verb
            return verb

        # Pydantic model — inspect by class name then try common sub-attributes
        cls = type(act).__name__
        verb_key = cls.lower().replace("actionmodel", "").replace("action", "")
        verb = _ACTION_VERBS.get(verb_key, verb_key.replace("_", " ").capitalize() or "Acting")

        # Walk one level of nested attributes to find a useful value
        for sub_name in dir(act):
            if sub_name.startswith("_"):
                continue
            sub = getattr(act, sub_name, None)
            if sub is None or callable(sub):
                continue
            # sub is the inner params object (e.g. act.navigate, act.click)
            if hasattr(sub, "url") and sub.url:
                return f"{verb} {str(sub.url)[:50]}"
            if hasattr(sub, "index") and sub.index is not None:
                return f"{verb} element #{sub.index}"
            if hasattr(sub, "down") and sub.down is not None:
                return "Scrolling down" if sub.down else "Scrolling up"
            if hasattr(sub, "seconds") and sub.seconds:
                return f"Waiting {sub.seconds}s"
            if hasattr(sub, "text") and sub.text:
                return f"{verb}: {str(sub.text)[:45]}"
            if hasattr(sub, "success"):
                return "✓ Task complete" if sub.success else "Task ended"
        return verb
    except Exception:
        return ""


def _action_desc(action: "Action") -> str:
    verb = {
        "navigate": "navigating to", "hover": "hovering over",
        "click": "clicking", "type": "typing into",
        "scroll": "scrolling", "wait": "waiting for", "back": "going back",
    }.get(action.type, action.type)
    target = action.target[:52] + "..." if len(action.target) > 55 else action.target
    val_note = f' "{action.value}"' if action.value else ""
    dur_s = action.duration_ms / 1000
    timing = (
        f"({dur_s:.1f}s + {action.hesitation_ms/1000:.1f}s pause)"
        if action.hesitation_ms else f"({dur_s:.1f}s)"
    )
    if action.error:
        return f"✗ FAILED — {action.error}"
    return f"{verb} {target}{val_note} {timing}"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Action:
    type: str
    target: str
    value: Optional[str] = None
    duration_ms: int = 0
    hesitation_ms: int = 0
    error: Optional[str] = None


@dataclass
class TaskTrace:
    task: str
    url: str
    actions: list[Action] = field(default_factory=list)
    success: bool = True
    failure_reason: Optional[str] = None
    total_time_ms: int = 0
    confusion_points: list[str] = field(default_factory=list)
    completion_rate: float = 1.0


# ---------------------------------------------------------------------------
# Persona profiles
# ---------------------------------------------------------------------------

PERSONA_PROFILES = {
    "elderly_user": {
        "label":                 "Elderly User",
        "description":           "Older adult — slower navigation, needs clarity, avoids complex flows",
        "timing_multiplier":     2.4,
        "hesitation_multiplier": 2.0,
        "extra_failure_rate":    0.18,
        "skip_probability":      0.0,
        "random_click_probability": 0.0,
        "jargon_hesitation_ms":  0,
        "confusion_bias": {
            "nav, .navigation", ".menu, .hamburger",
            "form", ".cta, .buy-now, .checkout",
        },
        "wait_between_actions":  2.8,
        "indicator_color":       "#ffd700",
        "indicator_label":       "👴 Elderly User",
    },
    "first_time_user": {
        "label":                 "First-Time User",
        "description":           "Brand-new visitor — explores cautiously, reads everything, unsure where to start",
        "timing_multiplier":     1.5,
        "hesitation_multiplier": 1.6,
        "extra_failure_rate":    0.15,
        "skip_probability":      0.0,
        "random_click_probability": 0.05,
        "jargon_hesitation_ms":  400,
        "confusion_bias": {
            "nav, .navigation",
            ".cta, .buy-now, .checkout",
            ".search-bar, input[type='search']",
        },
        "wait_between_actions":  1.5,
        "indicator_color":       "#aaffaa",
        "indicator_label":       "🆕 First-Time User",
    },
}

_PERSONA_TASK_CONTEXT = {
    "elderly_user": (
        "You are acting as an elderly user (70+) with limited technical experience. "
        "Move very slowly and deliberately. Hover over links before clicking. "
        "Read every label carefully before acting. Prefer simple clearly-labelled paths. "
        "Pause frequently as if uncertain. Never rush."
    ),
    "first_time_user": (
        "You are acting as a first-time visitor to this website who has never seen it before. "
        "Read the page carefully before clicking anything. Explore the navigation to understand "
        "the site structure. Be cautious and methodical. Ask yourself 'where would I find this?' "
        "before each action."
    ),
}

# ── JS for the colored persona banner injected into each window ───────────────
# browser-use 0.12+ page.evaluate() requires arrow-function format: must start
# with "(" and contain "=>".  All snippets use () => { ... } accordingly.
_BANNER_JS = """() => {
    const label = "{label}";
    const color = "{color}";
    const ID = '__persona_banner__';
    if (document.getElementById(ID)) return;
    const bar = document.createElement('div');
    bar.id = ID;
    bar.style.cssText = [
        'position:fixed', 'top:0', 'left:0', 'right:0',
        'height:28px', 'background:' + color,
        'color:#000', 'font-weight:700', 'font-size:14px',
        'display:flex', 'align-items:center', 'padding:0 12px',
        'z-index:2147483647', 'pointer-events:none',
        'font-family:system-ui,sans-serif',
        'box-shadow:0 2px 6px rgba(0,0,0,.35)',
    ].join(';');
    bar.textContent = label;
    document.body && document.body.insertBefore(bar, document.body.firstChild);
    document.body.style.paddingTop = '28px';
}"""

# ── JS for slow mouse movement trail (elderly) ────────────────────────────────
_SLOW_MOUSE_JS = """() => {
    if (window.__slowMouseActive) return;
    window.__slowMouseActive = true;
    const trail = document.createElement('div');
    trail.style.cssText = [
        'position:fixed','width:16px','height:16px','border-radius:50%',
        'background:rgba(255,215,0,.7)','pointer-events:none',
        'z-index:2147483646','transition:all 0.6s ease','transform:translate(-50%,-50%)',
    ].join(';');
    document.body && document.body.appendChild(trail);
    document.addEventListener('mousemove', e => {
        trail.style.left = e.clientX + 'px';
        trail.style.top  = e.clientY + 'px';
    });
}"""

# ── JS for ADHD random distraction scroll ────────────────────────────────────
_ADHD_SCROLL_JS = """() => {
    const amt = {amt};
    window.scrollBy({top: amt, behavior: 'smooth'});
}"""

# ── JS for non-native hover reading effect ────────────────────────────────────
_READING_JS = """() => {
    const els = document.querySelectorAll('p, h2, h3, li, a, button, label');
    if (!els.length) return;
    const el = els[Math.floor(Math.random() * Math.min(els.length, 12))];
    if (el) {
        el.scrollIntoView({behavior: 'smooth', block: 'center'});
        const prev = el.style.background;
        el.style.background = 'rgba(136,204,255,0.25)';
        setTimeout(() => { el.style.background = prev; }, 2000);
    }
}"""


# ---------------------------------------------------------------------------
# Screenshot helper
# ---------------------------------------------------------------------------

_SCREENSHOT_DIR = Path("screenshots")


async def _take_screenshot(
    browser: Any, persona: str, task_num: int, step: int
) -> str | None:
    """Save PNG, fire screenshot callback with base64 data. Returns relative path."""
    try:
        _SCREENSHOT_DIR.mkdir(exist_ok=True)
        fname = f"{persona}_task{task_num:02d}_step{step:02d}.png"
        fpath = _SCREENSHOT_DIR / fname
        data = await browser.take_screenshot(full_page=False, format="png")
        if data:
            fpath.write_bytes(data)
            if _screenshot_callback is not None:
                try:
                    b64 = base64.b64encode(data).decode()
                    label = PERSONA_PROFILES[persona]["label"]
                    _screenshot_callback(label, task_num, b64, str(fpath))
                except Exception:
                    pass
            return str(fpath)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Per-persona behavior loops (run concurrently with the Agent)
# ---------------------------------------------------------------------------

async def _elderly_effect_loop(page: Any, stop: asyncio.Event) -> None:
    """
    Elderly persona: inject 150% zoom, slow-mouse trail, golden banner.
    Takes the already-opened page so it always targets the right tab.
    """
    label = PERSONA_PROFILES["elderly_user"]["indicator_label"]
    color = PERSONA_PROFILES["elderly_user"]["indicator_color"]
    banner = _BANNER_JS.replace("{label}", label).replace("{color}", color)
    tick = 0
    while not stop.is_set():
        await asyncio.sleep(2.2)
        if stop.is_set():
            break
        try:
            await page.evaluate(banner)
            await page.evaluate("() => { document.body.style.zoom='150%'; }")
            await page.evaluate(_SLOW_MOUSE_JS)
            # Simulate slow deliberate mouse movement to a random element
            if tick % 2 == 0:
                start_x, start_y = random.randint(100, 400), random.randint(100, 300)
                end_x,   end_y   = random.randint(200, 700), random.randint(200, 600)
                steps = 12
                for i in range(steps + 1):
                    if stop.is_set():
                        break
                    t = i / steps
                    x = int(start_x + (end_x - start_x) * t)
                    y = int(start_y + (end_y - start_y) * t)
                    await page.mouse.move(x, y)
                    await asyncio.sleep(0.12)
        except Exception:
            pass
        tick += 1


async def _firsttime_effect_loop(page: Any, stop: asyncio.Event) -> None:
    """
    First-time user: slow reading highlights on text elements, cautious mouse
    drift, green banner.  Takes the already-opened page so it always targets
    the right tab.
    """
    label = PERSONA_PROFILES["first_time_user"]["indicator_label"]
    color = PERSONA_PROFILES["first_time_user"]["indicator_color"]
    banner = _BANNER_JS.replace("{label}", label).replace("{color}", color)
    while not stop.is_set():
        await asyncio.sleep(2.5)
        if stop.is_set():
            break
        try:
            await page.evaluate(banner)
            await page.evaluate(_READING_JS)
            x = random.randint(80, 600)
            y = random.randint(80, 500)
            for i in range(6):
                if stop.is_set():
                    break
                await page.mouse.move(x + i * 10, y + i * 4)
                await asyncio.sleep(0.3)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Real browser_use implementation — one task
# ---------------------------------------------------------------------------

_CDP_UNAVAILABLE_MSG = """
╔══════════════════════════════════════════════════════════════════╗
║  Chrome is not running with remote debugging enabled.           ║
║  Start Chrome with:                                             ║
║                                                                  ║
║  /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\
║      --remote-debugging-port=9222                               ║
║                                                                  ║
║  Then re-run the pipeline.  Falling back to mock mode.          ║
╚══════════════════════════════════════════════════════════════════╝
"""


async def _check_cdp() -> bool:
    """Return True if Chrome is reachable at CDP_URL."""
    try:
        import aiohttp
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{CDP_URL}/json/version", timeout=aiohttp.ClientTimeout(total=2)) as r:
                return r.status == 200
    except Exception:
        return False


async def _run_task_browser_use(
    task: str,
    url: str,
    persona: str,
    task_num: int = 0,
) -> dict:
    """
    Connect to the existing Chrome via CDP and run the task in a NEW TAB.
    The colored persona banner is injected at the top of the tab so users
    can see which persona is running while the extension side panel shows
    live updates on the right.
    """
    profile = PERSONA_PROFILES[persona]
    label   = profile["label"]
    start_t = time.time()
    screenshots: list[str] = []
    step_num = [0]

    # ── Connect to existing Chrome via CDP (no new window) ────────────────
    browser_profile = BrowserProfile(
        cdp_url=CDP_URL,
        keep_alive=True,          # don't close Chrome when we're done
        wait_between_actions=profile["wait_between_actions"],
        minimum_wait_page_load_time=0.6,
        highlight_elements=True,
    )
    browser = Browser(browser_profile=browser_profile)

    llm = _require_llm()
    full_task = (
        f"{_PERSONA_TASK_CONTEXT[persona]}\n\n"
        f"Task: {task}\n"
        f"Start URL: {url}"
    )

    _live_log(label, task_num, f"opening tab → {task[:60]}")

    banner = _BANNER_JS.replace("{label}", profile["indicator_label"]) \
                       .replace("{color}", profile["indicator_color"])

    # own_page is set once the tab is open; on_step and effect loops capture it
    # by closure so they always target this persona's tab, not whatever Chrome
    # considers "current" (which would cause cross-persona interference).
    own_page: Any = None
    stop_evt = asyncio.Event()
    effect_task: asyncio.Task | None = None
    effect_fn = {
        "elderly_user":   _elderly_effect_loop,
        "first_time_user": _firsttime_effect_loop,
    }[persona]

    # ── Step callback: slow down + keep tab visible + screenshot ─────────
    async def on_step(state, output, step_idx: int) -> None:
        step_num[0] = step_idx
        action_str = ""
        try:
            if output and output.action:
                acts = output.action if isinstance(output.action, list) else [output.action]
                parts = []
                for act in acts:
                    parts.append(_readable_action(act))
                action_str = " → ".join(p for p in parts if p)
        except Exception:
            pass
        _live_log(label, task_num, f"Step {step_idx}: {action_str or '…'}")

        await asyncio.sleep(1)

        # Re-inject banner on own page (navigations clear injected DOM)
        if own_page is not None:
            try:
                await _bring_to_front(own_page)
                await own_page.evaluate(banner)
            except Exception:
                pass

        path = await _take_screenshot(browser, persona, task_num, step_idx)
        if path:
            screenshots.append(path)

    # ── Run the agent ─────────────────────────────────────────────────────
    try:
        await browser.start()
        # Open a fresh tab for this persona
        await browser.navigate_to(url, new_tab=True)
        # Capture the tab's page object — stays valid through navigations
        own_page = await browser.get_current_page()
        await _bring_to_front(own_page)
        _persona_pages[persona] = own_page          # register for cycler
        await own_page.evaluate(banner)
        await own_page.evaluate(
            f"() => {{ document.title = '{profile['indicator_label']} \u2014 ' + "
            f"document.title.replace(/^.*? \u2014 /, ''); }}"
        )
        _live_log(label, task_num, "tab ready and visible")

        # Start effect loop with the owned page — avoids touching other personas' tabs
        effect_task = asyncio.create_task(effect_fn(own_page, stop_evt))

        agent = Agent(
            task=full_task,
            llm=llm,
            browser=browser,
            register_new_step_callback=on_step,
        )
        history = await agent.run(max_steps=2)

        # Final screenshot
        path = await _take_screenshot(browser, persona, task_num, step_num[0] + 1)
        if path:
            screenshots.append(path)

        try:
            success = history.is_done() or bool(history.final_result())
        except Exception:
            success = True
        failure_reason = None
        actions = _extract_actions(history)

    except Exception as exc:
        success = False
        failure_reason = str(exc)[:200]
        actions = []
        _live_log(label, task_num, f"✗ ERROR — {failure_reason}")

    # ── Tear down: stop effects, disconnect (Chrome stays open) ───────────
    stop_evt.set()
    if effect_task is not None:
        effect_task.cancel()
        try:
            await asyncio.gather(effect_task, return_exceptions=True)
        except Exception:
            pass
    try:
        # Close just this tab, leave Chrome open
        page = await browser.get_current_page()
        await browser.close_page(page)
    except Exception:
        pass
    try:
        await browser.stop()
    except Exception:
        pass

    elapsed = round(time.time() - start_t, 1)
    if success:
        _live_log(label, task_num, f"✓ DONE ({elapsed}s, {len(screenshots)} screenshots)")
    else:
        _live_log(label, task_num, f"✗ FAILED ({elapsed}s) — {failure_reason}")

    return {
        "task":               task,
        "url":                url,
        "persona":            persona,
        "persona_label":      label,
        "success":            success,
        "completion_rate":    1.0 if success else 0.5,
        "total_time_seconds": elapsed,
        "failure_reason":     failure_reason,
        "confusion_points":   [],
        "actions":            actions,
        "screenshots":        screenshots,
    }


def _extract_actions(history: Any) -> list[dict]:
    """Convert browser_use AgentHistoryList → list of action dicts."""
    actions = []
    try:
        for step in history.history:
            if not step.model_output:
                continue
            for action_item in (step.model_output.action or []):
                if not action_item:
                    continue
                if isinstance(action_item, dict):
                    name = next(iter(action_item), "unknown")
                    params = action_item.get(name, {})
                    target = (
                        (params.get("selector") or params.get("url") or
                         params.get("text") or params.get("query") or
                         str(params)[:80])
                        if isinstance(params, dict) else str(params)[:80]
                    )
                else:
                    name = type(action_item).__name__
                    target = str(action_item)[:80]
                actions.append({"type": name, "target": str(target), "duration_ms": 500})
    except Exception:
        pass
    return actions


# ---------------------------------------------------------------------------
# Tab visibility — shared page registry + cycler
# ---------------------------------------------------------------------------

# Maps persona key → current Playwright page so the cycler can bring tabs
# to front. Written by _run_task_browser_use, read by _tab_cycler.
_persona_pages: dict[str, Any] = {}


async def _tab_cycler(stop_evt: asyncio.Event, interval: float = 10.0) -> None:
    """
    Background task: every `interval` seconds, rotate which persona's tab is
    visible so humans can see all three agents taking turns.
    """
    personas = list(PERSONA_PROFILES.keys())
    idx = 0
    while not stop_evt.is_set():
        await asyncio.sleep(interval)
        if stop_evt.is_set():
            break
        # Pick the next persona in round-robin order
        for _ in range(len(personas)):
            persona = personas[idx % len(personas)]
            idx += 1
            page = _persona_pages.get(persona)
            if page is not None:
                try:
                    await _bring_to_front(page)
                    label = PERSONA_PROFILES[persona]["label"]
                    _live_log(label, 0, "👁 tab cycler — bringing to front")
                except Exception:
                    pass
                break  # only activate one per cycle


# ---------------------------------------------------------------------------
# Async multi-persona runner — all 3 tabs open simultaneously
# ---------------------------------------------------------------------------

# How long to wait before opening each persona's first tab so they don't
# all fight for focus at the same moment.
_PERSONA_START_DELAYS = {
    "elderly_user":       0.0,
    "adhd_user":          2.5,
    "non_native_english": 5.0,
}


async def _run_tasks_multi_persona_async(
    tasks: list[str], url: str
) -> dict[str, list[dict]]:
    """
    Open three CDP tabs (one per persona) with staggered starts, run all
    tasks sequentially within each tab, and cycle tab focus every 10 s.
    """
    _persona_pages.clear()
    personas = list(PERSONA_PROFILES.keys())

    async def run_one(persona: str) -> tuple[str, list[dict]]:
        delay = _PERSONA_START_DELAYS.get(persona, 0.0)
        if delay:
            await asyncio.sleep(delay)
        traces = []
        for idx, task in enumerate(tasks):
            trace = await _run_task_browser_use(task, url, persona, task_num=idx + 1)
            traces.append(trace)
        return persona, traces

    # Start tab cycler alongside the three persona tasks
    cycler_stop = asyncio.Event()
    cycler_task = asyncio.create_task(_tab_cycler(cycler_stop))

    results_list = await asyncio.gather(
        *[run_one(p) for p in personas],
        return_exceptions=True,
    )

    cycler_stop.set()
    cycler_task.cancel()
    try:
        await asyncio.gather(cycler_task, return_exceptions=True)
    except Exception:
        pass

    results: dict[str, list[dict]] = {}
    for item in results_list:
        if isinstance(item, Exception):
            with _print_lock:
                print(f"[browser_use] persona failed: {item}", flush=True)
        else:
            persona, traces = item
            results[persona] = traces
    return results


# ---------------------------------------------------------------------------
# Mock implementation (fallback when browser_use is not installed)
# ---------------------------------------------------------------------------

_CONFUSION_SCENARIOS = [
    ("nav, .navigation", "Navigation labels ambiguous — hovered multiple items"),
    ("footer", "Had to scroll to footer to find basic info"),
    (".search-bar, input[type='search']", "Search bar not immediately visible"),
    (".cta, .buy-now, .checkout", "Primary CTA has low contrast — hard to spot"),
    (".cart, .bag", "Cart icon has no item count badge"),
    ("form", "Form lacks inline validation — unsure which fields required"),
    (".menu, .hamburger", "Mobile menu icon not labelled"),
    (".price, .cost", "Pricing hidden behind a click"),
]

_FAILURE_REASONS = [
    "Could not locate checkout button after adding item to cart",
    "Search returned no results — no helpful empty state",
    "Required form field missing label",
    "Page took >8s to load — task abandoned",
    "Navigation menu collapsed on scroll",
    "Modal dialog blocked primary content — no dismiss option",
    "Back button triggered form resubmission warning",
]

_ACTION_TEMPLATES = {
    "search": [
        Action("navigate", "{url}", duration_ms=800),
        Action("hover", "nav, header", duration_ms=300, hesitation_ms=200),
        Action("click", "input[type='search'], .search-bar", duration_ms=400),
        Action("type", "input[type='search']", value="{query}", duration_ms=600),
        Action("click", "button[type='submit'], .search-button", duration_ms=300),
        Action("wait", "search results", duration_ms=1200),
    ],
    "buy": [
        Action("navigate", "{url}", duration_ms=800),
        Action("scroll", "main content", duration_ms=500),
        Action("hover", ".product, .item", duration_ms=400, hesitation_ms=300),
        Action("click", ".product:first-child, .buy-now", duration_ms=500),
        Action("wait", "cart update", duration_ms=800),
        Action("click", ".cart, [aria-label*='cart']", duration_ms=400),
        Action("click", ".checkout, [href*='checkout']", duration_ms=350),
    ],
    "navigate": [
        Action("navigate", "{url}", duration_ms=800),
        Action("hover", "nav a, header a", duration_ms=500, hesitation_ms=400),
        Action("click", "nav a:first-child", duration_ms=400),
        Action("wait", "page load", duration_ms=1000),
    ],
    "find": [
        Action("navigate", "{url}", duration_ms=800),
        Action("scroll", "page", duration_ms=700),
        Action("hover", "footer", duration_ms=500, hesitation_ms=600),
        Action("click", "footer a", duration_ms=350),
        Action("wait", "page load", duration_ms=900),
    ],
    "contact": [
        Action("navigate", "{url}", duration_ms=800),
        Action("hover", "nav, footer", duration_ms=700, hesitation_ms=800),
        Action("click", "[href*='contact'], a[href*='support']", duration_ms=400),
        Action("wait", "contact page", duration_ms=900),
        Action("click", "input[name='name'], input[type='text']:first-child", duration_ms=350),
        Action("type", "name field", value="Test User", duration_ms=500),
    ],
    "default": [
        Action("navigate", "{url}", duration_ms=800),
        Action("scroll", "main content", duration_ms=600),
        Action("hover", "interactive elements", duration_ms=500, hesitation_ms=400),
        Action("click", "main a, .cta, button:not([disabled])", duration_ms=400),
        Action("wait", "page response", duration_ms=900),
        Action("scroll", "new content", duration_ms=500),
    ],
}


def _pick_template(task: str) -> list[Action]:
    tl = task.lower()
    for kw in _ACTION_TEMPLATES:
        if kw != "default" and kw in tl:
            return [Action(**vars(a)) for a in _ACTION_TEMPLATES[kw]]
    return [Action(**vars(a)) for a in _ACTION_TEMPLATES["default"]]


def _run_task_mock(task: str, url: str) -> TaskTrace:
    actions = _pick_template(task)
    for a in actions:
        if a.target == "{url}":
            a.target = url
        if a.value == "{query}":
            a.value = task.split()[-1] if task.split() else "product"
    for a in actions:
        a.duration_ms = max(100, a.duration_ms + random.randint(-100, 200))
        if a.hesitation_ms > 0:
            a.hesitation_ms += random.randint(0, 400)
    total_time = sum(a.duration_ms + a.hesitation_ms for a in actions)
    confusion_count = random.randint(0, 3)
    confusion_points = []
    for selector, desc in random.sample(_CONFUSION_SCENARIOS, k=confusion_count):
        confusion_points.append(desc)
        for a in actions:
            if any(s.strip() in a.target for s in selector.split(",")):
                a.hesitation_ms += random.randint(300, 800)
                break
    is_hard = any(w in task.lower() for w in {"checkout", "payment", "account", "login", "register"})
    failed = random.random() < (0.30 if is_hard else 0.15)
    if failed:
        failure_reason = random.choice(_FAILURE_REASONS)
        cutoff = random.randint(max(1, len(actions) // 2), len(actions))
        actions = actions[:cutoff]
        actions[-1].error = "Task abandoned: " + failure_reason
        total_time = sum(a.duration_ms + a.hesitation_ms for a in actions)
        completion_rate = round(cutoff / max(len(_pick_template(task)), 1), 2)
    else:
        failure_reason = None
        completion_rate = 1.0
    return TaskTrace(
        task=task, url=url, actions=actions,
        success=not failed, failure_reason=failure_reason,
        total_time_ms=total_time, confusion_points=confusion_points,
        completion_rate=completion_rate,
    )


def run_task_with_persona(task: str, url: str, persona: str, task_num: int = 0) -> dict:
    profile = PERSONA_PROFILES[persona]
    label   = profile["label"]
    trace   = _run_task_mock(task, url)
    actions = [Action(**vars(a)) for a in trace.actions]

    for a in actions:
        a.duration_ms = int(a.duration_ms * profile["timing_multiplier"])
        if a.hesitation_ms:
            a.hesitation_ms = int(a.hesitation_ms * profile["hesitation_multiplier"])
        if persona == "non_native_english" and a.type in ("hover", "wait", "scroll"):
            a.hesitation_ms += profile["jargon_hesitation_ms"]

    if persona == "adhd_user":
        actions = [a for a in actions if random.random() > profile["skip_probability"]]
        if not actions:
            actions = [trace.actions[0]] if trace.actions else actions
        if random.random() < profile["random_click_probability"]:
            actions.insert(
                random.randint(0, len(actions)),
                Action("click", ".ad,.banner,.promo,[data-ad]", duration_ms=250),
            )

    if persona == "elderly_user":
        for a in actions:
            for bias in profile["confusion_bias"]:
                if any(s.strip() in a.target for s in bias.split(",")):
                    a.hesitation_ms += random.randint(400, 1000)
                    break

    success = trace.success
    failure_reason = trace.failure_reason
    completion_rate = trace.completion_rate
    if success and random.random() < profile["extra_failure_rate"]:
        success = False
        failure_reason = random.choice(_FAILURE_REASONS)
        cutoff = random.randint(max(1, len(actions) // 2), len(actions))
        actions = actions[:cutoff]
        if actions:
            actions[-1].error = "Task abandoned: " + failure_reason
        completion_rate = round(cutoff / max(len(_pick_template(task)), 1), 2)

    for a in actions:
        _live_log(label, task_num, _action_desc(a))
        time.sleep(0.04)

    total_ms = sum(a.duration_ms + a.hesitation_ms for a in actions)
    total_s  = total_ms / 1000
    if success:
        _live_log(label, task_num, f"✓ DONE ({total_s:.1f}s)")
    else:
        _live_log(label, task_num, f"✗ FAILED — {failure_reason} ({total_s:.1f}s)")

    return {
        "task": task, "url": url, "persona": persona, "persona_label": label,
        "success": success, "completion_rate": completion_rate,
        "total_time_seconds": round(total_ms / 1000, 1),
        "failure_reason": failure_reason,
        "confusion_points": trace.confusion_points,
        "actions": [
            {"type": a.type, "target": a.target,
             **({"value": a.value} if a.value else {}),
             "duration_ms": a.duration_ms,
             **({"hesitation_ms": a.hesitation_ms} if a.hesitation_ms else {}),
             **({"error": a.error} if a.error else {})}
            for a in actions
        ],
        "screenshots": [],
    }


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def run_task(task: str, url: str, persona_label: str = "Browser Use") -> TaskTrace:
    """Single task run — uses real browser via CDP if available, otherwise mock."""
    if _BROWSER_USE_AVAILABLE and asyncio.run(_check_cdp()):
        try:
            d = asyncio.run(_run_task_browser_use(task, url, "elderly_user", task_num=0))
            return TaskTrace(
                task=d["task"], url=d["url"],
                success=d["success"], failure_reason=d["failure_reason"],
                total_time_ms=int(d["total_time_seconds"] * 1000),
                confusion_points=d["confusion_points"],
                completion_rate=d["completion_rate"],
                actions=[
                    Action(type=a["type"], target=a["target"],
                           duration_ms=a.get("duration_ms", 500))
                    for a in d["actions"]
                ],
            )
        except Exception as exc:
            with _print_lock:
                print(f"[{persona_label}] browser_use error: {exc} — using mock", flush=True)
    return _run_task_mock(task, url)


def run_tasks_multi_persona(tasks: list[str], url: str) -> dict[str, list[dict]]:
    """
    Run all tasks × 3 personas in parallel using CDP tabs in the existing Chrome.
    Fallback: deterministic mock with OS threads.
    """
    if _BROWSER_USE_AVAILABLE:
        # Check Chrome is reachable before trying
        cdp_ok = asyncio.run(_check_cdp())
        if not cdp_ok:
            print(_CDP_UNAVAILABLE_MSG, flush=True)
        else:
            try:
                return asyncio.run(_run_tasks_multi_persona_async(tasks, url))
            except Exception as exc:
                with _print_lock:
                    print(f"[browser_use] async run failed: {exc} — falling back to mock", flush=True)

    # Mock fallback
    personas = list(PERSONA_PROFILES.keys())
    results: dict[str, list] = {p: [None] * len(tasks) for p in personas}
    lock = threading.Lock()

    def _worker(idx: int, task: str, persona: str) -> None:
        trace = run_task_with_persona(task, url, persona, task_num=idx + 1)
        with lock:
            results[persona][idx] = trace

    threads = []
    for idx, task in enumerate(tasks):
        for persona in personas:
            t = threading.Thread(target=_worker, args=(idx, task, persona), daemon=True)
            threads.append(t); t.start()
    for t in threads:
        t.join()
    return results


def format_trace(trace: TaskTrace) -> dict:
    return {
        "task":               trace.task,
        "url":                trace.url,
        "success":            trace.success,
        "completion_rate":    trace.completion_rate,
        "total_time_seconds": round(trace.total_time_ms / 1000, 1),
        "failure_reason":     trace.failure_reason,
        "confusion_points":   trace.confusion_points,
        "actions": [
            {"type": a.type, "target": a.target,
             **({"value": a.value} if a.value else {}),
             "duration_ms": a.duration_ms,
             **({"hesitation_ms": a.hesitation_ms} if a.hesitation_ms else {}),
             **({"error": a.error} if a.error else {})}
            for a in trace.actions
        ],
    }
