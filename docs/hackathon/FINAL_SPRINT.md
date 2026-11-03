# FINAL SPRINT — 3–5 Nov 2026 (post-WP-15, submission is READY)

State at the start: every WP merged, `scripts/validate_submission.py` READY (20 pass, 0 fail), final LLM run
`runs/final/final_llm/` (gemma4:12b-it-q4_K_M), eval `runs/final/eval/`. Everything below is **extra**.

## Rules that protect the submission
1. New work lives in **new commands and files**. A PR that changes `runs/final*`, `runs/final/eval`,
   `config/*.yaml` values, or any model/tool physics is **not merged** (exception: the one planned full eval
   rerun in step L2, done on the laptop with the whole S1–S11 suite, never a subset).
2. Merge a PR only when 5/5 checks are green (`Docs` "gh-pages … cannot lock ref" → re-run it) and PROGRESS.md
   still says READY. PROGRESS.md conflicts in text sections → "Accept both changes".
3. At most 3 cloud sessions at once. Sessions in the same wave own different files.
4. **Submission cut-off: 4 Nov 18:00** — fork + push `hackathon/<team>` with whatever is merged and green.
   5 Nov is buffer: later merges may be re-pushed to the fork once, after `validate_submission --pytest`.

## Waves (cloud sessions A–I, laptop steps L1–L5)
| Wave | Cloud (parallel) | Laptop |
|---|---|---|
| 1 — 3 Nov now | **A** README polish · **B** what-if · **F** S2/S5/S8 | **L1** deck + video (mandatory) · **L3** second full run (qwen3.6:27b) while recording |
| 2 — 3 Nov pm | **C** --speak, CHP 0.50, fig_tokens (after B) · **G** web Commander (after A) · **E** review loop (after B and F) | **L2** full S1–S11 eval rerun overnight (after F merged) |
| 3 — 4 Nov am | **D** push-to-talk (after C) · **H** speed-study write-up (after L4) · **I** dialogue loop (after B, D, E) | **L4** speed study runs (2 × S11) · test B/C/D on the Mac |
| 4 — 4 Nov pm | — | `validate_submission --pytest`, README last look, **submit** (CLOUD_SETUP §8) |
| 5 Nov | buffer: merge anything still open and green, re-push the fork once | |

Dependencies: C needs B (both edit `cli.py`); E needs B (uses what-if) and F (both edit the runner);
I needs B + D + E; H needs L4; L2 needs F. G is independent.

---
## Prompts (copy-paste into a new session on branch `main` unless stated)

### A — README polish
```text
README polish from a judge's-eye review (post-WP-15; docs only, no code behaviour change; never touch runs/final*,
runs/final/eval or config values). On a new branch from main:
1. README.md: replace both occurrences of `config/climate.yaml` with `config/climate_nl.yaml` ("Where the evidence
   is" and "API placeholders" tables); grep the repo docs for any other `climate.yaml`.
2. README.md: insert an "At a glance" block of 5 bullets right after the first paragraph, before the architecture
   diagram, with numbers copied from the existing Results section (do not retype from memory): selected design
   C2-opt-robust (ACCEPTED 46/46, approved); LCOH 44.6 / LCOC 26.3 USD/MWh, CAPEX 30.1 MUSD, CO2 −72 % vs C0;
   100 % of heating and cooling demand met on all 365 days (0 unmet MWh), P(demand met) 0.90 with the P90 boiler;
   driven by gemma4:12b-it-q4_K_M (open-weight 12B, 4-bit) on a MacBook Pro M3 36 GB via Ollama, 34 calls /
   99 301 tokens / ≈14.5 min wall; one-command offline reproduce `PYTHONPATH=src python -m gemini_agent.cli run
   --engine policy`. Also state the demand claim in words in the Results paragraph.
3. scripts/build_report_pack.py: the DR-01…DR-11 links in docs/results/REPORT.md §7 resolve to
   docs/results/DR-xx.md which do not exist; link to ../../runs/final/final_llm/decision_records/DR-xx.md (or
   wherever the DRs actually are — check) and regenerate REPORT.md with the script (only REPORT.md may change;
   diff it to confirm).
4. README Limitations: add one S7 line ("S7 in runs/final/eval reads fail — coder timeouts before #23; the post-#23
   local run closed the gap in 2 drafts, evidence in docs/results/s7_demo/"); in the P5 DR-08 bullet move "The
   chosen model never hit a budget" to the front; add that the token-figure placeholder affects every run dir under
   runs/final/eval as well; under the S11 table add "(qwen3.6:27b eval outcome pending = the cross-model check is
   not automated)"; in Reproduce change "same decisions as the final run" to "the policy engine reaches the same
   selected design and verdict (DR-10 in the LLM run was a policy fallback)"; next to "299 s of model latency" add
   the wall time ≈14.5 min (events.jsonl 22:26:15→22:40:47).
Keep README ≤ 25 KB. Run the full test command (docs-only change, must stay green) and a relative-link check by
hand (every relative path in README and REPORT.md exists). Open a PR titled "README: at-a-glance block, climate_nl
path, DR links, limitations wording".
```

