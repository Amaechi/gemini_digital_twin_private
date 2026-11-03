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

GitHub settings (see RUNBOOK.md A3): classic branch protection on `main` — require PR, no required approvals, no bypass; add the `hackathon_tests` required check after WP-00 is merged; allow auto-merge. Add team members as collaborators.

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
Do this after WP-14 (c694720) and the pre-L-2 PR (DR-11 backup capacity, OPT-ROBUST-BACKUP) are merged, so the LLM run takes the same decisions as the policy run in `runs/final_policy/`. These steps are **L-1** (pick the model with S11) and **L-2** (the final LLM run and the S1–S11 transcripts). They are the only way to make `scripts/validate_submission.py` pass **SUB-02, SUB-12, SUB-15, SUB-16 and SUB-17**, because a cloud session has no GPU. Run every command from the repo root, inside the venv, in an interactive terminal (zsh or bash). Everything is written under `runs/final/`, which is the only run folder git tracks apart from `runs/final_policy/`.

**What the five checks read** (`src/gemini_agent/eval/submission.py`; `validate_submission.py` defaults to `--run-dir runs/final --eval-root runs/final/eval`):

| Check | Passes when | Step |
|---|---|---|
| SUB-02 | `runs/final/<run>/run_summary.json` has `status` = `completed` and `approval` = `approved` | 7.5 (answer `yes`) |
| SUB-12 | the same run folder holds `llm_calls.jsonl` (≥ 1 record) and `token_summary.json` | 7.5 (`--engine llm`) |
| SUB-15 | some `runs/final/eval/<dir>/eval_results.json` with `engine` = `llm` has S10 with a `token_comparison` | 7.6 |
| SUB-16 | `runs/final/eval/model_comparison.json` lists ≥ 2 models with `engine` = `llm` | 7.3 + 7.4 |
| SUB-17 | **one** `runs/final/eval/<dir>/eval_results.json` (`engine` = `llm`) has every scenario S1–S11 (none `error` or `skipped`), and each scenario folder has `events.jsonl` and `llm_calls.jsonl` (a `refused` run is excused from `llm_calls.jsonl`) | 7.6 |

`validate_submission` reads `runs/final/run_summary.json` or the **only** `runs/final/*/run_summary.json`. If it finds two run folders, it stops with "several runs". So keep exactly one run folder there: `runs/final/final_llm/` (`--runs-dir runs/final --run-id final_llm`). The `runs/final/eval/` folder does not count as a run folder, because its runs sit one level deeper.

**Time estimates.** These are estimates, not laptop measurements. The tool times come from this repo's runs on a 2–4-core cloud machine: the full-budget policy build took 554 s (WP-14, PROGRESS.md), and quick-budget eval scenarios took 181–228 s each (`docs/results/eval_policy_quick/EVAL_REPORT.md`). The LLM time could not be measured here (no Ollama, no GPU). A completed run makes about 46 structured LLM calls (counted in the dry run, where every TestModel call fell back to the policy; the pre-L-2 DR-11 adds one; a real model may make more or fewer), each with a prompt of about 2 k tokens (6.7 k characters) and a short JSON answer. Assuming 3–30 s per call on a 16–36 GB laptop gives about 3–25 min of LLM time per run. That range depends on the model and hardware, and your machine may fall outside it. **Time your 7.3 run and scale the later steps from it.**

| Step | Estimate | Basis |
|---|---|---|
| 7.1 setup + model pulls | 10–40 min | download of 6.6–23 GB per model |
| 7.3 S11, per model (quick) | 5–30 min | ≈ 3 min tools + LLM |
| 7.4 compare | seconds | 0.6 s here |
| 7.5 final run (full) | 15–35 min + your answers | ≈ 9 min tools (fewer with more cores) + LLM |
| 7.6 S1–S11 (quick) | 1–5 h, ≈ 10–11 × your 7.3 time | 12 runs: S2 pauses and S3 is refused early; S10 runs twice, and its naive run sends the whole history |
| 7.7 validate | < 1 min (+ ≈ 7 min with `--pytest`) | 1.1 s here; tests 6 min 39 s here |
| 7.8 commit + push | a few minutes | ≈ 30–50 MB to push. The smoke-budget dry run wrote 20 MB for S1–S11 and 1.2 MB for the final run (caches excluded). Quick and full budgets write full-year CSVs (≈ 0.4 MB each), so expect more. No single file was over 2 MB. |

