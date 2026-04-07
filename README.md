# AgentUX — Agentic Usability Testing Platform

**Winner of the Wildcard Track at [DiamondHacks 2026](https://devpost.com/software/agent-ux) (UC San Diego)**

<img width="333" height="222" alt="medium" src="https://github.com/user-attachments/assets/0a5fdab3-05e2-42b5-aa1d-4efbb17f8e46" />

<img width="1511" height="940" alt="image" src="https://github.com/user-attachments/assets/3f3515d4-c911-4891-a7d5-25fe30055a1a" />


AgentUX replaces manual usability testing with parallel AI agents powered by [Browser Use](https://browser-use.com/). It runs diverse user personas simultaneously on any website through a Chrome extension, generating confusion heatmaps, actionable fix suggestions, and live browser previews — all with human-in-the-loop approval before changes are applied.

---

## Features

| Feature | Description |
|---------|-------------|
| **Parallel AI Personas** | Run multiple personas (elderly user, first-time visitor, custom) in parallel — each surfaces different usability failures |
| **Live Browser Previews** | Watch agents navigate your site in real-time via embedded Browser Use cloud sessions |
| **Confusion Heatmap** | Tracks hesitation, backtracks, retries, and misclicks to score usability, accessibility, and clarity |
| **Fix Suggestions with Validation** | LLM-generated CSS/JS fixes are visually validated with before/after screenshots via Playwright |
| **Human-in-the-Loop Approval** | Review and apply suggested fixes individually with severity ratings and rationale |
| **Persistent Fixes** | Applied fixes are saved per-domain in Chrome storage and re-injected on page reload |
| **Deploy to Code** | Copy a structured prompt with all issues to paste into Claude Code or your IDE |

## Architecture

```
Chrome Extension (Side Panel)  ←→  FastAPI Backend  ←→  Browser Use Cloud (Parallel Agents)
         ↓                              ↓                          ↓
   Setup / Progress / Results     Pipeline Engine            Persona Agents
   Live Agent Feed                Gemini LLM Calls           Browser Automation
   Fix Injection                  Scoring Engine              Confusion Tracking
```

### How It Works

1. **Setup**: User opens the extension side panel, selects personas, and generates usability tasks (LLM-powered from a page summary)
2. **Task Editing**: User can review, edit, or delete generated tasks before running
3. **Execution**: Each persona runs as a Browser Use cloud agent in parallel, autonomously navigating the site
4. **Live Monitoring**: WebSocket streams real-time logs color-coded by persona, with embedded live browser iframes
5. **Analysis**: Agent trajectories are parsed for confusion signals (hesitation, backtracks, retries, errors) and scored
6. **Suggestions**: LLM generates CSS/JS fix suggestions, validated visually with Playwright screenshots
7. **Approval & Application**: User reviews issues by severity, applies fixes individually — fixes persist across page reloads

## Quick Start

### Prerequisites
- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Chrome browser

### 1. Backend Setup

```bash
cd backend

# Create virtual environment and install dependencies
uv venv && source .venv/bin/activate && uv pip install -e .
# OR with pip:
# python -m venv .venv && source .venv/bin/activate && pip install -e .
```

### 2. Configure API Keys

```bash
cp .env.example .env
# Edit .env with your API keys:
#   BROWSER_USE_API_KEY=bu_your_key_here
#   GOOGLE_API_KEY=your_gemini_key_here
```

| Key | Source |
|-----|--------|
| `BROWSER_USE_API_KEY` | [Browser Use Cloud Settings](https://cloud.browser-use.com/settings?tab=api-keys) |
| `GOOGLE_API_KEY` | Google AI Studio (Gemini API) |

### 3. Start the Backend

```bash
python main.py
# Server starts on http://localhost:8000
```

### 4. Load the Chrome Extension

1. Navigate to `chrome://extensions`
2. Enable **Developer mode**
3. Click **Load unpacked** and select the `extension/` directory
4. Click the extension icon to open the side panel

### 5. Run a Test

1. Navigate to any website
2. Open the AgentUX side panel
3. Select personas and click **Generate Tasks**
4. Review/edit the generated tasks
5. Click **Run Evaluation** and watch agents test the site live
6. Review issues and apply fixes from the Results tab

## Project Structure

```
agentux/
├── backend/
│   ├── main.py          # FastAPI server (REST + WebSocket)
│   ├── pipeline.py      # 7-step usability testing pipeline
│   ├── personas.py      # Persona definitions with system prompts
│   ├── scoring.py       # Confusion signal extraction and scoring
│   ├── models.py        # Pydantic data models
│   ├── pyproject.toml   # Python dependencies
│   └── .env.example     # API key template
├── extension/
│   ├── manifest.json    # Chrome Manifest V3
│   ├── background.js    # Service worker (message relay)
│   ├── content.js       # Fix injection + persistent storage
│   ├── panel/
│   │   ├── panel.html   # Side panel UI
│   │   ├── panel.js     # Setup, progress, and results logic
│   │   └── panel.css    # Dark-mode design system
│   └── icons/           # Mascot SVG animations
└── README.md
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/summarize` | Summarize a page for task generation |
| `POST` | `/api/generate-tasks` | Generate usability tasks from a summary |
| `POST` | `/api/test` | Start full pipeline (summarize + generate + execute) |
| `POST` | `/api/execute` | Start pipeline with pre-defined tasks and personas |
| `GET` | `/api/runs` | List all test runs |
| `GET` | `/api/runs/{id}` | Get run status and results |
| `POST` | `/api/runs/{id}/approve` | Approve/reject suggested edits |
| `POST` | `/api/runs/{id}/regression` | Trigger regression re-test |
| `GET` | `/api/personas` | List available personas |
| `WS` | `/ws/{id}` | Live progress streaming |

## Built With

- **[Browser Use](https://browser-use.com/)** — AI browser automation (cloud agent sessions)
- **[Google Gemini](https://ai.google.dev/)** — LLM for summarization, task generation, and fix suggestions
- **[FastAPI](https://fastapi.tiangolo.com/)** — Python async web framework + WebSocket
- **[Playwright](https://playwright.dev/)** — Visual edit validation (before/after screenshots)
- **Chrome Manifest V3** — Extension side panel with vanilla JS

## Credits

Built by **Jaden Seangmany**, **Manjusri Gobiraj**, **Alice Lan**, and **Khang Nguyen**.
