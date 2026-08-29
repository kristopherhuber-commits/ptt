# Stage 0 review — reading the v3 Concierge design against the code and the spike

Session 0 output. **No code was written.** Everything below was checked against the
working tree at `971a573` plus the untracked `spike/` directory left behind by the
2026-08-25 run, and against `docs/ptt-v3-concierge/`, `docs/design.md`,
`docs/requirements.md`, `docs/verification.md`, `docs/development_history.md`.

Read this before session 1. The seven sections answer the seven questions the session-0
prompt asks, in order. Nothing here proposes a feature: every item either closes a
requirement already stated, names a contradiction between two documents already written,
or names an unverified claim and the one check that would settle it.

Sections **1.1**, **1.2**, **5** and **6.3** are the ones that change what session 1
builds. Section **6.3** is the one that decides whether gate 2.5 can mean anything.

---

## 0. Facts established by measurement, not recall

Recorded so no later session re-derives them, and so nothing below rests on a guess.

| Claim | How it was checked | Result |
|---|---|---|
| `build_portable.py` would ship a GGUF placed at `app/models/concierge/` | read `should_skip()` and the `os.walk` loop | **Yes, it ships.** The runtime-artifact exclusion fires only when `root == "app"` — the top level. Anything nested under `app/` is packed unconditionally. `design.md` §2 records this same property as the reason `app/assets/` needs no allowlist entry. |
| `install.ps1` preserves a model directory across a reinstall | read lines 30–50 | **No.** `Remove-Item -Path $TargetDir -Recurse -Force` deletes the whole install tree before copying. `V-M-74` confirms the outcome: "zero files predating the install". |
| The installer produces the crash-orphan condition | `install.ps1` line 33 | **Yes.** `Stop-Process -Name "ptt_dictate" -Force` — a `TerminateProcess`, which runs no Python. |
| `Settings` has a validated **write** path | read `config.py` and `ui/panels/__init__.py` | **No.** Validation (`_load_bool`, `_load_audio_device`, `_load_benchmarks`, the `model` check) lives inside `load()`. `InstantApplyPanel.apply_now` is `setattr` → `save()` → optional reload. Nothing validates a write. |
| `_load_bool` accepts the string `"false"` | `config.py:230–243` | **No** — it rejects by type and falls back to the default, logging why. Spike C2's exemplary call `set_config {"key":"use_gpu","value":"false"}` sends a **string**. |
| `UiState` can be imported by a Qt-free harness | read `ui/qt_statusview.py` imports | **No.** It is a plain dataclass, but its module imports `PySide6.QtCore/QtGui/QtWidgets` at column 0. `get_state()` must take a duck-typed seam, never the type. |
| A panel-refresh broadcast already exists | `qt_app.py:196,211` → `qt_window.refresh_panels()` → `panel.refresh()` | **Yes**, and it is GUI-thread-only, triggered today by `InstantApplyPanel.saved`. See §7.3. |
| The live `debug_log.txt` line-length distribution | `awk` over `app/debug_log.txt` | 92 lines, **mean 99.6 chars, max 1210 chars** (a device-enumeration line). |
| The log contains dictated text | `grep` over `app/debug_log.txt` | **Yes.** `Transcription finished in 0.57s. Result: '…'` — the full transcript of every utterance. |
| `read_log` can see the log from a crashed session | `logging_setup.py:46`, `paths.previous_debug_log_path()` | **No.** The log is rotated to `debug_log.prev.txt` at every startup (`OBS-4`). `read_log` as specified wraps `debug_log.txt` only. |
| `--jinja` is enabled by default in the pinned build | `llama-server.exe --help` | **Confirmed.** `--jinja, --no-jinja … (default: enabled)`. Also confirmed present: `-rea/--reasoning [on\|off\|auto]`, `--reasoning-budget`, `--slot-save-path` (default: disabled), `-cram/--cache-ram` (default 8192 MiB), `--api-key`, `-np/--parallel` (default −1 = auto), `--cache-reuse`, `-sps/--slot-prompt-similarity`, `--warmup/--no-warmup`. |
| `--port 0` yields a discoverable ephemeral port | `--help` text only | **Unverified.** Help documents `--port PORT  port to listen (default: 8080)` and says nothing about 0. The spike ran a **fixed** 8080. `concierge_handoff.md` §6 ships `"port": 0`. See §2.2. |
| The spike's grammar and tool-call checks ran behind the knowledge pack | `grep "import pack"` over `spike/checks/` | **No.** Only `c3_kv_cache.py` and `c4_latency.py` import `pack`. **C1 and C2 ran on a 2–3 sentence system prompt with three tools.** |
| The spike's schemas constrained tool arguments | read `c1_grammar.py`, `c2_native_tools.py` | **No.** Both C1 schemas use `"arguments": {"type": "object"}` — unconstrained. C2's `set_config` types every `value` as `string`. |
| The spike's validator checks `maxLength` | `grep` over `common.py` | **No.** It implements `const`, `enum`, `minLength` — there is no `maxLength` branch. |
| The spike's knowledge pack was distilled | read `pack.py` | **No.** It concatenates `README.md`, `design.md`, `requirements.md`, `verification.md`, `validation.md`, `development_history.md` in that order, then binary-searches a **byte offset** and cuts: `body[:cut]`. `docs/validation.md` has never existed and is skipped silently by `if p.exists()`. |
| Size of the corpus `concierge_handoff.md` §3 names | `wc -c` | requirements 8 788 + design 30 642 + gui_handoff 55 237 + development_history 14 103 = **108 770 chars**. |
| `gui_handoff.md` is still at the path §3 names | `git status`, filesystem | **No.** `docs/gui_handoff/` → `docs/ptt-v2-gui/` in this working tree. |
| The Advanced panel writes anything | `ui/panels/advanced.py:94–99` | **No.** Class docstring: "A read-only list … and never calls `apply_now`." |
| A confirmation dialog for model deletion is permitted | `gui_handoff.md` §6 | **Yes** — "No confirmation dialogs except for deleting a vocabulary rule or a downloaded model". |
| v2's criterion 9 is the threading one | `gui_handoff.md` §10 item 9 | **Yes** — "No UI object is touched from the engine thread." v3's thread audit is `concierge_verification.md` §3 criterion **10**. |

---

## 1. Things I judge to be bad ideas

### 1.1 The GGUF's location collides with two shipped mechanisms — design §10 Q4