### 7.1 One-time setup
```bash
cd gemini_digital_twin_private
```
```bash
git switch main
```
```bash
git pull
```
```bash
uv venv -p 3.12 .venv
```
```bash
source .venv/bin/activate
```
```bash
uv pip install -r requirements-hackathon.txt
```
Next, install Ollama (ollama.com) and pull the two models you will compare. The tags come from `config/llm.yaml` and were verified on ollama.com on 31 Oct 2026:
- 16 GB profile: `qwen3.5:9b` (6.6 GB, orchestrator) and `gemma4:12b` (8.0 GB, coder).
- 36 GB profile: `qwen3.6:35b-a3b` (23 GB) and `qwen3.6:27b` (18–19 GB).

S11 should compare a 16 GB-class model with a 36 GB-class model if your laptop can run the larger one. Otherwise compare the two 16 GB tags.

Placeholders used below (replace the `< >` as well):
- `<model-a>`, `<model-b>`: the two Ollama tags you compare, e.g. `qwen3.5:9b` (the `config/llm.yaml` 16gb orchestrator) and `gemma4:12b` or `qwen3.6:35b-a3b`.
- `<model>`: the tag you pick in 7.4.
- `<dir-a>`, `<dir-b>`, `<dir>`: their eval folders. The folder name is the tag with every character other than letters, digits, `.`, `_` and `-` replaced by `_`. For example `qwen3.5:9b` → `qwen3.5_9b`, `gemma4:12b` → `gemma4_12b`, `qwen3.6:35b-a3b` → `qwen3.6_35b-a3b`.
```bash
ollama pull <model-a>
```
```bash
ollama pull <model-b>
```
```bash
ollama show <model-a>
```
Under `Capabilities` the output must list `tools`. Repeat the check for `<model-b>`.

### 7.2 Start the Ollama server with the required settings
These are **server** settings, so they must be in the environment of the `ollama serve` process. Exporting them in the shell that runs `run_eval.py` or `gemini_agent.cli` does nothing, because that shell is only an HTTP client. The CLI prints an `OLLAMA_CONTEXT_LENGTH` reminder but cannot check the value. The Ollama desktop app starts its own server without these settings, so quit it first (menu bar → Quit Ollama). Then run this in a separate terminal and leave it open:
```bash
OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_NUM_PARALLEL=1 OLLAMA_CONTEXT_LENGTH=65536 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 ollama serve
```
- `OLLAMA_MAX_LOADED_MODELS=1` keeps one model in memory, because 16 or 36 GB cannot hold two. Without `--model`, the orchestrator and coder swap; with `--model`, one tag serves both roles.
- `OLLAMA_NUM_PARALLEL=1` handles one request at a time. Every parallel slot would need its own 64 K context.
- `OLLAMA_CONTEXT_LENGTH=65536` is **required**. Ollama's OpenAI-compatible endpoint ignores `num_ctx: 65536` from `config/llm.yaml` (spec §5.4.7), and the server default is much shorter, so long prompts would be truncated without any warning.
- `OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE=q8_0` are optional. Together they cut the KV-cache memory of the 64 K context roughly in half (the q8_0 cache needs flash attention). Leave both out if a model behaves oddly.

