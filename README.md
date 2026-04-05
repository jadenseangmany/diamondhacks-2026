# 🧪 AgentUX — AI-Powered Usability Testing Platform

**Replace manual usability testing with 11 parallel AI agents using [Browser Use](https://browser-use.com/).**

AgentUX runs 10 diverse user personas + 1 adversarial agent simultaneously on any website, generating a full usability report with confusion heatmaps, suggested improvements, and before/after regression testing.

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| **👥 10 Parallel Personas** | Elderly, first-time visitor, ADHD, non-native English, mobile-only, power user, visually impaired, low-tech, busy professional, and teen — each surfaces different failures |
| **🔥 Adversarial Agent Mode** | One agent actively tries to break the site — finds dark patterns, dead ends, confusing flows |
| **📊 Confusion Heatmap** | Tracks hesitation, backtracks, and retries to build a visual map of where agents got stuck |
| **✅ Human-in-the-Loop Approval** | Review suggested edits with a diff viewer before they're applied |
| **🔄 Regression Testing** | Automatically re-runs tasks after edits to verify improvements |

## 🏗️ Architecture

```
Frontend (HTML/CSS/JS)  ←→  FastAPI Backend  ←→  Browser Use Agents (Parallel)
         ↓                        ↓                       ↓
   Dashboard UI            Pipeline Engine          11 Agent Personas
   Heatmap View            Scoring Engine           Confusion Tracking
   Diff Viewer             WebSocket Stream         Task Execution
```

## 🚀 Quick Start

### Prerequisites
- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

### 1. Clone & Setup

```bash
cd diamondhacks-2026/backend

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
#   OPENAI_API_KEY=sk-your_key_here
```

### 3. Start the Backend

```bash
cd backend
python main.py
# Server starts on http://localhost:8000
```

### 4. Open the Frontend

```bash
cd frontend
python -m http.server 3000
# Open http://localhost:3000 in your browser
```

### 5. Run a Test

1. Enter any URL (e.g., `https://example.com`)
2. Watch 11 agents test the site simultaneously
3. Review the confusion heatmap
4. Approve or reject suggested edits
5. See regression test results

## 📁 Project Structure

```
diamondhacks-2026/
├── backend/
│   ├── main.py          # FastAPI server (REST + WebSocket)
│   ├── pipeline.py      # 7-step usability testing pipeline
│   ├── personas.py      # 11 persona definitions with system prompts
│   ├── scoring.py       # Confusion & confidence scoring engine
│   ├── models.py        # Pydantic data models
│   ├── pyproject.toml   # Python dependencies
│   └── .env.example     # API key template
├── frontend/
│   ├── index.html       # Dashboard UI
│   ├── style.css        # Dark-mode design system
│   └── app.js           # Application logic
└── README.md
```

## 🔧 API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/test` | Start a usability test (accepts `{ "url": "..." }`) |
| `GET` | `/api/runs/{id}` | Get run status and results |
| `POST` | `/api/runs/{id}/approve` | Approve/reject suggested edits |
| `POST` | `/api/runs/{id}/regression` | Trigger regression re-test |
| `WS` | `/ws/{id}` | Live progress streaming |

## 🛠️ Built With

- **[Browser Use](https://browser-use.com/)** — AI browser automation
- **FastAPI** — Python web framework
- **OpenAI** — LLM for summarization and task generation
- **Vanilla HTML/CSS/JS** — Premium dark-mode dashboard