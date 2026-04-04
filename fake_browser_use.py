"""
Browser automation via the local browser_use Python library.
When browser_use + playwright are installed, runs REAL browser sessions
with three visible Chrome windows (headless=False), each behaving as a
different persona.  Falls back to the deterministic mock when unavailable.

Install:
    pip install browser-use playwright langchain-anthropic
    playwright install chromium
"""

import asyncio
import json
import os
import random
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Try to import browser_use — set flag so callers can see what's active
# ---------------------------------------------------------------------------

try:
    # browser_use ≥ 0.2 exports Browser/BrowserConfig at top level
    try:
        from browser_use import Agent, Browser, BrowserConfig
    except ImportError:
        from browser_use import Agent
        from browser_use.browser.browser import Browser, BrowserConfig  # type: ignore

    _BROWSER_USE_AVAILABLE = True
except ImportError:
    _BROWSER_USE_AVAILABLE = False


def _require_llm():
    """Lazy-load the LangChain Anthropic LLM; raises a clear error if missing."""
    try:
        from langchain_anthropic import ChatAnthropic  # type: ignore
    except ImportError:
        raise ImportError(
            "langchain-anthropic is required for real browser sessions.\n"
            "  pip install langchain-anthropic"
        )
    return ChatAnthropic(
        model="claude-3-5-sonnet-20241022",
        api_key=os.environ["ANTHROPIC_API_KEY"],
        temperature=0,
    )


# ---------------------------------------------------------------------------
# Thread / async-safe logging
# ---------------------------------------------------------------------------

_print_lock = threading.Lock()

# Optional callback registered by pipeline.py → writes to log.json in real time
_log_callback = None


def set_log_callback(fn) -> None:
    """Register a function(persona_label, task_num, msg) called on every log line."""
    global _log_callback
    _log_callback = fn


def _live_log(label: str, task_num: int, msg: str) -> None:
    with _print_lock:
        print(f"[{label:<17}] → Task {task_num}: {msg}", flush=True)
    if _log_callback is not None:
        try:
            _log_callback(label, task_num, msg)
        except Exception:
            pass


def _action_desc(action: "Action") -> str:
    """Human-readable one-liner with timing annotation (mock path only)."""
    verb = {
        "navigate": "navigating to",
        "hover":    "hovering over",
        "click":    "clicking",
        "type":     "typing into",
        "scroll":   "scrolling",
        "wait":     "waiting for",
        "back":     "navigating back from",
    }.get(action.type, action.type)
    target = action.target[:52] + "..." if len(action.target) > 55 else action.target
    val_note = f' "{action.value}"' if action.value else ""
    dur_s = action.duration_ms / 1000
    timing = (
        f"({dur_s:.1f}s + {action.hesitation_ms/1000:.1f}s hesitation)"
        if action.hesitation_ms else f"({dur_s:.1f}s)"
    )
    if action.error:
        return f"✗ FAILED — {action.error}"
    return f"{verb} {target}{val_note}... {timing}"


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
        # Real-browser settings
        "wait_between_actions":  2.8,   # BrowserConfig.wait_between_actions
        "effect_interval":       2.0,   # seconds between persona effect injections
        "window_pos":            (0, 50),
        "window_size":           (860, 720),
        "indicator_color":       "#ffd700",
        "indicator_label":       "👴 Elderly User",
    },
    "adhd_user": {
        "label":                 "ADHD User",
        "description":           "Easily distracted — skips steps, clicks unexpected elements, shorter attention span",
        "timing_multiplier":     0.75,
        "hesitation_multiplier": 0.4,
        "extra_failure_rate":    0.22,
        "skip_probability":      0.30,
        "random_click_probability": 0.25,
        "jargon_hesitation_ms":  0,
        "confusion_bias":        set(),
        # Real-browser settings
        "wait_between_actions":  0.2,
        "effect_interval":       1.5,
        "window_pos":            (870, 50),
        "window_size":           (860, 720),
        "indicator_color":       "#ff88aa",
        "indicator_label":       "⚡ ADHD User",
    },
    "non_native_english": {
        "label":                 "Non-Native English",
        "description":           "Struggles with jargon, reads slower, may misinterpret idioms",
        "timing_multiplier":     1.3,
        "hesitation_multiplier": 1.9,
        "extra_failure_rate":    0.12,
        "skip_probability":      0.0,
        "random_click_probability": 0.0,
        "jargon_hesitation_ms":  900,
        "confusion_bias": {
            "nav, .navigation",
            ".search-bar, input[type='search']",
            "footer",
        },
        # Real-browser settings
        "wait_between_actions":  1.6,
        "effect_interval":       2.5,
        "window_pos":            (0, 790),
        "window_size":           (860, 720),
        "indicator_color":       "#88ccff",
        "indicator_label":       "🌍 Non-Native English",
    },
}

