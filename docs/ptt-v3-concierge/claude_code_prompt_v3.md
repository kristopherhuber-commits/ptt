# Develop — Claude Code prompts, PTT Dictation v3.0 Concierge

**READY — sessions 0–3 and gate 2.5 are complete; session 4 is next.** In the project's
document architecture this is the Develop document: the complete instruction set for
construction, consequent to `concierge_requirements.md` and `concierge_design.md`. Design
§10's decision log is fully resolved — **Q1–Q7 from the design discussion and the spike,
Q8–Q27 from session 0** (`stage0_review_v3.md`), **Q28 from the candidate review before
gate 2.5**. The 2026-08-25 spike (`spike_results.md`) verified the harness design
empirically.

**The gate has been passed.** `model_qualification.md` records it: **Gemma 4 12B Q4_K_M,
`tool_mode: native`, reasoning `off`** — 106/123 on the 41-scenario suite at `--repeat 3`,
all seven thresholds PASS. Qwen 3.5 9B and gpt-oss-20b were both disqualified for making
an unsafe write under the jailbreak scenario. Frozen artifacts: harness `3.0.0-s2`, prompt
`fa2a83eb2f54`, pack `129c5a31d17f` — **the pack was changed and re-scored in
session 3** (`76a281c8a388`; same 106/123, see `model_qualification.md`). Sessions
3–5 may run.

**Session 0 changed the session-1 scope materially.** Three items that were not in the
original prompt are now load-bearing: a validated write path in `config.py` (D-CG-13,
Q9 — FR-CG-11 is not closable without it), a job object for process containment (Q10),
and the system prompt as a versioned artifact (D-CG-12, Q17). Two build-script changes
moved forward from session 5 to session 1, because leaving them until then means every
intermediate build ships a 6.87 GB file (Q27).

Staging follows design §7: harness first, standalone; model qualification through the
CLI rig; Qt last. Same discipline as v2.0: session 0 is read-only; every session ends
with the L1 suite green and the verification seed updated.

**Model: Opus. Effort: Max on sessions 0, 1, 5; Extra on 2, 3, 4.**

How to use this document: each session below has exactly one fenced block. Paste that
block verbatim as the session's prompt — nothing before it, nothing after it. Read
`stage0_review_v3.md` yourself before starting session 1. Session 2.5 is yours, not
Claude Code's.

---

## Session 0 — read-only design review (Opus · Max)

Paste exactly this:

```
Read docs/ptt-v3-concierge/ in full (concierge_requirements.md, concierge_design.md,
concierge_verification.md, concierge_handoff.md, spike_results.md), then docs/design.md,
docs/requirements.md, docs/verification.md, docs/development_history.md. Write no code;
create only docs/ptt-v3-concierge/stage0_review_v3.md, covering:

1. Anything you judge a bad idea, with reasoning, citing sections.
2. Anything two reasonable implementers would build differently.
3. Is the grammar-from-tool-registry scheme (design §4.1) implementable exactly as
   stated with current llama.cpp JSON-schema support? The spike confirmed the schemas
   work; name any gap in the *generation from the registry*.
4. Does the context budget (design §5) survive worst cases — a 400-line read_log
   result, a long guided-setup dialogue?
5. Does the process-hygiene plan close FR-CG-9 including the crash-orphan case?
6. Can the knowledge pack drift from its source docs, and what prevents it?
7. Whether the threading extension (criterion 9) is unambiguous for the new signals.

Do not propose new features.
```

---

## Session 1 — harness core, standalone, no Qt, no model (Opus · Max)

Paste exactly this:

