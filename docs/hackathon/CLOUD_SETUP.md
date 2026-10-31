# Running the build in Claude Code cloud sessions — operator guide (for the human)

Facts below were checked against the Claude Code docs on 31 Oct 2026 (code.claude.com/docs: claude-code-on-the-web, cloud-environments, settings, sub-agents, context-window, ultrareview). Re-check if something behaves differently.

## 1. Repository strategy
| Repo | Role | Action |
|---|---|---|
| `Amaechi/gemini_digital_twin_private` | **The build repo.** All code, docs, tests, PRs. `main` = integration branch (currently identical to upstream `development` @ d86e8bb). | Upload the kit here (§2). Install the Claude GitHub App on it. |
| `Amaechi/plantdebugger_private` | Reference only. The needed files are vendored into `third_party/plantdebugger/` with LICENSE, so sessions never need this repo. | Nothing. Do not add it to sessions. |
| Public fork of `GEMINI-Digital-Twin/gemini-geothermal-suite-open` (create at submission) | Submission target ("commit and push to the GitHub repository; merging not required"). A private repo cannot be a fork of a public one. | §8. Ask on Discord whether a PR to upstream `development` is preferred. |

**Do NOT upload:** `books/`, `papers/`, `Gemini data/` PDFs, `useful input/` (organiser recordings/slides, Devpost docs) — copyright and repo size; everything needed is captured in the spec and the reference cards. Keep `GEOAGENT_DESIGN_SPEC.md` at the project folder too as your own copy.

## 2. Upload the kit (one time, from your Mac)
```bash
cd ~/Documents/Coding/VScode/geothermalhack26/gemini_digital_twin_private
git checkout main && git pull origin main
cp -R ../hackathon_repo_kit/. .
git add CLAUDE.md .claude docs/hackathon data third_party
git status   # check: no .DS_Store, no PDFs
git commit -m "Add hackathon build kit: spec, work packages, reference cards, vendored reference code"
git push origin main
```
Kit contents: `CLAUDE.md` (auto-loaded by every session), `.claude/agents/{wp-reviewer,physics-checker}.md` (review subagents), `docs/hackathon/{GEOAGENT_DESIGN_SPEC.md, WORK_PACKAGES.md, PROGRESS.md, CLOUD_SETUP.md}`, `data/demand/*.csv`, `data/knowledge/reference_cards/RC-01…12`, `third_party/plantdebugger/` (Apache-2.0 reference files + LICENSE + README).

Optional GitHub settings: protect `main` (require PR + passing `hackathon_tests` check) so sessions cannot push to it directly. Add team members as collaborators.

## 3. Connect GitHub and create the cloud environment
1. claude.ai/code → connect GitHub → install the **Claude GitHub App** on `gemini_digital_twin_private` (private repos need the app; it also enables PR auto-fix).
2. Create an environment named `geoagent`:
   - **Network access:** in the UI choose **Limited** and tick **Allow package managers** (the docs call this level "Trusted") — PyPI is allowed. Add `github.com` and `*.githubusercontent.com` under Allowed domains so the `uv python install` fallback can download if needed. No Ollama/Hugging Face access is needed in the cloud.
   - **Environment variables:**
     ```
     BASH_DEFAULT_TIMEOUT_MS=300000
     BASH_MAX_TIMEOUT_MS=600000
     ```
   - **Setup script** — paste ONLY the bash block below (not this Markdown file). Runs as root on Ubuntu 24.04 before Claude starts; cached ~7 days:
     ```bash
     #!/bin/bash
     set -euo pipefail
     if python3 -c 'import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] < (3,13) else 1)'; then
       PY=python3
     else
       uv python install 3.12
       PY="$(uv python find 3.12)"
     fi
     uv venv -p "$PY" /opt/geo-venv
     uv pip install --python /opt/geo-venv/bin/python \
       numpy==2.2.6 scipy==1.13.1 pandas==2.3.3 matplotlib==3.10.7 \
       "pydantic>=2.7,<3" pydantic-ai==2.64.0 pyyaml pytest pytest-cov ruff
     /opt/geo-venv/bin/python -c "import numpy, scipy, pydantic_ai; print('geo-venv ok')"
     ```
     Notes (docs checked 31 Oct 2026): `uv`, `pip`, `poetry`, `pytest` and `ruff` are pre-installed in the cloud image, so no installer download is needed; `uv python install` fetches from GitHub (add the two GitHub domains above). The environment variables above are **not** visible inside the setup script (they are set after it runs) — the script does not need them. The repository is cloned before the script runs, so after WP-00 lands you may replace the package list with `uv pip install --python /opt/geo-venv/bin/python -r requirements-hackathon.txt` — test once; if the path is not found, keep the explicit list.
3. Hardware is ≈ 4 vCPU / 16 GB RAM / 30 GB disk, **no GPU** ⇒ real LLM runs happen on your laptop (§7). Cloud sessions use the deterministic policy engine and Pydantic AI `TestModel`/`FunctionModel`.

