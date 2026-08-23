# Stage 0 review — reading the code against the GUI handoff

Session 0 output. No code was written. Everything below was checked against the
tree at `1644655`, plus the untracked `app/assets/` and `docs/gui_handoff/`.

Read this before session 1. Sections 3 and 4 are the ones that change what gets
built; section 2 is the one that decides whether it should be built at all.

---

## 0. Facts established by measurement, not recall

Recorded here so no later session has to re-derive them, and so nothing below
rests on a guess.

| Claim | How it was checked | Result |
|---|---|---|
| PySide6-Essentials installs on this interpreter | `pip index versions`, `pip download`, wheel `METADATA` | **Yes.** `PySide6_Essentials 6.11.2`, wheel tag `cp310-abi3-win_amd64`, `Requires-Python: <3.15,>=3.10`, classifiers list 3.14 explicitly. The venv is CPython **3.14.7** win-amd64. Stable-ABI wheel, so it loads on 3.14 without a 3.14-specific build. |
| Qt needs a VC++ redistributable on the target PC | listed the wheel contents | **No.** The wheel ships `msvcp140.dll`, `msvcp140_1.dll`, `msvcp140_2.dll`, `concrt140.dll` and `vcruntime140*.dll` next to `PySide6/`. `build_portable.py` copies only `vcruntime140*` from base Python, which would not have been enough. This is why `NFR-6`/`NFR-7` survive. |
| The Windows platform plugin ships | listed the wheel contents | **Yes.** `PySide6/plugins/platforms/qwindows.dll`, plus `qdirect2d`, `qminimal`, `qoffscreen`, and `plugins/styles/qmodernwindowsstyle.dll`. |
| Distribution cost of the toolkit | wheel inspection | **76.9 MB compressed, 211.4 MB uncompressed, 2471 files**, plus `shiboken6` at 1.2 MB compressed. Current `ptt_dictate_dist.zip` is **1.35 GB**, so roughly +6%. "Essentials" still includes QtQuick/QML, Designer, VirtualKeyboard and a 20.6 MB `opengl32sw.dll`. |
| `QSystemTrayIcon` has a hover signal | Qt 6 docs | **No.** Exactly two signals: `activated` and `messageClicked`. The docs also state the icon receives a `QEvent::ToolTip` **only on X11**. Cursor polling is therefore the only option on Windows — see §5.1. |
| `QSystemTrayIcon.geometry()` is reliable | Qt docs + qtbase source | **Conditionally.** Returns `QRect()` when the icon is not visible. Relevant because `README.md` documents that the icon commonly lands behind the Windows 11 `^` overflow chevron. |
| The four tray-icon colours in handoff §4 match the code | compared every RGB triple in `tray.py::create_icon_image` | **Exact match**, all four fills and all four outlines: `#0d9488`/`#0f766e`, `#ef4444`/`#b91c1c`, `#f59e0b`/`#d97706`, `#3b82f6`/`#1d4ed8`. |
| The benchmark clip matches its recorder | `wave` header read | **Yes.** 16 kHz, mono, 16-bit, 480 000 frames = exactly 30.00 s, 960 044 bytes. |
| The four named font files are present | filesystem | **Yes**, all four. Font tree is 3.7 MB total. |
| The fonts and the handoff are "committed to the repo" | `git ls-files` | **No.** `app/assets/` and `docs/gui_handoff/` are both **untracked**. See §3.14. |
| `tests/` exists | filesystem | **No.** `pyproject.toml` declares `testpaths = ["tests"]` and the directory does not exist. `design.md` §10 step 2 has not been done. See §4.1. |

---

## 1. The threading constraint, in my own words

**The rule.** `Engine.run()` blocks whichever thread calls it, and every
`on_state` / `on_text` invocation happens on that same thread — the engine's
docstring calls this "single producer" and is explicit that it makes *no promise
that this is the UI thread*. Under Qt, `QApplication.exec()` owns the main
thread, so the engine will be on a worker thread. Qt's rule is that a `QObject`
belongs to the thread that created it, and its widgets and painting machinery may
only be touched from that thread. The engine's callback is therefore on the wrong
side of that line by construction, and the *only* legal thing it may do is emit a
signal on a `QObject` that lives on the GUI thread. Qt's signal delivery is
itself thread-safe: the emit takes a lock, copies the arguments into a
`QMetaCallEvent`, and posts it to the receiver's event queue; the GUI thread picks
it up on its next spin and runs the slot. That queue hop is the entire mechanism,
and everything that touches a widget must be on the far side of it.

**Why pystray got away with it and Qt will not.** `tray.py`'s docstring says so
outright: pystray's setters post to the icon's own message loop, so
`icon.icon = …` from the engine thread was already an implicit queue hop. Qt
offers no such courtesy for `QWidget`, `QPixmap`, `QIcon` or `QMenu`. There is no
runtime guard and no exception.

**What specifically goes wrong when it is violated.** Four distinct failures, none
of which reproduce on demand:

1. **Painting on two threads.** `QWidget` paint state, the backing-store surface
   and the style engine are unguarded. A `setText` racing a `paintEvent` yields
   half-drawn text, stale rectangles, or a widget that never repaints again.
   Windows' compositor hides a lot of this, which is worse, not better.
2. **`QPixmap` is a hard error, not a race.** `QPixmap` is backed by a platform
   surface and is documented as usable only on the GUI thread. Constructing one on
   the engine thread — exactly what converting the PIL icon inside `on_state`
   would do — is undefined behaviour that in practice aborts the process. This is
   the specific way session 1 would break.
3. **Event-loop corruption on the menu.** `tray.py::on_state` assigns
   `self._icon.menu = self._create_menu()` on *every* state change. Doing the Qt
   equivalent from the engine thread — destroying and replacing a `QMenu` — while
   the user has that menu open destroys a widget the GUI thread is currently
   dispatching events into. Use-after-free.
4. **Silent swallowing, which is the real danger.** `Engine._emit` wraps the
   callback in `try/except Exception` and merely writes the traceback to
   `debug_log.txt`. So a violation that raises a Python-level exception produces
   *no visible symptom at all* — the poll loop keeps running, dictation keeps
   working, and the UI just stops updating. And failures 1–3 are mostly **not**
   Python exceptions, so they never reach even that log line. This is the same
   shape as issue #11: the primary symptom of the bug is the absence of evidence.

