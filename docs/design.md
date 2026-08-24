# PTT Dictation — Design

How the utility is built, and how it is going to be built. Cites requirement IDs from
[requirements.md](requirements.md).

Sections 4–9 describe the implementation as it stands after the `app/ptt/` split
(step 1 of section 10). Sections 2–3 record the state it replaced, and why, because the
reasoning is the part worth keeping.

---

## 1. System configuration

```yaml
system:
  target_os: "Windows 11"
  python_version: "3.14.2"
  gui_toolkit: "PySide6-Essentials (LGPL; see CON-3)"

dependencies:
  faster-whisper: "1.2.1"     # speech-to-text inference
  ctranslate2:    "4.7.2"     # execution engine; CUDA float16 on Blackwell
  sounddevice:    "0.5.5"     # microphone stream capture
  numpy:          "2.4.6"     # audio buffer concatenation
  keyboard:       "0.13.5"    # fallback only; see section 3
  pyperclip:      "1.11.0"    # clipboard read/restore
  PySide6-Essentials: "6.11.2"  # Qt6 GUI and QSystemTrayIcon
  pillow:         "12.2.0"    # draws the four tray icons; see section 4
  nvidia-*-cu12:  "see requirements.txt"

removed:
  pystray:        "0.19.5"    # replaced by QSystemTrayIcon; see section 4

model_parameters:
  default_model: "large-v3-turbo"
  device: "cuda"              # falls back to cpu/int8 per FR-6
  compute_type: "float16"     # CON-4
  language: "en"
  beam_size: 5
  vad_filter: true
  condition_on_previous_text: false   # NFR-5

active_hotkey:
  default: ["rctrl"]
  configurable_via: "config.json -> hotkey"
```

---

## 2. Current file map

| Path | Role |
|---|---|
| `ptt_dictate.py` | Entry point: console frontend. 68 lines. |
| `app/ptt_tray.py` | Entry point: system tray frontend. The one that ships. 68 lines. |
| `app/ptt/` | The implementation, shared by both entry points. See section 4. |
| `pyproject.toml` | Tooling configuration only — pytest and pyrefly. No build system. |
| `build_portable.py` | Provisions `.venv` from `requirements.txt`, installs the signed PSF interpreter, bundles `ptt_dictate_dist.zip`. |
| `run_tray.bat` | Self-elevating launcher; runs `.venv\Scripts\ptt_dictate.exe app\ptt_tray.py`. |
| `install.bat` / `install.ps1` | Self-elevating installer; copies `.venv` + `app` to `%LOCALAPPDATA%\Programs\ptt_dictate`, creates Desktop and Startup shortcuts marked run-as-administrator (`FR-C5`). |
| `app/assets/` | `style.qss`, the bundled Barlow faces with **both `OFL.txt` licence files**, and `benchmark_sample.wav`. Shipped by `build_portable.py`'s `os.walk` over `app/`, which is why nothing here needs an entry in `items_to_zip`. The licence files travelling with the fonts is a condition of the SIL OFL, not housekeeping; verified in the built archive as `V-M-64`. |
| `tests/` | The unit suite. See [verification.md](verification.md). |
| `requirements-dev.txt` | pytest and the packages the tests import. Never shipped — see section 8. |
| `docs/` | This document, `requirements.md`, `verification.md`, `development_history.md`, `gui_handoff/`. |

`build_portable.py`, `run_tray.bat` and `install.ps1` are unchanged by the split, which
is the whole reason section 4 chose `app/ptt/` over `src/ptt/`.

---

## 3. The state this replaced, and why it had to change

> **Resolved by the `app/ptt/` split.** The duplication metric below now reports **0**
> matched lines between the two entry points, down from 213. This section is kept
> because the reasoning still explains why the package is shaped the way it is.

**The two scripts are the same program twice.** 213 of the 370 lines in `ptt_dictate.py`
are byte-identical with `app/ptt_tray.py` — `Recorder`, `paste_text`, `chord_held`,
`VK_MAP`, the key tables, the state machine. Measure it with:

```bash
python -c "import pathlib,difflib; a=pathlib.Path('ptt_dictate.py').read_text().splitlines(); b=pathlib.Path('app/ptt_tray.py').read_text().splitlines(); print(sum(x.size for x in difflib.SequenceMatcher(None,a,b,autojunk=False).get_matching_blocks() if x.size>=5))"
```

