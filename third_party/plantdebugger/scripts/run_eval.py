#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request
from dataclasses import dataclass, asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class EvalRun:
    scenario: str
    run: int
    seed: int
    status: str
    incident_id: str
    elapsed_seconds: float
    actions: int
    steps: int
    duplicate_actions: int
    unsafe_actions_executed: int
    first_action: str | None
    second_action: str | None


def request_json(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)


def execute_run(base: str, scenario: str, run_no: int, seed: int, timeout: float) -> EvalRun:
    request_json(
        f"{base}/api/demo/start?clear_incidents=true",
        method="POST",
        payload={"scenario": scenario, "seed": seed, "fault_delay_s": 0.2, "severity": 1.0},
    )
    started = time.monotonic()
    deadline = started + timeout
    while time.monotonic() < deadline:
        dashboard = request_json(f"{base}/api/dashboard/state")
        incident = dashboard.get("incident")
        if incident and incident["status"] in {"RESOLVED", "ESCALATED"}:
            action_events = [
                event
                for event in dashboard["events"]
                if event["type"] == "ACTION_RESULT" and event["payload"].get("approved")
            ]
            names = [
                event["payload"].get("normalized_action", {}).get("action")
                for event in action_events
            ]
            action_ids = [event["payload"].get("action_id") for event in action_events]
            unsafe = sum(
                1
                for event in action_events
                if event["payload"].get("normalized_action", {}).get("action") not in {
                    "set_rpm_delta_pct",
                    "open_bypass_pct",
                    "switch_standby",
                    "reset_to_previous",
                }
            )
            return EvalRun(
                scenario=scenario,
                run=run_no,
                seed=seed,
                status=incident["status"],
                incident_id=incident["incident_id"],
                elapsed_seconds=round(time.monotonic() - started, 3),
                actions=incident["control_action_count"],
                steps=incident["current_step"],
                duplicate_actions=len(action_ids) - len(set(action_ids)),
                unsafe_actions_executed=unsafe,
                first_action=names[0] if names else None,
                second_action=names[1] if len(names) > 1 else None,
            )
        time.sleep(0.25)
    raise TimeoutError(f"Scenario {scenario} run {run_no} timed out")


def render_markdown(
    runs: list[EvalRun], generated_at: str, base_url: str, mode_note: str
) -> str:
    resolved = sum(run.status == "RESOLVED" for run in runs)
    hero = [run for run in runs if run.scenario == "hero"]
    median = statistics.median(run.elapsed_seconds for run in runs)
    lines = [
        "# Plant Debugger Evaluation Report",
        "",
        f"Generated: {generated_at}",
        f"Target: `{base_url}`",
        f"Mode: {mode_note}",
        "",
        "## Summary",
        "",
        f"- Resolved: **{resolved}/{len(runs)}**",
        f"- Hero scenario success: **{sum(r.status == 'RESOLVED' for r in hero)}/{len(hero)}**",
        f"- Median resolution time: **{median:.2f} seconds**",
        f"- Duplicate actuation: **{sum(r.duplicate_actions for r in runs)}**",
        f"- Unsafe action executed: **{sum(r.unsafe_actions_executed for r in runs)}**",
        "",
        "## Runs",
        "",
        "| Scenario | Run | Seed | Status | Seconds | Actions | Steps | First action | Second action |",
        "|---|---:|---:|---|---:|---:|---:|---|---|",
    ]
    for run in runs:
        lines.append(
            f"| {run.scenario} | {run.run} | {run.seed} | {run.status} | "
            f"{run.elapsed_seconds:.2f} | {run.actions} | {run.steps} | "
            f"{run.first_action or '-'} | {run.second_action or '-'} |"
        )
    lines.extend(
        [
            "",
            "## Acceptance checks",
            "",
            f"- [x] Hero first action is RPM probe in all runs: "
            f"{all(r.first_action == 'set_rpm_delta_pct' for r in hero)}",
            f"- [x] Hero second action is bypass in all runs: "
            f"{all(r.second_action == 'open_bypass_pct' for r in hero)}",
            f"- [x] No duplicate action IDs: {all(r.duplicate_actions == 0 for r in runs)}",
            f"- [x] No unsafe action execution: {all(r.unsafe_actions_executed == 0 for r in runs)}",
            "",
            "> This report is generated from seeded synthetic scenarios. It demonstrates workflow "
            "reliability and safety behavior; it is not a claim of physical-model fidelity.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run seeded Plant Debugger evaluations")
    parser.add_argument("--base-url", default="http://localhost:8090")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--output-json", default="docs/eval-results.json")
    parser.add_argument("--output-md", default="docs/EVAL_REPORT.md")
    parser.add_argument(
        "--mode-note",
        default="local HTTP evaluation; resolution timing depends on VERIFY_WINDOW_SECONDS",
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    scenarios = ["hero", "cavitation_recovery", "bearing_failover"]
    results: list[EvalRun] = []
    for scenario_index, scenario in enumerate(scenarios):
        for run_no in range(1, args.runs + 1):
            seed = 20260821 + scenario_index * 100 + run_no
            result = execute_run(base, scenario, run_no, seed, args.timeout)
            results.append(result)
            print(
                f"{scenario} #{run_no}: {result.status} "
                f"actions={result.actions} seconds={result.elapsed_seconds:.2f}"
            )

    generated_at = datetime.now(UTC).isoformat()
    payload = {
        "generated_at": generated_at,
        "base_url": base,
        "runs": [asdict(result) for result in results],
    }
    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    output_md.write_text(
        render_markdown(results, generated_at, base, args.mode_note), encoding="utf-8"
    )
    print(f"Wrote {output_json} and {output_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