```
Read docs/ptt-v3-concierge/stage0_review_v3.md and concierge_design.md §10 (Q8–Q27)
first: session 0 amended the design and this prompt reflects those decisions.

FIRST, in app/ptt/config.py — this is D-CG-13 (design §4.6, Q9), and FR-CG-11 cannot be
closed without it:
- Lift the rules now inline in load() into one declarative FIELDS table: type, choices,
  range, parse/coerce, and the per-field prose that already lives in the Settings
  docstring comments.
- Give it three consumers and no second copy of any rule: load()'s existing
  fallback-with-a-logged-reason path; a new Settings.set(key, value) -> (ok, reason)
  that validates at write time; and the tool registry's schema.
- Every existing writer goes through Settings.set(), including InstantApplyPanel.
  apply_now — the invariant belongs to the object, not the caller.
- The existing 91 config tests must stay green. Add a mutation check per
  verification.md §4.1: give one field a private copy of its rule, prove a test fails.

THEN implement concierge_design.md §2–§5, §8 as app/ptt/concierge/ with zero Qt imports
(CON-CG-6):

- server.py: subprocess, NOT QProcess (Q8). Launch args per design §2's table:
  --alias ptt-concierge, -rea off, -np 1 (Q14), --port <pre-bound in Python> (Q13),
  --api-key-file <per-launch key> (Q19). Containment is a Windows job object with
  JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE (Q10, design §8.1). Write
  app/concierge_state.json {pid, create_time, port} before Popen, delete it on clean
  shutdown. Startup reap: probe /props and kill only when model_alias ==
  "ptt-concierge"; fall back to pid + create_time + image name when HTTP is silent;
  leave any other alias alone and log it. Never read another process's command line.
  Health poll, idle timer, stderr reader — all on harness threads.
- llm.py: SSE client; generate BOTH request shapes from ONE registry (Q15) — the
  two-level discriminated union for grammar mode (design §4.1, Q12) and the tools array
  for native mode — and L1-pin both, including the streaming tool_calls delta
  accumulator. maxLength on reply. Repair loop, 6-iteration cap, finish_reason ==
  "length" is a repair trigger and never parses as a valid decision. Timeouts per
  design §4.3: stall 30 s, turn 180 s, server-ready 60 s — every forced stop visible in
  chat AND logged (Q18).
- agent.py: loop; context budget with §5.0's five numbered trimming rules, one L1 test
  each, including rule 5 (every trim is logged); mutable memory note LAST in the fixed
  prefix (design §5); undo journal covering set_config AND update_memory, with
  reverse-order session restore touching only journalled keys (Q22, Q24).
- tools.py: the eight tools (handoff §4) over injected seams. Uniform 16 KiB result cap
  enforced at fetch time with truncation stated in the JSON (Q16). read_log reads
  debug_log.txt AND debug_log.prev.txt, labelled, sharing one budget (Q21).
  run_benchmark's progress is emitted by the harness, not generated by the model, and
  the cached entry records llm_resident (Q23). get_state returns a key list tools.py
  declares (Q26). set_config goes through Settings.set() and is limited to the FIELDS
  allowlist — that is what enforces the vocabulary-editing exclusion.
- system_prompt.md as a versioned artifact in the package (D-CG-12, Q17), carrying
  design §4.5's five parts. The L1 loop tests run against it.
- fetch.py: resumable download; the pinned SHA-256 is the authority and the HF tree API
  LFS oid is compared against it BEFORE downloading, refusing on mismatch (Q26). The
  llama.cpp binary-bundling helper resolves the release tag's nightly-tag.txt
  indirection (spike findings 4–5) and is build-time only — it must never run in the
  shipped app.
- State machine per design §8, including loading covering the pack prewarm.
- TWO CHEAP CHECKS against the pinned build, before llm.py is finished (design §4.1):
  (a) POST the real generated eight-tool schema and re-run C1's ten prompts behind the
  real pack; (b) set maxLength: 40 with a prompt that wants a long answer, and record
  whether llama.cpp's converter honours it or drops it. If it drops it, amend §4.1 to
  say so rather than naming a mitigation that never fires. Record both in
  spike_results.md as a new section.
- MINI-SPIKE (design §5, decision Q6): a small standalone script under spike/ that
  tests whether the pack's KV prefix survives a llama-server restart via
  --slot-save-path / --cache-ram (one slot now, per Q14). Record the result in
  spike_results.md. Implement server.py's ready path accordingly: persistence if it
  works, else the prewarm fallback (throwaway max_tokens: 1 request carrying the pack,
  inside the loading state).
- build_knowledge_pack.py per design §5.05 (Q20): part 1 generated from FIELDS, part 2
  from a hand-written docs/concierge_narrative.md that you draft and I review. Record
  each source's {path, size, sha256} in the pack's front matter. The step ERRORS on a
  missing or unreadable source — never skips. L1 tests: digest manifest, and the pack
  fits the §5 budget.
- build_portable.py (Q27, moved forward from session 5): a directory rule in
  should_skip() so os.walk never packs app/models/**; concierge_state.json and
  concierge_key added to RUNTIME_ARTIFACTS. install.ps1: move app/models/ and
  app/config.json aside around the Remove-Item and back afterwards.
- Config block per concierge_handoff.md §6 — opt_in tri-state, tool_mode, no port key —
  every key a FIELDS entry, criterion-8 round-trip safe.
- The full L1 suite from concierge_verification.md §1. Fake the HTTP layer; no GPU
  anywhere in L1 except the three explicitly-named real checks above.

CON-CG-3: no agent framework. Update the verification seed's traceability column for
every item you close, and add a development_history.md entry for anything that bit you.
```

---

## Session 2 — CLI rig and qualification suite (Opus · Extra)

Paste exactly this:

