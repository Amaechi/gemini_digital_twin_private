/*
Adapted from marcosanyo/PlantDebugger (Apache-2.0), Copyright 2026 Plant Debugger contributors,
file services/plant-debugger/plant_debugger/static/commander.js @ d69507a.
Modified by GeoAgent team, 2026-11-03: the incident polling / telemetry / operator rendering was
replaced by renderers for the GeoAgent JSON API (run list, overview with phase budgets, Decision
Records, verifier report, figures, pending question with answer / approve / reject, tool calls,
LLM tokens, audit trail). Kept: the vanilla-JS structure (fetch → render(el) per sheet), the
escape helper and the status-stamp conventions. No framework; the page computes no number.
*/
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v, digits) => {
    if (v == null || v === "") return "–";
    if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString("en-GB") : v.toLocaleString("en-GB", { maximumSignificantDigits: digits || 4 });
    return String(v);
  };
  const kilo = (n) => (n >= 10000 ? Math.round(n / 1000) + "k" : n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n));
  const api = (path) => fetch(path, { cache: "no-store" }).then(async (r) => {
    const body = await r.json().catch(() => ({ error: r.statusText }));
    if (!r.ok) throw new Error(body.error || r.statusText);
    return body;
  });
  const post = (path, payload) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }).then(async (r) => {
    const body = await r.json().catch(() => ({ error: r.statusText }));
    return { ok: r.ok, status: r.status, body };
  });

  let currentRun = null;

  function stamp(el, text, cls) {
    el.textContent = text;
    el.className = "status-stamp" + (cls ? " " + cls : "");
  }
  function verdictClass(v) {
    return v === "ACCEPTED" ? "good" : v === "REJECTED" ? "bad" : v ? "warn" : "";
  }
  function warn(text) {
    const el = $("mode-warning");
    el.textContent = text || "";
    el.classList.toggle("hidden", !text);
  }

  // ------------------------------------------------------------------ run list
  async function loadRuns() {
    const runs = await api("/api/runs");
    const select = $("run-select");
    select.innerHTML = runs.length ? "" : '<option value="">(no runs in this directory)</option>';
    for (const run of runs) {
      const opt = document.createElement("option");
      opt.value = run.run_id;
      opt.textContent = `${run.run_id} · ${run.status}${run.pending ? " · question pending" : ""} · ${run.verdict || "no verdict"}`;
      select.appendChild(opt);
    }
    const wanted = new URLSearchParams(location.search).get("run");
    const pick = runs.find((r) => r.run_id === wanted) || runs.find((r) => r.pending) || runs[0];
    if (pick) {
      select.value = pick.run_id;
      await loadRun(pick.run_id);
    } else {
      warn("No run directory found: start one with `python -m gemini_agent.cli run --engine policy` and point `cli serve --runs-dir` at its parent.");
    }
  }

  // ------------------------------------------------------------------ run view
  async function loadRun(runId) {
    currentRun = runId;
    history.replaceState(null, "", "?run=" + encodeURIComponent(runId));
    warn("");
    const [overview, tools, records, verifier, figures, events] = await Promise.all([
      api(`/api/runs/${runId}`),
      api(`/api/runs/${runId}/tools`),
      api(`/api/runs/${runId}/records`),
      api(`/api/runs/${runId}/verifier`),
      api(`/api/runs/${runId}/figures`),
      api(`/api/runs/${runId}/events`),
    ]);
    renderOverview(overview);
    renderPhases(overview);
    renderPending(overview);
    renderLLM(overview);
    renderTools(tools);
    renderRecords(records);
    renderVerifier(verifier);
    renderFigures(runId, figures);
    renderEvents(events);
  }

  function renderOverview(o) {
    const card = $("now-card");
    card.className = "now-card state-" + (o.status || "idle");
    $("now-phase").textContent = o.phase || "—";
    $("now-kicker").textContent = `RUN ${o.run_id} · ${String(o.status || "").toUpperCase()} · ${o.input_mode || ""}`;
    const title = o.status === "paused" ? "Paused: a question needs the user" : o.status === "completed" ? `Completed: ${o.selected_candidate || "no design"}` : o.status === "escalated" ? "Escalated to a human" : o.status === "refused" ? "Request refused (out of scope)" : `Running phase ${o.phase}`;
    $("now-title").textContent = title;
    $("now-summary").innerHTML = esc(o.final_summary || o.escalation_reason || "No summary yet.") + (o.loop_count ? ` <code>loop-backs ${o.loop_count}</code>` : "");
    $("now-selected").textContent = o.selected_candidate || "—";
    $("now-verdict").textContent = o.verdict || "not verified";
    $("now-approval").textContent = o.approval || "—";
    $("now-engine").textContent = (o.engine || "—") + (o.model_tag ? " · " + o.model_tag : "");
    $("run-prompt").textContent = o.user_prompt || "";
    const badges = $("service-badges");
    badges.innerHTML = "";
    const add = (text, cls) => { const b = document.createElement("span"); b.className = "badge " + (cls || ""); b.textContent = text; badges.appendChild(b); };
    add(String(o.engine || "").startsWith("llm") ? "LLM ENGINE" : "POLICY ENGINE", String(o.engine || "").startsWith("llm") ? "live" : "policy");
    add(`${o.counts.tool_calls} TOOL CALLS`);
    if (o.counts.stub_tool_calls) add(`${o.counts.stub_tool_calls} STUB`, "stub");
    add(`${o.llm_fallbacks} FALLBACKS`, o.llm_fallbacks ? "policy" : "");
    add(`${o.counts.decision_records} DR`);
    stamp($("session-status"), `${o.status} · ${o.phase}`, o.status === "completed" ? "good" : o.status === "paused" ? "warn" : o.status === "running" ? "" : "bad");
    const report = $("report-link");
    report.classList.toggle("hidden", !o.has_report);
    report.href = `/api/runs/${encodeURIComponent(o.run_id)}/report`;
  }

  function bar(used, max) {
    const frac = max ? Math.min(1, used / max) : 0;
    return `<div class="bar"><i class="${used > max ? "over" : ""}" style="width:${(frac * 100).toFixed(0)}%"></i></div>`;
  }

  function renderPhases(o) {
    const rail = $("phase-rail");
    rail.innerHTML = "";
    for (const p of o.timeline) {
      const div = document.createElement("div");
      div.className = p.status;
      div.title = `${p.phase} ${p.title} (${p.role}): ${p.objective}\nsteps ${p.used.steps}/${p.budget.max_steps} · tool calls ${p.used.tool_calls}/${p.budget.max_tool_calls} · tokens ${p.used.tokens}/${p.budget.max_tokens}` + (p.decision_records.length ? `\nDecision Records: ${p.decision_records.join(", ")}` : "") + (p.passes > 1 ? `\npasses: ${p.passes}` : "");
      div.innerHTML = `<b>${esc(p.phase)}${p.passes > 1 ? " ×" + p.passes : ""}</b><span>${esc(p.title.toUpperCase())}</span>` +
        `<small>${p.used.steps}/${p.budget.max_steps} · ${p.used.tool_calls}/${p.budget.max_tool_calls} · ${kilo(p.used.tokens)}/${kilo(p.budget.max_tokens)}</small>` +
        `<div class="bars">${bar(p.used.steps, p.budget.max_steps)}${bar(p.used.tool_calls, p.budget.max_tool_calls)}${bar(p.used.tokens, p.budget.max_tokens)}</div>` +
        (p.decision_records.length ? `<small class="drs">${esc(p.decision_records.join(" "))}</small>` : "") +
        (p.fallbacks ? `<small>⚠ ${p.fallbacks} fallback${p.fallbacks > 1 ? "s" : ""}</small>` : "");
      rail.appendChild(div);
    }
  }

  // ------------------------------------------------------------------ pending question
  function renderPending(o) {
    const pending = o.pending;
    const form = $("answer-form");
    const actions = $("approval-actions");
    $("answer-feedback").textContent = "";
    $("answer-feedback").className = "answer-feedback";
    const list = $("answers-list");
    list.innerHTML = (o.answers || []).map((a) => `<li>${esc(a)}</li>`).join("");
    if (!pending) {
      $("task-title").textContent = o.status === "paused" ? "Paused without a question" : "Nothing pending";
      stamp($("task-status"), o.status === "completed" ? "DONE" : String(o.status || "—").toUpperCase(), o.status === "completed" ? "good" : "");
      $("task-reason").textContent = o.status === "completed" ? `The run finished; approval: ${o.approval || "—"}.` : "A paused run shows its question here; the answer goes through the runner's own resume logic.";
      $("task-body").className = "task-body empty";
      $("task-body").textContent = "No question is pending.";
      form.classList.add("hidden");
      actions.classList.add("hidden");
      return;
    }
    $("task-title").textContent = pending.is_approval ? "P10 approval" : `${pending.request_id}`;
    stamp($("task-status"), `PAUSED IN ${pending.phase}`, "warn");
    $("task-reason").textContent = pending.is_approval ? "Only the human approves (golden rule: the verifier accepts, the human approves). Same validation as `cli resume --answer yes|no`." : "Validated by the runner exactly as `cli resume --answer` (an invalid answer keeps the run paused).";
    const body = $("task-body");
    body.className = "task-body";
    const shown = typeof pending.default === "object" ? JSON.stringify(pending.default) : pending.default;
    body.innerHTML = `<b>${esc(pending.phase)} · ${esc(pending.request_id)}${pending.assumption_id ? " · " + esc(pending.assumption_id) : ""}</b>${esc(pending.question)}` +
      (shown != null && !pending.is_approval ? `<br/><span class="muted">default <code>${esc(shown)}</code>${pending.unit ? " " + esc(pending.unit) : ""}</span>` : "");
    form.classList.toggle("hidden", !!pending.is_approval);
    actions.classList.toggle("hidden", !pending.is_approval);
    $("answer-input").value = "";
    $("answer-input").placeholder = pending.unit ? `value in ${pending.unit}` : "answer";
  }

  async function submit(path, payload, label) {
    const feedback = $("answer-feedback");
    const buttons = document.querySelectorAll("#answer-form button, #approval-actions button");
    buttons.forEach((b) => (b.disabled = true));
    feedback.className = "answer-feedback";
    feedback.textContent = `${label}… the runner continues until the next question or the end of the run.`;
    try {
      const res = await post(path, payload);
      if (!res.ok) {
        feedback.className = "answer-feedback error";
        feedback.textContent = `${res.status}: ${res.body.error || "failed"}`;
        return;
      }
      feedback.className = "answer-feedback ok";
      feedback.textContent = `${res.body.status} in ${res.body.phase}` + (res.body.pending ? ` · next question ${res.body.pending.request_id}` : res.body.approval ? ` · approval ${res.body.approval}` : "");
      await loadRun(currentRun);
      await refreshRunList();
    } catch (err) {
      feedback.className = "answer-feedback error";
      feedback.textContent = String(err);
    } finally {
      buttons.forEach((b) => (b.disabled = false));
    }
  }

  async function refreshRunList() {
    const runs = await api("/api/runs");
    const select = $("run-select");
    for (const opt of select.options) {
      const run = runs.find((r) => r.run_id === opt.value);
      if (run) opt.textContent = `${run.run_id} · ${run.status}${run.pending ? " · question pending" : ""} · ${run.verdict || "no verdict"}`;
    }
  }

  // ------------------------------------------------------------------ LLM sheet
  function renderLLM(o) {
    const llm = o.llm || {};
    const total = llm.total || {};
    const hasCalls = (total.calls || 0) > 0;
    $("llm-title").textContent = hasCalls ? `${fmt(total.calls)} calls · ${fmt(total.total_tokens)} tokens` : "No LLM calls logged (policy engine)";
    stamp($("llm-source"), llm.source || "NO LOG", llm.source ? "" : "warn");
    const facts = $("llm-facts");
    facts.innerHTML = [
      ["INPUT TOKENS", fmt(total.input_tokens || 0), ""],
      ["OUTPUT TOKENS", fmt(total.output_tokens || 0), ""],
      ["FALLBACKS", fmt(o.llm_fallbacks || 0), "to policy"],
      ["RETRIES / ERRORS", `${fmt(total.retries || 0)} / ${fmt(total.errors || 0)}`, ""],
    ].map(([k, v, s]) => `<div><span>${k}</span><strong>${v}</strong>${s ? `<small>${s}</small>` : ""}</div>`).join("");
    const tbody = $("llm-table").querySelector("tbody");
    tbody.innerHTML = "";
    const phases = (o.timeline || []).map((p) => p.phase);
    for (const phase of phases) {
      const row = (llm.by_phase || {})[phase];
      if (!row) continue;
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${esc(phase)}</td><td class="num">${fmt(row.calls)}</td><td class="num">${fmt(row.input_tokens)}</td><td class="num">${fmt(row.output_tokens)}</td><td class="num">${fmt(row.fallbacks)}</td><td class="num">${fmt(row.errors)}</td><td class="num">${fmt(row.latency_s, 3)}</td>`;
      tbody.appendChild(tr);
    }
    const sources = Object.entries(o.decision_sources || {});
    if (sources.length) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td colspan="7" class="muted">decision sources: ${esc(sources.map(([k, v]) => `${k} ${v}`).join(" · "))}</td>`;
      tbody.appendChild(tr);
    }
  }

  // ------------------------------------------------------------------ tool calls
  function renderTools(tools) {
    const box = $("tools");
    const stubs = tools.filter((t) => t.stub).length;
    stamp($("tools-count"), `${tools.length} CALLS${stubs ? " · " + stubs + " STUB" : ""}`, stubs ? "bad" : tools.length ? "good" : "");
    if (!tools.length) { box.innerHTML = '<p class="muted">No tool calls.</p>'; return; }
    box.innerHTML = tools.map((t) => `<div class="tool ${t.ok ? "" : "err"}"><div class="tr">${esc(t.id)}<small>${esc(t.phase)}${t.loop ? " L" + t.loop : ""}</small></div><div><div class="head"><b>${esc(t.tool)}</b>` +
      `<span class="badge ${t.stub ? "stub" : "live"}">${t.stub ? "STUB" : "REAL"}</span>` +
      (t.source ? `<span class="badge">${esc(t.source)}</span>` : "") +
      (t.candidate_ids.length ? `<span class="badge">${esc(t.candidate_ids.slice(0, 4).join(" "))}${t.candidate_ids.length > 4 ? " …" : ""}</span>` : "") +
      (t.warnings ? `<span class="badge policy">${t.warnings} WARN</span>` : "") +
      `</div><p>${esc(t.error || t.summary)}</p></div></div>`).join("");
  }

  // ------------------------------------------------------------------ Decision Records
  function renderRecords(records) {
    const box = $("records");
    stamp($("records-count"), `${records.length} RECORDS`, records.length ? "good" : "");
    if (!records.length) { box.innerHTML = '<p class="muted">No Decision Records yet.</p>'; return; }
    box.innerHTML = records.map((r) => {
      const crit = r.criterion || {};
      const evidence = (crit.evidence_ids && crit.evidence_ids.length ? crit.evidence_ids : r.evidence_ids) || [];
      const keys = r.evidence_keys || crit.evidence_keys || [];
      const options = (r.options || []).map((opt) => `<li class="${opt.id === r.choice ? "" : ""}${opt.recommended ? " rec" : ""}${opt.eligible === false ? " inel" : ""}">${esc(opt.label || opt.id)}${opt.id === r.choice ? " ← chosen" : opt.recommended ? " (recommended)" : ""}</li>`).join("");
      const rejected = (r.rejected || []).map((x) => `<li>${esc(x.option_id)}: ${esc(x.reason)}</li>`).join("");
      return `<div class="record ${r.decision_source === "policy_fallback" ? "fallback" : ""} ${r.stub_evidence ? "stub" : ""}">` +
        `<div class="dr-id"><span>${esc(r.dr_id)} · ${esc(r.phase)}</span><span class="badge ${r.decision_source === "llm" ? "live" : r.decision_source === "policy_fallback" ? "policy" : ""}">${esc(r.decision_source || "")}</span></div>` +
        `<h3>${esc(r.title)}</h3>` +
        `<div class="choice"><span>CHOSEN OPTION</span><strong>${esc(r.choice_label || r.choice)}</strong></div>` +
        `<div class="crit"><b>CRITERION${crit.key ? " · " + esc(crit.key) : ""}</b>${esc(crit.text || "—")}</div>` +
        `<div class="evidence">${evidence.map((e) => `<i>${esc(e)}</i>`).join("")}${keys.map((k) => `<i title="evidence key">${esc(k)}</i>`).join("")}${r.stub_evidence ? '<i class="badge stub">STUB EVIDENCE</i>' : ""}</div>` +
        `<details><summary>rationale · ${(r.options || []).length} options</summary><p>${esc(r.rationale)}</p><ul>${options}</ul>${rejected ? `<ul>${rejected}</ul>` : ""}</details></div>`;
    }).join("");
  }

  // ------------------------------------------------------------------ verifier
  function renderVerifier(v) {
    const tbody = $("verifier-table").querySelector("tbody");
    const layers = $("verifier-layers");
    if (!v) {
      $("verifier-title").textContent = "No verification yet";
      stamp($("verifier-verdict"), "—");
      $("verifier-summary").textContent = "The DesignVerifier runs in P7 and P9.";
      layers.innerHTML = "";
      tbody.innerHTML = "";
      return;
    }
    $("verifier-title").textContent = `${v.candidate_id} · ${v.verifier} (${v.file})`;
    stamp($("verifier-verdict"), v.verdict || "—", verdictClass(v.verdict));
    $("verifier-summary").textContent = v.summary + (v.violations.length ? ` · violations: ${v.violations.join("; ")}` : "") + (v.not_evaluated.length ? ` · not evaluated: ${v.not_evaluated.join(", ")}` : "");
    layers.innerHTML = v.layers.map((l) => `<span class="${l.status}">${esc(l.layer)} · ${l.passed} pass · ${l.failed} fail · ${l.not_evaluated} n/e</span>`).join("");
    tbody.innerHTML = v.checks.map((c) => `<tr class="${esc(c.status)}" title="${esc(c.detail || "")}"><td>${esc(c.check_id)}</td><td>${esc(c.layer)}</td><td>${esc(c.name)}</td><td>${esc(c.status)}</td><td class="num">${fmt(c.value)}</td><td class="num">${fmt(c.limit)}</td><td class="num">${fmt(c.margin)}</td><td>${esc(c.unit)}</td></tr>`).join("");
  }

  // ------------------------------------------------------------------ figures
  async function renderFigures(runId, figures) {
    const box = $("figures");
    stamp($("figures-count"), `${figures.length} FILES`, figures.length ? "good" : "");
    if (!figures.length) { box.innerHTML = '<p class="muted">No figures (P10 plots them).</p>'; return; }
    box.innerHTML = "";
    for (const f of figures) {
      const url = `/api/runs/${encodeURIComponent(runId)}/figures/${encodeURIComponent(f.name)}`;
      const div = document.createElement("div");
      div.className = "figure";
      if (f.kind === "png") {
        div.innerHTML = `<a href="${url}" target="_blank"><img src="${url}" alt="${esc(f.name)}" loading="lazy" /></a><span>${esc(f.name)} · ${fmt(f.bytes)} B</span>`;
      } else {
        div.innerHTML = `<span>${esc(f.name)} · <a href="${url}" target="_blank">open ↗</a></span><pre>loading…</pre>`;
        fetch(url, { cache: "no-store" }).then((r) => r.text()).then((text) => { div.querySelector("pre").textContent = text; }).catch(() => { div.querySelector("pre").textContent = "unavailable"; });
      }
      box.appendChild(div);
    }
  }

  // ------------------------------------------------------------------ audit trail
  function renderEvents(events) {
    const box = $("timeline");
    stamp($("event-count"), `${events.length} EVENTS`);
    if (!events.length) { box.innerHTML = '<p class="muted">No events.</p>'; return; }
    box.innerHTML = events.map((e) => `<div class="event ${esc(String(e.type).toLowerCase())}"><span>#${e.seq} · ${esc(e.phase || "")} · ${esc(e.type)}</span><p>${esc(e.detail)}</p></div>`).join("");
  }

  // ------------------------------------------------------------------ wiring
  $("run-select").addEventListener("change", (e) => { if (e.target.value) loadRun(e.target.value).catch((err) => warn(String(err))); });
  $("refresh").addEventListener("click", () => { if (currentRun) loadRun(currentRun).catch((err) => warn(String(err))); else loadRuns().catch((err) => warn(String(err))); });
  $("answer-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const answer = $("answer-input").value;
    if (!answer.trim()) { $("answer-feedback").className = "answer-feedback error"; $("answer-feedback").textContent = "an answer is required"; return; }
    submit(`/api/runs/${encodeURIComponent(currentRun)}/answer`, { answer }, "Answering");
  });
  $("approve").addEventListener("click", () => submit(`/api/runs/${encodeURIComponent(currentRun)}/approve`, { decision: "approve" }, "Approving"));
  $("reject").addEventListener("click", () => submit(`/api/runs/${encodeURIComponent(currentRun)}/approve`, { decision: "reject" }, "Rejecting"));
  loadRuns().catch((err) => warn(String(err)));
})();
