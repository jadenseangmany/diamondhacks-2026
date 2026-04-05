"""
AgentUX — FastAPI Server
REST API + WebSocket for the AI-powered usability testing platform.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from models import RunStatus, TestRun
from personas import get_all_personas
from pipeline import run_pipeline, step_apply_edits, step_regression_test

load_dotenv(override=True)


# ── In-memory store ──────────────────────────────────────────────────────────

runs_store: dict[str, TestRun] = {}
websocket_connections: dict[str, list[WebSocket]] = {}


# ── App ───────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    print("🚀 AgentUX server starting...")
    yield
    print("👋 AgentUX server shutting down...")


app = FastAPI(
    title="AgentUX",
    description="AI-powered usability testing platform using Browser Use agents",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request/Response Models ───────────────────────────────────────────────────

class TestRequest(BaseModel):
    url: str
    personas: list[Any] = []  # Can perfectly handle string keys or custom dict payloads
    num_tasks: int = 2  # Number of usability testing tasks to generate


class ApprovalRequest(BaseModel):
    edit_ids: list[str] = []
    approved: bool = True


class RunResponse(BaseModel):
    id: str
    url: str
    status: str
    progress: float
    current_step: str
    site_summary: str
    tasks: list[dict]
    persona_results: list[dict]
    heatmap: list[dict]
    suggested_edits: list[dict]
    regression_results: list[dict]
    overall_usability_score: float
    accessibility_score: float
    clarity_score: float
    log_messages: list[str]
    created_at: str
    updated_at: str


def _run_to_response(run: TestRun) -> dict[str, Any]:
    """Convert a TestRun to a JSON-serializable dict."""
    return {
        "id": run.id,
        "url": run.url,
        "status": run.status.value,
        "progress": run.progress,
        "current_step": run.current_step,
        "site_summary": run.site_summary,
        "tasks": [t.model_dump() for t in run.tasks],
        "persona_results": [r.model_dump(mode="json") for r in run.persona_results],
        "heatmap": [h.model_dump() for h in run.heatmap],
        "suggested_edits": [e.model_dump() for e in run.suggested_edits],
        "regression_results": [r.model_dump() for r in run.regression_results],
        "overall_usability_score": run.overall_usability_score,
        "accessibility_score": run.accessibility_score,
        "clarity_score": run.clarity_score,
        "log_messages": run.log_messages[-50:],  # Last 50 messages
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
    }


# ── WebSocket broadcast ──────────────────────────────────────────────────────

async def broadcast_update(run: TestRun):
    """Send run update to all connected WebSocket clients."""
    run_id = run.id
    run.updated_at = datetime.now()
    runs_store[run_id] = run

    if run_id in websocket_connections:
        data = json.dumps(_run_to_response(run))
        disconnected = []
        for ws in websocket_connections[run_id]:
            try:
                await ws.send_text(data)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            websocket_connections[run_id].remove(ws)


def sync_broadcast(run: TestRun):
    """Synchronous wrapper for broadcast - used as pipeline callback."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(broadcast_update(run))
        else:
            loop.run_until_complete(broadcast_update(run))
    except Exception:
        # Store update even if broadcast fails
        runs_store[run.id] = run


# ── REST Endpoints ────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "name": "AgentUX",
        "version": "0.1.0",
        "description": "AI-powered usability testing platform",
    }


@app.get("/api/personas")
async def list_personas():
    """List all available agent personas."""
    return {"personas": get_all_personas()}


@app.post("/api/test")
async def start_test(request: TestRequest):
    """Start a new usability test run."""
    run = TestRun(url=request.url)
    runs_store[run.id] = run

    # Start pipeline in background
    asyncio.create_task(_run_pipeline_background(run, request.personas, request.num_tasks))

    return {
        "id": run.id,
        "status": "started",
        "url": run.url,
        "message": f"Usability test started for {run.url}",
    }


