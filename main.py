"""
Entry point — runs the full usability testing pipeline on a given URL,
pretty-prints results at each step, and saves an HTML report.

Usage:
    python main.py <url>
    python main.py https://apple.com
"""

import sys
from pipeline import run_pipeline


def _section(title: str) -> None:
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python main.py <url>")
        sys.exit(1)
    url = sys.argv[1]
    results = run_pipeline(url)

    # ── Step 1: Site Summary ────────────────────────────────────────
    _section("STEP 1 — Site Summary")
    summary = results["summary"]
    print(f"Purpose  : {summary.get('purpose', '')}")
    print(f"Audience : {summary.get('target_audience', '')}")
    for flow in summary.get("key_flows", []):
        print(f"  • {flow}")

    # ── Step 2: Tasks ───────────────────────────────────────────────
    _section("STEP 2 — Usability Tasks")
    for i, task in enumerate(results["tasks"], 1):
        print(f"  {i}. {task}")

    # ── Step 3: Multi-Persona Results ───────────────────────────────
    _section("STEP 3 — Multi-Persona Results")
    persona_traces = results.get("persona_traces", {})
    for persona, traces in persona_traces.items():
        passed = sum(1 for t in traces if t and t.get("success"))
        total  = len(traces)
        avg_t  = sum(t.get("total_time_seconds", 0) for t in traces if t) / max(total, 1)
        print(f"  {persona:20s}: {passed}/{total} passed  avg {avg_t:.1f}s")

    # ── Step 4: Friction Analysis ────────────────────────────────────
    _section("STEP 4 — Friction Analysis")
    analysis = results.get("multi_analysis", {})
    print(f"\n{analysis.get('summary', '(no summary)')}\n")
    smap = analysis.get("severity_map", {})
    for level in ("critical", "high", "medium", "low"):
        count = smap.get(level, 0)
        if count:
            print(f"  {level.upper():8s}: {'█' * count} ({count})")
    friction = analysis.get("friction_points", [])
    for fp in friction[:5]:
        affected = fp.get("affected_personas", [])
        tag = f"  [{', '.join(affected)}]" if affected else ""
        print(f"  [{fp.get('severity','?').upper():8s}] {fp.get('element','?')} — {fp.get('description','')}{tag}")

    # ── Step 5: Visual Fixes ─────────────────────────────────────────
    _section("STEP 5 — Visual Fixes")
    fixes = results.get("visual_fixes", [])
    if not fixes:
        print("  No fixes generated.")
    else:
        print(f"  {len(fixes)} fix(es)\n")
        for fix in fixes:
            print(f"  [{fix.get('severity','?').upper():8s}] {fix.get('element','?')}")
            print(f"  {fix.get('description','')}")
            print()

    # ── Done ─────────────────────────────────────────────────────────
    _section("PIPELINE COMPLETE")
    print(
        f"  Tasks: {len(results['tasks'])}  |  "
        f"Friction points: {len(friction)}  |  "
        f"Fixes: {len(fixes)}"
    )
    print()
    print("  Results broadcast to Chrome extension (ws://localhost:7655).")
    print("  Open the extension side panel to view the full report.")
    print()


if __name__ == "__main__":
    main()