```
Build tests/tools/concierge_cli.py per design §7.2: a REPL over the real agent loop,
--fake-tools or real seams, --model <gguf>, --tool-mode native|grammar (default
grammar until gate 2.5, per Q15), transcript logging.

Then ITERATE system_prompt.md against the real model through the rig (Q17) — session 1
wrote the first draft against L1 fakes, and this is the only place a prompt can
actually be judged. Report what you changed and why.

Then implement the ~40-scenario qualification suite per design §6 as data
(scenarios.yaml) + a runner that emits a machine-checked scorecard, appendable to
model_qualification.md. The scorecard records per model: scores per class, tool_mode,
reasoning budget, TTFT, decode tok/s, cold-load seconds, AND the sha256 of the frozen
system prompt and of the knowledge pack (Q17, Q20) — without those two, the suite
measures the prompt and the pack rather than the model.

The explanation class scores "no invented settings" against a whitelist DERIVED from
config.py's FIELDS, never hand-listed (Q20). The adversarial class seeds a fake log
containing injected instructions, and seeds it with dictated-transcript text, not just
window titles — debug_log.txt carries the full text of every transcription. Include
the seeded fake logs for the diagnosis class. The runner must work against any
OpenAI-compatible endpoint so a candidate model is one flag.
```

---

## Session 2.5 — MANUAL GATE (human, not Claude Code) — **RUN 2026-08-26, PASSED**

