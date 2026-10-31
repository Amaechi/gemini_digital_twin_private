# Vendored reference code: Plant Debugger

- Upstream: https://github.com/marcosanyo/PlantDebugger
- Commit: d69507aa48aec773a5cf72761e5ff60c491eefc1 (2026-08-30)
- Licence: Apache-2.0 (see `LICENSE`), "Copyright 2026 Plant Debugger contributors"

These files are **unmodified reference copies**. They are not imported by our code. When code is adapted into `src/gemini_agent/`, the adapted file MUST start with:

```
# Adapted from marcosanyo/PlantDebugger (Apache-2.0), Copyright 2026 Plant Debugger contributors,
# file <original path> @ d69507a. Modified by <team>, <date>: <what changed>.
```

Mapping (see docs/hackathon/GEOAGENT_DESIGN_SPEC.md §11):

| Reference file | Adapt into |
|---|---|
| agent/decision_engine.py | src/gemini_agent/agents/engine.py |
| agent/schemas.py | src/gemini_agent/schemas/decision.py |
| agent/prompt.py (style only) | src/gemini_agent/prompts/ |
| runner/incident_runner.py | src/gemini_agent/runner/design_runner.py |
| safety/action_guard.py | src/gemini_agent/verify/design_guard.py |
| tools/verify.py | src/gemini_agent/verify/design_verifier.py |
| tools/runbook.py | src/gemini_agent/tools/knowledge.py |
| persistence/base.py, sqlite_store.py | src/gemini_agent/memory/event_store.py |
| field_ops/site_knowledge.py, field_ops/models.py (JSON parts) | src/gemini_agent/memory/project_knowledge.py |
| logging_utils.py | src/gemini_agent/logging/ |
| scripts/run_eval.py, scripts/validate_submission.py | scripts/ |
| agent/adk_agent.py, field_ops/adk_orchestration.py | ideas only (repair turn, plan validation, pause/resume) — not copied when using Pydantic AI |

Exclude `third_party/` from linting and test collection.