### B — what-if on a finished run
```text
Post-submission feature, "what-if" on a finished run (new files only; the submitted artefacts in runs/final,
runs/final_policy and runs/final/eval must not change). Read docs/hackathon/PROGRESS.md and
docs/hackathon/FINAL_SPRINT.md first, then only what you need in tools/uncertainty.py (WP-11 TL-15
Scenario/cases), tools/simulate_annual.py, tools/economics.py, runner/decision_records.py and cli.py.
Add `cli whatif --run-id <id> --runs-dir runs/final --set <LEVER>=<value> [--set ...] [--budget quick|full]`
in src/gemini_agent/tools/whatif.py + cli.py. Levers = the bounded design levers that already exist as register
ids or TL-15 case keys: DB-31 (heating return °C), DB-32, DB-30 (A|B), ECO-CO2-GAS, CHP-EL-FRAC, robust backup
on/off (OPT-ROBUST-BACKUP), design_flow_m3h, doublets (1–3, DesignGuard checks), and the selected concept
(re-pick from the run's screened candidates). It loads the saved selected design from the run folder, applies
the lever through the existing TL-15 Scenario mechanism (no new physics), reruns fast year + economics (+ CO2,
exergy), and prints a before → after table of the KPISet (LCOH, LCOC, CAPEX, CO2, unmet days, P(demand met) if
TL-15 is rerun with --budget full, min constraint margins) plus a DesignGuard/verifier verdict for the changed
design. Writes runs/<id>/whatif/<n>/ (JSON + Markdown + a DR-style record "DR-W<n>: what-if <lever>" with
evidence keys, same rules as the Decision Records). Out-of-limit levers are refused with the guard's reason.
Expose the same function as `whatif(run_dir, levers, budget) -> WhatIfResult` for other callers (review loop,
voice). Tests (offline, smoke budget): DB-31=65 on a fixture run changes LCOH and keeps the verdict; doublets=5
refused; an unknown lever lists the valid ones; the run's original artefacts are byte-identical after the command.
Document it in README (new "What-if" subsection under Reproduce, with one example and its output table) and in
CLOUD_SETUP §7. Run black + isort, the full test command and wp-reviewer; update PROGRESS.md; open a PR titled
"Feature: what-if on a finished run".
```