async def _run_pipeline_background(run: TestRun, selected_personas: list[str] = None, num_tasks: int = 2):
    """Run the pipeline in the background with WebSocket updates."""
    try:
        await run_pipeline(
            url=run.url,
            run=run,
            on_progress=sync_broadcast,
            stop_before_edits=True,
            selected_personas=selected_personas or [],
            num_tasks=num_tasks,
        )
    except Exception as e:
        run.status = RunStatus.FAILED
        run.log_messages.append(f"[ERROR] Pipeline failed: {str(e)}")
    finally:
        runs_store[run.id] = run
        await broadcast_update(run)


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    """Get the current status and results of a test run."""
    if run_id not in runs_store:
        raise HTTPException(status_code=404, detail="Run not found")
    return _run_to_response(runs_store[run_id])


@app.get("/api/runs")
async def list_runs():
    """List all test runs."""
    return {
        "runs": [
            {
                "id": r.id,
                "url": r.url,
                "status": r.status.value,
                "progress": r.progress,
                "created_at": r.created_at.isoformat(),
                "overall_usability_score": r.overall_usability_score,
            }
            for r in runs_store.values()
        ]
    }


@app.post("/api/runs/{run_id}/approve")
async def approve_edits(run_id: str, request: ApprovalRequest):
    """Approve or reject suggested edits (human-in-the-loop)."""
    if run_id not in runs_store:
        raise HTTPException(status_code=404, detail="Run not found")

    run = runs_store[run_id]

    if run.status != RunStatus.AWAITING_APPROVAL:
        raise HTTPException(
            status_code=400,
            detail=f"Run is not awaiting approval (current status: {run.status.value})"
        )

    # Mark edits as approved/rejected
    for edit in run.suggested_edits:
        if not request.edit_ids or edit.id in request.edit_ids:
            edit.approved = request.approved
        elif edit.approved is None:
            edit.approved = False

    approved_count = sum(1 for e in run.suggested_edits if e.approved)
    run.log_messages.append(
        f"[{datetime.now().isoformat()}] Human approved {approved_count}/{len(run.suggested_edits)} edits"
    )

    # Continue pipeline: apply edits
    asyncio.create_task(_continue_pipeline(run))

    return {
        "status": "approved",
        "approved_count": approved_count,
        "total_edits": len(run.suggested_edits),
    }


async def _continue_pipeline(run: TestRun):
    """Continue the pipeline after human approval."""
    try:
        await step_apply_edits(run, sync_broadcast)
        await step_regression_test(run, sync_broadcast)
    except Exception as e:
        run.status = RunStatus.FAILED
        run.log_messages.append(f"[ERROR] Post-approval pipeline failed: {str(e)}")
    finally:
        runs_store[run.id] = run
        await broadcast_update(run)


@app.post("/api/runs/{run_id}/regression")
async def trigger_regression(run_id: str):
    """Trigger a regression test re-run."""
    if run_id not in runs_store:
        raise HTTPException(status_code=404, detail="Run not found")

    run = runs_store[run_id]
    asyncio.create_task(_run_regression(run))

    return {"status": "regression_started"}


async def _run_regression(run: TestRun):
    """Run regression testing."""
    try:
        await step_regression_test(run, sync_broadcast)
    except Exception as e:
        run.log_messages.append(f"[ERROR] Regression test failed: {str(e)}")
    finally:
        runs_store[run.id] = run
        await broadcast_update(run)


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws/{run_id}")
async def websocket_endpoint(websocket: WebSocket, run_id: str):
    """WebSocket endpoint for live progress updates."""
    await websocket.accept()

    if run_id not in websocket_connections:
        websocket_connections[run_id] = []
    websocket_connections[run_id].append(websocket)

    try:
        # Send current state immediately
        if run_id in runs_store:
            await websocket.send_text(
                json.dumps(_run_to_response(runs_store[run_id]))
            )

        # Keep connection alive
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                # Handle ping/pong
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # Send keepalive
                try:
                    await websocket.send_text(json.dumps({"type": "keepalive"}))
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    finally:
        if run_id in websocket_connections:
            try:
                websocket_connections[run_id].remove(websocket)
            except ValueError:
                pass


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
