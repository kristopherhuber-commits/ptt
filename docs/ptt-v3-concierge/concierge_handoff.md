# PTT Dictation v3.0 — Concierge UI/UX Design

A local agent, docked to the settings window, that explains the application, answers
questions about any setting, and reconfigures the app on the user's behalf.

**Scope note (document architecture):** this file is the *UI/UX half of the Design
document*, alongside the mockups (`PTT Dictation UI.dc.html` §5, options 5a/5b). The
software/harness design is `concierge_design.md`; requirements are
`concierge_requirements.md`; verification seeds are `concierge_verification.md`; the
Develop document is `claude_code_prompt_v3.md`. Where §2–§4 below sketch architecture,
`concierge_design.md` supersedes them.

## 1. Decisions (locked)

| Decision | Choice |
|---|---|
| Surface | Chat panel docked right of the settings window (`QSplitter`, collapsible); tray menu gains `Concierge…` which opens Settings with the panel expanded |
| Writes | Fully autonomous — the agent applies changes without confirmation, every change gets an inline **Undo** chip, and each session records a full config snapshot restorable from the panel header |
| Runtime **(rev. session 0)** | Bundled `llama-server.exe` (llama.cpp, MIT) run as a **`subprocess`, inside a Windows job object**, from the Qt-free harness — not a `QProcess` (design §2, Q8; CON-CG-6 and the §7.2 CLI rig both forbid Qt here). OpenAI-compatible endpoint on `127.0.0.1`, **port pre-bound in Python** rather than `--port 0` (Q13), `-np 1` (Q14), `-rea off` (design §6), `--api-key-file` (Q19). Pinned build: llama.cpp `b10621`, `cuda-12.4` binaries + separate `cudart` runtime zip (spike setup §1) |
| Model | Gemma 4 12B, Q4_K_M GGUF (**6.87 GB** — text-only; the multimodal `mmproj` projector is not shipped). Pinned artefact: `lmstudio-community/gemma-4-12B-it-GGUF` / `gemma-4-12B-it-Q4_K_M.gguf`, SHA-256 `95d83ba36642b1f385fb906b5962a71763361be3bac930a709945f72d97473f8`, 7 381 382 944 bytes (spike setup §2). **Provenance, verified 2026-08-26 — see below.** Single tier for v3.0; a 24 GB+ tier is deferred, and the config key is written to allow it (`concierge.model`) |
| Weights **(rev. session 0)** | Not in the zip — and now actually excluded: `build_portable.py`'s `should_skip()` gains a directory rule, because its runtime-artifact test only covers the top level of `app/` and `os.walk` would otherwise pack the GGUF (Q27). `install.ps1` moves `app/models/` and `app/config.json` aside around its `Remove-Item -Recurse`, so a reinstall no longer destroys either. Downloaded from Hugging Face on first Concierge open, with a progress bar; resumable; **the pinned SHA-256 above is the authority and the HF tree API's LFS `oid` is compared against it *before* the download** (FR-CG-7, Q26). Dictation keeps working throughout |
| VRAM residency | Slider 0–30 minutes since last message; **0 = unload when the chat panel closes**; default 5. Unload kills the `llama-server` process |
| First run **(rev. session 0)** | Prompt once (opt-in card, 5b). Declining leaves a `Concierge…` entry in Settings and the tray menu. Opt-in state is a **tri-state `opt_in` key** — `unset` / `accepted` / `declined` — separate from `enabled` (Q26): only three values can distinguish "never asked" from "said no" from "said yes, currently switched off", and a pre-v3 `config.json` upgraded in place must arrive `unset` rather than silently opted in |
| Proactivity | None. The agent reads `debug_log.txt` only when asked |
| No CUDA | Concierge disabled with a visible reason, same pattern and wording style as the Model tab's GPU radio (criterion 7) |
| Reasoning | Disabled (`-rea off`): the model answers directly with no hidden deliberation. No thinking UI — the former `▸ thinking · N s` toggle is removed (design §10 Q7); a future reasoning-qualified model re-earns it through the §6 record |
| Sessions | Fresh each open — no prior transcripts in context. Nameable, saved transcripts (last 20) for rereading; a ≤ ~1k-token memory note persists across sessions, user-editable (design §5.1) |
| Name | "Concierge" everywhere user-facing |

### 1.1 The model pin's provenance (verified 2026-08-26)

A candidate review asked whether the pin predated a July 2026 Gemma 4 update and was
therefore aiming the prompt iteration at superseded weights. **It does not.** The pin is
the post-update artefact, and the check is recorded here so the next person answers it by
reading rather than by four API calls.