### F — close the pending eval items S2, S5, S8
```text
Close the three pending eval checks (WP-13/WP-14 follow-ups). Read PROGRESS.md and FINAL_SPRINT.md first, then
evals/scenarios.yaml, src/gemini_agent/eval/, runner/design_runner.py (P0 intake, P1 gaps, CONFLICT events) and
the S2/S5/S8 entries in runs/final/eval/gemma4_12b-it-q4_K_M/eval_results.json (read-only evidence).
Do not touch runs/final*, runs/final/eval, config values or any tool physics; a parallel session is adding
tools/whatif.py and a `whatif` CLI command — do not edit cli.py beyond what S5 strictly needs.
1. S2 — inputs missing from the REQUEST: at intake, detect which of {demand CSVs, cooling demand, reservoir
   data} the request text/brief does not provide (the runner always loads config/ and data/demand/, so today
   nothing is detected); raise ONE InputRequest in P1 that lists the missing inputs and offers "use the repository
   defaults (design basis + demand CSVs)" as the default answer — so the full brief (S1) is unaffected and the
   final-run questions stay exactly DB-30/31/32 + approval.
2. S5 — change of basis: when a CONFLICT with DB-16/DB-17 (supply temperature) is recorded, add a confirm-basis
   InputRequest with a register id (add a register entry CFG-BASIS-CONFIRM or reuse an existing id if one fits)
   whose default keeps the design basis; in --input-mode assume the default applies.
3. S8 — cross-run check: add a scenario check type that compares the S8 design (50 mD) with S1's sizing from the
   same eval run (ESP power / flow / depth / doublets) and passes when the doublets are re-sized for the lower PI;
   S8 pending → real check.
Keep the eval harness pending-reason mechanism for anything you cannot close. Tests offline (FunctionModel/
TestModel, smoke budget) for each; the S1 policy run must produce the same selected design (regression test on
the fixture). Expected after the laptop rerun: S2, S5, S8 pass. Run black + isort, the full test command and
wp-reviewer; update PROGRESS.md and README (eval table note: "S2/S5/S8 closed on <date>, eval rerun pending/done");
open a PR titled "Eval: close S2 (request inputs), S5 (confirm basis), S8 (cross-run sizing)".
```

### C — small wins (after B is merged)
```text
Three small post-submission items; never regenerate runs/final, runs/final_policy or runs/final/eval. Read
PROGRESS.md and FINAL_SPRINT.md first.
1. `--speak` flag on `cli run`/`resume`/`whatif` (macOS `say` via subprocess when available, otherwise a no-op
   with a note): speaks each InputRequest question with its default, the P10 summary + verifier verdict, and
   the DR-11 rationale; prints everything as before. Test with the subprocess monkeypatched.
2. Add the CHP-EL-FRAC 0.50 TL-15 case now that chp.py has electrical_fraction (WP-11): register the case in
   UQ-CASES if absent, make sure it runs in the uncertainty tool (smoke test), and mention it in README
   sensitivities/Limitations. Do not rerun the final runs.
3. fig_tokens.png is a placeholder in LLM runs because P10 plots before token_summary.json is written: write
   token_summary.json (or an interim one) before the P10 plot call, or generate fig_tokens.png at run end from
   the final summary; add a test with the LLM logging path (TestModel) that the figure is real. Regenerate only
   that figure for runs/final/final_llm and each runs/final/eval run dir from their existing token_summary.json
   via a one-off script (figures only, nothing else), and note the regeneration in PROGRESS.md.
Run black + isort, the full test command and wp-reviewer; update PROGRESS.md and README; open a PR titled
"Post-submission: --speak, CHP 0.50 case, fig_tokens".
```

### G — web "Design Commander" page (independent; after A to avoid README conflicts)
```text
Web view of a run (spec §4.2 MAY: "web view reusing Plant Debugger static Commander page"). Read PROGRESS.md and
FINAL_SPRINT.md, then third_party/plantdebugger/.../static/commander.html (+ its CSS/JS) for the shell, and
memory/event_store.py for the SQLite schema. Pure presentation, zero new dependencies: a `cli serve --runs-dir
runs/final [--port 8765]` built on the standard library (http.server) that serves src/gemini_agent/ui/static/
(adapted from Plant Debugger with the Apache header) and a small JSON API read from events.sqlite /
run_summary.json / decision_records / figures: run list; per run a phase timeline P0–P10 with budgets used,
tool calls (stub flag), LLM calls/tokens/fallbacks; Decision Record cards (criterion, evidence keys, chosen
option); the verifier report with margins; the figures; the pending question with an Answer box that calls the
existing resume logic; an Approve/Reject button for P10 (same validation as the CLI). Read-only otherwise; never
writes into runs/final except through resume. Tests: API endpoints on the fixture run (TestClient-style via
http.client on an ephemeral port), static files served, answer/approve round trip on a paused smoke run.
Document in README ("Web view (optional)") with a screenshot in docs/results/. Run black + isort, the full test
command and wp-reviewer; update PROGRESS.md; open a PR titled "Feature: web Design Commander view".
```