## 4. Running work packages
- Start: claude.ai/code (or the desktop app → Cloud environment `geoagent`) → repo `gemini_digital_twin_private`, branch `main` → model **Opus 5.5** → paste the prompt from `WORK_PACKAGES.md` with the WP number. CLI alternative from your clone (push first): `claude --cloud "<prompt>"`.
- One WP per session. ≤ 3 sessions in parallel (they share your rate limits). Follow the batches in `WORK_PACKAGES.md`.
- Each session ends by opening a PR (Changes card → Create PR). CI (`hackathon_tests`) runs on the PR.
- If a session stalls or you want to finish it on your Mac: `claude --teleport <session-id>` (needs clean git state, branch pushed).

## 5. Tests and reviews (yes — built into every WP)
1. **In-session:** tests written with the code; the session runs the quiet test command, then the `wp-reviewer` subagent (and `physics-checker` for physics WPs) and fixes findings before opening the PR.
2. **CI:** `hackathon_tests` workflow on every PR (offline tests + ruff). With the GitHub App you can enable PR **auto-fix** for CI failures and review comments.
3. **Independent review session (recommended for WP-01, 02, 05, 07, 10):** start a fresh session on the PR branch with:
   ```
   Review the open PR for WP-XX against its acceptance criteria in docs/hackathon/WORK_PACKAGES.md and CLAUDE.md.
   Use the wp-reviewer and physics-checker subagents. Run the tests. Report findings; fix only confirmed issues,
   push to the same branch, and summarise what changed.
   ```
4. **You** skim the PR diff and the PROGRESS.md entry, then merge. Rebase other open branches on `main` (ask the session to do it).
5. **Final:** run `/code-review` in a session on the final branch (WP-15). Optional deep review: `/code-review ultra` (research preview; 3 free runs per account, then billed ≈ $5–25 each; you must launch it yourself).
6. If your plan is Team/Enterprise, automatic GitHub Code Review on PRs can be enabled by an owner.

## 6. Context window — do you need to worry? Yes, a little. What to do
- Sessions auto-compact; after compaction only the root `CLAUDE.md`, the plan and a few recent files are re-injected — **conversation-only instructions are lost**. Therefore all durable state lives in files: `PROGRESS.md`, `WORK_PACKAGES.md`, `config/assumptions.yaml`, PR descriptions.
- The kit is built for this: `CLAUDE.md` is short (< 200 lines) and tells sessions to read only their WP and the listed spec sections, to grep instead of cat (the spec is ~60 KB ≈ 15k tokens; CSVs and logs are long), to run pytest quietly, and to delegate wide reading to subagents.
- One WP per session; start the next WP in a fresh session (not `/clear` in the same one, so each PR maps to one session).
- If a session gets long: ask it to "update PROGRESS.md with the current state, commit, push", then `/compact focus on WP-XX remaining tasks` or start a new session on the same branch with "continue WP-XX from PROGRESS.md".
- `/context` shows usage. Do not paste large files or logs into the chat.

## 7. Local steps you run on your laptop (needs Ollama; GPU/unified memory)
After WP-13/14 are merged:
```bash
cd gemini_digital_twin_private && git pull
uv venv -p 3.12 .venv && source .venv/bin/activate && uv pip install -r requirements-hackathon.txt
# install Ollama (ollama.com), then pull candidate models (check exact tags on ollama.com/library):
#   16 GB: qwen3.5:9b  + gemma4:12b
#   36 GB: qwen3.6:35b (orchestrator) + qwen3.6:27b (coder)
export OLLAMA_CONTEXT_LENGTH=65536
ollama serve &
PYTHONPATH=src python scripts/run_eval.py --engine llm --model <tag> --scenarios S11   # L-1 pick model
PYTHONPATH=src python -m gemini_agent.cli run --engine llm --model <tag> --out runs/final   # L-2
PYTHONPATH=src python scripts/run_eval.py --engine llm --model <tag> --out runs/final/eval   # S1–S11 transcripts
git add runs/final && git commit -m "Final LLM run and evaluation transcripts" && git push
```
Record the model, hardware and token totals in PROGRESS.md. Then run WP-15 in the cloud.
Optional commercial comparison: same commands with a provider model configured in `config/llm.yaml` (API key in your shell, never committed).

## 8. Submission
1. GitHub → fork `GEMINI-Digital-Twin/gemini-geothermal-suite-open` to your account (public).
2. Push the final branch there:
   ```bash
   git remote add submission https://github.com/<you>/gemini-geothermal-suite-open.git
   git push submission main:hackathon/<team-name>
   ```
3. Check the public branch contains no secrets, no PDFs, and includes `third_party/plantdebugger/LICENSE` and `NOTICE`.
4. Submit the deck (≤ 10 slides + appendix), the ≤ 5-min video, and the branch URL as instructed by the organisers.

## 9. Timeline sketch (adjust to the confirmed deadline)
| When | Cloud sessions | You |
|---|---|---|
| Day 1 | WP-00 → WP-01, WP-04, WP-10 | Upload kit, environment, Discord questions, install Ollama + models |
| Day 2 | WP-02, WP-05, WP-09 → WP-03, WP-06, WP-08 | Merge PRs, review |
| Day 3 | WP-07, WP-12, WP-13 → WP-11 | L-1 model pick |
| Day 4 | WP-14 (design freeze) | L-2 final run, slides, video draft |
| Day 5 | WP-15 | Submit |