To check the settings, look at the server's first log lines: they should print a `server config` line containing these values. If you see "address already in use", another Ollama server is still running; quit it and start again. To keep the desktop app instead, set the variables for it: on macOS run `launchctl setenv OLLAMA_CONTEXT_LENGTH 65536` (one call per variable) and restart the app; on a Linux service run `sudo systemctl edit ollama`, add `Environment="OLLAMA_CONTEXT_LENGTH=65536"` lines under `[Service]`, then restart the service.

### 7.3 Step (a), L-1: S11 for each candidate model
```bash
PYTHONPATH=src python scripts/run_eval.py --engine llm --model <model-a> --tool-profile quick --scenarios S11 --eval-root runs/final/eval
```
```bash
PYTHONPATH=src python scripts/run_eval.py --engine llm --model <model-b> --tool-profile quick --scenarios S11 --eval-root runs/final/eval
```
Each call writes `runs/final/eval/<dir-a>/` (and then `<dir-b>/`) containing:
- `S11/`: a normal run folder with `events.jsonl`, `llm_calls.jsonl` and `token_summary.json`;
- `eval_results.json` and `EVAL_REPORT.md`;
- a `cache/` folder (not committed, see 7.8).

`--tool-profile quick` is both the `run_eval.py` default and the eval budget set by the register entry RUN-BUDGET: the candidate's own point, one DE generation and 12 Monte Carlo samples. `run_eval.py` has no `--budget` flag and ignores `GEOAGENT_BUDGET`. Exit code 1 only means a scenario check failed; the files are still written. Run `ls runs/final/eval` to see the folder names.

### 7.4 Step (b), L-1: compare and pick
```bash
PYTHONPATH=src python scripts/run_eval.py --compare runs/final/eval/<dir-a> runs/final/eval/<dir-b> --eval-root runs/final/eval
```
This prints the S11 table and writes `runs/final/eval/model_comparison.json` and `model_comparison.md` (SUB-16). Pick `<model>` in this order: S11 completed, highest tool-call validity, fewest fallbacks, retries and step rejections, then fewest tokens and shortest runtime. **Run the comparison now, while each folder holds only S11, and do not re-run it after 7.6.** `--compare` adds up every scenario in a folder, so after 7.6 it would compare S1–S11 of one model with S11 of the other. `model_comparison.json` keeps the S11 numbers from this step.

### 7.5 Step (c), L-2: the final LLM run, approved by you
```bash
PYTHONPATH=src python -m gemini_agent.cli run --engine llm --model <model> --budget full --runs-dir runs/final --run-id final_llm --input-mode ask
```
`--budget full` is the RUN-BUDGET setting for the submitted run. It is also the default unless `GEOAGENT_BUDGET` is set. The run writes `runs/final/final_llm/`: `run_summary.json`, `events.sqlite`, `events.jsonl`, `llm_calls.jsonl`, `token_summary.json`, `decision_records/`, `figures/`, `tool_results/`, `report_draft.md` and the other run files.

With the default brief, the terminal asks four questions (order checked in the dry run):
1. P1, DB-30: how to read "330 daily steps" (A or B). Press **Enter** to keep the register default, `A`.
2. P1, DB-31: the heating return temperature. Press **Enter** to keep `55` °C, or type a value.
3. P1, DB-32: the cooling return temperature. Press **Enter** to keep `26` °C, or type a value.
4. P10: `Approve the recommended design <id> (verifier verdict …)? yes/no`. Read the verdict, then type **`yes`**.

Between P1 and P10 nothing needs you. In P8 the LLM (Economics role) decides **DR-11**: keep the TL-13 base-year backup nameplates or install the TL-15 P90 robust backups (register OPT-ROBUST-BACKUP recommends the robust ones when they raise the probability of meeting demand; the policy run chose `C2-opt-robust`). The option it picks becomes the selected design, so the approval question may name `<…>-opt-robust`; the P9 DesignVerifier verifies that design before you are asked.

