"""
Confusion & Confidence Scoring Engine for AgentUX.

Analyzes agent action logs to detect hesitation, backtracks, retries,
and errors — producing per-element confusion scores and heatmap data.
"""

from __future__ import annotations

import re
from collections import defaultdict
from models import (
    ConfusionSignal,
    HeatmapEntry,
    PersonaResult,
    ConfusionLevel,
)


# ── Signal detection patterns ─────────────────────────────────────────────────

CONFUSION_PATTERNS = {
    "hesitation": {
        "keywords": ["unsure", "not sure", "confused", "don't understand",
                      "what does this", "where is", "can't find", "looking for",
                      "unclear", "ambiguous", "hard to read", "confusing"],
        "severity": 0.5,
    },
    "backtrack": {
        "keywords": ["go back", "going back", "return to", "back to previous",
                      "wrong page", "didn't mean to", "let me try again",
                      "back button", "navigate back"],
        "severity": 0.7,
    },
    "retry": {
        "keywords": ["try again", "retry", "attempt again", "one more time",
                      "let me re-", "failed, trying", "didn't work"],
        "severity": 0.8,
    },
    "error_encounter": {
        "keywords": ["error", "404", "not found", "broken", "doesn't work",
                      "crashed", "unresponsive", "loading forever", "timed out"],
        "severity": 0.9,
    },
    "frustration": {
        "keywords": ["frustrating", "annoying", "terrible", "awful", "hate",
                      "give up", "impossible", "why is this", "so hard",
                      "ridiculous", "unacceptable"],
        "severity": 0.85,
    },
    "misclick": {
        "keywords": ["wrong button", "wrong link", "didn't mean to click",
                      "accidental", "misclicked", "clicked wrong"],
        "severity": 0.6,
    },
}


def extract_confusion_signals(
    agent_output: str,
    persona_type: str,
    page_url: str = "",
) -> list[ConfusionSignal]:
    """
    Parse agent output text and extract confusion signals based on keyword matching.
    """
    signals = []
    lines = agent_output.split("\n")

    for line in lines:
        line_lower = line.lower()
        for signal_type, config in CONFUSION_PATTERNS.items():
            for keyword in config["keywords"]:
                if keyword in line_lower:
                    # Try to extract element reference from the line
                    element = _extract_element_ref(line)
                    signals.append(ConfusionSignal(
                        element_selector=element.get("selector", ""),
                        element_description=element.get("description", line[:100]),
                        signal_type=signal_type,
                        description=line.strip(),
                        severity=config["severity"],
                        page_url=page_url,
                    ))
                    break  # Only match first pattern per line

    return signals


def _extract_element_ref(text: str) -> dict:
    """Try to extract an element reference from agent text."""
    # Look for CSS-selector-like patterns
    selector_match = re.search(r'[#\.][a-zA-Z][\w\-]*', text)
    # Look for quoted element descriptions
    desc_match = re.search(r'"([^"]+)"', text)
    # Look for element type references
    element_match = re.search(
        r'\b(button|link|input|form|menu|dropdown|modal|popup|nav|header|footer|'
        r'sidebar|image|icon|tab|checkbox|radio|slider|toggle)\b',
        text, re.IGNORECASE
    )

    return {
        "selector": selector_match.group(0) if selector_match else "",
        "description": (
            desc_match.group(1) if desc_match
            else (element_match.group(0) if element_match else text[:80])
        ),
    }


def build_heatmap(
    persona_results: list[PersonaResult],
) -> list[HeatmapEntry]:
    """
    Aggregate confusion signals across all personas into a heatmap
    showing where on the site agents struggled.
    """
    element_data: dict[str, dict] = defaultdict(lambda: {
        "description": "",
        "total_severity": 0.0,
        "signal_count": 0,
        "signal_types": set(),
        "personas": set(),
        "page_url": "",
    })

    for result in persona_results:
        for signal in result.confusion_signals:
            key = signal.element_description or signal.element_selector or "unknown"
            data = element_data[key]
            data["description"] = signal.element_description or key
            data["total_severity"] += signal.severity
            data["signal_count"] += 1
            data["signal_types"].add(signal.signal_type)
            data["personas"].add(result.persona_type.value)
            if signal.page_url:
                data["page_url"] = signal.page_url

    # Normalize scores and create heatmap entries
    heatmap = []
    if not element_data:
        return heatmap

    max_severity = max(d["total_severity"] for d in element_data.values()) or 1.0

    for selector, data in element_data.items():
        normalized_score = min(data["total_severity"] / max_severity, 1.0)
        heatmap.append(HeatmapEntry(
            element_selector=selector,
            element_description=data["description"],
            confusion_score=round(normalized_score, 3),
            signal_count=data["signal_count"],
            signal_types=list(data["signal_types"]),
            personas_affected=list(data["personas"]),
            page_url=data["page_url"],
        ))

    # Sort by confusion score descending
    heatmap.sort(key=lambda h: h.confusion_score, reverse=True)
    return heatmap


def compute_persona_score(result: PersonaResult) -> float:
    """
    Compute an overall usability score for a persona's experience.
    100 = perfect, 0 = terrible.
    """
    if result.tasks_total == 0:
        return 50.0

    # Task completion ratio (0-1)
    completion_ratio = result.tasks_completed / result.tasks_total

    # Confusion penalty (0-1, lower is worse)
    confusion_count = len(result.confusion_signals)
    confusion_penalty = max(0, 1.0 - (confusion_count * 0.1))

    # Average severity of confusions
    if result.confusion_signals:
        avg_severity = sum(s.severity for s in result.confusion_signals) / len(result.confusion_signals)
        severity_penalty = 1.0 - avg_severity
    else:
        severity_penalty = 1.0

    # Weighted combination
    score = (
        completion_ratio * 50 +
        confusion_penalty * 25 +
        severity_penalty * 25
    )

    return round(min(max(score, 0), 100), 1)


def compute_overall_scores(
    persona_results: list[PersonaResult],
) -> dict[str, float]:
    """Compute aggregate scores across all personas."""
    if not persona_results:
        return {"usability": 0, "accessibility": 0, "clarity": 0}

    # Filter out adversarial agent for overall scoring
    standard_results = [r for r in persona_results if r.persona_type.value != "adversarial"]
    if not standard_results:
        standard_results = persona_results

    scores = [r.overall_score for r in standard_results]
    avg_score = sum(scores) / len(scores)

    # Accessibility score: weight visually_impaired and low_tech personas more
    accessibility_personas = [r for r in standard_results
                             if r.persona_type in ("visually_impaired", "low_tech", "elderly")]
    access_score = (
        sum(r.overall_score for r in accessibility_personas) / len(accessibility_personas)
        if accessibility_personas else avg_score
    )

    # Clarity score: weight first_time, non_native, low_tech personas more
    clarity_personas = [r for r in standard_results
                       if r.persona_type in ("first_time", "non_native", "low_tech")]
    clarity_score = (
        sum(r.overall_score for r in clarity_personas) / len(clarity_personas)
        if clarity_personas else avg_score
    )

    return {
        "usability": round(avg_score, 1),
        "accessibility": round(access_score, 1),
        "clarity": round(clarity_score, 1),
    }


def get_confusion_level(score: float) -> ConfusionLevel:
    """Map a confusion score to a severity level."""
    if score < 0.25:
        return ConfusionLevel.LOW
    elif score < 0.5:
        return ConfusionLevel.MEDIUM
    elif score < 0.75:
        return ConfusionLevel.HIGH
    else:
        return ConfusionLevel.CRITICAL