### E — interactive design review loop (after B and F are merged)
```text
Interactive design review after P9 (keeps golden rule 1: the LLM chooses a bounded lever, tools compute). Read
PROGRESS.md and FINAL_SPRINT.md, then tools/whatif.py (merged), runner/design_runner.py, agents/roles.py,
schemas/decision.py, runner/decision_records.py. Never touch runs/final*, runs/final/eval or config values.
Add a P9b "review" step, enabled with `cli run --review` (default off, so existing runs/evals are unchanged):
after the verifier verdict the user is asked (ask/pause modes; voice later) "Any objection to the recommended
design? (Enter = none)". Free text (e.g. "the boiler is too big", "I'd prefer 3 doublets") goes to the Lead
Engineer role with a NativeOutput schema that maps it to exactly one what-if lever + value from the bounded list
(or "no lever matches" → say so and ask again, max 3 rounds); the runner calls whatif(...) with that lever,
shows the before → after KPI table and the guard/verifier verdict, and asks "adopt the change? yes/no". Adopting
reruns P6–P9 on the changed design (same code path as the verifier loop-back, counted against the ≤ 3 loop
limit) and writes DR-12 "User review: <objection> → <lever>" with evidence keys; declining records DR-12 as
"kept". Policy engine: maps a few keyword patterns (boiler/backup, return temperature, doublets, depth) so
--engine policy also works. Tests offline: FunctionModel maps "boiler too big" → robust backup off; unmatched
text asks again; adopt reruns and writes DR-12; --review off leaves events identical. Document in README under
Reproduce ("Review loop") and in CLOUD_SETUP §7. Run black + isort, the full test command and wp-reviewer;
update PROGRESS.md; open a PR titled "Feature: interactive design review loop (DR-12)".
```

### D — push-to-talk voice front-end (after C is merged)
```text
Voice front-end, offline, new files only (src/gemini_agent/ui/voice.py, cli.py `talk` subcommand,
requirements-voice.txt, tests). Read PROGRESS.md and FINAL_SPRINT.md, then cli.py (ask-mode prompt function,
--speak from PR C) and the runner's ask_fn. Design: push-to-talk (press Enter to start/stop recording) →
faster-whisper STT (model "small", CPU/Metal) → text; TTS via macOS `say` (fallback: print only).
`cli talk --engine llm --model <tag> [--budget]` does: (a) record the request, confirm the transcript ("I heard:
… — use it? [Y/n]"), start a run with it as --prompt; (b) every InputRequest: speak question + default, record,
normalise the answer with one NativeOutput LLM call into the expected type (choice/number/yes-no; "the default"
→ default), echo "I understood 65 — apply? [Y/n]", re-ask on failure; (c) P10: speak the summary + verdict, ask
approve yes/no. Leave a clearly marked hook for the post-run dialogue loop (PR I). Every LLM call goes through
the logger; voice never bypasses DesignGuard/verifier/approval. Dependencies go in requirements-voice.txt only
(faster-whisper, sounddevice); the main CI must not install them; import lazily and fail with a clear message if
missing. Tests offline: STT/TTS monkeypatched, normaliser with FunctionModel, the talk loop drives a
smoke-budget policy run end-to-end. Document in README ("Voice (experimental)") and CLOUD_SETUP §7 with the
exact Mac install steps (`uv pip install -r requirements-voice.txt`, first-run model download size). Run black +
isort, the full test command and wp-reviewer; update PROGRESS.md; open a PR titled "Feature: push-to-talk voice
front-end".
```

### I — dialogue loop (after B, D and E are merged)
```text
Voice dialogue loop: connect `cli talk` (PR D) to the review loop (PR E) and what-if (PR B). Read PROGRESS.md and
FINAL_SPRINT.md, then ui/voice.py, cli.py and the review step in the runner. After the P10 approval (or when
`cli talk --run-id <id>` is pointed at a finished run), loop: speak "What would you like to change?" → record →
the same Lead Engineer lever-mapping schema as the review loop → run whatif → speak a 3-sentence before → after
summary (numbers read from the WhatIfResult, rounded, with units) → "Adopt it, try another, or stop?" → adopt
reruns P6–P9 via the review-loop path and writes DR-12; stop ends. Every understood utterance is echoed and
confirmed before acting; max 5 rounds. Tests offline with monkeypatched STT/TTS and FunctionModel: a scripted
3-turn dialogue (change return temperature to 65 → try 3 doublets → stop) produces two what-if records and no
change to the run's original artefacts. README "Voice" section: add the dialogue example transcript. Run black +
isort, the full test command and wp-reviewer; update PROGRESS.md; open a PR titled "Feature: voice dialogue
loop (what-if by voice)".
```