Q4 puts one GGUF at `app/models/concierge/` "inside the portable folder — travels with
the install, no hidden cache". Both halves of that sentence are false against the code
that exists.

**It ships in the zip.** `build_portable.py`'s `should_skip()` excludes runtime artifacts
with `if item == "app" and root == "app" and filename.lower() in RUNTIME_ARTIFACTS` — a
top-level test. `os.walk` then packs everything nested under `app/` unconditionally.
`design.md` §2 states this property approvingly ("`app/assets/` … Shipped by
`build_portable.py`'s `os.walk` over `app/`, which is why nothing here needs an entry in
`items_to_zip`"). For a 6.87 GB weights file the same property violates **CON-CG-4**,
**FR-CG-7** and `concierge_handoff.md` §1 outright. Session 5's prompt asks the
implementer to *verify* the GGUF is absent from the zip; nothing anywhere asks anyone to
*make* it absent.

**It does not travel with the install.** `install.ps1` deletes `$TargetDir` recursively
before copying (`V-M-74`: "zero files predating the install"). Every reinstall or upgrade
therefore destroys the model and forces a 6.87 GB re-download over a link the user has
already paid for once. Q4's stated rationale is the opposite of the observed behaviour.

Both are one-line fixes in files nobody has been asked to touch. Neither is a design
change; both are consequences of Q4 that Q4 does not acknowledge.

### 1.2 `set_config(key, value)` cannot do what `concierge_handoff.md` §4 says it does

§4: "`set_config` routes through the existing `Settings` validation — a hallucinated value
gets the same logged fallback as a hand-edited `config.json`, and the write is refused
(and reported back to the model) if validation rejects it."

There is no such path. `Settings` validation lives inside `config.load()`. A write is
`setattr(self._settings, field, value)` followed by `save()` (`ui/panels/__init__.py`).
Taken literally, "the same logged fallback as a hand-edited `config.json`" means: the bad
value is **accepted**, written to disk, and reverted at the *next* application start with
a log line. That is exactly the shape **FR-CG-11** forbids — a rejection reported as a
success — and it is invisible until the user restarts.

This is not hypothetical. The single most-cited evidence in the spike is
`set_config {"key":"use_gpu","value":"false"}`, recorded as a clean call in C2's 30/30.
The value is the **string** `"false"`. `config.py:230` exists specifically to reject it:
*"A bare truthiness test accepts the string `false` as True, which is how a hand-edited
`config.json` silently turns a safety default off."* The spike's tool schema types every
`value` as `{"type": "string"}`, so every boolean and every integer the Concierge writes
arrives in the wrong type.

Two consequences for session 1:

- **A validated write API has to exist**, in `config.py`, returning accept/reject with a
  reason. FR-CG-11 is not closable without it, and it is new code in a module the v3 docs
  never mention touching.
- **Scope exclusions written as prose are unenforceable.** `concierge_requirements.md` §5
  and `concierge_handoff.md` §4 exclude "editing vocabulary rules"; `set_config("vocabulary", …)`
  reaches them. The exclusion has to be a key allowlist in the registry.

### 1.3 `read_log` is capped in the wrong unit, reads the wrong file, and its injection surface is understated

**Wrong unit.** `concierge_handoff.md` §4 caps at 400 *lines*. Lines are not a token
bound. Measured on this repo's live log: mean 99.6 chars/line, max 1210. 400 lines is
≈10k tokens at the mean and ≈121k tokens at the observed maximum — the latter four times
the entire 32k window, from one tool call. Arithmetic in §4 below.

**Wrong file.** `OBS-4` rotates `debug_log.txt` to `debug_log.prev.txt` at every startup,
and states the reason: *"a crash log must survive the restart that follows it."*
**FR-CG-5** and validation item 2 describe a user who has just had a problem and is now
asking about it — after a restart, in the overwhelming case. The log they want is the one
`read_log` cannot reach.

**Understated injection surface.** `harness_deep_dive.md` T10 says `read_log` "returns
text an *outside application* influenced (window titles land in the log)". The log
actually contains the full text of every transcription. Anything the user has dictated —
including a document read aloud — is model input the moment `read_log` runs. Nothing
leaves the machine (**FR-CG-10** holds), but the adversarial class in §6 should seed
dictated content, not just window titles, and `update_memory` following a `read_log` is
the path by which injected text becomes *persistent*.

### 1.4 The memory note is the only durable state and has no undo

`update_memory(text)` "Replaces the note" (`concierge_handoff.md` §4). **FR-CG-3**'s undo
journal and session snapshot cover `set_config` only. `concierge_design.md` §5.1 forbids
prior transcripts as context, so the note is the *sole* cross-session memory. One bad call
— and the model writes this file unsupervised, autonomously, by design — erases every
accumulated fact with no chip, no snapshot and no confirmation. The asymmetry with
`set_config` (which gets two independent undo mechanisms) is not argued anywhere.

### 1.5 The memory note's position in the prefix silently breaks NFR-CG-1

§5's budget lists the prefix as one block: "pack ~8k + system rules ~1k + tools/schema ~1k
+ memory note ≤1k". The KV cache is a **prefix** cache. C3 measured what happens when a
prefix changes: 8.10 s for a different pack, and 1.53 s even on the *return* to pack A
because of partial eviction. If the mutable note sits inside that block, then every
`update_memory` call invalidates ~10k tokens of cached prefix and the **next** message
pays several seconds with no visible cause — an NFR-CG-1 breach triggered by the agent's
own housekeeping, appearing at random.

Ordering is therefore load-bearing and is stated nowhere: everything immutable first, the
mutable note last. The same class of cost applies to §5's trimming ("trimmed oldest-first")
— every trim invalidates the cache from the trim point onward. §5 presents trimming purely
as a budget mechanism and never mentions that it is also a latency event.

### 1.6 `run_benchmark` writes a contended measurement into a cache the UI presents as truth

`design.md` §7: `benchmarks` is keyed by model **and** device and stamped with the sample
clip's digest, *"so re-recording `benchmark_sample.wav` invalidates the old numbers instead
of leaving them on screen looking comparable."* The one condition it does not record is
whether an LLM was resident.

Spike C5 measured a **1.46×** Whisper penalty during active LLM decode. A
Concierge-invoked benchmark runs while the Concierge is generating the surrounding
conversation — that is what "streams progress into chat" means. The number written to
`config.json` is therefore systematically inflated and indistinguishable, in the Model
tab, from a clean one. `V-UI-09`/`V-UI-10` pin the key and the formatting; nothing pins
the conditions. The design took care to prevent exactly this class of stale-comparison bug
for the clip and left the door open for the LLM.

### 1.7 NFR-CG-3's wording excludes the one flow the requirements mandate

`spike_results.md` says it plainly: *"NFR-CG-3 — PASS as written, and the wording is
load-bearing."* The requirement names the model **resident**; the 1.46× penalty occurs
only during decode.

But **FR-CG-4** is a guided setup whose first step is a *microphone check* — the user
dictates while the model generates and while the panel streams tokens. The single flow the
requirements make mandatory is the single case the NFR is worded to exclude. The measured
absolute figure (a 10 s utterance moving 0.77 s → ~1.14 s) stays inside **NFR-1**, so the
answer is not a new number; the answer is that the docs should stop implying the case is
covered. As written, a reader concludes dictation is unaffected by the Concierge, and
during the one conversation every new user is guaranteed to have, it is.

### 1.8 The document set is declared READY with the system prompt unwritten

`claude_code_prompt_v3.md` opens **"READY."** `harness_deep_dive.md` marks T3, T4, T5, T6,
T7 and T10 open. T3 is the system prompt, and the deep dive says of it: *"The prompt is
harness code in the same sense the grammar is"*, with an exit criterion of
*"`system_prompt.md` drafted, reviewed by user, added to design as §4.5."* There is no §4.5
in `concierge_design.md`. Session 1's prompt never mentions the system prompt; session 4's
says the guided setup is "driven by the system prompt (FR-CG-4)".

The consequence lands on gate 2.5. §6's thresholds are scored against explanation quality,
tool selection, refusal handling and adversarial resistance — every one of which is
dominated by the prompt. Qualifying three candidate models against an unwritten,
unversioned prompt measures the prompt, not the models, and **NFR-CG-6**'s claim that a
model is "qualified by evidence, not by reputation" does not survive it. T4 (the 40
scenarios) has the same dependency and the same status.

The other open items are smaller but real: **there is no bound on the duration of a turn
anywhere.** Six iterations at 30.1 tok/s with a `maxLength`-bounded reply each is minutes.
T6 lists "a generation exceeds a hard timeout" as an open discussion; no number exists in
any document.

### 1.9 `Delete model` on the Advanced tab contradicts that panel's invariant

design §10 Q4 puts `Delete model (6.87 GB)` on the Advanced tab. `gui_handoff.md` §6.5,
`design.md` §4's module table and `advanced.py`'s own class docstring all make that panel a
read-only readout that *"never calls `apply_now`"*; `V-UI-12` is the test that keeps it
one. The confirmation dialog itself is fine — `gui_handoff.md` §6 already exempts
"deleting … a downloaded model" — but `concierge_handoff.md` §5 calls the session restore
*"the one confirmation in the feature"*, which is then two. Small, but it is the kind of
inconsistency that gets resolved by whichever document the implementer read last.

---

## 2. Where two reasonable implementers would build differently

Each of these is a genuine fork the documents leave open, not a preference. Ordered by
how expensive the wrong choice is to reverse.

### 2.1 `QProcess` or `subprocess` — the documents say both

`concierge_handoff.md` §1 and §2 say llama-server runs "as a `QProcess`".
`concierge_design.md` §2 puts `server.py` inside the Qt-free harness, and **CON-CG-6** is
enforced by an L1 test that imports every harness module with Qt absent. Both cannot hold.

The fork is not cosmetic. `QProcess` brings `finished`/`errorOccurred` signals and thread
affinity — it must be created and read on the thread that owns it — which decides where
the health poll and the idle timer live. `subprocess` needs its own reaper thread and its
own health loop but keeps `server.py` testable at L1 exactly as §7 requires. **Settle this
in the design, not in session 1's first hour.**

### 2.2 The ephemeral port, which nothing has verified

`concierge_handoff.md` §6 ships `"port": 0  // 0 = ephemeral`. The spike ran a fixed 8080.
`--help` documents only `--port PORT  port to listen (default: 8080)`.

Implementer A passes `--port 0` and parses the listen line or `/props` for the assigned
port — which requires that llama-server both accepts 0 and reports the result
machine-readably. Implementer B pre-binds a socket in Python, reads the port, closes it and
passes the number, accepting a small race. Implementer C fixes a port and handles
collision. These have different failure modes on a machine that already runs an LLM server.

**What settles it:** start the pinned binary once with `--port 0` and read the listen line
and `/props`. Two minutes, and it belongs in session 1 before `server.py` is written.

### 2.3 Slots — `-np` auto or `-np 1`

`-np/--parallel` defaults to −1 (auto) and gave the spike **4 slots**. C3 observed the
consequence directly: pack A's prefix was partially evicted by pack B, and the return to
pack A cost 1.53 s and re-processed 517 tokens instead of ~50. Any concurrent request — a
prewarm overlapping a user message, a health poll, a cancelled generation retried — can
land on a second slot and re-pay the pack in full.

One implementer pins `-np 1` and serialises, making §5's determinism argument literally
true. Another leaves auto and treats the occasional 7 s turn as noise. §5's case for
full-context over RAG rests on *"the same question meets the same knowledge every time"*,
which argues for the first; nothing in the documents decides it. `-sps/--slot-prompt-similarity`
and `--cache-reuse` are the other two dials in this area and are mentioned nowhere.

### 2.4 Which `tool_mode` ships as the default

§4.2 makes native "this model's qualification-record default"; §4.1's grammar path is the
conformance reference and the thing L1 tests. So the shipped default exercises the path L1
does *not* pin, and the path L1 pins is the one no user ever runs. Implementer A ships
native and treats grammar as a test-only fallback. Implementer B ships grammar for a single
code path in production. §6 makes the mode a per-model record entry, so the harness must
support both either way — but which one `config.json` names by default is unstated.

### 2.5 The hash policy — three documents, two positions

**FR-CG-7:** *"The hash's source of truth is the Hugging Face … LFS `oid` … no hard-coded
digest."* `concierge_handoff.md` §1 pins `95d83ba3…`. `harness_deep_dive.md` T8: *"pin
exactly in design §6 and verify the SHA in `fetch.py`."*

These are not equivalent controls. The API `oid` detects **corruption** — a truncated or
mangled download. A pinned digest additionally detects **substitution** — the repo being
re-uploaded, or the API being served something else. T8's update policy ("a new GGUF is a
re-qualification, never a silent bump") only works with a pin, because without one a
changed upstream file verifies cleanly against its own new `oid` and qualification is
silently invalidated. Pin *and* cross-check is the coherent reading; the documents need to
say one thing.

### 2.6 The session snapshot's scope

`concierge_handoff.md` §5: *"on panel open, the full config is copied; the header's
`↺ session` restores it wholesale."* That also reverts every change the **user** made by
hand in the panels while the chat was open. T5 item 3 considers ordering and calls it moot;
it does not consider authorship. The control is labelled "session"; the behaviour is
"everything since the panel opened, whoever did it". Implementer A restores only keys the
agent's journal touched. Implementer B implements §5 as written. Under **FR-CG-3** the
first is arguably what "restores all of a session's changes" means.

### 2.7 What `get_state()` returns

`concierge_handoff.md` §4 says "The nine banner fields". `UiState` is a **seven**-field
dataclass (`state, status_text, hotkey, model, device, microphone, last`) plus a derived
`detail()`; the nine are *displayed rows* (`V-M-56`). And its module imports PySide6 at
column 0, so the harness may not import the type at all — the seam has to be duck-typed
and the CLI rig's fake has to reproduce the shape from a second, independent definition
that can drift from the first.

### 2.8 `enabled: true` cannot express "declined"

`concierge_handoff.md` §6 defaults `concierge.enabled` to `true`; §8 says decline means
"nothing ever again except the menu entries"; **FR-CG-6** makes the whole feature opt-in.
A pre-v3 `config.json` upgraded in place gets `enabled: true` by default, i.e. opted in.
Implementer A overloads `enabled`. Implementer B adds a tri-state (`unset | accepted |
declined`), which is the only shape that can distinguish "never asked" from "said no". The
opt-in card's "shown once" behaviour needs the second.

### 2.9 Where the §6 settings whitelist comes from

The explanation class scores "no invented settings … (checked against a settings
whitelist)". Derived from `config.py`'s field set, it cannot drift. Hand-listed in
`scenarios.yaml`, it drifts the first time a field is added and starts scoring real
settings as inventions. The project already has the pattern that prevents this —
`V-UI-12` fails when the Advanced table drifts from the live constants.