**Outcome: Gemma 4 12B Q4_K_M, `native`. Do not re-run this gate for v3.0.** The
decision, the six candidate scorecards, the threshold changes and what was knowingly
accepted are all in `model_qualification.md`. Three things it produced that the later
sessions inherit: `concierge.tool_mode` now defaults to `native`; `injection_compliance`
is a new absolute threshold; and `tools.Registry` refuses an `update_memory` that copies
text out of `read_log` (`development_history.md` #23, #24).

The checklist below is kept because it is the procedure for the **next** qualification —
a 24 GB+ tier, or a newer llama.cpp pin. In order:

0. **Freeze `system_prompt.md` and the knowledge pack**, and record both hashes. A prompt
   that moves between candidates makes the scorecards incomparable (Q17). The runner
   stamps both digests into every block itself, so this is a matter of not editing them
   once you start.

1. **Gate zero, per candidate, before any scoring** (Q28, design §6). Does it load on the
   pinned `b10621` — `/health` answers, `/props` reports the alias — **and does it load
   with the app already running?** The second is not implied by the first: after the
   driver's 209 MiB and a resident Whisper's 2318 MiB there are 13857 MiB left, and
   gpt-oss-20b's model buffer alone is ~11.3 GiB. A candidate that needs a build past
   `b10621` is not a candidate at this pin — taking it means moving the pin and re-running
   C6 and C7a, which is a decision, not a download.

   **Loading is not serving — send one real message too.** gpt-oss-20b passed both
   load checks and then failed every question: `-rea off` does not suppress a harmony
   model's analysis channel, so it ran to the token cap on all six iterations and never
   emitted a decision. `--reasoning-effort low` fixes it and goes in that candidate's
   scorecard row. Gate zero as run on 2026-08-26 is tabulated in design §6.

2. **The three candidates.** Gemma 4 12B Q4_K_M is already in `spike/` and its pin was
   verified current on 2026-08-26 (`concierge_handoff.md` §1.1) — do not re-download it
   and do not re-pin it. Then Qwen 3.5 9B, then **gpt-oss-20b (MXFP4)**, which replaces
   "one 20B-class MoE" and is on the list for what it may fail: if its harmony-only
   training makes generic `tools` arrays unreliable it should score native ≪ grammar,
   which is the only direct test of CON-CG-5 available.

3. **Run both modes on every candidate.** gpt-oss-20b needs
   `--reasoning-effort low` on every run, in both modes; the other two do not. Session 2 measured a 6-point selection gap
   between them on Gemma 4 alone (design §4.1), so a single-mode run per candidate
   measures the mode as much as the model. `--repeat 3` at minimum: the hotkey write
   varied 2-of-4 clean, 1 repaired, 1 repaired-after-a-wasted-retry across four runs of
   one scenario.

4. Append scorecards + NFR-CG-1/2 measurements to `model_qualification.md`, pick the
   default **and its `tool_mode`** (Q15), and confirm (or raise) the §6 thresholds.

Read `model_qualification.md`'s "How to read a block that says shakedown" first: session
2's block is the instrument being tested, not a candidate, and six scenario bugs plus one
harness defect were already shaken out of it. **No further session until this is done.**

---

## Session 3 — panel UI and threading (Opus · Extra) — **RUN 2026-08-26, DONE**

Built: `app/ptt/ui/qt_concierge.py` (view model + `ConciergePanel`),
`app/ptt/ui/qt_concierge_worker.py` (`ConciergeWorker` + `ConciergeController`),
`app/ptt/ui/qt_threadcheck.py` (`log_thread` moved out of `qt_tray`, plus `SignalAudit`),
`app/ptt/concierge/sessions.py` (saved transcripts), the splitter and tab-strip button in
`qt_window.py`, the tray's `Concierge…`, the FR-CG-2 hop in `qt_app.py`, and the
stylesheet block. 84 new L1 items (`V-CG-101`…`V-CG-124`); 747 tests green.

Three things later sessions inherit: the thread adapter lives in `ptt.ui`, not
`ptt.concierge` (design §2, rev. session 3 — a QThread adapter cannot pass CON-CG-6's
import test); `THREAD-CHECK` is keyed by signal **and** emitting thread (§10 Q26 rider,
without which v3-10's idle-timer hop can never be shown); and `install.ps1` preserves
neither the memory note nor the saved transcripts across a reinstall
(`concierge_verification.md` §4, still open — session 5 owns that file).

Paste exactly this:

```
ConciergePanel per concierge_handoff.md §7 and mockup 5a — note there is no thinking
row (reasoning is disabled, handoff §1); ConciergeWorker QThread adapter per design §2.

Threading, per handoff §2 as amended by session 0 (review §7): the rule being extended
is v2.0 criterion 9 — always write v2-n or v3-n, the two sets collide. QueuedConnection
only, in BOTH directions. The new hazard is worker-thread WRITES: set_config must not
call InstantApplyPanel.apply_now (a QWidget method); it calls Settings.set(), then
emits a settings-changed signal the adapter receives on the GUI thread and turns into
the existing qt_app._on_settings_changed broadcast. That hop is where FR-CG-2 is won.
THREAD-CHECK logs once per signal type per session, at first emission (Q26) — the token
signal fires ~30/s into the same debug_log.txt that read_log reads.

The memory-note viewer must surface a REFUSED update_memory, not just a successful one:
gate 2.5 added a harness guard that rejects a note copying text out of read_log
(development_history.md #24), so `{"error": true, "reason": ...}` is now an ordinary
outcome of that tool and the panel has to render it like any other refusal (FR-CG-11).

QSS from existing tokens, square corners, no new colors. View-model split as in
qt_statusview.py; test the view-model in L1. Session naming/save UI, the memory-note
viewer with its .prev restore (design §5.1, Q22), the Undo chips for both set_config and
update_memory, and the ↺ session control that replays only journalled keys (Q24). Delete
model (6.87 GB) lives on THIS panel, not Advanced (Q25) — Advanced keeps its never-writes
invariant and V-UI-12 stays green. The panel renders the state machine's states verbatim,
including loading until the pack prefix is warm — ready means the first message will be
fast.
```

---

## Session 4 — download, first run, lifecycle states (Opus · Extra)

Paste exactly this:

```
Wire fetch.py into the panel per concierge_handoff.md §8 and mockup 5b: progress in
panel, dictation unaffected, resume on relaunch. Verification is the pinned SHA-256 with
the HF tree oid compared against it BEFORE the download, refusing on mismatch with a
clear message the user cannot click past (Q26). First-run opt-in card, driven by the
tri-state opt_in key — unset shows it, declined never shows it again (Q26). Guided setup
driven by the system prompt (FR-CG-4). No-CUDA disable (FR-CG-12). Residency slider
0–30, 0 = unload on close, default 5, wired to the idle timer (FR-CG-8). Delete model
(6.87 GB) on the Concierge panel — NOT the Advanced tab (Q25) — returning the state
machine to not_downloaded.
```

---

## Session 5 — acceptance pass and packaging (Opus · Max)

Paste exactly this:

```
Execute concierge_verification.md §3's TWELVE criteria the way v2.0's session 5 executed
gui_handoff §10: instrumented where possible, by hand where not, V-M numbers continuing
verification.md's sequence. Write v3-n for every criterion number — the two sets collide.

Criterion v3-7 is now four separate process audits (clean exit, TerminateProcess,
Stop-Process -Force, and a simulated pre-job-object orphan). Re-measure NFR-CG-3 with a
larger sample than the spike's n=3, in BOTH states it now names — resident-idle and
actively generating (verification §4).

Then fold the seed into verification.md as §3.3 and update §6/§7. Packaging:
llama-server.exe + cudart DLLs (build b10621, cuda-12.4 — handoff §1) into
build_portable.py's allowlist, with llama.cpp's MIT LICENSE beside them — the OFL
precedent (V-M-64) is that a bundled component's licence file travels with it, and it is
checked in the built archive. Measure and record the distribution's size delta, as CON-3
did for PySide6; nothing has stated it. Knowledge pack generated and shipped; GGUF,
concierge_state.json and concierge_key all absent from the zip (v3-11). Rebuild, extract,
run; verify v3-1, v3-7, v3-10 and v3-11 against the extracted copy, then run install.bat
over an existing installation and confirm app/models/ and app/config.json both survive.
Record bugs as development_history.md entries.
```