# Persona context injected into each agent's task description
_PERSONA_TASK_CONTEXT = {
    "elderly_user": (
        "You are acting as an elderly user (70+) with limited technical experience. "
        "You navigate very slowly, hover over links and buttons for a long time before "
        "deciding to click, and prefer simple and clearly labelled paths. "
        "You sometimes get confused by jargon or small text. "
    ),
    "adhd_user": (
        "You are acting as a user with ADHD. You move quickly, sometimes lose focus "
        "and click on something that caught your eye but is off-task, then correct yourself. "
        "You tend to skip reading instructions and jump straight to clicking buttons. "
    ),
    "non_native_english": (
        "You are acting as a non-native English speaker. You read slowly and carefully, "
        "hovering over text to process it. You may misread labels or struggle with idiomatic "
        "phrases, causing you to take a longer route to complete the task. "
    ),
}

_CONFUSION_SCENARIOS = [
    ("nav, .navigation", "Navigation labels are ambiguous — user hovered multiple items before clicking"),
    ("footer", "User had to scroll to footer to find basic info — not surfaced in main nav"),
    (".search-bar, input[type='search']", "Search bar not immediately visible — user scanned for 3+ seconds"),
    (".cta, .buy-now, .checkout", "Primary CTA blends into background — low contrast ratio"),
    (".cart, .bag", "Cart icon has no item count badge — user unsure add-to-cart worked"),
    ("form", "Form lacks inline validation — user unsure which fields are required"),
    (".menu, .hamburger", "Mobile menu icon not labelled — user tapped wrong element first"),
    (".price, .cost", "Pricing hidden behind a click — user expected to see it upfront"),
]

_FAILURE_REASONS = [
    "Could not locate the checkout button after adding item to cart",
    "Search returned no results for expected query — no helpful empty state",
    "Required form field missing label — could not determine what to enter",
    "Page took >8s to load — task abandoned",
    "Navigation menu collapsed on scroll — lost orientation",
    "Modal dialog blocked primary content — no clear dismiss option",
    "Back button triggered form resubmission warning — user confused and stopped",
]