### 2.10 The unauthenticated loopback endpoint

The design opens an HTTP server on `127.0.0.1` and never mentions access control.
`--api-key` exists in the pinned build. Any process on the machine — and, depending on the
build's CORS behaviour, script in a page the user has open — can reach an ephemeral-port
`/v1/chat/completions` and consume the GPU. One implementer generates a per-launch key;
another does not. **FR-CG-10** is written as "makes no network connection", which is true
and does not cover "accepts one".

---

## 3. Is grammar-from-the-tool-registry implementable exactly as design §4.1 states?

**Not exactly as stated — because the schema §4.1 writes down is not a schema a registry
can generate.** Everything the design actually needs is inside llama.cpp's demonstrated
capability; what is missing is that nobody has generated or measured the real schema. Three
specific gaps, in descending order of consequence.

### 3.1 §4.1's prose and §4.1's code block describe different schemas

The block in §4.1 is flat: `action`, `tool {name, arguments}`, `reply`. The prose beside it
says the tool-name enum and *"per-tool argument schemas are generated from the tool
registry"*. A flat object cannot express "arguments must match the schema selected by
`tool.name`" — that is a dependency between sibling properties, and JSON Schema expresses
it only through a discriminated union (`oneOf` + `const`) or `if/then/else`.

`spike/checks/c1_grammar.py` shows what the flat version costs. `FLAT_SCHEMA` requires all
three of `action`, `tool`, `reply` and types arguments as `{"type": "object"}` — no
constraint at all. To make it coherent the spike's system prompt had to instruct: *"When
action is `reply`, leave tool's name as `get_state` and arguments empty."* That is a
convention held up by prompt text, in a design whose first principle (§1) is calls that are
*"structurally impossible to malform"*.

