"""Web "Design Commander" view of a run (spec §4.2 MAY; FINAL_SPRINT §G).

``gemini_agent.ui.run_view`` reads a run directory (``events.sqlite``, ``run_summary.json``,
``decision_records/``, ``tool_results/``, ``figures/``, ``llm_calls.jsonl``) into JSON-ready
dictionaries; ``gemini_agent.ui.server`` serves them and the static page from the standard
library's ``http.server``. Pure presentation: no new dependency, nothing is written into the
runs directory except through the runner's own ``resume`` (answer / approve).
"""