**The consequence for the code.** `EngineBridge.on_state` must be one statement.
Not "mostly one statement" — no string formatting that could raise, no
`log_debug`, no reading a widget to decide what to emit. Format the string in the
slot, on the GUI thread. The handoff's snippet is correct as written and should
not be elaborated.

**One correction to the handoff's reasoning.** §3 says to connect "with
`Qt.QueuedConnection` (the default for cross-thread connections)". The default is
`Qt.AutoConnection`, which is *resolved at emit time* by comparing the emitting
thread against **the receiver object's thread affinity** — not against where
`connect()` was called. That distinction is what actually matters:
`AutoConnection` is correct and sufficient **provided `EngineBridge` and the tray
object are both constructed on the GUI thread**. If a later session ever moves the
bridge onto the engine thread, `AutoConnection` silently degrades to a direct call
and every failure above comes back. I would rather pass `Qt.QueuedConnection`
explicitly than rely on a correct default, precisely because the wrong version of
this is invisible.

---

## 2. The blocking issue: `CON-3` forbids what the handoff mandates

This is not a nit, and it is not resolvable inside the handoff.

`docs/requirements.md` §6:

> `CON-3` — GUI additions may only use `tkinter`. It is the sole toolkit present
> in the portable environment … so using it adds no dependency and no
> distribution size.

The handoff §2 mandates PySide6, which is a new dependency and 211 MB of
distribution size. It never mentions `CON-3`.

`CON-3` is load-bearing across four other places:

- `design.md` §1: `gui_toolkit: "tkinter (bundled; see CON-3)"`.
- `design.md` §4 module table: `app/ptt/ui/hotkey_dialog.py` — "tkinter capture window. **Not built yet — step 3.**"
- `design.md` §6 "The picker": "Tray menu item **Set Hotkey…** opens a small tkinter window (`CON-3`)", followed by a paragraph of reasoning about tkinter's `mainloop` threading.
- `design.md` §8: "`CON-3` forbids adding [pytest] to `requirements.txt`" — the constraint is already being used as an argument about the dependency list, not only about toolkits.

Handoff §12 says "Where this document and the code disagree, the code is right."
That rule does not reach this, because `CON-3` is not code — it is a stated
requirement, and requirements outrank both.

**What I need from you.** Amend `CON-3` explicitly before session 1, with the
reasoning written down. Draft:

> `CON-3` *(revised)* — The GUI is built on PySide6-Essentials. This was
> originally constrained to `tkinter` on the grounds that it adds no dependency
> and no distribution size; that trade was re-taken when the GUI grew from a
> single hotkey-capture dialog into three layers and six panels. Measured cost:
> +77 MB compressed on a 1.35 GB distribution. `NFR-6`/`NFR-7` are unaffected —
> the wheel bundles its own MSVC runtime and platform plugins, and the launcher is
> still the PSF-signed interpreter.

Do the same to `design.md` §1, §4 and §6 in the same pass. Otherwise session 1
lands code the project's own requirements document says is not allowed, and the
docs — which this project treats as its source of truth — become the thing nobody
trusts. Session 5 fixing them afterwards is five sessions too late.

---

## 3. Where the handoff contradicts the code as it actually exists

Ordered by how much work the contradiction adds.

### 3.1 The engine cannot supply two of the popover's five rows

Handoff §1 states the engine "**does not change** except where a section below
says so explicitly", and §6.2 is the only section naming an engine change. But
§5's popover — built in **session 2** — specifies:

| Row | What the code can supply today |
|---|---|
| `Microphone` — "active device name" | **Nothing.** `audio.Recorder.open_stream()` calls `sd.InputStream(samplerate=…, channels=1, dtype="float32", callback=…)` with no `device=` argument and never queries PortAudio for a name. No code path anywhere produces a device name. |
| `Last` — "duration + word count of the last transcription" | **Duration: nothing.** `engine.run()` computes `t1-t0` (lines 198–201) and passes it only to `log_debug`. It is never emitted. `on_text(text)` gives the text, so word count is derivable — but it arrives *before* the paste and carries no timing. |

So session 2 is asked to render two values that do not exist, using an engine it
is forbidden to modify. §6.3 defers device selection to session 4, which is
*after* the popover ships.

Either the engine gains a small "last result" report (duration, char count, device
name) — a real widening of the callback contract, which should be designed once
and deliberately — or session 2 ships those two rows as `—` and session 4 fills
them in. I recommend the latter, stated explicitly in the handoff.

### 3.2 The tray menu "keeps the current items", then lists two that do not exist

Handoff §4: "Right-click menu keeps the current items … Status (disabled), Hotkey
(disabled), Use GPU / Use CPU (checkable), **Settings…**, **Pause**, Exit."

`tray.py::_create_menu` builds: Status, Hotkey, separator, Use GPU (CUDA), Use
CPU, separator, Exit. There is no `Settings…` and no `Pause`.

`Settings…` is fine — it is new by design and opens layer 3. **`Pause` is not.**
There is no pause anywhere: `Engine`'s entire public API is `stop()` and
`request_model_reload()`. Nothing suspends the poll loop.

Worth noting, because it makes this cheap: **`Engine.__init__` already has the
seam.** It takes `chord_held=None`, stores `self._chord_held = chord_held or
hotkey_mod.chord_held`, and the loop calls `self._chord_held(self._settings.hotkey)`.
A frontend can pass `lambda chord: (not self._paused) and hotkey_mod.chord_held(chord)`
and get Pause with **zero engine changes** — the same seam `design.md` step 2's
tests were meant to use. That is the implementation to use if Pause is wanted, and
it should be written into the handoff rather than left to be reinvented as an
engine change.

Session 1's prompt says "the menu works" without saying which menu. Decide before
session 1 (§8).

### 3.3 The engine never emits an `error` state

Handoff §7's state table has a row `error paths`, and the mockup (turn 3a) renders
a literal `error` state chip. The engine emits exactly four state strings —
`loading`, `idle`, `recording`, `transcribing`. Both error paths emit **`idle`**:

- `engine.py:130` — `self._emit("idle", status_text)`, where `status_text` may be
  `"Error loading model"` from `load_model_with_fallback`.
- `engine.py:214` — `self._emit("idle", f"Error: {str(e)}")`.