Every fix lands twice. Issue #9 records "Updated in both"; issue #11 did the same. The
duplication is the reason a fix can be applied to the dev script and forgotten in the one
that ships.

**State is module globals mutated across threads.** `model`, `use_gpu`, `app_status`,
`running`, `HOTKEY_MODS` are globals; the tray thread and the transcription thread both
touch them. It works because writes are coarse and rare, but there is no place to put a
settings object, which is what `FR-4` now needs.

**`keyboard` is still imported but must not be trusted.** Per `FR-C2` it survives only as
an exception-path fallback in `chord_held()` and `paste_text()`. All real detection is
`GetAsyncKeyState`; all real injection is `keybd_event`.

**There are no tests.** Nothing prevents a regression of any of the eleven solved issues.

---

## 4. Target module layout

A package at **`app/ptt/`**, with the two current scripts reduced to entry points.

`app/ptt/` rather than the conventional `src/ptt/` because the distribution path is built
around the `app/` directory: `build_portable.py` zips `app/` wholesale, `run_tray.bat`
invokes `app\ptt_tray.py`, and `install.ps1` copies `.venv` + `app`. Keeping the package
inside `app/` means **the build script, launcher, and installer need no changes**, and
the 1.4 GB distribution does not need re-validating on a target PC. This project ships a
portable environment, not a wheel, so the packaging benefit of `src/` does not apply.

| Module | Responsibility |
|---|---|
| `app/ptt/paths.py` | **Sole owner of every application-relative path.** No other module computes a directory. |
| `app/ptt/runtime.py` | `main_guard()`. The only module permitted to call `os._exit` (`FR-9`). |
| `app/ptt/config.py` | Settings dataclass; load, validate, save, migrate `config.json`. |
| `app/ptt/hotkey.py` | VK table, chord parse/format, `GetAsyncKeyState` polling, safety classifier. |
| `app/ptt/vocabulary.py` | Replacement-rule type, validation and the substitution itself. Pure, like `hotkey.py`: no Qt, no config file, no model. |
| `app/ptt/inject.py` | The only module permitted to call `keybd_event`. Paste, modifier neutralisation, `suppress_alt_menu`, focus diagnostics. |
| `app/ptt/audio.py` | `Recorder`: stream lifecycle, pre-roll buffer, idle release. |
| `app/ptt/transcribe.py` | Model load, CUDA detection, CPU fallback, text cleanup. |
| `app/ptt/engine.py` | The state machine. Owns the poll loop; emits state changes to a listener. |
| `app/ptt/logging_setup.py` | `debug_log.txt` writer (`OBS-4`). |
| `app/ptt/ui/qt_app.py` | `QApplication` owner and `EngineBridge`, the engine-thread-to-GUI-thread boundary. |
| `app/ptt/ui/qt_tray.py` | `QSystemTrayIcon`, menu, state-to-icon mapping. |
| `app/ptt/ui/qt_popover.py` | Frameless, non-activating hover panel (layer 2). |
| `app/ptt/ui/qt_window.py` | `QMainWindow`, tab bar, status bar (layer 3). |
| `app/ptt/ui/qt_statusview.py` | The read-only state display, embedded in both the popover and the window's banner. |
| `app/ptt/ui/qt_theme.py` | Font registration and `style.qss`, applied to the `QApplication`. |
| `app/ptt/ui/qt_marks.py` | The `+` registration marks (`gui_handoff` §9). A mixin the two ground surfaces inherit, plus the pure geometry function behind it. |
| `app/ptt/ui/panels/__init__.py` | `InstantApplyPanel`: the one write-field-then-save-then-tell-the-engine sequence every control uses. |
| `app/ptt/ui/panels/hotkey.py` | Qt keyboard-diagram picker; live `GetAsyncKeyState` shading. |
| `app/ptt/ui/panels/model.py` | Whisper size tiers, GPU/CPU choice, measured latency. |
| `app/ptt/ui/panels/audio.py` | Input-device picker, live level meter, the three recording-behaviour checkboxes. |
| `app/ptt/ui/panels/vocabulary.py` | The replacement-rule table. The matching itself lives in `ptt/vocabulary.py`. |
| `app/ptt/ui/panels/advanced.py` | The engine's constants, read from the modules that own them. Writes nothing. |
| `app/ptt/ui/panels/diagnostics.py` | CUDA state, median latency, last paste target, and the tail of `debug_log.txt`. |
| `app/ptt_tray.py` | Entry point: builds the tray UI, starts the engine. Unchanged invocation path. |
| `ptt_dictate.py` | Entry point: prints state to the console, starts the engine. |