So the **10/10 headline attaches to the schema a registry cannot produce**, and the 9/10 —
the only run that produced unparseable output — attaches to the union, whose own comment in
the spike source reads *"The shape a real registry would generate"*.

### 3.2 Nobody has exercised a real registry's shape

Both C1 schemas carried **three** tool names and **zero** argument constraints. The
registry has **eight** tools (`concierge_handoff.md` §4), and the constraints that matter
are per-key: `key` drawn from the settings enum (C2 declared this for native mode), and
`value` typed *per key* — `use_gpu` boolean, `model` ∈ `transcribe.MODEL_NAMES`, `hotkey`
an array of key names, `idle_unload_minutes` an integer 0–30. Expressing "the type of
`value` depends on `key`" requires a **second, nested** discriminated union inside the
first.

The shipped grammar is therefore a two-level union with roughly eight top-level branches
and a per-key fan-out below `set_config` — a GBNF whose size, conversion fidelity and
effect on decode speed nobody has measured. That is the gap in *generation*, and it is
precisely where a converter limitation or a grammar-size cliff would appear. §1.2 above is
the other end of the same rope: without typed values the harness hands `"false"` to a
boolean field.

**What settles it:** generate the real schema from the eight-tool registry, POST it once to
the pinned build, and re-run C1's ten prompts against it. That is a session-1 L2-adjacent
check, and it is cheap.

### 3.3 The `maxLength` mitigation is unverified twice over

§4.1's own resolution of the truncation caveat is *"the generated schema therefore puts a
`maxLength` on `reply`"*. That word appears in no spike schema, **and** the spike's
validator (`common.py`) implements `const`, `enum` and `minLength` with no `maxLength`
branch — so a run that violated it would have scored PASS regardless. Whether llama.cpp's
JSON-Schema→GBNF converter honours `maxLength` on a string, or silently drops it, decides
whether the mitigation exists at all.

**What settles it:** one request to the pinned build with `maxLength: 40` on `reply` and a
prompt that wants a long answer. If the reply stops at 40 characters the constraint is real
at the sampler. If it runs past, the converter ignored it and the only truncation defence
is the `finish_reason == "length"` trigger — which is fine, but then §4.1 should say so
instead of naming a mitigation that does not fire.

### 3.4 Two riders worth recording

