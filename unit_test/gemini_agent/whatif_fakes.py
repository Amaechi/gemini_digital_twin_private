"""Fake what-if for runner / dialogue tests (offline): real levers and guard, fabricated KPIs.

``fake_whatif`` keeps the real lever parsing, bounds and DesignGuard (``apply_levers`` on the
stored run) and fabricates the KPIs, so tests of the review loop and of the voice dialogue stay on
the stub toolbox. Shared by ``runner/test_review.py`` and ``ui/test_dialogue.py``.
"""

from __future__ import annotations

from pathlib import Path

from gemini_agent.schemas.decision import DesignVerdict
from gemini_agent.schemas.results import KPISet
from gemini_agent.tools import whatif as whatif_tool
from gemini_agent.tools.whatif import (
    ToolEvidence,
    WhatIfOutcome,
    WhatIfResult,
    apply_levers,
    build_record,
    build_table,
    load_run,
    next_index,
    parse_levers,
)


def fake_whatif(calls: list, *, lcoh_after: float = 40.0, verdict=DesignVerdict.UNVERIFIED):
    """A what-if that parses, bounds and guards the lever for real and fabricates the KPIs."""

    def run(run_dir, levers, budget="quick", *, config_dir=None):
        parsed = parse_levers(levers)
        calls.append(dict(parsed))
        snapshot = load_run(run_dir)
        index = next_index(run_dir)
        design = apply_levers(snapshot, parsed, index=index, config_dir=config_dir)
        out_dir = Path(run_dir) / whatif_tool.WHATIF_DIR / str(index)
        out_dir.mkdir(parents=True, exist_ok=False)
        base = snapshot.selected

        def outcome(label, candidate, lcoh, prefix):
            return WhatIfOutcome(
                label=label,
                candidate=candidate,
                status="ok",
                guard_approved=True,
                guard_reason="ok",
                kpis=KPISet(
                    candidate_id=candidate.id,
                    lcoh_per_mwh=lcoh,
                    lcoc_per_mwh=26.0,
                    unmet_heating_mwh=0.0,
                    unmet_cooling_mwh=0.0,
                    unmet_heating_days=0,
                    unmet_cooling_days=0,
                ),
                year_mode="fake",
                verdict=verdict,
                verifier_summary=f"fake verifier: {verdict.value}",
                evidence=[
                    ToolEvidence(
                        id=f"{prefix}-01",
                        tool="simulate_annual",
                        candidate_id=candidate.id,
                        ok=True,
                        data_keys=["lcoh_per_mwh", "lcoc_per_mwh"],
                    )
                ],
            )

        before = outcome("before", base, 45.0, f"TR-W{index}B")
        after = outcome("after", design.candidate, lcoh_after, f"TR-W{index}A")
        record = build_record(index, snapshot.run_id, parsed, before, after)
        result = WhatIfResult(
            run_id=snapshot.run_id,
            index=index,
            budget=budget,
            levers=parsed,
            applied=design.applied,
            scenario=design.scenario,
            before=before,
            after=after,
            table=build_table(before, after),
            record=record,
            outputs={"json": f"{whatif_tool.WHATIF_DIR}/{index}/whatif.json"},
            summary=f"FAKE what-if {index} {whatif_tool.format_levers(parsed)}",
        )
        (out_dir / "whatif.json").write_text(result.model_dump_json(indent=1), "utf-8")
        return result

    return run


__all__ = ["fake_whatif"]