The engine must not import the UI. It reports state through a callback the frontend
supplies, which is what allows one core to serve both a tray icon and a console.

### 4.1 The UI package

`app/ptt/ui/` is three layers and a theme, and the split in the table above is exactly
those layers. `gui_handoff.md` §§4–6 is the specification; this is how it landed.

| Layer | Module | What it is |
|---|---|---|
| — | `qt_app.py` | The `QApplication` owner and `EngineBridge`. Not a layer: the boundary. |
| 1 | `qt_tray.py` | `QSystemTrayIcon`, four state icons, the right-click menu. |
| 2 | `qt_popover.py` | Frameless, non-activating hover panel. |
| 3 | `qt_window.py` | `QMainWindow`: banner, tab bar, six panels, status bar. |
| 2 + 3 | `qt_statusview.py` | The read-only display, built once and embedded in both. |
| — | `qt_theme.py` | Font registration and `style.qss`, applied to the `QApplication`. |
| 2 + 3 | `qt_marks.py` | The `+` corner marks, mixed into `StatusView` and `InstantApplyPanel`. |

Four rules hold it together, each of which fails silently rather than loudly if broken:

1. **Nothing on the engine thread touches a widget.** `Engine.run()` invokes `on_state`
   from its own thread and its docstring makes no promise about which one that is.
   `EngineBridge.on_state` therefore only emits; Qt copies the arguments into a
   `QMetaCallEvent` and the slot runs on the GUI thread. Every connection from a bridge
   signal is made with an explicit `Qt.QueuedConnection` — `AutoConnection` resolves
   correctly today but degrades silently to a direct call if anything later moves the
   receiver. `Engine._emit` wraps the callback in `try/except Exception` and only logs,
   so a violation that raises produces **no visible symptom**: dictation keeps working
   while the UI stops updating. The worst violations — building a `QPixmap` off-thread,
   replacing a `QMenu` the event loop is dispatching into — are not Python exceptions at
   all. Verified as `V-M-57`.
2. **`qt_statusview.StatusView` is the only thing that draws the state rows**, and both
   the popover and the window's banner embed it. Two implementations of the same six rows
   would drift, and the user asked specifically for the popover-to-window transition to
   feel like one object growing.
3. **No colour is written in Python**, including colours a `paintEvent` draws. `StatusDot`
   and the model table's delegates read theirs off a widget through `qproperty-`, written
   from `style.qss`. A delegate is not a widget, so no selector can reach one directly —
   the indirection is what keeps the rule literal.
4. **Every control applies instantly, through one method.** There is no OK, Apply or
   Cancel in the settings window. `InstantApplyPanel.apply_now` is the single path:
   write the field, `Settings.save()`, then tell the engine if it needs to know. That
   order is the contract — reversing the last two races a model reload against a disk
   write.

**`pystray` has been removed**, along with `app/ptt/ui/tray.py`. `gui_handoff.md` §11
required it: the two tray implementations were never to coexist, and git history is the
fallback. Two things about the port are worth recording, because both look like
gratuitous complexity and are not:

- **The icon drawing code is unchanged.** `create_icon_image` was moved across verbatim
  and still returns a PIL image, which is why `pillow` is still a dependency. The four
  images it produces are byte-identical to the pystray build's (`V-M-50`).
- **`ICON_SIZES = (16, 24, 32, 48, 64)` is the faithful port, not an embellishment.**
  pystray never handed Windows the 64 px image: it saved an `.ICO`, and PIL's ICO writer
  emits one `thumbnail(size, LANCZOS)` frame per default size not larger than the source.
  Reproducing those five frames is what preserves the appearance; handing Qt a single
  64 px pixmap and letting it scale at paint time would have been the change.