| | |
|---|---|
| `lmstudio-community/gemma-4-12B-it-GGUF` **current `main`** LFS oid | `95d83ba3…73f8`, 7 381 382 944 B |
| the pin in `fetch.py` / the table above | `95d83ba3…73f8`, 7 381 382 944 B |
| `sha256sum` of the GGUF in `spike/models/` | `95d83ba3…73f8` |

Three-way match. The history behind it, which is the part that matters:

- **2026-07-15** — upstream `google/gemma-4-12b-it` merged a chat-template fix (#35:
  "null handling, reasoning preservation, turn-tag balance, input validation"), whose
  description includes restoring model turns after **tool responses** and preventing
  extra turn tags.
- **2026-07-20** — upstream added `response_template` to `tokenizer_config.json` (#43),
  and `lmstudio-community` re-uploaded the Q4_K_M GGUF the same day. That upload did
  change the file: the previous revision was `e4db6f8c…bc8c` at 7 381 384 864 B, so the
  bytes moved by 1 920 — a difference far too small to be a re-quantisation of 12B
  weights, and consistent with a template baked into GGUF metadata. *(That last clause is
  inference from the size delta, not something the commit says.)*
- **2026-08-25** — the spike downloaded the file, five weeks after that upload, and
  pinned what it got.

**Consequences.** No re-pin. C7a does not need re-running on pin grounds — it ran against
this exact blob. Session 2's native-mode measurements (13/14 on the reply/tool boundary)
were taken on the **post-fix** chat template, as were spike C2's, so neither is stale, and
the two are comparable with each other. And the tool-calling half of the July fix touches
the chat template, which is the path `tool_mode: native` uses and the one grammar mode
bypasses entirely — which is consistent with session 2's finding that grammar-mode
over-calling did not move with the prompt, though nothing here *demonstrates* that link:
doing so would need the June GGUF and an A/B, and neither is worth the 7.4 GB.

## 2. Architecture

```
qt_window ── QSplitter ── ConciergePanel (chat view, input, header)
                              │  QueuedConnection signals (tokens, tool events, state)
                        ConciergeWorker (QThread)
                              │  agent loop: prompt → SSE stream → tool calls → repeat
                        llama-server (QProcess, localhost, OpenAI-compatible /v1)
                              │
                        Gemma 4 12B Q4 GGUF (downloaded to models dir)
```

- **The agent loop is hand-written**, ~200 lines in `app/ptt/concierge/agent.py`. No
  LangChain-class framework: one system prompt, one tool schema, a while loop that
  forwards tool results until the model stops calling tools. `CON-3` (no new heavyweight
  runtime deps) stands — the only additions are `llama-server.exe` as a binary asset and
  the GGUF download.
- **Threading (rev. session 0).** The rule this extends is **v2.0 acceptance criterion 9**
  (`ptt-v2-gui/gui_handoff.md` §10) — *not* v3 criterion 9, which is "all ten v2.0 criteria
  re-pass"; the v3 thread audit is criterion **10**. Always qualify which document a
  criterion number belongs to. Three amendments from review §7:
  - v2's rule names *the engine thread*; the rule that generalises is **no UI object is
    touched from any thread other than the GUI thread.**
  - The genuinely new hazard runs the *other* way — worker-thread **writes**. `set_config`
    must not call `InstantApplyPanel.apply_now`, which is a QWidget method. It calls
    `Settings.set()` (design §4.6), then emits a settings-changed signal that the Qt
    adapter receives on the GUI thread and turns into the existing broadcast
    (`qt_app._on_settings_changed` → `qt_window.refresh_panels()` → `tray.refresh_menu()`).
    That hop is where FR-CG-2's "banner, tabs and status bar reflect the change without
    restart" is won or lost.
  - `Settings.save()` gains a **third** writer. `design.md` §7's field discipline —
    whole-value rebinds, never an in-place mutation of a tuple, list or dict already on the
    object — now binds tool code too, and `set_config("vocabulary", …)` and
    `set_config("benchmarks", …)` are exactly where an in-place mutation is the natural
    implementation. L1 pins it.
  - `THREAD-CHECK` logs **once per signal type per session**, at first emission, matching
    `qt_tray.py`'s existing pattern (Q26). The token signal fires ~30/s into the same
    `debug_log.txt` that `read_log` reads and Diagnostics tails every 1.5 s.
- **Process hygiene (rev. session 0):** containment is a **Windows job object** with
  `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (design §8.1, Q10) — the kernel kills the child on
  any parent death, including `TerminateProcess`, which no Python code can intercept and
  which `install.ps1` performs before every reinstall. `llama-server` is additionally
  killed on idle timeout and on panel close when the slider is 0. Startup reaps a
  leftover orphan via `app/concierge_state.json` + a `/props` `model_alias` check (Q11),
  never by reading another process's command line.

## 3. Knowledge, not code-reading

**Superseded by `concierge_design.md` §5.05 (Q20).** The paragraph that stood here asked
for a build step that both *distilled* 108,770 characters into ~8k tokens and was *never
hand-edited* — which cannot both be true, and which the spike's `pack.py` resolved by
byte-truncating a concatenation mid-sentence.

The agent still never browses source at runtime. `build_knowledge_pack.py`, invoked by
`build_portable.py`, now produces `app/assets/concierge_kb.md` from two sources:

- **Part 1, generated** from `config.py`'s `FIELDS` table (design §4.6), whose docstrings
  already carry the "what it does, when to change it, what can go wrong" structure this
  section asked for. It cannot drift, because it *is* the code.
- **Part 2, hand-written**: `docs/ptt-v3-concierge/concierge_narrative.md`, ~2–3k tokens — what the app is
  for, the pre-roll and Alt-menu stories, and **the Concierge's own controls**, which no
  earlier corpus contained, so the pack could not answer questions about the chat panel.
  It records each source document's `{path, size, sha256}` in its front matter and an L1
  test fails, naming the file, when a digest changes.

The build step **errors** on a missing or unreadable source rather than skipping it —
`pack.py` silently listed a `docs/validation.md` that has never existed, and
`gui_handoff.md` has since moved to `docs/ptt-v2-gui/`, which a skip would have swallowed.

## 4. Tools

Every tool is a thin wrapper over existing seams. **`set_config` routes through
`Settings.set()`** — the validated write path built in session 1 from the `FIELDS` table
(design §4.6). The claim that stood here, that it "routes through the existing `Settings`
validation", was not true of the code: validation lives inside `load()`, a write is bare
`setattr`, and a bad value would have been accepted, saved, and reverted at the next start
— which is the "reported as success" outcome FR-CG-11 forbids.

**Every result is capped at 16 KiB at fetch time**, with truncation stated in the returned
JSON (design §4.4, Q16). The cap is uniform: `read_log` is not the only unbounded tool.

| Tool | Wraps | Notes **(rev. session 0)** |
|---|---|---|
| `get_config()` | `Settings` read | Full current config — including every benchmark entry and vocabulary rule, hence the cap |
| `set_config(key, value)` | **`Settings.set()`** + instant apply | Rejects with a reason rather than accepting and reverting. Records inverse for Undo. Emits a settings-changed signal the Qt adapter turns into the panel refresh — it must **not** call `apply_now`, which is a QWidget method (§2) |
| `get_state()` | `UiState`, by injection | Returns the key list `tools.py` **declares**; the Qt adapter fills it. The harness may not import `UiState` — its module imports PySide6 at column 0 (CON-CG-6). L1 asserts the tool emits the declared keys; a Qt-side test asserts the adapter supplies them (Q26) |
| `list_audio_devices()` | `audio.py` enumeration | 14 devices produced a 1210-character line on the reference machine |
| `list_models()` | Model tab's table source | Includes measured latencies |
| `run_benchmark(model)` | The Measure button path | **Progress is emitted by the harness as tool activity, not generated by the model** (Q23), so the LLM stays idle and the measurement is clean — C5 measured a 1.46× Whisper penalty during decode and none at all while resident-idle. The cached entry records `llm_resident` beside `{seconds, at, clip}` |
| `read_log(tail_lines, include_previous=True)` | `debug_log.txt` **and `debug_log.prev.txt`** | Read-only. Both files, labelled, sharing one 16 KiB budget, current first (Q21). `OBS-4` rotates at every startup precisely so a crash log survives the restart — and the user asking for a diagnosis has almost always restarted |
| `update_memory(text)` | memory note file | Replaces the note; capped ~1k tokens; user-viewable and editable. **Records its inverse in the undo journal and keeps one `memory_note.prev.txt`** (Q22, FR-CG-3) |

**Scope exclusions are enforced by the `FIELDS` allowlist, not by prose.** "Editing
vocabulary rules" is out of scope, and `set_config("vocabulary", …)` reaches it unless the
registry says otherwise. Also out of scope for v3.0: launching recordings, any tool that
injects keystrokes.

## 5. Undo

- Each `set_config` **and each `update_memory`** appends `{key, old, new, ts}` to the
  session's change list; the chat renders a chip per change (`use_gpu: false → true ·
  Undo`). Undo replays the inverse through `Settings.set()`.
- **Session restore (rev. session 0, Q24):** `↺ session` replays the journal's inverses in
  reverse order, touching **only keys the agent wrote**. The earlier design — snapshot the
  whole config on panel open, write it back wholesale — also reverted changes the *user*
  made by hand in the panels while the chat was open, behind a confirm dialog that said
  nothing about it. FR-CG-3's "all of a session's changes" sits in a sentence about
  Concierge-made changes. Reverse-order replay is well defined when several entries touch
  one key, so the ordering objection T5 item 3 raised does not apply.
- Two confirmations exist in the feature, not one: `↺ session`, and `Delete model`
  (permitted by `gui_handoff.md` §6's "deleting a vocabulary rule or a downloaded model").

## 6. Config additions

```json
"concierge": {
  "opt_in": "unset",             // unset | accepted | declined  (Q26)
  "enabled": true,               // the switch, once accepted
  "model": "gemma-4-12b-q4_k_m",
  "tool_mode": "grammar",        // native | grammar; set by the §6 record at gate 2.5 (Q15)
  "idle_unload_minutes": 5,      // 0 = unload when chat closes; max 30
  "history_limit": 20
}
```

`port` is **gone** (Q13): it is pre-bound in Python at every launch and recorded in
`app/concierge_state.json`, not configured. Every key above is a `FIELDS` entry, so each
is validated on write as well as on load (design §4.6).

Unknown-key round-tripping (criterion 8) must keep passing against a pre-v3 build.

Two sibling files live beside `config.json`, neither of them configuration:
`concierge_state.json` (`{pid, create_time, port}`, deleted on clean shutdown — design
§8.1) and `concierge_key` (the per-launch API key — design §2). Both are per-machine
runtime artifacts and both belong in `build_portable.py`'s `RUNTIME_ARTIFACTS` set
alongside `config.json` and `debug_log.txt`.

## 7. Panel spec (5a)

- Header: `CONCIERGE` + tag `Gemma 4 12B · local` + `↺ session` + close.
- **`Delete model (6.87 GB)` lives here, not on Advanced (Q25).** The download already
  lives in this panel (§8), so one surface owns the model's whole lifecycle and the state
  machine that connects the two. The Advanced tab keeps its documented invariant — a
  read-only readout that never calls `apply_now` (`gui_handoff.md` §6.5, `advanced.py`'s
  class docstring, `V-UI-12`) — which design §10 Q4's original placement would have broken.
- Transcript: user bubbles right (accent-100 fill, accent-300 hairline), agent bubbles
  left (bg fill, divider hairline), tool activity as a muted mono line (`Concierge is
  reading design.md §4…`), change chips as accent-outlined rows. (No thinking row:
  reasoning is disabled — §1.)
- Input: single-line grows to 4; Enter sends; footer legend `Runs locally · no account ·
  changes are undoable`.
- Status bar gains one segment: `Concierge: <model> resident · unloads after N min idle`
  (or `unloads on close`, or absent when not downloaded/disabled).
- Panel width 360 px default, splitter-resizable 300–520; collapse via the `Concierge ▸`
  button at the right end of the tab strip.
- All styling QSS from the existing theme; bubbles are square-cornered; no new colors.
- The panel shows `loading` until the knowledge-pack prefix is warm (design §5/§8):
  `ready` means the first message will be fast.

## 8. First run and download (5b)

1. First app launch after upgrade shows the opt-in card once. Decline = nothing ever
   again except the menu entries.
2. Accept (or first later open) starts the GGUF download with a progress bar in the
   panel; resumable; dictation unaffected. **The pinned SHA-256 (§1) is the authority**;
   the HF tree API's LFS `oid` is fetched first and compared against it, so a re-uploaded
   file is refused with a clear message rather than silently accepted (FR-CG-7, Q26). A
   refusal is a re-qualification event, not something the user can click past.
3. When ready, the Concierge greets with the guided setup: microphone check → hotkey
   choice → model choice with an offer to run the benchmark against
   `benchmark_sample.wav` → done. Each step is ordinary conversation backed by the same
   tools, not a separate wizard UI.

## 9. Acceptance criteria

Moved to `concierge_verification.md` §3, where they belong in the document
architecture (verification, not design).

## 10. Out of scope for v3.0

24 GB+ model tier (config key reserved); proactive log monitoring; voice-sample
vocabulary tailoring; per-application awareness; any cloud fallback; UI highlighting
from chat ("point at the control") — a candidate for v3.1 once the panel is stable.