### H — speed/accuracy study write-up (after laptop step L4)
```text
Write up the §4.3 settings study from the laptop runs on branch origin/study-evidence (runs/study/…; read with
git show/checkout into a scratch dir, never merge that branch): S11 with gemma4:12b-it-q4_K_M under (1) the
submitted settings (thinking off, num_ctx 65536 — reuse runs/final/eval), (2) thinking on, (3) num_ctx 32768.
Produce docs/results/SETTINGS_STUDY.md with one table (completed?, validity, retries, fallbacks, tokens, wall
time, phase where it stopped) and a 10-line interpretation; add a Limitations/evidence pointer in README; copy
the three eval_results.json into docs/results/settings_study/. No code changes. Open a PR titled "Docs: §4.3
settings study (thinking, context length)".
```

---
## Laptop steps
### L1 — deck + video (mandatory, SUB-23)
From `docs/results/DECK_OUTLINE.md` and the figures in `docs/results/`. Export PDF/PPTX to `docs/deck/`, put the
video link in `docs/deck/VIDEO.md`; branch `deck` → PR → merge.

### L3 — second full model run (optional, while recording; ~15–35 min)
```bash
PYTHONPATH=src python -m gemini_agent.cli run --engine llm --model qwen3.6:27b --budget full --runs-dir runs/final_qwen27b --run-id final_llm --input-mode ask
```
Enter ×3, `yes` at P10. Then `git switch -c second-model && git add -f runs/final_qwen27b && git commit -m "Second full LLM run (qwen3.6:27b)" && git push -u origin second-model`, PR, merge; ask session C (or a one-line session) to add `!runs/final_qwen27b/` to .gitignore and a README row.

### L2 — full eval rerun (overnight 3→4 Nov, after F is merged; 1–5 h)
```bash
git switch main && git pull
PYTHONPATH=src python scripts/run_eval.py --engine llm --model gemma4:12b-it-q4_K_M --tool-profile quick --eval-root runs/final/eval
PYTHONPATH=src python scripts/run_eval.py --compare runs/final/eval/gemma4_12b-it-q4_K_M runs/final/eval/qwen3.6_27b runs/final/eval/qwen3.6_35b-a3b runs/final/eval/gemma4_26b-a4b runs/final/eval/qwen3.5_9b-q4_K_M --eval-root runs/final/eval
PYTHONPATH=src python scripts/validate_submission.py
```
Expect S2/S5/S8 → pass (8/11), SUB-15/16/17 still pass. Commit `runs/final/eval` + PROGRESS on branch
`eval-rerun`, PR, merge. If anything regresses (e.g. a scenario now errors), do NOT merge; keep the old eval.

### L4 — speed study (4 Nov am, ~1 h; needs Ollama restarted with the other context length for run 3)
```bash
# run 2: thinking on — edit config/llm.yaml calls.thinking: true for this run only, revert after
PYTHONPATH=src python scripts/run_eval.py --engine llm --model gemma4:12b-it-q4_K_M --tool-profile quick --scenarios S11 --eval-root runs/study/thinking_on
git checkout config/llm.yaml
# run 3: restart Terminal A with OLLAMA_CONTEXT_LENGTH=32768 (other flags as before), then
PYTHONPATH=src python scripts/run_eval.py --engine llm --model gemma4:12b-it-q4_K_M --tool-profile quick --scenarios S11 --eval-root runs/study/ctx32k
git switch -c study-evidence && git add -f runs/study && git commit -m "§4.3 settings study runs (not for merge)" && git push -u origin study-evidence && git switch main
```
Restart Ollama with 65536 afterwards.

### L5 — submit (4 Nov pm; CLOUD_SETUP §8)
```bash
git switch main && git pull
PYTHONPATH=src python scripts/validate_submission.py --pytest
```
READY → fork + push `hackathon/<team-name>` → submit branch URL + deck + video. 5 Nov: re-push once if more is merged and green.