The run should end with exit code 0 and `approval approved`.
- Answering `no` exits with code 6 and fails SUB-02. Exit code 4 means the run escalated.
- An LLM may word a P1 question differently, so read each prompt; the default is always shown.
- The terminal must be interactive. If input is piped (`printf 'yes\n' | …`), the CLI does not read stdin: the run pauses at the first P1 question and exits with code 3. Continue it with `resume` as shown below.
- A run id cannot be used twice ("already exists; use resume"). To start over, run `rm -rf runs/final/final_llm` first. Never leave a second run folder in `runs/final/`.

**Alternative: `--input-mode pause`.** This answers each question with a separate command:
```bash
PYTHONPATH=src python -m gemini_agent.cli run --engine llm --model <model> --budget full --runs-dir runs/final --run-id final_llm --input-mode pause
```
The run exits with code 3 at each question and prints it. Answer each question with the following command, once per question:
```bash
PYTHONPATH=src python -m gemini_agent.cli resume --budget full --runs-dir runs/final --run-id final_llm --answer <answer>
```
Use the answer that matches the printed question: DB-30 → `A`, DB-31 → `55`, DB-32 → `26` (the register defaults), APPROVAL → `approve` (or `yes`). After the last P1 answer, that resume runs P2–P10 (the long part) and then pauses for the approval. The last resume exits with code 0.
- `resume` reuses the engine and `--model` stored in the run's `events.sqlite`, but not the budget, so repeat `--budget full`.
- The CLI's own `resume with:` hint leaves out `--runs-dir`. Without `--runs-dir runs/final`, `resume` looks in `runs/final_llm/` and fails with "unknown run".

### 7.6 Step (d), L-2: the full S1–S11 eval for the chosen model
```bash
PYTHONPATH=src python scripts/run_eval.py --engine llm --model <model> --tool-profile quick --eval-root runs/final/eval
```
Without `--scenarios`, the command runs all of S1–S11. S10 runs twice (`S10-naive/` and `S10-optimised/`) and writes `S10-token_comparison.json`, which SUB-15 needs. This step replaces the S11-only `runs/final/eval/<dir>/eval_results.json` and the `S11/` folder from 7.3.

Every `run_eval.py` call rewrites `eval_results.json` with only the scenarios it ran. So never finish with a `--scenarios` subset in `<dir>`: SUB-17 needs S1–S11 in one file. To repeat a scenario, re-run this full command. Exit code 1 means some scenario check failed (see `EVAL_REPORT.md`). SUB-17 only needs every scenario to have run without `error`.

### 7.7 Step (e): validate
```bash
PYTHONPATH=src python scripts/validate_submission.py
```
The defaults are `--run-dir runs/final --eval-root runs/final/eval`. SUB-02, 12, 15, 16 and 17 must read `pass`. The other checks judge the LLM run's design in the same way they judged the WP-14 policy run: SUB-03 needs ACCEPTED, SUB-14 and SUB-22 warn, SUB-23 and SUB-24 are manual. Exit code 1 means some check failed. Add `--pytest` to also run the unit tests.

### 7.8 Step (f): branch, commit, PR (never on `main`)
First record the model, the hardware (chip, RAM), the token totals from `runs/final/final_llm/token_summary.json` and the SUB table from 7.7 in `docs/hackathon/PROGRESS.md`.
```bash
git switch -c local-llm-run
```
```bash
git add runs/final docs/hackathon/PROGRESS.md
```
```bash
git status --short
```
Check the staged files:
- Only `runs/final/final_llm/…`, `runs/final/eval/…` and `PROGRESS.md` should be staged, and nothing else.
- No `cache/` folders. They hold the tools' `design_cache.sqlite` memo (≈ 1 MB each in the dry run), which is not evidence; `.gitignore` excludes `runs/final/**/cache/`.
- The `events.sqlite` files are meant to be committed, as in `runs/final_policy/`.
- A file over 50 MB would be unusual (GitHub warns at 50 MB and refuses 100 MB); look into it before pushing.
```bash
du -sh runs/final
```
```bash
git commit -m "L-1/L-2: final LLM run (<model>) and S1–S11 eval transcripts"
```
```bash
git push -u origin local-llm-run
```
Then open the PR on github.com: open the repo → **Compare & pull request** → base `main`. Use the title `L-1/L-2: Final LLM run and evaluation transcripts`, click Create, wait for `hackathon_tests` to pass, then **Squash and merge**. (Optional, with the GitHub CLI: `gh pr create --base main --head local-llm-run --title "L-1/L-2: Final LLM run and evaluation transcripts" --body "Model <model>; validate_submission SUB-02/12/15/16/17 pass"`.) After the merge, run WP-15 in the cloud.

