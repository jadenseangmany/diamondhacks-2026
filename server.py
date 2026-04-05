"""
Usability Pipeline — FastAPI server.

Run:
    uvicorn server:app

This starts:
  1. Chrome with remote debugging (port 9222) + extension auto-loaded
  2. WebSocket broadcaster (port 7655) for the extension side panel
  3. HTTP server at http://localhost:8000

Trigger a pipeline run:
  • Web UI:  http://localhost:8080
  • CLI:     curl -X POST "http://localhost:8080/run?url=https://example.com"
"""

import asyncio
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from pipeline import init_live_log, run_pipeline

# ── Chrome paths ──────────────────────────────────────────────────────────────
_CHROME = {
    "darwin": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "linux":  "google-chrome",
    "win32":  r"C:\Program Files\Google\Chrome\Application\chrome.exe",
}.get(sys.platform, "google-chrome")

_EXT_DIR     = str(Path(__file__).parent / "extension")
_PROFILE_DIR = "/tmp/chrome-usability-pipeline"

_chrome_proc: subprocess.Popen | None = None


def _launch_chrome() -> subprocess.Popen | None:
    """Start Chrome with remote debugging + unpacked extension loaded."""
    cmd = [
        _CHROME,
        "--remote-debugging-port=9222",
        f"--user-data-dir={_PROFILE_DIR}",
        f"--load-extension={_EXT_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "about:blank",
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)  # let Chrome finish starting
        print(f"[server] Chrome started (pid={proc.pid})", flush=True)
        print(f"[server] Extension loaded from: {_EXT_DIR}", flush=True)
        return proc
    except FileNotFoundError:
        print(f"[server] Chrome not found at: {_CHROME}", flush=True)
        print("[server] Start Chrome manually with --remote-debugging-port=9222", flush=True)
        return None
    except Exception as e:
        print(f"[server] Chrome launch error: {e}", flush=True)
        return None


# ── App lifespan ──────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _chrome_proc
    print("[server] Starting up…", flush=True)
    _chrome_proc = _launch_chrome()
    init_live_log("log.json")
    print("[server] Ready — open http://localhost:8080 to run a pipeline", flush=True)
    yield
    if _chrome_proc and _chrome_proc.poll() is None:
        print("[server] Shutting down Chrome…", flush=True)
        _chrome_proc.terminate()


app = FastAPI(title="Usability Pipeline", lifespan=lifespan)
_pipeline_running = False


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    status_color = "#ff8800" if _pipeline_running else "#44dd88"
    status_text  = "Running…" if _pipeline_running else "Ready"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Usability Pipeline</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
           background: #0d0d1a; color: #e0e0f0; padding: 2rem; max-width: 520px; margin: 0 auto; }}
    h1   {{ font-size: 1.3rem; margin-bottom: 0.3rem; }}
    p    {{ color: #888899; font-size: 0.85rem; margin-bottom: 1.5rem; }}
    .status {{ font-size: 0.75rem; color: {status_color}; margin-bottom: 1rem; }}
    input  {{ width: 100%; padding: 10px 12px; border-radius: 6px; border: 1px solid #2a2a45;
              background: #13131f; color: #e0e0f0; font-size: 0.9rem; box-sizing: border-box; }}
    button {{ margin-top: 10px; width: 100%; padding: 10px; border-radius: 6px; border: none;
              background: #7c6fff; color: #fff; font-size: 0.9rem; font-weight: 700;
              cursor: pointer; }}
    button:hover {{ background: #6a5fff; }}
    button:disabled {{ background: #333; color: #666; cursor: default; }}
  </style>
</head>
<body>
  <h1>Usability Pipeline</h1>
  <p>Runs a 2-persona usability test and shows results in the Chrome extension side panel.</p>
  <div class="status">● {status_text}</div>
  <input id="url" type="url" placeholder="https://example.com" value="">
  <button id="btn" onclick="run()" {'disabled' if _pipeline_running else ''}>
    {'Pipeline running…' if _pipeline_running else 'Run Pipeline'}
  </button>
  <script>
    async function run() {{
      const url = document.getElementById('url').value.trim();
      if (!url) return;
      document.getElementById('btn').disabled = true;
      document.getElementById('btn').textContent = 'Starting…';
      const r = await fetch('/run?url=' + encodeURIComponent(url), {{method:'POST'}});
      const d = await r.json();
      if (r.ok) {{
        document.getElementById('btn').textContent = 'Pipeline running…';
      }} else {{
        document.getElementById('btn').textContent = d.detail || 'Error';
        document.getElementById('btn').disabled = false;
      }}
    }}
    // Poll status so button re-enables when done
    setInterval(async () => {{
      const r = await fetch('/status');
      const d = await r.json();
      const btn = document.getElementById('btn');
      if (!d.running && btn.disabled) {{
        btn.disabled = false;
        btn.textContent = 'Run Pipeline';
      }}
    }}, 3000);
  </script>
</body>
</html>"""


@app.post("/run")
async def run(url: str, background_tasks: BackgroundTasks):
    global _pipeline_running
    if _pipeline_running:
        raise HTTPException(status_code=409, detail="Pipeline already running")
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="url must start with http/https")

    _pipeline_running = True

    async def _task():
        global _pipeline_running
        try:
            await asyncio.to_thread(run_pipeline, url)
        finally:
            _pipeline_running = False

    background_tasks.add_task(_task)
    return {"status": "started", "url": url}


@app.get("/status")
async def status():
    return {"running": _pipeline_running}
