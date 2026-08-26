# PTT Dictation v3.0 Concierge — Requirements

Requirements are hypotheses; validation (by the end user, not by us) is the attempt to
disprove them. Every FR/NFR below is written observably so that a person can execute it.
Traceability: each item is cited by `concierge_design.md` elements, which are in turn
cited by `concierge_verification.md` items. Anything not traceable here does not get built.

**ID legend.** `FR` functional requirement, `NFR` non-functional, `CON` constraint, `OBS`
observability (`../requirements.md` §5), `D` design element, `V` verification item.
**`CG` is the Concierge infix**, separating v3's IDs from v2's `FR-1`…`FR-9`. So
`FR-CG-11` reads "Concierge functional requirement 11". Recorded because no document
expanded it and session 0 had to infer it.

**Amended 2026-08-25 by session 0.** `stage0_review_v3.md` found several requirements
that named a mechanism the code does not have, or that two documents stated two ways.
The nineteen decisions taken in response are logged in `concierge_design.md` §10 (Q8–Q26);
the items they changed are marked **(rev. session 0)** below.

## 1. Functional requirements

| ID | Requirement |
|---|---|
| FR-CG-1 | Asked about any setting, control, or behavior of the application, the Concierge answers grounded in the project documentation, in plain language, without inventing settings that do not exist |
| FR-CG-2 | Told to change a setting ("use the medium model"), the Concierge applies it through the same validated `Settings` path the panels use, with instant apply; the UI (banner, tabs, status bar) reflects the change without restart |
| FR-CG-3 | Every Concierge-made change carries an inline Undo that restores the prior value; a per-session snapshot restores all of a session's changes at once (one confirm dialog) |
| FR-CG-4 | On first opt-in, the Concierge runs a guided setup conversation: microphone check → hotkey choice → model choice, with an offered benchmark against `benchmark_sample.wav` |
| FR-CG-5 | Asked to investigate a problem, the Concierge reads `debug_log.txt` (read-only, on request only — no proactive monitoring) and explains findings with the evidence quoted |
| FR-CG-6 | The Concierge is strictly optional: declined, disabled, or never downloaded, every v2.0 behavior and all ten v2.0 acceptance criteria are unaffected |
| FR-CG-7 **(rev. session 0)** | Model weights are not shipped in the distribution; they download on first use with visible progress, resumable across app restarts, hash-verified before first load. **The pinned SHA-256 in `concierge_handoff.md` §1 is the authority**; the Hugging Face `/api/models/{repo}/tree/main` LFS `oid` is fetched *before* the download and compared against it, so a re-uploaded file is refused rather than silently accepted. The two controls catch different failures — the post-download hash catches corruption, the pre-download `oid` comparison catches substitution — and only the pin makes "a new GGUF is a re-qualification, never a silent bump" enforceable. The runtime bundling step must resolve the llama.cpp release tag's `nightly-tag.txt` indirection to locate binary assets (spike setup findings 4–5); it is a **build-time** step and never runs in the shipped app |
| FR-CG-8 | The LLM unloads from VRAM per the residency setting: a 0–30 minute slider where 0 means unload when the chat panel closes; default 5 minutes after the last message |
| FR-CG-9 **(rev. session 0)** | No Concierge process (`llama-server`) survives application exit, including crash exit. **The primary mechanism is a Windows job object** with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`: the kernel terminates the child when the last handle closes, which covers every exit path — clean return, `os._exit` from any of `runtime.py`'s four call sites, `TerminateProcess`, Task Manager, `Stop-Process -Force`, and a hard crash. Startup additionally reaps an orphan left by a build predating the job object or by a failed assignment (extends FR-9) |
| FR-CG-10 **(rev. session 0)** | The Concierge makes no network connection except the model download; no telemetry, no cloud inference, no account. **The permitted set is enumerated**: `huggingface.co` (the tree API) and the LFS CDN host its download redirect names — nothing else, and the L1 socket monkeypatch asserts exactly that set. Binding a loopback listener is not a network connection; the endpoint is nonetheless key-protected (design §2) |
| FR-CG-11 **(rev. session 0)** | An invalid or hallucinated write is rejected **at the moment of writing**, logged, and reported in the chat as a rejection — never as success. This requires a validated write path that does not exist today: `config.py`'s rules live inside `load()`, so a bad value written through the current `setattr` path would be accepted, saved, and silently reverted at the next start — the forbidden shape. `Settings.set(key, value) -> (ok, reason)` is built in session 1 from the declarative `FIELDS` table (design §4.6), and every writer, including the existing panels, goes through it |
| FR-CG-12 | On a machine without a CUDA device the Concierge control is disabled with a visible reason (criterion 7 pattern); nothing attempts to start the runtime |
| FR-CG-13 | Each session starts fresh: context is the knowledge pack plus the memory note, never prior transcripts. Sessions can be named and saved; saved transcripts exist for the user to reread |
| FR-CG-14 **(rev. session 0)** | A persistent memory note (≤ ~1k tokens, stored beside `config.json`, viewable and editable by the user) carries durable facts across sessions; the agent updates it through a tool. **The note is covered by FR-CG-3** — "every Concierge-made change" is not "every setting change" — so `update_memory` records its inverse in the undo journal and renders a chip. Because the journal is session-scoped, every write additionally keeps exactly one previous version as `memory_note.prev.txt`, restorable from the panel: the `OBS-4` log-rotation idiom applied to the only durable state the Concierge has |

## 2. Non-functional requirements

Bracketed values were placeholders pending measurement; the 2026-08-25 spike
(`spike_results.md` C3–C5) measured them on the reference machine (RTX 3080 Ti Laptop,
16 GB). Where a bound is conditional on the §5 pack-cost resolution, both branches are
stated.

| ID | Requirement |
|---|---|
| NFR-CG-1 | Model resident and pack prefix warm: first token within [2 s] of send. Measured: 0.342 s median behind the cached 8k pack — 6× margin |
| NFR-CG-2 | Model cold load (reopen after unload): genuinely ready — pack prefix included — within [10 s] if prompt-cache persistence works (design §5 mini-spike), else within [15 s] under the prewarm fallback (measured 13.34 s), with the loading state visible throughout |
| NFR-CG-3 **(rev. session 0)** | Dictation latency stays within **NFR-1** both with the Concierge model **resident** and while it is **actively generating**. Measured (spike C5): resident-idle costs nothing measurable — all three idle-window dictations beat the baseline model's prediction; during active decode ×1.46, i.e. a 10 s utterance moving 0.77 s → ~1.14 s, well inside NFR-1's 2 s. Both states are named because FR-CG-4's guided setup has the user dictating *while the model generates*, and the earlier wording ("with the model resident") excluded exactly that case. Idle-window sample was n=3 — repeat both measurements in L3 |
| NFR-CG-4 | The default model plus Whisper large-v3-turbo plus desktop overhead fit a 16 GB card with headroom for the configured context window. Measured: 11 681 MiB peak, 4703 MiB free (29 %) with both models resident and the LLM generating |
| NFR-CG-5 | Groundedness: on the qualification suite (`concierge_verification.md` §3), the shipped model meets the pass thresholds for answer accuracy and tool-call correctness |
| NFR-CG-6 | A model qualification suite exists and is re-runnable, so that any candidate model (including a future 24 GB+ tier) is qualified by evidence, not by reputation |

## 3. Constraints

| ID | Constraint |
|---|---|
| CON-CG-1 | No subscription, no cloud, no account — ever |
| CON-CG-2 | Runtime is bundled `llama-server` (llama.cpp, MIT). No dependence on Ollama, LM Studio, or any separately installed product |
| CON-CG-3 | The agent loop is hand-written. No LangChain-class framework (extends CON-3) |
| CON-CG-4 | Weights never ship in the zip |
| CON-CG-5 | The harness is model-agnostic: OpenAI-compatible API plus grammar-constrained output. Swapping the GGUF requires no harness code change |
| CON-CG-6 | The harness core imports no Qt — it must run and be tested standalone (`concierge_design.md` §7) |

## 4. Validation — what the end user does

Only the user can validate. Each is observable:

1. Ask "what does the pre-roll buffer do?" — the answer matches `development_history.md` issue 6 in substance. (FR-CG-1)
2. Say "dictation feels slow, fix it" — the Concierge reads the log, explains, acts or recommends; any change shows an Undo chip that works. (FR-CG-2, 3, 5)
3. Decline the first-run prompt; use the app for a week — nothing about v2.0 changed. (FR-CG-6)
4. Watch Task Manager after closing the panel with the slider at 0 — VRAM returns; exit the app — no `llama-server.exe` remains. (FR-CG-8, 9)
5. Disconnect from the network after the model is downloaded — the Concierge works fully offline. (FR-CG-10)

## 5. Out of scope for v3.0

24 GB+ model tier (config key reserved; qualified later via NFR-CG-6's suite); proactive
log monitoring; voice-sample vocabulary tailoring; UI highlighting from chat; editing
vocabulary rules; any tool that injects keystrokes.