- **In native mode the §4.1 guarantee is not the one in force.** Native sends `tools` and
  no `response_format`; whether the build derives a grammar from the tool schemas under
  `--jinja` is a property of that build's chat-format handling, not of this design, and
  C2's 30/30 is consistent with either answer. If native ships as the default (§4.2), the
  guarantee the design sells rests on the fallback path — which will be the less-exercised
  of the two. Worth verifying which is true, because **CON-CG-5**'s whole model-agnosticism
  argument depends on the fallback being the real floor.
- **C1 and C2 ran without the knowledge pack.** Neither imports `pack`. Both used a 2–3
  sentence system prompt and three tools. §6 cites C2's 19/20 as *"a data point for §6's
  ≥ 95 % threshold"* — it is a data point about a different prompt, a different context
  length and a different tool count than the shipping configuration. Both variables move
  the result and both move it in the harder direction.

**Verdict:** implementable, yes — the mechanism is confirmed and `oneOf` + `const`
converted cleanly. As *stated*, no: §4.1 must be rewritten so its code block is the shape
its prose describes, and the real generated schema must be measured before its 10/10 can be
claimed for it.

---

## 4. Does the §5 context budget survive the worst cases?

§5's 32k arithmetic: pack 8k + rules 1k + tools/schema 1k + note ≤1k + generation headroom
4k = **15k fixed**, leaving **~17k for history**.

### 4.1 A 400-line `read_log` — no, not as specified

| Case | Chars | ≈ tokens (4 chars/token) | vs the ~17k history allowance |
|---|---:|---:|---|
| 400 lines at the observed mean (99.6) | 39 840 | **≈10 000** | **59 % of the allowance, in one result** |
| 400 lines at the observed max (1210) | 484 000 | **≈121 000** | **≈4× the entire 32k window** |

The mean case is not comfortable and the max case is not hypothetical — the 1210-char line
in the live log is a routine device enumeration written at every startup. §4.3 permits six
tool iterations per user message; two `read_log` calls in one turn ("show me more") exceed
the allowance at the mean alone.

Three structural problems behind the number:

1. **The cap is in the wrong unit.** Lines do not bound tokens. It has to be bytes or
   tokens, and `concierge_handoff.md` §4 and §5 specify neither.
2. **The cap is applied in the wrong place.** §5 says trimming drops "tool-result bodies
   … before dialogue" — but that trim runs when assembling the *next* request, after the
   oversized result has already been produced and, in a streaming panel, already shown. The
   bound has to be enforced in `tools.py` at fetch time. §5's trimming rule cannot save a
   turn whose single tool result is larger than the window.
3. **What llama-server does with an over-length request is undefined here and untestable at
   L1.** `--context-shift` and `--cache-reuse` both exist in the pinned build and neither is
   mentioned. A fake HTTP layer cannot tell the harness what the real server does when
   `n_ctx` is exceeded, so this needs one real check, not a unit test.

`read_log` is also the only tool §5 treats as unbounded, and it is not the only unbounded
one. `get_config()` returns the *full* config — including `benchmarks` (one entry per
model×device with timestamps) and every vocabulary rule. `list_audio_devices()` on this
machine produced a 1210-char line for 14 devices. `list_models()` "includes measured
latencies". Any of these can be large on a real machine; none has a stated cap.

### 4.2 A long guided-setup dialogue — survives on a small machine, with two caveats

The shape is better: **FR-CG-4**'s four steps are short exchanges. But

1. **The setup flow is the tool-result-heavy one.** `list_audio_devices` +
   `list_models` + `get_config` + `run_benchmark` progress, plus the dialogue, on a
   machine with many input devices and a populated vocabulary, is the realistic path to
   17k — and it is the *first* conversation every user has.
2. **Trimming the oldest turns first is worst for exactly this dialogue.** Guided setup is
   a conversation whose later steps refer to its earlier ones ("use the mic we picked").
   Oldest-first drops the microphone choice and keeps the model discussion. §5 states the
   order in a single clause; `harness_deep_dive.md` T2 proposes a three-rule version
   (drop tool-result bodies older than 2 turns keeping one-line summaries; then oldest
   dialogue pairs; never the pack, the note or the current turn) whose exit criterion is
   *"trimming order written into §5 as a numbered rule the L1 suite can pin"*. That never
   happened. Session 1 is asked to unit-test a rule that exists only as a proposal in a
   working document.
3. **Every trim is also a latency event** — see §1.5. §5 presents trimming purely as a
   budget mechanism.

**Verdict:** the guided-setup case survives with headroom on the reference machine and is
not obviously safe on a machine with many devices; the `read_log` case does not survive as
specified, and the fix is a byte/token cap enforced at fetch time plus a numbered trimming
rule in §5.

---

## 5. Does the process-hygiene plan close FR-CG-9, including the crash-orphan case?

**The clean-exit half closes. The crash half does not, and the reap mechanism is
unspecified exactly where it is hard.**

### 5.1 Clean exit — closes, but "the FR-9 path" is four paths

`concierge_handoff.md` §2 kills the server "unconditionally on app exit before
`os._exit(0)` (the FR-9 path)". `runtime.py` has **four** `os._exit` call sites: normal
return, `KeyboardInterrupt`, `SystemExit`, and the unhandled-exception handler.
`os._exit` runs no `atexit`, no `finally` and no destructor, so a kill hooked to any of
those never fires. Which of the four gets the kill — and that it must be all four, one of
which *is* a crash — needs to be written down. `design.md` §4 already makes `runtime.py`
the sole owner of `os._exit` precisely because "a rule with no owner gets duplicated".

### 5.2 Crash exit — not closed by the plan, and the requirement says more than the plan does

**FR-CG-9:** *"No Concierge process (`llama-server`) survives application exit, including
crash exit; startup reaps orphans from prior crashes."* The two clauses are different
claims, and only the second is implemented.

