# RUNBOOK — running the build as autonomously and safely as possible (for the human)

Start state: the kit is pushed to `main` of `Amaechi/gemini_digital_twin_private` (commit 9965aca). Details for each step are in `CLOUD_SETUP.md`; this file is the short order of operations.

What needs a human (everything else the sessions do themselves):
- one-time setup in a browser (Part A, ~20 min);
- pressing "start" on each batch of sessions and "merge" on each pull request (7 batches, ~16 PRs, a minute each);
- the two local Ollama runs on your laptop (Part D);
- submission (Part E).

## Part A — One-time setup (browser)

**A1. Let Claude see the repo.** claude.ai/code → connect GitHub → install the **Claude GitHub App** with access to only `gemini_digital_twin_private`.
Why: a private repo is invisible to cloud sessions without it; it also enables automatic CI fixes on pull requests.

**A2. Define the machine the sessions run on.** claude.ai/code → Environments → New environment:
- Name `geoagent`; Network access **Limited** with **Allow package managers** ticked, plus allowed domains `github.com` and `*.githubusercontent.com`;
- Environment variables `BASH_DEFAULT_TIMEOUT_MS=300000`, `BASH_MAX_TIMEOUT_MS=600000`;
- Setup script: paste only the bash block from `CLOUD_SETUP.md` §3 (not the whole file).
Why: every session boots a fresh Ubuntu machine; this makes each one identical and ready. Limited + package managers = the machine reaches PyPI and GitHub and nothing else.

**A3. Protect `main`.** GitHub → repo → Settings → Branches → Add rule for `main` → tick **Require a pull request before merging** (leave status checks for B3). Settings → General → tick **Allow auto-merge**.
Why: no session can ever write directly to `main`; all work arrives as a pull request you approve with one click. Auto-merge lets GitHub merge a PR the moment its tests pass.

## Part B — First session: WP-00 (alone)

**B1. Start.** claude.ai/code → New session → repo `gemini_digital_twin_private`, branch `main`, environment `geoagent`, model **Opus 5.5**. Paste the prompt from `WORK_PACKAGES.md` ("How to start a session") with `XX` = `00`.

**B2. Wait.** The session builds the environment files, CI workflow and skeleton, runs the review agents and opens a PR `WP-00: …`. You are notified.

**B3. Merge.** On the PR: CI check `hackathon_tests` green, and `PROGRESS.md` in the diff says WP-00 done → **Squash and merge**.
Then Settings → Branches → `main` rule → tick **Require status checks to pass** → select `hackathon_tests` (visible only after its first run).
Why: from now on GitHub itself refuses to merge anything whose tests fail.

## Part C — Repeat for Batches 2–7

Batches (`WORK_PACKAGES.md`, "Suggested order"):
Batch 2: WP-01, 04, 10 · Batch 3: WP-02, 05, 09 · Batch 4: WP-03, 06, 08 · Batch 5: WP-07, 12, 13 · Batch 6: WP-11 · Batch 7: WP-14.

For each batch:
1. **Start** one session per WP (as B1, changing the number); up to 3 at once. They own different files by design.
2. **Wait** for the PRs. CI failure → the GitHub App auto-fix handles it. Dead session → new session on the same branch: "continue WP-XX from docs/hackathon/PROGRESS.md".
3. **Merge** each PR: glance at the CI check and the PROGRESS.md line → **Enable auto-merge** (or Squash and merge if already green). Click **Update branch** if GitHub shows it.
4. Start the next batch only when every PR of the current batch is merged.

Optional extra scrutiny (WP-01, 02, 05, 07, 10): before merging, start one more session with the review prompt in `CLOUD_SETUP.md` §5 item 3.

## Part D — Your laptop (after Batch 7)

Follow `CLOUD_SETUP.md` §7 exactly: pull, install Ollama and the model tags, run the three commands (model pick L-1, final run L-2, evaluation transcripts), commit `runs/final/`, push. Cloud machines have no GPU, so this is the only part that needs your hardware.

## Part E — Finish

Start the **WP-15** session (as B1), merge its PR. Then `CLOUD_SETUP.md` §8: fork the public GEMINI repo, push `main` there as `hackathon/<team-name>`, submit the branch URL, deck and video.

## What keeps this safe
- Sessions cannot write to `main`; only PRs with green tests can be merged.
- Each PR has unit tests, CI and two independent review agents before you see it.
- Cloud machines reach only PyPI and GitHub; no API keys exist anywhere (local Ollama needs none).
- All state lives in repo files (`PROGRESS.md`), so a dead session loses nothing.
- Your recurring job is the one-minute "check green → merge" per PR. For maximum hands-off, enable auto-merge as soon as each PR appears; the test gate still protects `main`.
