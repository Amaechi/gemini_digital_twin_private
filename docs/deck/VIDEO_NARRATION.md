# Narration for `geoagent_demo_clips.mp4` (46 s, 1920×1080, three clips in one file)

Play the file in the 2:00–3:15 slot of the video script (slide 4, "The agent at work"). Read each
line while the matching segment is on screen; the times are where each segment starts. Everything
shown on screen comes from real runs: clips 1 and 3 were recorded on the policy engine in the cloud
session (same tools, same verifier; no language model), clip 2 replays the laptop run of 3 November
whose evidence is in `docs/results/s7_demo/`.

| Time | On screen | Say |
|---|---|---|
| 0:00 | Caption "Clip 1 of 3", then the command `geoagent run … --prompt 'Design a geothermal heating system for 30 MW'` | "First: what happens when the request is incomplete. We ask for a heating system for thirty megawatts and give no data at all." |
| 0:03 | The run output: "paused (phase P1)", the question listing the missing demand CSVs, cooling demand and reservoir data, and "resume with …" | "The run stops in phase one. It lists exactly what is missing, the demand profiles, the cooling load, the reservoir data, and asks whether to use the repository defaults. Nothing is assumed before the user answers. The language model never fills a gap with a number." |
| 0:09 | Caption "Clip 2 of 3", then the command `python -m gemini_agent.agents.model_builder --sandbox runs/s7_demo --model gemma4:12b-it-q4_K_M --max-attempts 3` | "Second: the agent extends the digital twin itself. For this scenario the free-cooling model was removed from the GEMINI registry." |
| 0:12 | "registry lacks component type 'free_cooling'", "reading reference card RC-07" | "The gap analysis finds the missing component. The Model Builder reads reference card seven, which cites the textbook page for free cooling and the wet-bulb formula." |
| 0:15 | "draft 1 … 2 tests, 1 failure", "draft 2 … 2 tests, 0 failures" | "It writes the model and its test in a sandbox. The first draft fails one of two tests; the failure goes back to the coder; the second draft passes both, in about seventy seconds per draft on a twelve-billion-parameter model." |
| 0:20 | "FreeCoolingRc07 registered at runtime after 2 draft(s); 2 tests passed", "registration: runtime only; promotion into src/ needs a reviewed pull request" | "The class is registered at runtime and the design run continues. It is never promoted into the code base without a reviewed pull request: tests are the gate, a human is the gate after that." |
| 0:24 | Caption "Clip 3 of 3", then `geoagent resume --run-id demo --answer 26` | "Third: the end of a full design run. The last open input, the cooling return temperature, is answered, and phases two to ten run: subsurface, nine candidates, screening, optimisation, the full year, Monte Carlo, verification." |
| 0:29 | "paused (phase P10)", "Approve the recommended design C2-opt-robust (verifier verdict ACCEPTED)? yes/no" | "Seven minutes later it stops again, at the only question that matters: approve the recommended design, C2-opt-robust, which the verifier has accepted?" |
| 0:33 | The verifier table excerpt: "all 46 checks pass", rows PH-02 brine cooling 40 K margin 0, PH-18 ESP intake margin 0.86 bar, PH-19 NPSH, PD-03 spacing 1 200 m, PD-04 interference 1 bar, PD-05 breakthrough 34.4 years | "Before answering, the verifier report: forty-six checks, all pass, with a margin on every one. The tightest are the limits the brief fixes, forty kelvin of brine cooling and one bar of interference, used to the full on purpose. The pump intake stays above the bubble point, and breakthrough is thirty-four years against thirty required." |
| 0:39 | `geoagent resume --run-id demo --answer yes`, then "completed (phase P10) … selected C2-opt-robust (verifier verdict ACCEPTED); 11 Decision Records; approval approved; 0/23 tool calls were stubs" | "A human says yes. The run completes with eleven Decision Records, an approved design, and every one of the twenty-three tool calls real. Only the verifier can accept; only a person can approve." |
| 0:44 | Final frame holds | (pause, then continue to slide 5) |

Segments run back to back with about two seconds of hold after each block of output; if you need more time to speak, pause the player on the held frame. If the slot is too short, cut clip 2's two middle lines (0:12 and 0:15) into one sentence: "It reads the
reference card, writes the model and its test, fails once, fixes it, and registers the class."