The menu has no `Pause`, and that is now a decision rather than a gap. Earlier drafts of
`gui_handoff` §4 listed one; `stage0_review.md` §3.2 pointed out that nothing in the
engine could implement it, and it was struck in session 5. Exiting and relaunching is the
pause. The mechanism was never the hard part — `Engine.__init__` takes a `chord_held`
seam, so a frontend can suppress the hotkey in one line without touching the engine —
but a real Pause also needs a fifth tray icon (§4 ships four and §11 declines a fifth), a
status string the engine cannot supply because the pause would live in the frontend, and
a ruling on whether it persists to `config.json`. Three decisions for a case a right-click
already covers.

Three structural constraints hold this layout together. Each is checkable, and each
exists because breaking it fails silently rather than loudly:

1. **`app/ptt/__init__.py` is empty, and `transcribe.py` has no `faster_whisper` or
   `ctranslate2` import at column 0.** Both are imported inside functions that call
   `ensure_cuda_dll_dirs()` first. If CTranslate2 loads before those directories are
   registered the GPU is simply not found — no exception, just CPU inference at roughly
   ten times the latency (issue #1). Measured on this machine: 0.5 s against 5.5 s.
2. **`os._exit` appears only in `runtime.py`.** `FR-9` is load-bearing and a rule with no
   owner gets duplicated, which is how the original two-script problem began.
3. **`paths.py` is the only module that computes a directory.** The application's paths
   are anchored one level *above* the package; a module deriving them from its own
   `__file__` would relocate `config.json` into `app/ptt/` and orphan every existing
   installation's settings.

---

## 5. Keystroke injection contract

`inject.py` is the only place `keybd_event` may be called. Five rules, each bought with a
bug report:

1. **Every event carries a real scan code** from `MapVirtualKeyW`. UWP targets reject
   synthetic keys without one (`FR-C1`, issue #8).
2. **Navigation-block keys set `KEYEVENTF_EXTENDEDKEY`** (`0x01`). `Insert` is in that
   block (`FR-C1`, issue #8).
3. **`Alt` is disarmed before it is released** — see below (`FR-C3`, issue #11).
4. **Modifier release is conditional and side-aware.** Only modifiers actually reported
   down are released, and `VK_LCONTROL`/`VK_RCONTROL` are released explicitly: injecting
   the unsided `VK_CONTROL` release leaves the right-hand key state set.
5. **The clipboard is captured before and restored after every paste** (`FR-C4`, issue
   #5). Insertion goes via the clipboard, so the user's contents must come back. Stated
   here because a rule that appears only in the code is a rule that gets simplified away.

### The Alt menu-activation problem

Windows activates a window's menu bar — in WinUI apps such as Windows 11 Notepad, the
access-key layer — when `Alt` goes **up with no other key pressed in between**. Focus
moves off the document and every injected keystroke is silently discarded.

Measured against a live Notepad window with `GetGUIThreadInfo`
(`GUI_CARETBLINKING` indicates the document still owns a caret):

| Sequence | Caret | Paste lands |
|---|---|---|
| `Shift+Insert`, no Alt involved | alive | yes |
| bare `Alt` down→up, then `Shift+Insert` | **dead** | **no** |
| bare `Alt` down→up, then `Ctrl+V` | **dead** | **no** |
| bare `Alt` down→up, `Esc`, then `Shift+Insert` | alive | yes |
| inert key tapped while `Alt` held, then `Alt` up | alive | yes |
| full `Shift`+`Alt` chord released, nothing else | **dead** | — |
| `Ctrl`+`Shift` chord released | alive | — |
| bare `Alt` **keyup alone**, no preceding keydown | alive | yes |

Three conclusions the design depends on:

- It is **not** a paste-mechanism problem. `Ctrl+V` fails identically, so this is
  unrelated to the UWP scan-code work in issue #8.
- `Shift` does not count as an intervening key. Any `Alt`-containing chord triggers it.
- Activation requires a full press. A stray `Alt` *keyup* on its own is harmless, which
  is why the pre-existing unconditional release was only fatal when the user's own chord
  contained `Alt`.

**`suppress_alt_menu()`** taps `VK_NONAME` (`0xFC`, reserved and unassigned, so it
produces no character and no command) while `Alt` is still held. That supplies the
missing intervening keypress and renders the release inert. It is called twice:

- **on record start**, covering the user's own physical release — by the time the poll
  loop notices the release, activation has already happened, so the guard must be
  installed at the start of the hold;
- **inside the paste path**, covering the synthetic release in rule 4.

### Focus diagnostics

Before pasting, `inject.py` checks whether the foreground window still owns a caret and
logs a warning when it does not, along with the target window class (`OBS-1`, `OBS-2`).
This is diagnostic only — the paste is attempted regardless, since a caretless window is
not proof of failure.

---

## 6. Hotkey selection

### Default

`("rctrl",)` — Right Ctrl alone. A lone modifier: no character, no scroll, no menu
activation (`FR-C3`), and unlike `Alt+Shift` or `Ctrl+Shift` it is not a Windows
input-language or keyboard-layout switch. Verified inert while held for over a second
against a focused Notepad window.

Keyboards without a right-hand Ctrl exist (some compact laptops, Mac hardware running
Windows). Those users change the chord — which is why `FR-4` exists.

### Chord representation

A chord is an ordered tuple of key names resolved through `hotkey.KEYS`, the one
declarative table every other name in the module is derived from — `VK_MAP`,
`KEY_LABELS`, `BINDABLE_KEYS` and the picker's `BINDABLE_BY_VK`. Left/right variants
are distinct names (`lctrl`, `rctrl`, `lshift`, …); unsided names (`ctrl`) match either
side. `chord_held()` is true when every key in the tuple is reported down by
`GetAsyncKeyState` (`FR-C2`).

Each entry carries **every** virtual key that satisfies its name, not one, and that
plural is load-bearing for exactly one key. `ctrl`, `shift` and `alt` have real unsided
virtual keys the OS reports for either side; **Windows has no unsided Win key** — `0x5B`
is `VK_LWIN`. `"win"` therefore used to claim it matched either side while detecting only
the left one. It now carries `(0x5B, 0x5C)` and the claim is true.

### The picker

The settings window's **Hotkey** panel draws a full keyboard diagram (`CON-3`).

- It runs **on the GUI thread**, like every other widget. The engine reports state from
  its own thread and that is marshalled across by `ui/qt_app.py`'s `EngineBridge`;
  nothing in the UI may be touched from the engine thread.
- Capture **polls `GetAsyncKeyState`** rather than reading Qt key events. Same
  reason as `chord_held()`: side-aware virtual keys, and immunity to the hook loss
  described in `FR-C2`. It also means the dialog captures the chord identically to the
  way the engine will later detect it — the picker and the detector share one code path.
- Binding is **click-to-bind, not hold-to-record**: clicking a bindable cap toggles it in
  the chord, up to three keys, and a fourth click replaces the chord outright. Recording a
  held combination was the original plan and was dropped — it cannot express "either side"
  and it fights the live shading, which is already showing what is held.
- The chord is displayed with the classifier's warnings for the chord **as it will be
  written**, which matters because "match either side" is the one control that can turn a
  safe binding into a hazardous one.
- **There is no Save or Cancel.** Each click writes `config.json` and hot-swaps the chord
  live: the engine's poll loop re-reads the setting each iteration, so no restart is
  required. A chord may never be empty — clicking the last bound key leaves it bound.
- A chord already in `config.json` is displayed exactly as stored and is never reordered
  or re-spelled on the user's behalf. Only a chord the user builds is canonicalised.

### What makes the live re-read safe

`Settings` is **not frozen**, and every field holds an immutable value. The engine
re-reads `settings.hotkey` inside its poll loop on every iteration, never caching it in
a local or on `self`. That is safe only because writes are whole-value rebinds —
`settings.hotkey = ("rshift",)`, never `settings.hotkey.append(...)`. An attribute
rebind is a single bytecode, so a reader on another thread sees either the old tuple or
the new one, never a half-built one. **No lock is needed and none should be added.**

Two ways to break this, both tempting: making `Settings` frozen, which stops the picker
writing to it at all; and making `hotkey` a list mutated in place, which turns a clean
hand-off into a race that shows up once a month.

### Safety classifier

`hotkey.classify()` returns a list of warnings for a candidate chord, and `SAFE_NOTE` is
what the panel shows when that list is empty. Pure — no Win32, no Qt — so every row below
is testable without a keyboard, and so the panel renders what it returns rather than
restating the rules beside the widgets. This is `FR-C3` made visible at the moment of
choosing, rather than discovered months later:

| Condition | Warning |
|---|---|
| Contains a printable or scrolling key (`space`, letters, digits) | Types a character or scrolls the focused window while held (issue #9). |
| Contains any `Alt` | Activates the target window's menu on release. Neutralised automatically, but a non-Alt chord is safer (issue #11). |
| Contains any `Win` | Opens the Start menu when released on its own, taking focus off the target. `inject.suppress_alt_menu` neutralises the Alt case and has no Win equivalent, so this one is not disarmed. |
| Is exactly `alt`+`shift`, or `ctrl`+`shift` | Windows' input-language / keyboard-layout switch when a second layout is installed. |
| Is a single common modifier (`shift`, `ctrl` unsided) | Will fire constantly during ordinary typing. |
| Empty | Rejected, not warned. |

Warnings do not block saving. The user may know better than the classifier; the point is
that they choose with the information.

---

## 7. Configuration

`config.json`, written next to the application.

```json
{
  "version": 1,
  "use_gpu": true,
  "hotkey": ["rctrl"],
  "model": "large-v3-turbo",
  "benchmarks": {
    "large-v3-turbo|cuda": { "seconds": 1.18, "at": "2026-08-23T14:22:07", "clip": "1b00eade0c24" }
  }
}
```

- `model` is validated against `transcribe.MODEL_NAMES`. An unrecognised name falls back
  to `transcribe.DEFAULT_MODEL` with a logged reason, rather than being handed to
  faster-whisper, which would try to fetch it from Hugging Face by that name.
- `benchmarks` caches measured latency, keyed by model **and** device — a CPU figure and a
  CUDA figure are different numbers about different hardware. Each entry stores a digest of
  the sample clip it was measured against, so re-recording `benchmark_sample.wav`
  invalidates the old numbers instead of leaving them on screen looking comparable.
  Entries that fail validation are dropped individually, with a reason, not silently.

- Unknown keys are preserved on write, so a newer build's settings survive a rollback.
- An invalid or unrecognised `hotkey` falls back to the default and logs why (`OBS-3`).
- Files without `version` are treated as v1 — that is what today's `{use_gpu}` files are.
- `config.py` owns the schema. No other module reads or writes the file. The CUDA
  fallback reaches it through a callback rather than importing it, so this holds
  literally rather than approximately.
- **Known keys win a collision** with a preserved unknown key: they are serialised last.
- **`version` is written back on the next save**, not on read. Migration is lazy; loading
  a config never rewrites it.
- **Every field is validated by type, not by truthiness, and every fallback logs why.**
  `{"use_gpu": "false"}` is a truthy string; read naively it silently forces GPU on a
  machine that may not have one.
- **`save()` writes a temp file and `os.replace()`s it into place, under a lock.** `"w"`
  truncates first, so a process that died mid-dump left a zero-byte file — which `load()`
  handles correctly by falling back, meaning the user's symptom is their settings silently
  resetting. It also has two writers now: the GUI thread on every control, and the engine
  thread on a CUDA fallback. That lock guards the **file**; it is not the field lock the
  `Settings` docstring forbids, and the engine's live re-read stays lock-free.

---

## 8. Testability

The tests themselves, what each one verifies, and their results are in
**[verification.md](verification.md)**. This section is only what is a *design* decision
rather than a test: the seams that make the behaviour reachable without hardware, and why
the test framework is not in the distribution.

**Seams.** Every point at which a module would touch Win32, an audio device, a model or an
event loop is reached through one indirection, so it can be replaced. None of these exists
for the tests' benefit — each was already required by something else, and testability is
the dividend:

| Seam | Exists because | Also makes testable |
|---|---|---|
| `hotkey._key_state()` | one `GetAsyncKeyState` call site, so the picker and the detector share a failure mode (`FR-C2`) | chord detection, with no keyboard |
| `Engine(chord_held=…)` | the loop must be drivable without a keyboard | the whole state machine |
| `on_state` / `on_text` / `on_benchmark` | the engine must not import the UI (§4) | every state assertion |
| `paths.asset_path()` | `paths.py` is the only module that computes a directory | the benchmark clip |

That last column is the reason to keep them. A seam that only the tests use is a seam that
gets refactored away; these four are each load-bearing in production.

**Where the test framework lives.** Not in `.venv`. `build_portable.py` zips `.venv`
wholesale, so `pip install pytest` there ships the framework to every target PC, and
`CON-3` forbids adding it to `requirements.txt`. `requirements-dev.txt` holds it instead,
and `items_to_zip` is an explicit allowlist, so neither it nor `tests/` reaches a
distribution. `pyproject.toml` supplies `pythonpath = ["app"]` so `import ptt` resolves
without installing anything. Confirmed against the built archive as `V-M-64`: no
`tests/`, no `requirements-dev.txt`, no `pyproject.toml`, no `docs/`.

The allowlist is only half of it, and the other half has a hole. `.venv` is zipped
**wholesale**, and `pip install -r requirements.txt` does not uninstall a package a
requirement no longer names — so dropping `pystray` from `requirements.txt` did not
remove it from `.venv`, and it is still in the distribution. Nothing imports it, but a
distribution that does not match `requirements.txt` cannot be reasoned about from
`requirements.txt`. Rebuilding `.venv` from scratch is the fix, and is also the only way
to prove the pinned set is complete. Recorded in `verification.md` §7.

**Manual Win32 probes** (`tests/tools/probe_paste.py`, **not yet written**). Menu
activation, caret loss and paste delivery are behaviours of *another process's* window and
cannot be unit-tested. The probe harness from the issue #11 investigation is kept as a
runnable script that reproduces the evidence table in section 5.

Its one non-negotiable design rule: **the harness pins a target window handle and refuses
to inject anything unless that window currently has focus**, re-asserting focus if it
drifted and aborting if it cannot. During the issue #11 work, an earlier version aimed at
whatever `GetForegroundWindow()` returned, and a Notepad window that failed to come to the
front meant a sequence of pastes and select-alls went into an unrelated application. An
input-injecting test harness that trusts ambient focus is a hazard, not a test.

**Every solved issue maps to a verification item.** That rule lives in
[verification.md](verification.md), which records which issues currently satisfy it and
which do not.

---

## 9. Explicitly not changing

The audio pipeline (`Recorder`, pre-roll, idle release), model parameters, the CUDA DLL
resolution logic, `build_portable.py`, the installer, and the elevation model all stay as
they are. They work, they are covered by the retrospective log, and touching them would
put `NFR-6`/`NFR-7` back in play for no gain.

---

## 10. Sequencing

1. ~~Split into `app/ptt/` with entry-point shims; add `pyproject.toml`.~~ **Done.**
   Behaviour-neutral *for the tray*, verified against a captured baseline at every step.
   The console frontend was deliberately upgraded onto the same engine, gaining
   `config.json`, logging, CPU fallback and the caret diagnostics it had drifted behind
   on. `debug_log.txt` now rotates rather than truncating, because both frontends write
   it.
2. ~~Add the unit tests~~ **Done**, out of order — after step 3 rather than before it,
   which is why session 3 built a classifier and two validated `Settings` fields on top of
   an untested config layer. See [verification.md](verification.md).
   **The pinned-window probe harness is still outstanding** (`tests/tools/probe_paste.py`);
   it injects real keystrokes into another process's window, so it cannot run unattended
   and belongs with the acceptance pass rather than with the unit suite.
3. ~~Build the picker dialog and the safety classifier on top of the settled config layer.~~
   **Done.** The picker is the settings window's Hotkey panel — a keyboard diagram with
   click-to-bind and instant apply, not the modal Save/Cancel dialog section 6 originally
   described. The classifier is `hotkey.classify()`.
4. ~~Build the Qt interface: the tray, the popover, the window and its six panels, and
   retire `pystray`.~~ **Done**, over four sessions, to
   [gui_handoff.md](gui_handoff/gui_handoff.md). See section 4.1.
5. **Verify and package.** Worked through, and the results are
   [verification.md](verification.md) §5.3 and §6. All ten acceptance criteria were
   exercised. Criteria 1, 2, 3, 6, 8 and 9 are closed. Criteria 4 and 5 pass with one
   named residual each — a physical numeric keypad, and a human voice. Criterion 7 needs
   a machine with no CUDA device and criterion 10 a clean Windows 11 machine and a run of
   `install.bat`. §7 of that document is the list, and it ends with the steps a person
   has to take.