The handoff half-notices this in §4 ("Two states have no icon today, because the
engine reports them as `idle` with a different status string") and then
contradicts itself in §7 by tabulating `error` as if it were a state. Any UI keyed
on `state` will never see it. The only available discriminator is sniffing
`status_text.startswith("Error")`, which is a string-matching contract nobody
wrote down.

Same problem, worse: §7's `No microphone` has **no state and no status text at
all**. `Recorder.open_stream()` catches the failure, writes `"ERROR: Failed to open
audio input stream: …"` to the log, and returns normally. The engine never learns.
The mockup shows a "No microphone found · device removed · pick another in Audio"
popover for a condition the application currently cannot detect.

### 3.4 "Derive the bindable key set from `hotkey.VK_MAP`" is not possible from `VK_MAP`

Handoff §6.1 and the session-3 prompt both insist: "Do not hard-code this list in
the panel — derive it from `hotkey.VK_MAP` so adding a key to the engine adds it to
the UI." And then name the list anyway: "the nine: `lctrl`, `rctrl`, `lshift`,
`rshift`, `lalt`, `ralt`, `lwin`, `rwin`, `space`."

`VK_MAP` has **thirteen** entries. The extra four are the unsided aliases `ctrl`,
`shift`, `alt`, `win`. `VK_MAP` carries no attribute distinguishing a physical
side-specific key from an alias. The only derivations available are:

- **by name prefix** (`k[0] in "lr"`, plus a special case for `space`) — which is
  hard-coding wearing a hat, and would also match a future key named `left`;
- **by VK value** — which fails outright, because `VK_MAP["win"] == VK_MAP["lwin"]
  == 0x5B` (§3.5).

And `space` is not side-specific, so the stated rule does not even describe the
stated list.

To honour the intent, `hotkey.py` needs a declarative table — a `BINDABLE_KEYS`
tuple, or a per-key record carrying `(vk, label, side, bindable)`. That is a change
to `hotkey.py`, which the handoff does not authorise and session 3's prompt does
not mention. Add it, or session 3 will either hard-code nine names (violating the
instruction) or invent a heuristic (violating the spirit).

### 3.5 `win` does not "match either side" — a pre-existing defect the GUI will expose

```
VK_MAP = { …, "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C, … }
```

`0x5B` is `VK_LWIN`. **Windows has no unsided Win virtual key.** `ctrl`, `shift`
and `alt` have real unsided VKs (`0x11`, `0x10`, `0x12`) that `GetAsyncKeyState`
reports for either side; `win` does not. So:

- `chord_held(("win",))` detects **Left Win only**, silently.
- `KEY_LABELS["win"] = "Win"` displays it as if unsided.
- `README.md` says "Unsided names (`ctrl`) match either side" and lists `win` among
  the valid names.
- `design.md` §6 says the same.

Both documents are wrong for this one key, and it is currently unreachable because
nobody has set `hotkey: ["win"]`. Handoff §6.1's **"Match either side" checkbox
makes it reachable**: tick it with Right Win bound and the panel writes `["win"]`,
the readout says "Win", and the hotkey silently stops responding to the key the
user is pressing. Fix `hotkey.py` before session 3 — either drop `"win"` from
`VK_MAP`, or have `chord_held` special-case it as "either `0x5B` or `0x5C` down" —
and correct both docs.

### 3.6 The Model panel is three code changes, not "one engine change"

Handoff §6.2: "Requires one engine change: `transcribe.MODEL_SIZE` is currently a
module constant." The constant is read in **three** places, and two of them are not
the obvious one:

1. `transcribe.resolve_model_path()` — takes no arguments and closes over the
   global. Must gain a parameter.
2. `transcribe.load_model_with_fallback()` lines 166–167 — the **CPU fallback path**
   re-reads `MODEL_SIZE` directly rather than reusing `model_path`. This is already
   a latent bug: if a local bundled model directory exists, the CUDA attempt uses
   the directory but the CPU fallback re-downloads by name.
3. `app/ptt_tray.py:46` — `log_debug(f"MODEL_SIZE: {transcribe.MODEL_SIZE}")`, one of
   the startup lines `OBS-3` covers, and one that `logging_setup.init`'s docstring
   says is diffed against a captured baseline.

`paths.local_model_dir(model_size)` already takes the name as a parameter, so that
one is fine.

### 3.7 Vocabulary substitution cannot go where the handoff says without editing the engine

Handoff §6.4: applied "**after** `transcribe.clean_text` and **before**
`inject.paste_text`".

`clean_text` is called *inside* `transcribe_audio` (line 196) — it is not a separate
step a frontend can wrap. The only point between the two named functions is inside
`engine.run()`:

```
199  text = transcribe.transcribe_audio(self._model, samples)
…
204      self._emit_text(text)
…
207      inject.paste_text(text)
```

So vocabulary substitution requires editing `engine.py`, which §1 forbids and §6.4
does not exempt. Also undefined: whether substitution runs before or after
`_emit_text`. If after, the console frontend prints one string and pastes a
different one — precisely the "the log said it worked" failure class `OBS-1` exists
to close.

Cleanest resolution that keeps the engine honest: put the substitution inside
`transcribe.transcribe_audio`, immediately after `clean_text`, taking the rules as a
parameter. `engine.py` then changes by one argument rather than gaining a new
concept. Decide it in the handoff, not in session 4.

### 3.8 The two Audio checkboxes are already Advanced-panel values

`IDLE_THRESHOLD_SEC` (240 s) and `MIN_RECORD_SEC` (0.30 s) appear in **§6.3 as
checkboxes** and in **§6.5 as read-mostly values**. They are durations. See §4.2 for
why the checkbox framing is worse than a mere duplicate.

### 3.9 `tray.py`: keep it or delete it

- §3 module layout: "`tray.py` # EXISTS (pystray). Replaced by `qt_tray.py`, **or
  kept for fallback**."
- §11 out of scope: "**delete** `app/ptt/ui/tray.py` … **Do not keep both.**"

Internally contradictory. §11 is clearly the intent; §3's table should be fixed.

Related, and sharper: session 1's prompt says "**Keep that drawing code** and convert
PIL to QPixmap" *and* "**Delete** `app/ptt/ui/tray.py`". `create_icon_image` lives in
`tray.py`. The instruction only makes sense as *move the function to `qt_tray.py`
verbatim, then delete the file*. Worth saying plainly so it is not read as "re-derive
the drawing".

### 3.10 `build_portable.py` already ships the assets — the handoff implies work that is not needed

Handoff §8: "make sure `build_portable.py` either ships the whole `assets/fonts` tree
or is told to include exactly these four files."

It already does. `items_to_zip` contains `"app"`, and the walk adds every file under
it, skipping only `__pycache__`, `.pytest_cache`, `*.old` and the three
`RUNTIME_ARTIFACTS` (`config.json`, `debug_log.txt`, `debug_log.prev.txt`). Fonts
(3.7 MB), both `OFL.txt` licences and `benchmark_sample.wav` (940 KB) all ship
automatically. **No change is required**, which is worth writing down so nobody
"fixes" it. The only judgement call is whether to prune the ~30 unused weights and
italics; at 3.7 MB against 1.35 GB I would leave them, since the `OFL.txt` condition
is easiest to satisfy by shipping the tree as downloaded.

### 3.11 The status-text list in §5 is missing the common case

§5 lists the engine's `status_text` values as `Ready (CUDA)`, `Ready (CPU Fallback)`,
`Recording...`, `Transcribing...`, `Loading Model...`, `Error loading model`.

`Engine._ready_text()` returns `f"Ready ({self.current_device.upper()})"`, so the
steady-state string after a deliberate CPU switch is **`Ready (CPU)`** — omitted from
the list, though acceptance criterion 6 uses it. `Ready (CPU Fallback)` only ever comes
from `load_model_with_fallback`'s fallback return, and only until the next
`_emit("idle", self._ready_text())` overwrites it with `Ready (CPU)` at the end of the
next transcription. So the popover's headline **changes on its own** after the first
dictation following a CUDA failure. Not a bug, but it will look like one, and the
mockup's sticky "Ready (CPU fallback) · CUDA load failed · saved use_gpu=false" panel
implies otherwise.

### 3.12 Safety-classifier rules disagree with `design.md` §6

`design.md` §6 specifies a classifier living in `hotkey.py` that "classifies a candidate
chord and returns warnings", with a five-row table. **That function does not exist** —
`hotkey.py` has `parse_chord`, `chord_label`, `chord_held`, and nothing else. It is
`design.md` §10 step 3, not done.

Handoff §6.1 respecifies the rules inline in the panel, differently:

| | `design.md` §6 | handoff §6.1 |
|---|---|---|
| printable / scrolling key | "types a character or scrolls the focused window while held" | only `space` named |
| any `alt` | warn | warn — same |
| layout switch | **exactly** `alt+shift` or `ctrl+shift` | "a **multi-key combination including a shift**" |
| lone common unsided modifier (`ctrl`, `shift`) | "will fire constantly during ordinary typing" | **absent** |
| result shape | a *list* of warnings | mutually exclusive, "otherwise → Safe" |

Two of these matter. The layout-switch rule is **wrong as broadened**: Windows' switch
is specifically `Alt+Shift` / `Ctrl+Shift`, so warning on `Win+Shift` or `Ctrl+Alt+Shift`
cries wolf and trains the user to ignore the box. And dropping the
lone-unsided-modifier warning removes the one rule that guards the exact configuration
the "Match either side" checkbox exists to produce (§3.5, §4.4).

Decide where the classifier lives. `design.md` says `hotkey.py`, and that is right — it
is pure, unit-testable, and `design.md` §8 already names it as test coverage. The panel
should call it, not reimplement it.

### 3.13 `paths.py` has no assets accessor, and `design.md` says only it may compute one

Handoff §8: "Resolve the path relative to the application directory via `paths`, not the
working directory." `paths.py` exposes `PACKAGE_DIR`, `APP_DIR`, `config_path()`,
`debug_log_path()`, `previous_debug_log_path()`, `local_model_dir()`. There is no
`assets_dir()` or `font_path()`.

`design.md` §4 constraint 3 makes this non-optional: "`paths.py` is the only module that
computes a directory." So session 2 must add one — a small change, but to a module with
an explicit ownership rule, so it should be named rather than improvised. The same
accessor serves `benchmark_sample.wav` in session 3 and `style.qss` if that is loaded
from disk.

### 3.14 "The TTFs are committed to the repo" — they are not

Handoff §8: "the TTFs are **committed to the repo** … **They are already in place**."
`git ls-files app/assets` returns nothing; `git status` shows `?? app/assets/` and
`?? docs/gui_handoff/`. The files exist on disk and are untracked, along with the entire
handoff directory and this review.

Consequence for **acceptance criterion 8**, which names `future_setting` in
`app/config.json` as the round-trip test case: `app/config.json` is in `.gitignore` and
is a per-machine runtime artifact. A fresh clone has neither the fonts nor that test
file. Commit `app/assets/` and `docs/gui_handoff/` before session 1, and move the
round-trip case into a fixture rather than depending on a gitignored file.

---

## 4. Things in the handoff I think are bad ideas

`CON-3` (§2) is the largest and is not repeated here.

### 4.1 Building four new validated `Settings` fields and a classifier before writing the tests that cover them

`design.md` §10 sequences: 1 split (done), **2 tests + probe harness**, 3 picker. The GUI
plan jumps straight over step 2. `design.md` §8 names exactly two test files:

- `test_hotkey.py` — chord parsing, unknown-name rejection, left/right resolution,
  labels, **and every row of the safety-classifier table**.
- `test_config.py` — round-trip, defaults, invalid-chord fallback, **preservation of
  unknown keys**, v0→v1 migration.

Sessions 3 and 4 then add `model` (validated), `audio_device` (validated, `None`
sentinel), `vocabulary` (list of objects, validated field by field) and `benchmarks`
(keyed by model and device, timestamped) — four new `config.py` fields — plus the safety
classifier in `hotkey.py`. That is precisely the surface those two files were specified
to cover.

Acceptance criterion 8 ("unknown keys survive a round trip") is **a unit test**, being
run by hand against a gitignored file at the end of a five-session project.

These tests are pure Python: no Qt, no audio device, no model, no Windows API. They are
cheap — an hour, not a session — and they turn "instant-apply writes `config.json` on
every checkbox" from a leap of faith into something checkable. **Recommendation: insert a
session 0.5** that does step 2 against the *current* schema, then let sessions 3 and 4
extend the tests as they extend the schema. The open question `design.md` §8 flags — where
pytest lives, since `build_portable.py` zips `.venv` wholesale — has a clean answer: a
separate `.venv-dev` from `requirements-dev.txt`, which never ships because `items_to_zip`
is an explicit allowlist.

### 4.2 The three Audio checkboxes are mistyped, and two of them can switch off a requirement

§6.3: "Three checkboxes: keep the stream warm while active (maps to `IDLE_THRESHOLD_SEC`),
ignore holds shorter than 0.30 s (maps to `MIN_RECORD_SEC`), and an optional
start-of-recording click."

`IDLE_THRESHOLD_SEC = 240.0` and `MIN_RECORD_SEC = 0.3` are **durations**. A checkbox has
no value to write. Unchecking them means writing what — zero?

- `IDLE_THRESHOLD_SEC = 0` → the engine closes the stream on every poll iteration where
  idle ≥ 0, i.e. constantly. A direct re-entry into **issue #6**: PortAudio open/close per
  press, ~1 s of hardware wake-up latency and an audible headset chime. `NFR-2` and
  `NFR-4` sit on opposite sides of this number; it is a *tuning* knob, not an on/off.
- `MIN_RECORD_SEC = 0` → **`FR-3` is off**. Every accidental tap becomes a transcription
  attempt, and `engine.run()` calls `transcribe_audio` on a near-empty buffer.

And both already appear in §6.5's Advanced table as read-mostly values with their real
units. The same two constants are specified twice, in two panels, with two incompatible
control types.

What I would do instead: leave both in Advanced only, as values. If they must be editable
they become validated `Settings` fields with a **floor** (`MIN_RECORD_SEC ≥ 0.1`,
`IDLE_THRESHOLD_SEC ≥ 30`) and a logged fallback, exactly as §6.5's own paragraph demands.
The Audio panel then has one checkbox — the recording click — and §9's "never mix radio
buttons and checkboxes in one panel" rule is satisfied by a panel holding one combo box
and one checkbox.

### 4.3 Instant-apply on top of a non-atomic, unlocked `Settings.save()`

`Settings.save()` opens the file with mode `"w"` and `json.dump`s into it. `"w"` truncates
*first*. There is no lock and no temp-file-plus-rename.

Today that is nearly harmless: saves happen on a CUDA fallback and on a tray menu toggle —
a handful per process lifetime. Instant-apply changes the arithmetic. Every checkbox,
radio, combo selection, vocabulary edit and hotkey click writes the file, and two threads
can now write it: the GUI thread (every control) and the **engine thread**
(`Engine._persist_cpu_fallback` → `settings.save()`).

Failure mode: the file is truncated to zero bytes and the process dies, or two writers
interleave. `load()` handles the resulting garbage correctly — it logs and falls back to
defaults — so the user sees no crash. **They see their settings silently reset.** That is
the exact failure class `OBS-3` was written for.

The fix is four lines and belongs in `config.py` before session 3: write to
`config.json.tmp`, `os.replace()` onto the target (atomic on NTFS), and take a
module-level `threading.Lock` around it. `save()` still never raises. I would also
debounce the hotkey panel — clicking three keys to build a chord should not write three
times.

### 4.4 "Match either side" is a footgun, and it argues with the safety classifier

Two problems, one of them silent:

1. **It is broken for Win** (§3.5): `rwin` → `win` → detects Left Win only.
2. **It manufactures the configuration the classifier warns about.** `design.md` §6's
   table says a lone unsided `ctrl` or `shift` "will fire constantly during ordinary
   typing" — true, and severe: bind unsided `ctrl` and every `Ctrl+C` starts a recording.
   The checkbox's entire function is to turn `rctrl` into exactly that. And handoff §6.1's
   rule list **dropped** that warning (§3.12), so the UI will convert a safe binding into
   a hazardous one and then display "Safe: types no character, scrolls nothing, activates
   no menu bar."

If it ships, it must re-run the classifier on the *written* chord rather than the clicked
one, and Win must be fixed first. Honestly I would drop it from the first pass: a user who
needs it can write `["ctrl"]` into `config.json`, which `README.md` already documents, and
the panel can display an unsided binding correctly without offering to create one.

### 4.5 Feeding the level meter with a Qt signal from the PortAudio callback

§6.3: "Live input level meter — a custom `QWidget.paintEvent`, fed by a signal from the
audio thread. Never read the audio buffer from the GUI thread."

The second sentence is right. The first is a dropout hazard. `Recorder._callback` runs on
**PortAudio's realtime callback thread**, whose cardinal rule is: no allocation, no locks,
no blocking. `Signal.emit()` across threads does all three — it allocates a
`QMetaCallEvent`, copies arguments, takes the receiver's post-event mutex, and may wake the
GUI thread. At 16 kHz that is a few hundred emits per second into a queue the GUI thread
drains only when it feels like it. Under load the queue grows and the meter lags; worse,
the callback overruns its deadline and you get audio glitches — in a dictation app, dropped
words.

The correct shape is the one this codebase already uses everywhere else: **the producer
writes a plain value, the consumer polls it.** `_callback` computes a peak or RMS and
assigns it to a float attribute — a single attribute rebind, the same argument
`config.py`'s `Settings` docstring already makes about `hotkey`. A `QTimer` on the GUI
thread reads it at 30 Hz and repaints. No signal, no allocation in the callback, no queue.

### 4.6 The benchmark button loads a second model into VRAM

§6.2: "A `Measure on this machine` button transcribes the bundled
`app/assets/benchmark_sample.wav` … with the selected model and records the wall time."

The engine holds a `WhisperModel` for the whole process lifetime. Measuring `large-v3`
while `large-v3-turbo` is resident means **two models allocated at once** — roughly 3.1 GB
plus 1.6 GB of `float16` weights on the GPU before activations. On a card already hosting a
desktop, that is a plausible CUDA OOM, and the failure mode is not clean: an allocation
failure during a *measurement* could take down the *working* model.

Three things the handoff does not say, all of which matter:

- **Which thread it runs on.** It blocks for seconds, so not the GUI thread; and not the
  engine thread either, because that stalls dictation. That means a third thread and a way
  to report completion back through the same bridge discipline as §1.
- **Whether the engine's model is unloaded first.** I would hand off so exactly one model is
  resident at a time: unload, measure, reload the real one. Slower, but it cannot OOM the
  working path.
- **The WAV is int16 and `transcribe_audio` wants float32.** `Recorder.stop()` returns
  `np.float32`; `benchmark_sample.wav` is 16-bit PCM. Something has to divide by 32768. No
  code does this today.

Note also the honesty problem this creates: a measurement taken while another model is
resident is not comparable to one taken when it was not — and §6.2's whole argument for
measuring is that invented numbers must not ship as fact.

### 4.7 Deferring the only genuinely risky packaging question to session 5

Acceptance criterion 10 — "`python build_portable.py` produces a zip that extracts and runs
on a clean Windows 11 machine" — is the criterion that can invalidate the toolkit choice,
and it is scheduled last.

Most of the risk turned out to be absent (§0: the wheel bundles its own MSVC runtime and
`qwindows.dll`). What inspecting a wheel does **not** answer is the interaction with this
project's specific launcher: a **renamed `pythonw.exe`**, started **elevated** via a
byte-patched `.lnk`, from the **Startup folder**, at login. That combination is unusual
enough that I would not assume it.

Move a 20-minute smoke test to the end of session 1: build the zip, extract it on a clean
box or a fresh user profile, run `install.bat`, log out and back in, confirm the tray icon
appears. If it does not, you have learned it while the only thing that changed is the tray.

### 4.8 One thing the handoff gets right that is worth defending

§6.2's decision to **delete the speed and accuracy bars and measure instead** is the best
call in the document, and someone will be tempted to undo it because the mockup's bars look
good. Do not. Published WER on a different corpus, rendered as a bar in a settings window,
is a fabricated fact — and this project's docs are unusually careful about exactly that
distinction. The `Character` column carrying a qualitative phrase is the honest version.

---

## 5. Underspecified — where I would have to guess

Numbered so they can be answered as a list.

### 5.1 How the popover learns about hover (session 2)

§5 offers "poll cursor position against the icon geometry on a ~150 ms `QTimer`, **or
install an event filter** — whichever proves reliable on Windows 11."

The event-filter option does not exist. `QSystemTrayIcon` is a `QObject`, not a widget; it
has no enter/leave events to filter on Windows, and Qt documents the `ToolTip` `QHelpEvent`
as **X11-only** (§0). Polling is the only option. That is fine, but then §5's other
assumption needs a decision: `geometry()` returns `QRect()` when the icon is not visible,
and `README.md` documents that the icon commonly lands in the Windows 11 overflow flyout
behind the `^` chevron. **What should the popover do when `geometry()` is empty?** Fall
back to the cursor position, fall back to a corner of the primary screen's
`availableGeometry()`, or suppress the popover entirely. I would fall back to the cursor.
Needs deciding, not guessing.

### 5.2 `setQuitOnLastWindowClosed`

Never mentioned. A `QApplication` quits by default when its last window closes, so closing
the settings window in session 2 would kill the app and the tray icon. It must be `False`.
Harmless to set in session 1 (no windows yet) and easy to forget in session 2. I will set it
in session 1.

### 5.3 Whether the tray menu is rebuilt or updated in place

`tray.py::on_state` assigns a whole new menu on every state change. The Qt-faithful port
would be `QMenu`/`QAction` objects created once and mutated via `setText`/`setChecked`.
Rebuilding is a correctness hazard if the menu is open (§1, failure 3). I intend to build
once and mutate, and to call it out as a deliberate deviation from a literal port — but if
you want a strictly literal port, say so.

### 5.4 Icon rendering at small sizes

`create_icon_image` returns one 64×64 PIL image. pystray hands it to Windows and Windows
scales it. A `QIcon` built from a single 64 px pixmap will also be scaled, but by Qt's
smooth transform, at whichever size the shell asks for — and on a 125%/150% display that is
20 or 24 px. "Preserved exactly" therefore needs a definition. My proposal: render the 64 px
master once, produce 16/20/24/32 px pixmaps with PIL's LANCZOS filter, and add all four to
the `QIcon`, so Windows picks an exact match and nothing is resampled at draw time. That is
*closer* to today's appearance than a single pixmap, but it is not bit-identical to pystray —
and you said you value these icons specifically, so I want this confirmed rather than
discovered.

### 5.5 `Settings…` and `Pause` in the session-1 menu

§3.2. Session 1 has no window to open and no pause. Options: (a) omit both, (b) add
`Settings…` disabled, (c) add `Pause` now via the `chord_held` seam. I recommend (a), with
(c) in session 2 — but the session-1 prompt says "the menu works", so this needs an answer.

### 5.6 Where the safety classifier lives

§3.12. `design.md` says `hotkey.py`; the handoff writes the rules into the panel. I will
follow `design.md` unless told otherwise, but the handoff should be amended so it does not
read as an instruction to duplicate. **Session 3 needs this answered.**

### 5.7 What the hotkey panel shows for an unsided chord already in `config.json`

`{"hotkey": ["ctrl"]}` is valid today and matches either physical side. The board has two
Ctrl caps. Both lit? Neither, with the readout showing "Ctrl"? A third visual state? And if
the user then clicks one of them, does the chord become `lctrl`, or `ctrl` plus that one?

### 5.8 What happens to a chord longer than the panel can represent

§6.1 caps the UI at three keys and says a fourth click replaces the chord. `parse_chord` has
no such limit, so `config.json` may legitimately hold four. Does the panel display it, refuse
it, or silently truncate on first interaction? A silent truncation is a config rewrite the
user did not ask for.

### 5.9 Chord ordering

In the mockup, clicking Left Alt while Right Ctrl was bound produced `["lalt","rctrl"]` — not
click order. `chord_held` is order-independent; `chord_label` is not. Is there a canonical
order (keyboard position? `VK_MAP` order?), and does re-saving an existing chord reorder it?
If it does, `config.json` churns on every visit to the panel.

### 5.10 Median latency and last-paste-target in Diagnostics

§6.6 says these are "all already logged today", which is true — and *only* logged. Deriving a
median means either parsing `debug_log.txt` (fragile: `OBS-4` guarantees the file is plain
text, not that its lines are a stable format) or holding state somewhere new. Which?

### 5.11 The `benchmarks` config schema

"Store results under a new `benchmarks` key in `config.json`, keyed by model name and device,
with a timestamp." Nested object or list? Timestamp format? What invalidates an entry —
`record_sample.py`'s docstring says re-recording the clip invalidates every cached
measurement, but nothing detects that. A hash of the WAV stored alongside each result would
make it self-invalidating.

### 5.12 Vocabulary rule schema and the `Scope` column

The mockup shows scopes `Always` and `Editors`, and a rule whose replacement is `\n\n`. So:
how is scope matched — against `inject.foreground_window_class()`? Where does the class list
come from? Is `\n\n` a literal backslash-n-backslash-n or an escape the substitution
interprets? (It has to be an escape to be useful, which means a mini-language and an escaping
rule.) And ordering: are rules applied in list order, with later rules seeing earlier rules'
output?

### 5.13 "Start with Windows" — read-only or writable

§6.5 says it "reflects whether the Startup-folder shortcut the installer creates is present".
Reflecting is a file-existence check. *Setting* it means creating a `.lnk` via `WScript.Shell`
COM and re-applying `install.ps1`'s `$bytes[0x15] -bor 0x20` run-as-admin patch — real work,
and it duplicates installer logic inside the app. The mockup draws it as a toggle.

### 5.14 Where `style.qss` lives at runtime

A file read via `paths` (§3.13), or a Python string constant? A file is nicer to iterate on; a
file is also one more thing that can be missing from the distribution, and a missing
stylesheet produces a working-but-unstyled window with no error. If it is a file, its absence
must be logged.

### 5.15 `industry.css` uses `color-mix()`, which QSS does not support

§9 says "Take the values from `industry.css` … Do not invent colours." But `--color-divider`
is `color-mix(in srgb, #1d1f20 16%, transparent)`, `.text-muted` is the same at 55%, and the
shadow tokens use it too. **QSS has no `color-mix()`.** Each must be resolved to a literal
`rgba(29, 31, 32, 0.16)` or a flat hex against the known ground. Mechanical, but exactly the
kind of thing that gets "invented" under time pressure — so the resolved values should be
written into §9's table once, not re-derived per panel.

Note also that `--color-accent` is `#5980a6` while `--color-accent-600` is `#597ea3`. §9's
table says accent = `#5980a6`. Fine, but they are two different colours one step apart and
both appear in the sheet.

---

## 6. Proposed plan for session 1, file by file

Scope: replace pystray with `QSystemTrayIcon`, build the engine→UI bridge, keep the app fully
usable. No window, no popover, no panels.

### Files created

**`app/ptt/ui/qt_app.py`** — the `QApplication` owner and the thread boundary.

- `EngineBridge(QObject)`
  - `state_changed = Signal(str, str)`, `text_ready = Signal(str)`.
  - `on_state(self, state, status_text=None)` — **one statement**:
    `self.state_changed.emit(state, status_text or "")`. Nothing else: no formatting that
    could raise, no logging, no widget access (§1).
  - `on_text(self, text)` — same shape.
  - Constructed on the GUI thread so affinity is right; connections made with **explicit**
    `Qt.QueuedConnection` anyway (§1, last paragraph).
- `QtApp`
  - Builds `QApplication`; sets `setQuitOnLastWindowClosed(False)` (§5.2).
  - Checks `QSystemTrayIcon.isSystemTrayAvailable()`; if False, logs and retries on a timer
    rather than failing silently (§7.4).
  - Two-phase wiring mirroring `TrayApp` exactly, so `ptt_tray.py` changes shape as little as
    possible:
    ```
    app    = QtApp(settings, cuda_supported)
    engine = Engine(settings, cuda_supported, on_state=app.bridge.on_state)
    app.attach(engine)
    app.run()
    ```
  - `run()` starts the engine on a daemon `threading.Thread` (as `TrayApp._setup` does) and
    calls `app.exec()`.
  - The thread diagnostic lives here — §7.2.

**`app/ptt/ui/qt_tray.py`** — the icon, the state map, the menu.

- `create_icon_image(state)` — **moved verbatim** from `tray.py`. Not retyped, not restyled,
  not re-derived. Still returns a PIL image; PIL stays a dependency.
- `_pil_to_qicon(img)` — the conversion, with the buffer-lifetime fix (§7.1) and the
  multi-size rendering from §5.4.
- `QtTray(QObject)` — owns the `QSystemTrayIcon`, a state→`QIcon` cache built **once** (four
  icons; build all four up front rather than converting on every state change), the tooltip
  `f"PTT Dictation ({status})"`, and the context `QMenu`.
- Menu built **once** with retained `QAction` references and mutated in place on state change
  (§5.3): Status (disabled), Hotkey (disabled), separator, Use GPU (CUDA) (checkable,
  `setEnabled(cuda_supported)`), Use CPU (checkable), separator, Exit.
- `on_state_changed(state, status_text)` — the **slot**, on the GUI thread. Does the string
  formatting `tray.py::on_state` used to do, including the
  `status_text if status_text else state.capitalize()` fallback.
- Exit: `engine.stop()` → `trayicon.hide()` → `QApplication.quit()`. **No join** — and the
  comment explaining why (`tray.py:104-107`) travels with it. `hide()` before quit is new and
  deliberate (§7.3).

### Files modified

**`app/ptt_tray.py`** — swap `from ptt.ui.tray import TrayApp` for
`from ptt.ui.qt_app import QtApp`, plus the three wiring lines. Everything else untouched:
`logging_setup.init()` first, then `transcribe.ensure_cuda_dll_dirs()` **before** the heavy
imports, then the guarded import block. PySide6 is imported **inside** that guarded block —
an import failure under `pythonw.exe` has no console to print to, and the guard is the only
reason it would ever be diagnosable.

**`requirements.txt`** — add `PySide6-Essentials==6.11.2` (pinned, matching every other line's
convention and `CON-2`'s reproducibility argument; the handoff's unpinned line is out of house
style). `shiboken6==6.11.2` arrives transitively. Remove `pystray==0.19.5`. Keep `pillow`
(icon drawing) and `keyboard` (still imported at module scope by `hotkey.py` and `inject.py`
as the `FR-C2` exception-path fallback).

**`docs/design.md`** — §1's `gui_toolkit:` line and §4's module table. One-line touches. I
would rather not leave the source-of-truth document actively false for four sessions, even
though the session plan schedules the full docs pass at the end.

### Files deleted

**`app/ptt/ui/tray.py`** — after the Qt tray is verified by hand, not before.
`create_icon_image` moves first (§3.9).

### Files explicitly not touched

`engine.py`, `hotkey.py`, `inject.py`, `audio.py`, `transcribe.py`, `config.py`, `paths.py`,
`runtime.py`, `logging_setup.py`, `ptt_dictate.py`, `build_portable.py`, `install.ps1`,
`run_tray.bat`.

The console frontend keeps working unchanged, which is a free correctness check: if
`ptt_dictate.py` still dictates after session 1, the engine really was untouched.

---

## 7. Where I expect trouble in session 1

Ordered by likelihood, not severity.

### 7.1 The PIL→QImage buffer lifetime — the one that crashes

```python
data = img.tobytes("raw", "RGBA")
qimg = QImage(data, w, h, QImage.Format_RGBA8888)   # does NOT copy
```

`QImage` constructed over a Python buffer **references** it. When `data` goes out of scope and
is collected, the `QImage` points at freed memory. The symptom is not a clean error: garbage
pixels, or a crash minutes later, or nothing at all on the machine you tested on. Fix:
`.copy()` the `QImage` immediately and build the `QPixmap` from the copy. This is the single
most likely way session 1 produces code that works here and fails elsewhere — exactly the
failure profile the session plan says Max effort is for.

### 7.2 Making the thread assertion actually prove something

The acceptance criterion says to assert `QThread.currentThread() == qApp.thread()` **in the
state handler**. That assertion is tautological: the handler is a slot on a queued connection,
so Qt guarantees it. It passes whether or not the bridge is doing anything.

The informative check is a **pair**, and it must be a log, not an `assert`:

- In `EngineBridge.on_state` (the raw callback): record the current thread. It should **not**
  be `qApp.thread()`. If it is, the engine is running on the GUI thread and the whole bridge
  is decorative.
- In `QtTray.on_state_changed` (the slot): record it again. It **must** be `qApp.thread()`.

Two reasons it must log rather than assert. First, `assert` is stripped under `-O`. Second and
worse: `Engine._emit` wraps the callback in `try/except Exception` and only writes the
traceback to `debug_log.txt` — so an `AssertionError` raised inside `on_state` is
**swallowed**, the poll loop continues, and there is no visible signal at all. A failed
invariant that produces no symptom is the exact shape of issue #11.

So: one `log_debug` line from each side, emitted once (guard with a flag so it does not write
every 20 ms), giving the two thread identities. Verification is then reading two lines of
`debug_log.txt` and seeing that they differ — something you can actually check, rather than an
assertion you have to trust.

### 7.3 The ghost tray icon

`runtime.main_guard` calls `os._exit(0)` immediately after `entry()` returns, which is correct
and load-bearing (`FR-9`, issue #8) — CTranslate2's thread pools would otherwise block
shutdown. But `os._exit` skips every destructor, including `QSystemTrayIcon`'s, which is what
normally issues `Shell_NotifyIcon(NIM_DELETE)`. Windows then leaves a dead icon in the tray
until the user hovers over it.

pystray called `icon.stop()` explicitly, which removed it. The Qt equivalent is an explicit
`trayIcon.hide()` in the Exit path before `quit()`. This needs verifying by *looking at the
tray after exit*, not assuming — and it needs checking on the crash path too, where
`main_guard`'s `except` branch calls `os._exit(1)` with no chance to hide anything.

### 7.4 `isSystemTrayAvailable()` at login

The installer creates a **Startup-folder shortcut**, so the normal launch is at login, racing
Explorer's initialisation of the notification area. pystray blocked until the tray existed;
Qt's `QSystemTrayIcon.show()` on an unavailable tray fails quietly.

This is the most likely *real-world* session-1 failure, and it will not reproduce when you
launch from the desktop shortcut by hand. Plan: check `isSystemTrayAvailable()` at startup and,
if False, retry on a `QTimer` for a few seconds before logging and giving up.

### 7.5 Menu mutation versus rebuild

`tray.py` rebuilds the whole menu on every state change, i.e. several times per dictation.
Porting that literally means destroying a `QMenu` that may be open on screen. I intend to build
once and mutate (§5.3), which changes the internal structure while preserving observable
behaviour — but that is a claim needing a manual check: right-click the icon, hold the menu
open, and start a dictation with the other hand. The menu should update or stay stable, not
vanish.

### 7.6 `QPixmap` before `QApplication`

`QPixmap` requires a `QGuiApplication` to exist. `create_icon_image` returns PIL and is safe to
call any time; `_pil_to_qicon` is not. If the four icons are built as module-level constants,
or in a `QtTray.__init__` that runs before `QApplication(...)`, it aborts. An ordering trap
that is easy to introduce while tidying.

### 7.7 Elevated process, non-elevated shell

Every user runs this elevated (`FR-C5`, byte-patched `.lnk`). Tray icons from elevated
processes work — pystray's do today — but Qt's tray implementation is a different
`Shell_NotifyIcon` client, and its interaction with UIPI deserves one deliberate smoke test
rather than an assumption. Specifically: does the context menu appear and respond when the
process is elevated and Explorer is not?

### 7.8 Distribution growth is real but not a problem

+76.9 MB compressed on a 1.35 GB zip. Stated as a number here rather than discovered at
session 5. `build_portable.py` needs **no change** — `.venv` is zipped wholesale and
`should_skip` only drops `pyvenv.cfg`, `*.old` and the three runtime artifacts, none of which
PySide6 produces.

---

## 8. Decisions needed before session 1 starts

1. **`CON-3`.** Amend it, or the session-1 code contradicts the requirements document from the
   first commit. §2 has draft wording. *This one is blocking.*
2. **`Settings…` and `Pause` in the session-1 menu** — omit both, or add `Pause` now via the
   `chord_held` seam? §3.2, §5.5.
3. **Icon fidelity at small sizes** — confirm the multi-size `QIcon` approach in §5.4, since
   you said you value these icons specifically.
4. **Commit `app/assets/` and `docs/gui_handoff/`.** Both are untracked, and acceptance
   criterion 8's test case is a gitignored file. §3.14.
5. **Recommended: insert a session 0.5** for `design.md` step 2's unit tests, before four new
   validated config fields and a classifier get written on top of an untested config layer.
   §4.1.

Answering 1–4 unblocks session 1. Everything in §5 can be answered as it arises in sessions
2–4, except §5.6 (classifier location), which session 3 needs.

---

## 9. What I did not do

- Did not run the application. The engine needs a GPU, an elevated console and a microphone;
  nothing above required it.
- Did not install PySide6 into `.venv`. The wheel was downloaded to a scratch directory,
  inspected, and left there. The project environment is unchanged.
- Did not write, modify or delete any project file other than this one.
