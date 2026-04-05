# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AgentUX is an AI-powered usability testing platform that replaces manual usability testing with parallel AI agents. It tests websites with diverse user personas (elderly, millennial, etc.) plus an adversarial agent, generating confusion heatmaps, suggested improvements, and regression testing.

## Architecture

The system has two main components:

### Backend (FastAPI + Python 3.11+)
- **FastAPI server** (`main.py`): REST API + WebSocket endpoints at http://localhost:8000
- **7-step pipeline** (`pipeline.py`):
  1. Summarize website
  2. Generate usability tasks via LLM
  3. Distribute tasks to personas
  4. **Execute tasks via Browser Use agents** (parallel browser automation)
  5. Collect feedback and confusion signals
  6. Suggest improvements via LLM
  7. Apply edits (human-in-the-loop approval)
- **Browser Use integration**: Core agent execution engine that controls browsers via cloud sessions
  - Each persona runs as a Browser Use agent with custom system prompts
  - Agents autonomously navigate websites, click elements, fill forms, etc.
  - Returns trajectory data (actions taken, observations, errors) used for confusion scoring
- **Persona system** (`personas.py`): Agent definitions with system prompts passed to Browser Use
- **Scoring engine** (`scoring.py`): Confusion heatmap and confidence scoring from agent trajectories
- **Data models** (`models.py`): Pydantic models for all pipeline data

### Chrome Extension
- **Side panel UI** (`extension/panel/`): Dashboard for triggering tests and viewing results
- **Background worker** (`extension/background.js`): Message relay to FastAPI backend
- **Content script** (`extension/content.js`): Injects fixes and tracks interactions