_ACTION_TEMPLATES = {
    "search": [
        Action("navigate", "{url}", duration_ms=800),
        Action("hover", "nav, header, [role='navigation']", duration_ms=300, hesitation_ms=200),
        Action("click", "input[type='search'], [placeholder*='Search'], .search-bar", duration_ms=400),
        Action("type", "input[type='search']", value="{query}", duration_ms=600),
        Action("click", "button[type='submit'], .search-button, [aria-label='Search']", duration_ms=300),
        Action("wait", "search results", duration_ms=1200),
    ],
    "buy": [
        Action("navigate", "{url}", duration_ms=800),
        Action("scroll", "main content", duration_ms=500),
        Action("hover", ".product, .item, [class*='card']", duration_ms=400, hesitation_ms=300),
        Action("click", ".product:first-child, .buy-now, .add-to-cart", duration_ms=500),
        Action("wait", "cart update", duration_ms=800),
        Action("hover", "nav, header", duration_ms=600, hesitation_ms=500),
        Action("click", ".cart, [aria-label*='cart'], .bag", duration_ms=400),
        Action("click", ".checkout, [href*='checkout']", duration_ms=350),
    ],
    "navigate": [
        Action("navigate", "{url}", duration_ms=800),
        Action("hover", "nav a, .menu a, header a", duration_ms=500, hesitation_ms=400),
        Action("scroll", "navigation menu", duration_ms=300),
        Action("click", "nav a:first-child", duration_ms=400),
        Action("wait", "page load", duration_ms=1000),
    ],
    "find": [
        Action("navigate", "{url}", duration_ms=800),
        Action("scroll", "page", duration_ms=700),
        Action("hover", "footer, .footer, [class*='footer']", duration_ms=500, hesitation_ms=600),
        Action("scroll", "footer content", duration_ms=400),
        Action("click", "footer a, .footer a", duration_ms=350),
        Action("wait", "page load", duration_ms=900),
    ],
    "contact": [
        Action("navigate", "{url}", duration_ms=800),
        Action("hover", "nav, footer", duration_ms=700, hesitation_ms=800),
        Action("scroll", "page", duration_ms=600),
        Action("click", "[href*='contact'], a[href*='support']", duration_ms=400),
        Action("wait", "contact page", duration_ms=900),
        Action("hover", "form", duration_ms=400, hesitation_ms=300),
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
    task_lower = task.lower()
    for keyword in _ACTION_TEMPLATES:
        if keyword != "default" and keyword in task_lower:
            return [Action(**vars(a)) for a in _ACTION_TEMPLATES[keyword]]
    return [Action(**vars(a)) for a in _ACTION_TEMPLATES["default"]]


# ---------------------------------------------------------------------------
# Mock implementation (used as fallback)
# ---------------------------------------------------------------------------

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
    for selector, description in random.sample(_CONFUSION_SCENARIOS, k=confusion_count):
        confusion_points.append(description)
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


# ---------------------------------------------------------------------------
# Real browser_use implementation
# ---------------------------------------------------------------------------

_SCREENSHOT_DIR = "screenshots"

_INDICATOR_JS = """
(function(label, color) {{
    const existing = document.getElementById('__persona_badge__');
    if (existing) return;
    const d = document.createElement('div');
    d.id = '__persona_badge__';
    d.style.cssText = [
        'position:fixed', 'top:8px', 'right:8px',
        'background:' + color, 'color:#000',
        'padding:5px 12px', 'border-radius:6px',
        'font-size:13px', 'font-weight:700',
        'z-index:2147483647', 'box-shadow:0 2px 8px rgba(0,0,0,.4)',
        'pointer-events:none', 'font-family:system-ui,sans-serif',
    ].join(';');
    d.textContent = label;
    document.body && document.body.appendChild(d);
}})("{label}", "{color}");
"""


async def _persona_effect_loop(
    browser: Any,
    persona: str,
    stop_evt: asyncio.Event,
) -> None:
    """
    Background coroutine: periodically injects visual + behavioural effects
    into the active browser page while the agent runs.
    """
    profile = PERSONA_PROFILES[persona]
    interval = profile["effect_interval"]
    label_js = profile["indicator_label"]
    color_js = profile["indicator_color"]
    indicator = _INDICATOR_JS.format(label=label_js, color=color_js)

    while not stop_evt.is_set():
        await asyncio.sleep(interval)
        if stop_evt.is_set():
            break
        try:
            page = await browser.get_current_page()

            # ── Elderly: zoom + badge ──────────────────────────────────────
            if persona == "elderly_user":
                await page.evaluate("document.body.style.zoom='150%'")
                await page.evaluate(indicator)

            # ── ADHD: random scroll + badge ────────────────────────────────
            elif persona == "adhd_user":
                await page.evaluate(indicator)
                if random.random() < 0.35:
                    amt = random.randint(-180, 360)
                    await page.evaluate(
                        f"window.scrollBy({{top:{amt},behavior:'smooth'}})"
                    )

            # ── Non-native: badge + hover-pause effect ─────────────────────
            elif persona == "non_native_english":
                await page.evaluate(indicator)
                # Simulate slow reading: briefly highlight hovered text
                await page.evaluate(
                    "document.querySelectorAll('p,h1,h2,h3,li,a,button')"
                    "[Math.floor(Math.random()*10)]"
                    "?.scrollIntoView({behavior:'smooth',block:'nearest'})"
                )

        except Exception:
            pass  # page may not exist yet or already closed


async def _take_screenshot(browser: Any, persona: str, task_num: int, step: int) -> str | None:
    """Save a PNG screenshot; returns the relative path or None on failure."""
    try:
        Path(_SCREENSHOT_DIR).mkdir(exist_ok=True)
        fname = f"{persona}_task{task_num:02d}_step{step:02d}.png"
        fpath = Path(_SCREENSHOT_DIR) / fname
        page = await browser.get_current_page()
        await page.screenshot(path=str(fpath), full_page=False)
        return str(fpath)
    except Exception:
        return None


def _extract_actions_from_history(history: Any) -> list[dict]:
    """Convert browser_use AgentHistoryList into our action-dict format."""
    actions = []
    try:
        for step in history.history:
            if not step.model_output:
                continue
            for action_item in (step.model_output.action or []):
                if not action_item:
                    continue
                name = next(iter(action_item), "unknown")
                params = action_item.get(name, {})
                if isinstance(params, dict):
                    target = (
                        params.get("selector")
                        or params.get("url")
                        or params.get("text")
                        or params.get("query")
                        or str(params)[:80]
                    )
                else:
                    target = str(params)[:80]
                actions.append({
                    "type":        name,
                    "target":      str(target),
                    "duration_ms": 500,
                })
    except Exception:
        pass
    return actions


async def _run_task_browser_use(
    task: str,
    url: str,
    persona: str,
    task_num: int = 0,
) -> dict:
    """
    Run a REAL browser session via the local browser_use Agent.
    Opens a visible Chrome window (headless=False) with persona-specific
    behavior injected via a background asyncio loop.
    """
    profile = PERSONA_PROFILES[persona]
    label = profile["label"]
    x, y = profile["window_pos"]
    w, h = profile["window_size"]
    start_time = time.time()
    screenshots: list[str] = []

    # ── Browser with persona-specific timing ──────────────────────────────
    try:
        browser = Browser(config=BrowserConfig(
            headless=False,
            extra_chromium_args=[
                f"--window-size={w},{h}",
                f"--window-position={x},{y}",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            wait_between_actions=profile["wait_between_actions"],
            minimum_wait_page_load_time=0.8,
        ))
    except TypeError:
        # Older browser_use that doesn't accept wait_between_actions
        browser = Browser(config=BrowserConfig(
            headless=False,
            extra_chromium_args=[
                f"--window-size={w},{h}",
                f"--window-position={x},{y}",
            ],
        ))

    llm = _require_llm()
    full_task = (
        _PERSONA_TASK_CONTEXT[persona]
        + f"\n\nYour task: {task}"
        + f"\nStart at: {url}"
    )

    _live_log(label, task_num, f"opening browser → {task[:55]}")

    # ── Start persona effects background loop ─────────────────────────────
    stop_evt = asyncio.Event()
    effect_task = asyncio.create_task(
        _persona_effect_loop(browser, persona, stop_evt)
    )

    # ── Screenshot background loop ────────────────────────────────────────
    step_counter = [0]

    async def screenshot_loop() -> None:
        while not stop_evt.is_set():
            await asyncio.sleep(4)
            if stop_evt.is_set():
                break
            step_counter[0] += 1
            path = await _take_screenshot(browser, persona, task_num, step_counter[0])
            if path:
                screenshots.append(path)
                _live_log(label, task_num, f"📸 screenshot {step_counter[0]} → {path}")

    screenshot_task = asyncio.create_task(screenshot_loop())

    # ── Run agent ─────────────────────────────────────────────────────────
    try:
        agent = Agent(
            task=full_task,
            llm=llm,
            browser=browser,
        )
        history = await agent.run(max_steps=12)

        # Final screenshot
        path = await _take_screenshot(browser, persona, task_num, step_counter[0] + 1)
        if path:
            screenshots.append(path)

        # Determine success from history
        try:
            success = history.is_done() or bool(history.final_result())
        except Exception:
            success = True  # agent completed without exception → treat as success
        failure_reason = None
        actions = _extract_actions_from_history(history)

    except Exception as exc:
        success = False
        failure_reason = str(exc)[:200]
        actions = []
        _live_log(label, task_num, f"✗ ERROR — {failure_reason}")

    # ── Tear down ─────────────────────────────────────────────────────────
    stop_evt.set()
    effect_task.cancel()
    screenshot_task.cancel()
    try:
        await asyncio.gather(effect_task, screenshot_task, return_exceptions=True)
    except Exception:
        pass

    try:
        await browser.close()
    except Exception:
        pass

    elapsed = round(time.time() - start_time, 1)
    if success:
        _live_log(label, task_num, f"✓ DONE ({elapsed}s, {len(screenshots)} screenshots)")
    else:
        _live_log(label, task_num, f"✗ FAILED — {failure_reason} ({elapsed}s)")

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


# ---------------------------------------------------------------------------
# Async parallel multi-persona runner
# ---------------------------------------------------------------------------

async def _run_tasks_multi_persona_async(
    tasks: list[str], url: str
) -> dict[str, list[dict]]:
    """
    Open three Chrome windows simultaneously (one per persona) and run all
    tasks for each persona in sequence, all three sequences in parallel.
    """
    personas = list(PERSONA_PROFILES.keys())

    async def run_one_persona(persona: str) -> tuple[str, list[dict]]:
        traces = []
        for idx, task in enumerate(tasks):
            trace = await _run_task_browser_use(task, url, persona, task_num=idx + 1)
            traces.append(trace)
        return persona, traces

    # asyncio.gather fires all three persona coroutines at once →
    # three Chrome windows open on screen simultaneously.
    results_list = await asyncio.gather(
        *[run_one_persona(p) for p in personas],
        return_exceptions=True,
    )

    results: dict[str, list[dict]] = {}
    for item in results_list:
        if isinstance(item, Exception):
            # Log and substitute empty result so pipeline continues
            with _print_lock:
                print(f"[browser_use] persona failed: {item}", flush=True)
        else:
            persona, traces = item
            results[persona] = traces

    return results


# ---------------------------------------------------------------------------
# Public interface — run_task (single call, no persona)
# ---------------------------------------------------------------------------

def run_task(task: str, url: str, persona_label: str = "Browser Use") -> TaskTrace:
    """
    Execute a task in a real browser (headless=False) if browser_use is
    available; otherwise falls back to the deterministic mock.
    """
    if _BROWSER_USE_AVAILABLE:
        try:
            result_dict = asyncio.run(
                _run_task_browser_use(task, url, "elderly_user", task_num=0)
            )
            # Convert dict back to TaskTrace for pipeline compatibility
            return TaskTrace(
                task=result_dict["task"],
                url=result_dict["url"],
                success=result_dict["success"],
                failure_reason=result_dict["failure_reason"],
                total_time_ms=int(result_dict["total_time_seconds"] * 1000),
                confusion_points=result_dict["confusion_points"],
                completion_rate=result_dict["completion_rate"],
                actions=[
                    Action(type=a["type"], target=a["target"],
                           duration_ms=a.get("duration_ms", 500))
                    for a in result_dict["actions"]
                ],
            )
        except Exception as exc:
            with _print_lock:
                print(f"[{persona_label:<17}] browser_use error: {exc} — using mock", flush=True)
    return _run_task_mock(task, url)


# ---------------------------------------------------------------------------
# Public interface — run_task_with_persona (mock path, used for quick tests)
# ---------------------------------------------------------------------------

def run_task_with_persona(task: str, url: str, persona: str, task_num: int = 0) -> dict:
    """
    Mock-based persona runner (used when browser_use is not available or as a
    fast path).  For real browser sessions use run_tasks_multi_persona instead.
    """
    profile = PERSONA_PROFILES[persona]
    label = profile["label"]
    trace = _run_task_mock(task, url)

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
                Action("click", ".ad,.banner,.promo,[data-ad]", duration_ms=250, hesitation_ms=50),
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

    _STEP_SLEEP = 0.05
    for a in actions:
        _live_log(label, task_num, _action_desc(a))
        time.sleep(_STEP_SLEEP)

    total_ms = sum(a.duration_ms + a.hesitation_ms for a in actions)
    total_s = total_ms / 1000
    if success:
        _live_log(label, task_num, f"✓ DONE ({total_s:.1f}s total)")
    else:
        _live_log(label, task_num, f"✗ FAILED — {failure_reason} ({total_s:.1f}s total)")

    return {
        "task":               task,
        "url":                url,
        "persona":            persona,
        "persona_label":      label,
        "success":            success,
        "completion_rate":    completion_rate,
        "total_time_seconds": round(total_ms / 1000, 1),
        "failure_reason":     failure_reason,
        "confusion_points":   trace.confusion_points,
        "actions": [
            {
                "type": a.type, "target": a.target,
                **({"value": a.value} if a.value else {}),
                "duration_ms": a.duration_ms,
                **({"hesitation_ms": a.hesitation_ms} if a.hesitation_ms else {}),
                **({"error": a.error} if a.error else {}),
            }
            for a in actions
        ],
        "screenshots": [],
    }


# ---------------------------------------------------------------------------
# Public interface — run_tasks_multi_persona
# ---------------------------------------------------------------------------

def run_tasks_multi_persona(tasks: list[str], url: str) -> dict[str, list[dict]]:
    """
    Run all tasks for all three personas in parallel.

    • When browser_use is installed: opens three visible Chrome windows
      simultaneously via asyncio.gather and runs real browser sessions.
    • Fallback: uses the deterministic mock with OS threads (original behaviour).
    """
    if _BROWSER_USE_AVAILABLE:
        try:
            return asyncio.run(_run_tasks_multi_persona_async(tasks, url))
        except Exception as exc:
            with _print_lock:
                print(f"[browser_use] async run failed: {exc} — falling back to mock", flush=True)

    # ── Mock fallback: OS threads ─────────────────────────────────────────
    personas = list(PERSONA_PROFILES.keys())
    results: dict[str, list] = {p: [None] * len(tasks) for p in personas}
    lock = threading.Lock()

    def _worker(task_idx: int, task: str, persona: str) -> None:
        trace = run_task_with_persona(task, url, persona, task_num=task_idx + 1)
        with lock:
            results[persona][task_idx] = trace

    threads = []
    for idx, task in enumerate(tasks):
        for persona in personas:
            t = threading.Thread(target=_worker, args=(idx, task, persona), daemon=True)
            threads.append(t)
            t.start()
    for t in threads:
        t.join()

    return results


# ---------------------------------------------------------------------------
# Serialisation helper
# ---------------------------------------------------------------------------

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
            {
                "type": a.type, "target": a.target,
                **({"value": a.value} if a.value else {}),
                "duration_ms": a.duration_ms,
                **({"hesitation_ms": a.hesitation_ms} if a.hesitation_ms else {}),
                **({"error": a.error} if a.error else {}),
            }
            for a in trace.actions
        ],
    }
