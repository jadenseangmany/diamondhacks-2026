"""
Entry point — runs the full usability testing pipeline on a given URL,
pretty-prints results at each step, and saves an HTML report.

Usage:
    python main.py <url>
    python main.py https://apple.com
"""

import sys
from pipeline import generate_html_report, run_pipeline


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
    print(f"Purpose       : {summary.get('purpose', '')}")
    print(f"Audience      : {summary.get('target_audience', '')}")
    print("Key flows:")
    for flow in summary.get("key_flows", []):
        print(f"  • {flow}")

    # ── Step 2: Generated Tasks ─────────────────────────────────────
    _section("STEP 2 — Usability Tasks")
    for i, task in enumerate(results["tasks"], 1):
        print(f"  {i}. {task}")

    # ── Step 3: Execution Traces (default persona) ──────────────────
    _section("STEP 3 — Browser Execution Traces")
    for trace in results["traces"]:
        status = "✓ PASS" if trace["success"] else "✗ FAIL"
        print(
            f"  {status}  [{trace['total_time_seconds']:.1f}s]  "
            f"{trace['task'][:60]}"
        )
        if trace.get("failure_reason"):
            print(f"         ↳ {trace['failure_reason']}")
        for cp in trace.get("confusion_points", []):
            print(f"         ⚠  {cp}")

    # ── Step 3b: Multi-Persona Traces ───────────────────────────────
    _section("STEP 3b — Multi-Persona Traces")
    persona_traces = results.get("persona_traces", {})
    for persona, traces in persona_traces.items():
        passed = sum(1 for t in traces if t and t.get("success"))
        total = len(traces)
        avg_time = (
            sum(t.get("total_time_seconds", 0) for t in traces if t) / max(total, 1)
        )
        print(f"  {persona:25s}: {passed}/{total} passed  avg {avg_time:.1f}s")

    # ── Step 4: Friction Analysis ───────────────────────────────────
    _section("STEP 4 — Friction / Confusion Analysis")
    # Prefer multi-persona analysis for richer output
    analysis = results.get("multi_analysis") or results["analysis"]
    print(f"\nSummary: {analysis.get('summary', '')}\n")

    smap = analysis.get("severity_map", {})
    if smap:
        print("Severity breakdown:")
        for level in ("critical", "high", "medium", "low"):
            count = smap.get(level, 0)
            if count:
                print(f"  {level.upper():8s}: {'█' * count} ({count})")

    friction = analysis.get("friction_points", [])
    if friction:
        print(f"\nTop friction points ({len(friction)} found):")
        for fp in friction[:5]:
            affected = fp.get("affected_personas", [])
            personas_str = f"  [{', '.join(affected)}]" if affected else ""
            print(
                f"  [{fp.get('severity', '?').upper():8s}] "
                f"{fp.get('element', '?')} — {fp.get('description', '')}"
                f"{personas_str}"
            )

    # ── Step 5: Fix Recommendations ─────────────────────────────────
    _section("STEP 5 — Recommended Fixes")
    fixes = results["fixes"]

    if not fixes:
        print("  No fixes generated.")
    else:
        print(f"  {len(fixes)} fix(es) — sorted highest priority first\n")
        for fix in fixes:
            sev = fix.get("severity", "?").upper()
            pri = fix.get("priority", "?")
            element = fix.get("element", "?")
            print(f"  Priority {pri}  [{sev}]  {element}")
            print(f"  Problem : {fix.get('problem', '')}")
            print(f"  Fix     : {fix.get('fix', '')}")
            code = fix.get("code", "")
            if code:
                indented = "\n".join("    " + line for line in code.splitlines())
                print(f"  Code    :\n{indented}")
            print()

    # ── Pipeline summary ─────────────────────────────────────────────
    _section("PIPELINE COMPLETE")
    fail_count = sum(1 for t in results["traces"] if not t["success"])
    print(
        f"  Tasks: {len(results['tasks'])}  |  "
        f"Failures: {fail_count}  |  "
        f"Friction points: {len(friction)}  |  "
        f"Fixes: {len(fixes)}"
    )

    # ── HTML Report ──────────────────────────────────────────────────
    _section("STEP 6 — HTML Report")
    report_path = generate_html_report(results, "report.html")
    print(f"  Report written and opened: {report_path}")
    print()


if __name__ == "__main__":
    main()