### Key Architectural Patterns
- **Browser Use as agent runtime**: All user personas execute as Browser Use agents (https://browser-use.com/)
  - Browser Use provides the browser automation layer (Playwright-based)
  - AgentUX provides the personas, tasks, and scoring layer on top
  - Agents run in Browser Use cloud sessions for parallel execution
- **In-memory storage**: All test runs stored in `runs_store` dict (no database)
- **WebSocket streaming**: Live progress updates at `/ws/{run_id}`
- **Async execution**: Pipeline runs in background via `asyncio.create_task`
- **Human-in-the-loop**: Edits require approval via `/api/runs/{id}/approve` before application

## Development Setup

### Backend
```bash
cd backend
uv venv && source .venv/bin/activate && uv pip install -e .
# OR: python -m venv .venv && source .venv/bin/activate && pip install -e .

# Configure API keys
cp .env.example .env
# Edit .env with:
#   BROWSER_USE_API_KEY=bu_your_key_here
#   GOOGLE_API_KEY=your_gemini_key_here

# Start server
python main.py  # Runs on http://localhost:8000
```

### Chrome Extension
1. Open Chrome and navigate to `chrome://extensions`
2. Enable "Developer mode"
3. Click "Load unpacked" and select the `extension/` directory
4. Click the extension icon to open the side panel

### API Keys Required
- **BROWSER_USE_API_KEY**: Get from https://cloud.browser-use.com/settings?tab=api-keys
- **GOOGLE_API_KEY**: Gemini API key for LLM calls (summarization, task generation)

## Key Files

| File | Purpose |
|------|---------|
| `backend/main.py` | FastAPI server with REST + WebSocket endpoints |
| `backend/pipeline.py` | 7-step usability testing pipeline orchestration |
| `backend/personas.py` | Agent persona definitions (elderly, millennial, custom) |
| `backend/scoring.py` | Confusion signal extraction and heatmap generation |
| `backend/models.py` | Pydantic data models (TestRun, PersonaResult, etc.) |
| `extension/background.js` | Chrome extension service worker |
| `extension/panel/panel.js` | Side panel UI logic |

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/test` | Start usability test (body: `{"url": "...", "personas": [], "num_tasks": 2}`) |
| `GET` | `/api/runs/{id}` | Get test run status and results |
| `POST` | `/api/runs/{id}/approve` | Approve/reject suggested edits (body: `{"edit_ids": [], "approved": true}`) |
| `POST` | `/api/runs/{id}/regression` | Trigger regression re-test |
| `GET` | `/api/personas` | List all available agent personas |
| `WS` | `/ws/{id}` | Live progress streaming |

## Pipeline State Machine

```
PENDING → SUMMARIZING → GENERATING_TASKS → EXECUTING → ANALYZING → 
SUGGESTING → VALIDATING_EDITS → AWAITING_APPROVAL → APPLYING_EDITS → 
REGRESSION_TESTING → COMPLETED (or FAILED)
```

The pipeline pauses at `AWAITING_APPROVAL` for human review before applying edits.

## Data Flow

1. **Extension → Backend**: User clicks "Test This Page" → sends URL via POST `/api/test`
2. **Backend**: Creates `TestRun` object, starts pipeline in background, returns run ID
3. **Extension ↔ Backend**: Opens WebSocket at `/ws/{run_id}` for live updates
4. **Pipeline**: Executes 7 steps, broadcasts progress via WebSocket after each step
5. **Human Approval**: Pipeline pauses at step 6, waits for POST `/api/runs/{id}/approve`
6. **Regression**: After edits applied, re-runs tasks to verify improvements

## Testing & Development

### Running the Backend
```bash
cd backend
source .venv/bin/activate
python main.py
```

### Testing the Extension
1. Load extension in Chrome
2. Navigate to any website
3. Open extension side panel
4. Click "Test This Page"
5. Monitor WebSocket stream for live updates

### Testing API Directly
```bash
# Start a test
curl -X POST http://localhost:8000/api/test \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "num_tasks": 2}'

# Get results
curl http://localhost:8000/api/runs/{run_id}

# Approve edits
curl -X POST http://localhost:8000/api/runs/{run_id}/approve \
  -H "Content-Type: application/json" \
  -d '{"approved": true}'
```

## Important Implementation Details

### Persona System & Browser Use
- **Browser Use agents**: Each persona executes as a Browser Use agent instance
  - Browser Use (https://browser-use.com/) is an LLM-powered browser automation framework
  - Agents receive persona system prompts that modify their behavior (e.g., "You are Grandma, 74 years old...")
  - Agents autonomously navigate websites using natural language instructions
  - Returns action trajectories that AgentUX analyzes for confusion signals
- Personas defined in `personas.py` with system prompts
- Currently supports: `elderly` (Grandma) and `millennial`
- Custom personas can be passed via API as dicts with `{name, emoji, color, description, system_prompt}`
- Multiple personas run in parallel via Browser Use cloud sessions

### Confusion Signals
- Extracted from agent trajectories in `scoring.py`
- Signal types: `hesitation`, `backtrack`, `retry`, `misclick`, `error`
- Heatmap aggregates signals by element selector and page URL
- Severity scored 0.0-1.0 based on signal type and frequency

### LLM Integration
- Uses Google Gemini API (`gemini-3-flash-preview` model)
- All LLM calls route through `_llm_call()` in `pipeline.py`
- JSON mode enabled for structured outputs (task generation, edit suggestions)
- Temperature: 0.7, Max tokens: 4000

### WebSocket Protocol
- Client sends "ping", server responds "pong" for keepalive
- Server broadcasts JSON updates on every pipeline state change
- Disconnected clients automatically removed from `websocket_connections`

## Debugging

### Backend Logs
- All pipeline steps log to `run.log_messages[]`
- Logs visible in API responses and WebSocket streams
- Check terminal output for FastAPI server logs

### Extension Debugging
- Open Chrome DevTools → Console for panel UI logs
- Background worker logs: `chrome://extensions` → "Inspect views: service worker"
- Content script logs: Page DevTools → Console (look for "AgentUX" prefix)

### Common Issues
- **"Run not found"**: Check if run ID is valid via GET `/api/runs`
- **WebSocket disconnects**: Backend may have restarted; reconnect with same run ID
- **Pipeline stuck**: Check `run.status` and `run.current_step` for state
- **API key errors**: Verify `.env` file has valid BROWSER_USE_API_KEY and GOOGLE_API_KEY
