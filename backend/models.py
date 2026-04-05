"""
Pydantic models for AgentUX pipeline data.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────────────

class RunStatus(str, Enum):
    PENDING = "pending"
    SUMMARIZING = "summarizing"
    GENERATING_TASKS = "generating_tasks"
    EXECUTING = "executing"
    ANALYZING = "analyzing"
    SUGGESTING = "suggesting"
    AWAITING_APPROVAL = "awaiting_approval"
    APPLYING_EDITS = "applying_edits"
    REGRESSION_TESTING = "regression_testing"
    COMPLETED = "completed"
    FAILED = "failed"


class PersonaType(str, Enum):
    ELDERLY = "elderly"
    FIRST_TIME = "first_time"
    ADHD = "adhd"
    NON_NATIVE = "non_native"
    MOBILE = "mobile"
    POWER_USER = "power_user"
    VISUALLY_IMPAIRED = "visually_impaired"
    LOW_TECH = "low_tech"
    BUSY_PROFESSIONAL = "busy_professional"
    TEEN = "teen"
    ADVERSARIAL = "adversarial"


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class ConfusionLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ── Core Models ────────────────────────────────────────────────────────────────

class ConfusionSignal(BaseModel):
    """A single confusion event detected during agent task execution."""
    element_selector: str = ""
    element_description: str = ""
    signal_type: str = ""  # hesitation, backtrack, retry, misclick, error
    description: str = ""
    severity: float = Field(default=0.5, ge=0.0, le=1.0)
    timestamp: datetime = Field(default_factory=datetime.now)
    page_url: str = ""


class UsabilityTask(BaseModel):
    """A single usability testing task for agents to perform."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    title: str
    description: str
    expected_outcome: str = ""
    priority: str = "medium"  # low, medium, high


class PersonaResult(BaseModel):
    """Results from a single persona's usability testing session."""
    persona_type: PersonaType
    persona_name: str = ""
    tasks_completed: int = 0
    tasks_failed: int = 0
    tasks_total: int = 0
    overall_score: float = Field(default=0.0, ge=0.0, le=100.0)
    confusion_signals: list[ConfusionSignal] = []
    feedback: str = ""
    suggestions: list[str] = []
    status: TaskStatus = TaskStatus.PENDING
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    task_results: list[dict] = []  # per-task results


class SuggestedEdit(BaseModel):
    """A suggested code/content edit to improve the website."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    file_or_element: str = ""
    description: str
    rationale: str = ""
    before_snippet: str = ""
    after_snippet: str = ""
    severity: str = "medium"  # low, medium, high, critical
    personas_affected: list[str] = []
    approved: Optional[bool] = None
    fix_js: str = ""   # JavaScript to inject via content script
    fix_css: str = ""  # CSS to inject via content script


class HeatmapEntry(BaseModel):
    """Confusion heatmap entry for a specific page element."""
    element_selector: str
    element_description: str = ""
    confusion_score: float = Field(default=0.0, ge=0.0, le=1.0)
    signal_count: int = 0
    signal_types: list[str] = []
    personas_affected: list[str] = []
    page_url: str = ""


class RegressionResult(BaseModel):
    """Before/after comparison for regression testing."""
    task_id: str
    task_title: str
    before_score: float = 0.0
    after_score: float = 0.0
    before_confusion_count: int = 0
    after_confusion_count: int = 0
    improved: bool = False
    notes: str = ""


class TestRun(BaseModel):
    """Top-level model for a complete usability test run."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    url: str
    status: RunStatus = RunStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    # Pipeline outputs
    site_summary: str = ""
    tasks: list[UsabilityTask] = []
    persona_results: list[PersonaResult] = []
    heatmap: list[HeatmapEntry] = []
    suggested_edits: list[SuggestedEdit] = []
    regression_results: list[RegressionResult] = []

    # Scores
    overall_usability_score: float = 0.0
    accessibility_score: float = 0.0
    clarity_score: float = 0.0

    # Progress tracking
    progress: float = Field(default=0.0, ge=0.0, le=100.0)
    current_step: str = ""
    log_messages: list[str] = []
