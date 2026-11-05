# GeoAgent engineering rules (SPE GeoHackathon 2026, on the GEMINI repo)

## What we are building
An agentic AI application that uses the GEMINI geothermal digital twin to select, size and validate a deep-geothermal district **heating (100 °C) and cooling (20 °C)** system, compares alternatives, explains every decision, and logs prompts/answers/tokens. Deadline ≈ 4–5 Nov 2026.

## Golden rules
1. **The LLM never computes numbers.** All physics, sizing, costs, KPIs come from deterministic, unit-tested Python. Report numbers come from tool outputs.
2. **No invented coefficients.** Use the reference card; if a value is missing use the card's `[A]` default and record it in the assumptions register (`config/assumptions.yaml`).
3. **Only `DesignVerifier` can mark a design ACCEPTED.**
4. **Hard limits** (never violate silently): 1–3 doublets; 50–300 m³/h per active doublet; brine cooling ≤ 40 K; pump efficiency 70 %; Q_inj = Q_prod; heating supply 100 °C; cooling supply 20 °C.
5. **Units**: SI internally; field names carry unit suffixes (`_m3h`, `_m3s`, `_bar`, `_pa`, `_degc`, `_k`, `_w`, `_mw`, `_mwh`). GEMINI mixes units (surface m³/s & bar, wells m³/h, nodal Pa/K) — convert at boundaries only.
6. **Do not change GEMINI default behaviour** unless the WP says so; new behaviour behind parameters with defaults that keep existing tests green.

## Repository map
- `src/gemini_model/` GEMINI component models (`StaticModel`: `update_parameters`, `calculate_output(u, x)`, `get_output`; outputs `power_th` W, `power_el` W, `emission` kg/s).
- `src/gemini_simulator/` registry (`COMPONENT_REGISTRY`), `project_loader.build_project`, `solver.run`, `well_components.py`, `nodal_solver.py`, `kpi.py`. JSON keys: `components`, **`boundary_inputs`** (plural), `connections`; connections execute **in declared order**; producer flow is an OUTPUT; wellhead temperature is a boundary INPUT.
- `src/gemini_agent/` OUR application (agents, runner, tools, verify, memory, logging, schemas, prompts, cli).
- `unit_test/` mirrors `src/` one-to-one (GEMINI convention). New tests go in the mirrored path.
- `simulator_examples/` GEMINI examples (`run_doublet_plant_example.py`).
- `config/` YAML (design basis, climate, limits, costs, assumptions, llm). `data/demand/` CSVs. `runs/` outputs (git-ignored except the final submitted run).
- Ignore: `src/gemini_framework`, `src/gemini_interface`, `src/gemini_application`, `gemini-project/`, Docker, `third_party/` (reference only).

## Environment and commands
- Python 3.12 virtual environment: `uv venv -p 3.12 .venv && uv pip install -r requirements-hackathon.txt`.
- Run tests (quiet, always): `PYTHONPATH=src python -m pytest -q unit_test/gemini_model unit_test/gemini_simulator unit_test/gemini_agent -x`
- Single test file: `PYTHONPATH=src python -m pytest -q <path> -x`
- Lint new code: `ruff check src/gemini_agent unit_test/gemini_agent` (and new model folders). GEMINI's upstream `Lint` workflow also runs **black + isort + flake8** on every push — run `black <new files> && isort <new files>` before committing so that check stays green.
- GEMINI example: `PYTHONPATH=src python simulator_examples/run_doublet_plant_example.py`
- CLI (once built): `PYTHONPATH=src python -m gemini_agent.cli run --engine policy`
- Do NOT install GEMINI's full Poetry stack (Flask, Celery, Chroma…). Only numpy/scipy/pandas + our deps.

## LLMs in this repo
- Production engine: Pydantic AI agents on a local **Ollama** open-weight model.
- In tests and CI: **never call a real LLM or external API.** Use `--engine policy` (deterministic) or Pydantic AI `TestModel` / `FunctionModel`. Tests must pass offline.
- Every LLM call goes through `gemini_agent.logging.llm_logger` (prompt, response, tokens, latency, decision source).

## Code conventions
- Python 3.12, type hints, Pydantic v2 models for all tool I/O, docstrings citing reference cards (e.g. `Source: RC-05 Method A (Guidebook p.268)`).
- Tools are pure functions or small classes; deterministic; seeded randomness; return a compact `summary: str` (≤ 300 chars) plus full data.
- New GEMINI models: `src/gemini_model/<name>/<name>.py` + `__init__.py` + `REFERENCE.md` (copy of the relevant card content) + test in `unit_test/gemini_model/<name>/` + registry entry + `.rst` page in `src/gemini_documentation/source/`.
- Licensing: adapted Plant Debugger code starts with the header in `third_party/plantdebugger/README.md`; modified GEMINI files get `# Modified by <team> for SPE GeoHackathon 2026, <date>: <what>` near the top.
- No secrets in the repo. No network calls in tests.