A commercial comparison model is not available: `config/llm.yaml` has only the Ollama endpoint, and `--model` takes Ollama tags.

**Verification.** On 2 Nov 2026, every `run_eval.py`, `gemini_agent.cli` and `validate_submission.py` command above was dry-run in a cloud session into a scratch copy of this layout (`<scratch>/runs/final/…`, passed through the same `--runs-dir` and `--eval-root` flags). The dry run used `--engine testmodel` for `run_eval.py`, `--engine policy` for `cli run`/`resume` (which accept only policy or llm), and `--budget/--tool-profile smoke`. Results:
- ask mode on a pseudo-terminal: Enter × 3, then `yes` → `completed`, `approval approved`, exit code 0;
- piped `printf 'yes\n'` → paused at DB-30, exit code 3;
- pause mode: `run` → exit code 3; resume with `A` → 3, `55` → 3, `26` → 3 (now at the approval), `approve` → 0, `approval approved`;
- `--compare` wrote `runs/final/eval/model_comparison.json`;
- the full suite wrote S1–S11 including `S10-token_comparison.json` (364 s with the smoke budget);
- `validate_submission` with the default-style paths found `runs/final/final_llm/` next to `eval/` and `cache/`. SUB-02 passed for both the ask run and the pause run. SUB-12 and SUB-15–17 failed only because no run had `engine` = `llm`.
- On a copy with the eval results relabelled as `llm`, SUB-15, SUB-16 and SUB-17 passed. SUB-12 passed on a run folder written through the LLM logging path (TestModel).

What was not checked: real Ollama calls, the settings in 7.2, real timings, and the git commands (the `.gitignore` rule for `runs/final/**/cache/` was checked with `git check-ignore`).

### 7.9 What-if on the final run (post-submission, optional)
`cli whatif` changes one or more bounded design levers of a finished run's selected design, reruns the TL-12 year, TL-14 economics (+ CO₂ vs C0, exergy) and the DesignGuard/DesignVerifier on the changed design, and prints a before → after table. It reads `runs/final/final_llm/` and writes only `runs/final/final_llm/whatif/<n>/` (git-ignored; the submitted files stay byte-identical), so it is safe on the submitted run. No LLM is involved.
```bash
PYTHONPATH=src python -m gemini_agent.cli whatif --run-id final_llm --runs-dir runs/final --set DB-31=65 --budget quick
```
Levers (`--set`, repeatable; `whatif --help` lists them with their bounds): `DB-31`, `DB-32` (return temperatures, register range), `DB-30=A|B`, `ECO-CO2-GAS`, `CHP-EL-FRAC` (register range), `OPT-ROBUST-BACKUP=on|off` (the run's TL-15 robust nameplates), `design_flow_m3h`, `doublets` (DesignGuard DB-10/11), `concept=<id>` (re-pick a screened candidate of the run). `--budget quick` (default) simulates the full year on both sides (≈ 1–3 min on a laptop); `--budget full` also reruns the TL-15 Monte Carlo on the changed design for P(demand met) (≈ the P8 time of the run). An out-of-limit lever is refused with the guard's reason (exit 5); an unknown lever lists the valid ones (exit 2). Outputs: `whatif.json`, `whatif.md`, `DR-W<n>.json/.md` (Decision-Record layout, evidence keys), `tool_results/`.

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