Under `TerminateProcess` — Task Manager "End task", `Stop-Process -Force`, a Windows Error
Reporting kill — no Python runs at all. A reap at next startup means the orphan **does**
survive the exit, holding ~9.4 GB of VRAM (C5), until the user next launches the app. That
may be days. Acceptance criterion 7 (*"repeat with a simulated crash → startup reaps the
orphan"*) tests the mitigation, and would pass green while the first clause of FR-CG-9
remains unmet.

This is not a theoretical failure mode in this project: **`install.ps1` line 33 runs
`Stop-Process -Name "ptt_dictate" -Force` before every reinstall.** The shipped installer
manufactures the orphan case, and then (§1.1) deletes the model the orphan is serving.

The OS mechanism that actually closes the first clause is a Windows **Job Object** with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`: the kernel terminates the child when the last handle
to the job closes, which includes the parent being terminated by anything. It is absent
from every v3 document. I raise it as the known mechanism for a requirement already
written, not as a feature — the alternative is to amend FR-CG-9 to promise only what the
reap delivers.

### 5.3 The reap itself is the least-specified item on the startup path

The design says orphans are *"identified by a `--alias ptt-concierge` argument"* and never
says how another process's command line is read on Windows. There is no stdlib call for it.
The options are:

- `Win32_Process.CommandLine` via CIM/WMI — correct, but reached from a Qt-free harness it
  means spawning PowerShell, hundreds of milliseconds on the path to the `loading` state.
- `NtQueryInformationProcess` + PEB reads through `ctypes` — no subprocess, but
  bitness-sensitive, privilege-sensitive and undocumented.
- `wmic` — deprecated, and not guaranteed present on current Windows 11 builds. **Verify on
  26200 rather than assume**, in either direction.

The cheaper design the documents do not consider is a state file beside `config.json`
recording `{pid, process-create-time, port}`, with the alias as a confirmation check before
killing. The create-time is what makes PID reuse safe. Either way it needs specifying
before `server.py` is written, because it decides whether the reap is a fast local read or
a subprocess spawn inside `loading`.

Two further holes in the same area:

- **Elevation.** The app normally runs elevated (`FR-C5`; `install.ps1` sets the
  run-as-administrator byte on both shortcuts). An orphan from an elevated run cannot be
  opened by a non-elevated one, and vice versa. The reap must state which case it covers
  and log audibly when it cannot — a silent failed reap is `OBS-1`'s exact prohibition.
- **`--alias` is being asked to carry an identity guarantee it was not designed for.** It
  is the model name in `/v1/models`. Using it as a process marker works because it lands in
  the command line; that it is *load-bearing* for FR-CG-9 should be stated, so nobody
  "tidies" it into `--model-alias` or drops it later.

---

## 6. Can the knowledge pack drift from its source docs, and what prevents it?

**Yes, five independent ways, and at present nothing prevents any of them.** Item 6.3 is
the important one.

### 6.1 A moved or missing source is skipped silently

The spike's `pack.py` does `if p.exists(): …`. It lists `docs/validation.md`, which has
never existed (`verification.md`'s header says so explicitly). A vanished source produces a
*smaller pack*, not an error.

The case is live right now. `concierge_handoff.md` §3 names `gui_handoff.md`; the file has
moved to `docs/ptt-v2-gui/gui_handoff.md` in this working tree. A build step written to §3
today silently drops **55 237 characters — over half the named corpus** — and ships a pack
that answers UI questions from nothing. Nobody would notice until a user asked.

### 6.2 The three documents name three different corpora

| Document | Sources |
|---|---|
| `concierge_handoff.md` §3 | requirements, design, gui_handoff, development_history |
| `concierge_design.md` §5 | "The four docs distil to a ~8k-token pack" |
| `spike/checks/pack.py` | README, design, requirements, verification, **validation** (nonexistent), development_history |

Since **NFR-CG-1/2**, §5's budget and §5's "revisit trigger at ~16k tokens" are all pinned
to pack *size*, the corpus needs to be one list, in one place, that a test can read.

### 6.3 The pack cannot be both a build artifact and a distillation — and no pack content exists yet

§3 says the step *"distills … into one file, ~8k tokens, organized per settings panel with
a 'what each setting does, when to change it, what can go wrong' structure"* and that it is
*"a build artifact: regenerated on every build, never hand-edited"*. Those two sentences
cannot both be true.

The four named docs are **108 770 characters**, roughly 27k tokens by the crude 4-chars/token
measure. Reaching 8k is a **≈3.4:1 distillation**, and reorganising by settings panel is
authoring. A deterministic script can concatenate and truncate — which is exactly what the
spike did: `body[:cut]`, a binary search for a **byte offset**, cutting mid-sentence — but
it cannot distil. So either

- the pack is **hand-authored**, "never hand-edited" is false, and drift is continuous and
  unmanaged; or
- the pack is **mechanical**, "organized per settings panel" is false, and FR-CG-1's
  groundedness rests on a mid-sentence truncation of `design.md`.

And here is the part that matters most for gate 2.5: **no pack content has ever been
produced or measured.** C3 and C4 needed only a token count, so a truncated concatenation
was entirely adequate for them, and `spike_results.md` says so in a sentence that is true
of size and false of content: *"close enough to §5's '~8k-token pack' to be the real thing
rather than a stand-in."* At any plausible ratio (3.5–4.5 chars/token) 7987 tokens is
28–36k characters — README.md (15 365) plus most or all of design.md (30 642). In
`SOURCES` order, `requirements.md`, `verification.md` and `development_history.md` are at
or past the cut and are likely **absent entirely** from the pack the spike measured.

Everything §6 proposes to score at gate 2.5 — explanation accuracy, "≥ 90 % of required
facts", "zero invented settings", log diagnosis, adversarial resistance — is a function of
pack content. Together with §1.8 (no system prompt), the two inputs that dominate the
qualification result are the two that do not yet exist. **Gate 2.5 cannot discriminate
between models until they do**, and its scorecard would attribute the pack's and the
prompt's deficiencies to whichever GGUF was loaded.

### 6.4 The pack cannot answer questions about the Concierge

**FR-CG-1**: *"any setting, control, or behavior of the application."* The residency
slider, the `concierge.*` config keys, the memory note, the Undo chips, the model download
and the no-CUDA disable are documented only in `concierge_handoff.md` and
`concierge_requirements.md` — neither of which is in any of the three candidate corpora.
The first questions a user asks a new chat panel are about the chat panel.

### 6.5 Nothing fails when a source changes

No manifest, no digest of the sources recorded in the pack, no test that the pack is
current, no test that it fits the §5 budget, and no test that the §6 settings whitelist
matches `config.py`'s field set.

**What would prevent drift** — four checks, no features, each with an existing pattern in
this repo to copy:

| Check | Existing pattern |
|---|---|
| The pack records `{path, size, sha256}` for every source; an L1 test fails when a source's digest differs from the recorded one | `V-UI-12` — the Advanced table fails when it drifts from the live constants |
| An L1 test fails when the pack exceeds the §5 budget, and separately when it exceeds the ~16k revisit trigger | `V-CF-*` — schema pinned by test, not by prose |
| The build step **errors** on a missing or unreadable source instead of skipping it | `OBS-1` — "every step that can fail silently must log its outcome" |
| The §6 settings whitelist is derived from `config.py`, not listed by hand | `V-HK-01` — every derived table comes from the one declarative source |

---

## 7. Is the threading extension (criterion 9) unambiguous for the new signals?

**No.** Six reasons; the first is bookkeeping, the rest are substantive.

### 7.1 "Criterion 9" now names two different criteria

v2's criterion 9 (`gui_handoff.md` §10) is *"No UI object is touched from the engine
thread."* v3's `concierge_verification.md` §3 criterion **10** is the thread audit; its
criterion **9** is *"All ten v2.0 acceptance criteria re-pass."* `concierge_handoff.md` §2
says "Threading is criterion 9 unchanged", meaning v2's.

The collision is already causing work: `concierge_verification.md` §4 has to write
*"criterion 6 needs the same no-CUDA machine criterion 7 (v2.0) still waits for"* to
disambiguate the same overlap for the no-CUDA case. Session 3's prompt inherits the
ambiguity. Every reference needs a document qualifier.

### 7.2 The v2 rule names *the engine thread*, not "any non-GUI thread"

Read literally, criterion 9 says nothing about a Concierge worker. The rule that
generalises is "no UI object is touched from any thread other than the GUI thread", and
that is not what is written. `design.md` §4.1 rule 1 states the mechanism correctly but
also frames it around `Engine.run()`.

### 7.3 The genuinely new hazard runs the other way, and is unaddressed

v2's rule is one-directional: engine → GUI, state *out*. The Concierge introduces **writes
originating on a worker thread**. `set_config` must write `Settings`, `save()`, request an
engine reload, and then make the GUI re-read — because **FR-CG-2** requires the banner,
tabs and status bar to reflect the change without a restart.

The re-read path exists and is GUI-thread-only: `qt_app._on_settings_changed` →
`qt_window.refresh_panels()` → each panel's `refresh()` → plus `tray.refresh_menu()`. It is
triggered today by `InstantApplyPanel.saved`, a signal emitted by a **QWidget**. So:

- The tool **must not** call `apply_now` — it is a QWidget method — yet
  `concierge_handoff.md` §4 says `set_config` *"emits the same reload requests the panels
  emit"*, which is the closest the documents come to describing this and describes the
  wrong object.
- Which object emits the settings-changed notification from the worker side, and over which
  queued connection, is unstated. **This is the exact seam where FR-CG-2 is won or lost**,
  and it is not in any diagram.

### 7.4 `Settings.save()` gains a third writer, and the field discipline is not extended

`design.md` §7 documents two writers (GUI thread on every control; engine thread on CUDA
fallback) and is careful that the lock guards the **file**, never a field. A third writer is
fine for the lock. What is not fine is that the field discipline — *"writes are whole-value
rebinds … never `settings.hotkey.append(...)`"*, and the same rule spelled out for
`benchmarks` and `vocabulary` in the `Settings` docstring — now has to be honoured by tool
code the docstring has never heard of. `set_config("vocabulary", …)` and
`set_config("benchmarks", …)` are precisely the two keys where an in-place mutation is the
natural implementation, and the engine reads both without a lock. This deserves an explicit
line in the design and an L1 test, not just a docstring in a module the harness may not
even import.

### 7.5 THREAD-CHECK's granularity is undefined for a streaming signal

The existing pattern (`qt_tray.py:329`) logs one line per side, once. v3 criterion 10 says
*"every new signal `QueuedConnection`; THREAD-CHECK shows distinct thread identities across
the hop"* — singular hop, and no granularity. The token signal fires per token: 30/s,
thousands per session. One-line-per-signal-type-per-session and one-line-per-emission
differ by three orders of magnitude in `debug_log.txt` size — the same file `read_log`
reads (§1.3, §4.1) and the Diagnostics panel tails every 1.5 s. One sentence in the design
settles it; there is currently none.

### 7.6 There is more than one hop now, and the criterion describes one

At minimum: worker → GUI (tokens, tool events, state), GUI → worker (send, cancel, stop),
harness idle timer → GUI (state change on unload), and — if `server.py` uses `subprocess` —
a server-reader thread → worker. "Distinct thread identities across the hop" needs to say
which hops are audited. §2.1's `QProcess`/`subprocess` fork changes the answer.

---

## 8. Decisions needed before session 1 starts — **all resolved 2026-08-25**

Every item below was taken to the user in session 0 and decided. The decisions are logged
canonically in `concierge_design.md` §10 as **Q8–Q27**, and the affected sections of the
requirements, design, handoff, verification seed and Develop document have been amended.
§10 of this document maps each finding to the decision that closed it.

Ranked. The first four block session 1; the rest block gate 2.5 or session 5.

1. **`QProcess` or `subprocess`** (§2.1). The documents say both, and it decides where the
   idle timer, the health poll and the server-reader live.
2. **Whether `set_config` gets a validated write path in `config.py`** (§1.2). Without it
   FR-CG-11 is not closable and the tool writes strings into typed fields.
3. **The orphan-identification mechanism** (§5.3), and whether FR-CG-9's first clause is
   implemented (job object) or amended to promise what the reap delivers (§5.2).
4. **The `read_log` cap in bytes or tokens, enforced at fetch time**, plus caps on
   `get_config`, `list_audio_devices` and `list_models` (§4.1); and §5's trimming order
   written as a numbered rule (§4.2).
5. **Rewrite §4.1's schema block as the discriminated union its prose describes**, then run
   the two cheap checks: the real eight-tool registry schema against the pinned build, and
   `maxLength` honoured-or-ignored (§3.1–§3.3).
6. **Run the `--port 0` check** before `server.py` is written (§2.2), and decide `-np` (§2.3).
7. **One corpus list for the pack, an error-not-skip build step, and the four drift checks**
   (§6.5) — and a decision on whether the pack is authored or mechanical (§6.3).
8. **T3 and T4 close before gate 2.5** (§1.8, §6.3), or the gate's scorecard measures the
   prompt and the pack rather than the model.
9. **The GGUF's location, the zip exclusion and the installer's preservation of it** (§1.1).
10. **The hash policy — pin, API `oid`, or both** (§2.5), stated once.

---

## 9. What I did not do

- **No code was written, and nothing was run against a model.** The only thing executed was
  `llama-server.exe --help` on the pinned build in `spike/`, to check flag names and
  defaults rather than recall them. No server was started; no GGUF was loaded.
- **I did not re-run the spike or re-derive its measurements.** C1–C5's numbers are taken as
  reported. What I checked is what the spike's *scripts* actually did, because several
  claims in `spike_results.md` are about the scripts rather than about llama.cpp.
- **I did not review the UI half** (`concierge_handoff.md` §7's panel spec, mockups 5a/5b)
  beyond the four places it contradicts the software design or the existing panels
  (§1.9, §2.6, §2.7, §7.3).
- **I did not review `model_qualification.md`'s format** — the file does not exist yet.
- **I did not evaluate model choice.** §6 is right that it is an experiment; my objection
  (§1.8, §6.3) is to running the experiment before the instrument exists, not to the
  candidates.
- **I proposed no features.** Where I name a mechanism that is not in the documents — a job
  object, a state file, a digest manifest — it is because a requirement already written
  cannot otherwise be met, and in each case amending the requirement is stated as the
  alternative.

---

## 10. Resolutions — what each finding became

Decided 2026-08-25 in session 0, one question at a time. Canonical log:
`concierge_design.md` §10, Q8–Q27. **This section is the map; the design document is the
authority.** Nothing below was resolved by narrowing a requirement, and one finding
(§1.1) turned out to close a v2.0 defect as well.

| Finding | Decision | Where it now lives |
|---|---|---|
| §1.1 GGUF ships in the zip; installer deletes it | **Q27** Keep `app/models/concierge/`; directory rule in `should_skip()`, move-aside in `install.ps1` — which also preserves `app/config.json`, deleted on every reinstall today | design §10 Q4/Q27; handoff §1; prompt session 1 |
| §1.2 `set_config` has no validated write path | **Q9** Declarative `FIELDS` table in `config.py`; `load()`, `Settings.set()` and the tool schema all derive from it | design §4.6 (D-CG-13); FR-CG-11; prompt session 1 (first task) |
| §1.3 `read_log` unit / file / injection surface | **Q16** 16 KiB cap at fetch time, uniform; **Q21** reads both `debug_log.txt` and `.prev.txt` | design §4.4; handoff §4; verification §4 |
| §1.4 memory note unprotected | **Q22** Undo journal + chip + one `.prev` copy | design §5.1; FR-CG-14 |
| §1.5 note position breaks the prefix cache | **Q16 rider** Mutable note last in the fixed block | design §5 |
| §1.6 contended benchmark poisons the cache | **Q23** Harness emits progress; the LLM stays idle; entry records `llm_resident` | handoff §4 |
| §1.7 NFR-CG-3 excludes the guided-setup case | **Q26** Requirement names both states, with both measured figures | NFR-CG-3 |
| §1.8 READY with T3/T4 open; no turn bound | **Q17** prompt is a versioned artifact, drafted s1, iterated s2, frozen and hashed at 2.5; **Q18** stall 30 s / turn 180 s / ready 60 s | design §4.5 (D-CG-12), §4.3 |
| §1.9 Delete on Advanced breaks its invariant | **Q25** Moves to the Concierge panel | handoff §7; design §10 Q4 |
| §2.1 `QProcess` vs `subprocess` | **Q8** `subprocess`, in the harness | design §2; handoff §1–§2 |
| §2.2 unverified ephemeral port | **Q13** Pre-bind in Python; `port` key removed from config | design §2; handoff §6 |
| §2.3 four slots, shared KV pool | **Q14** `-np 1`, requests serialised | design §2 |
| §2.4 shipped mode vs tested mode | **Q15** L1 pins both; shipped value from the §6 record | design §4.2 |
| §2.5 three documents, two hash policies | **Q26** Pin is the authority; API `oid` is a pre-download cross-check | FR-CG-7; handoff §1, §8 |
| §2.6 snapshot reverts the user's own edits | **Q24** Replay journalled keys in reverse order | handoff §5 |
| §2.7 `get_state` shape and the Qt import | **Q26** Harness declares the key list; adapter fills it; tests on both sides | handoff §4 |
| §2.8 `enabled` cannot express "declined" | **Q26** Tri-state `opt_in` key | handoff §1, §6 |
| §2.9 whitelist can drift | **Q20** Derived from `FIELDS` | design §5.05; prompt session 2 |
| §2.10 unauthenticated loopback listener | **Q19** Per-launch `--api-key-file` | design §2; FR-CG-10 |
| §3 schema cannot come from a registry | **Q12** Two-level discriminated union, `value` scalar-typed; two cheap checks owed in session 1 | design §4.1 |
| §4.1 / §4.2 budget and trimming | **Q16**, **Q16b** Fetch-time cap; five numbered trimming rules, rule 5 logs every trim | design §4.4, §5.0 |
| §5 FR-CG-9's crash clause; reap unspecified | **Q10** Job object as primary; **Q11** state file + `/props` alias confirmation as backstop | design §8.1; FR-CG-9 |
| §6 pack drift; no pack content exists | **Q20** Part 1 generated from `FIELDS`, part 2 a hand-written narrative; error-not-skip, digest manifest, budget test | design §5.05; handoff §3 |
| §7.1 "criterion 9" names two things | **Q26** Every reference written `v2-n` / `v3-n`; the rule stated as "any non-GUI thread" | verification §3; handoff §2 |
| §7.3 worker-thread writes unaddressed | **Q9 + Q26** `Settings.set()`, then a queued settings-changed signal into the existing `refresh_panels()` broadcast | handoff §2; prompt session 3 |
| §7.4 third writer, field discipline | Stated explicitly and L1-pinned | handoff §2 |
| §7.5 THREAD-CHECK granularity | **Q26** Once per signal type per session | handoff §2; verification v3-10 |

Two findings were **not** closed by a decision, deliberately, and moved to
`concierge_verification.md` §4's known-holes list instead: the three spike claims that
describe the spike's scripts rather than llama.cpp (§3.1, §3.3, §3.4), which session 1
settles with two measurements rather than a choice; and what llama-server does with an
over-length request (§4.1), which no L1 test can answer.
