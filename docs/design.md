# PTT Dictation — Design

How the utility is built, and how it is going to be built. Cites requirement IDs from
[requirements.md](requirements.md).

Sections 1–3 describe what exists today. Sections 4–9 are the **target** design and are
not implemented yet.

---

## 1. System configuration

```yaml
system:
  target_os: "Windows 11"
  python_version: "3.14.2"
  gui_toolkit: "tkinter (bundled; see CON-3)"

dependencies:
  faster-whisper: "1.2.1"     # speech-to-text inference
  ctranslate2:    "4.7.2"     # execution engine; CUDA float16 on Blackwell
  sounddevice:    "0.5.5"     # microphone stream capture
  numpy:          "2.4.6"     # audio buffer concatenation
  keyboard:       "0.13.5"    # fallback only; see section 3
  pyperclip:      "1.11.0"    # clipboard read/restore
  pystray:        "0.19.5"    # system tray icon
  pillow:         "12.2.0"    # tray icon rendering
  nvidia-*-cu12:  "see requirements.txt"

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
| `ptt_dictate.py` | Command-line developer version. |
| `app/ptt_tray.py` | Headless system tray version. The one that ships. |
| `build_portable.py` | Provisions `.venv` from `requirements.txt`, installs the signed PSF interpreter, bundles `ptt_dictate_dist.zip`. |
| `run_tray.bat` | Self-elevating launcher; runs `.venv\Scripts\ptt_dictate.exe app\ptt_tray.py`. |
| `install.bat` / `install.ps1` | Self-elevating installer; copies `.venv` + `app` to `%LOCALAPPDATA%\Programs\ptt_dictate`, creates Desktop and Startup shortcuts marked run-as-administrator (`FR-C5`). |
| `docs/` | This document, `requirements.md`, `development_history.md`. |

---

## 3. Current state, and why it needs to change

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
| `app/ptt/config.py` | Settings dataclass; load, validate, save, migrate `config.json`. |
| `app/ptt/hotkey.py` | VK table, chord parse/format, `GetAsyncKeyState` polling, safety classifier. |
| `app/ptt/inject.py` | The only module permitted to call `keybd_event`. Paste, modifier neutralisation, `suppress_alt_menu`, focus diagnostics. |
| `app/ptt/audio.py` | `Recorder`: stream lifecycle, pre-roll buffer, idle release. |
| `app/ptt/transcribe.py` | Model load, CUDA detection, CPU fallback, text cleanup. |
| `app/ptt/engine.py` | The state machine. Owns the poll loop; emits state changes to a listener. |
| `app/ptt/logging_setup.py` | `debug_log.txt` writer (`OBS-4`). |
| `app/ptt/ui/tray.py` | pystray icon, menu, state-to-icon mapping. |
| `app/ptt/ui/hotkey_dialog.py` | tkinter capture window. |
| `app/ptt_tray.py` | Entry point: builds the tray UI, starts the engine. Unchanged invocation path. |
| `ptt_dictate.py` | Entry point: prints state to the console, starts the engine. |

The engine must not import the UI. It reports state through a callback the frontend
supplies, which is what allows one core to serve both a tray icon and a console.

---

## 5. Keystroke injection contract

`inject.py` is the only place `keybd_event` may be called. Four rules, each bought with a
bug report:

1. **Every event carries a real scan code** from `MapVirtualKeyW`. UWP targets reject
   synthetic keys without one (`FR-C1`, issue #8).
2. **Navigation-block keys set `KEYEVENTF_EXTENDEDKEY`** (`0x01`). `Insert` is in that
   block (`FR-C1`, issue #8).
3. **`Alt` is disarmed before it is released** — see below (`FR-C3`, issue #11).
4. **Modifier release is conditional and side-aware.** Only modifiers actually reported
   down are released, and `VK_LCONTROL`/`VK_RCONTROL` are released explicitly: injecting
   the unsided `VK_CONTROL` release leaves the right-hand key state set.

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

A chord is an ordered tuple of key names resolved through `VK_MAP`. Left/right variants
are distinct names (`lctrl`, `rctrl`, `lshift`, …); unsided names (`ctrl`) match either
side. `chord_held()` is true when every key in the tuple is reported down by
`GetAsyncKeyState` (`FR-C2`).

### The picker

Tray menu item **Set Hotkey…** opens a small tkinter window (`CON-3`).

- It runs **on its own thread with its own `mainloop`**. pystray menu callbacks run on
  the tray thread, and tkinter requires the thread that created a root window to run its
  loop. Only one dialog may exist at a time; the menu item is disabled while it is open.
- Capture **polls `GetAsyncKeyState`** rather than reading tkinter key events. Same
  reason as `chord_held()`: side-aware virtual keys, and immunity to the hook loss
  described in `FR-C2`. It also means the dialog captures the chord identically to the
  way the engine will later detect it — the picker and the detector share one code path.
- The user holds the desired combination; the dialog records the **maximal set held
  simultaneously**, and settles when everything is released.
- The resulting chord is displayed with any safety warnings, then **Save** or **Cancel**.
- Saving writes `config.json` and **hot-swaps the chord live**. The engine's poll loop
  re-reads the setting each iteration, so no restart is required.

### Safety classifier

`hotkey.py` classifies a candidate chord and returns warnings. This is `FR-C3` made
visible at the moment of choosing, rather than discovered months later:

| Condition | Warning |
|---|---|
| Contains a printable or scrolling key (`space`, letters, digits) | Types a character or scrolls the focused window while held (issue #9). |
| Contains any `Alt` | Activates the target window's menu on release. Neutralised automatically, but a non-Alt chord is safer (issue #11). |
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
  "hotkey": ["rctrl"]
}
```

- Unknown keys are preserved on write, so a newer build's settings survive a rollback.
- An invalid or unrecognised `hotkey` falls back to the default and logs why (`OBS-3`).
- Files without `version` are treated as v1 — that is what today's `{use_gpu}` files are.
- `config.py` owns the schema. No other module reads or writes the file.

---

## 8. Test strategy

**Unit tests** (`tests/`), pure and fast, no Windows API and no audio device:

- `test_hotkey.py` — chord parsing and validation, rejection of unknown key names,
  left/right resolution, human-readable labels, and every row of the safety-classifier
  table above.
- `test_config.py` — round-trip, defaults when the file is absent, fallback when the
  chord is invalid, preservation of unknown keys, v0→v1 migration.

Wired through `pyproject.toml` with `pythonpath = ["app"]` so `import ptt` resolves
without installing anything.

**Manual Win32 probes** (`tests/tools/probe_paste.py`). Menu activation, caret loss, and
paste delivery cannot be unit-tested — they are behaviours of another process's window.
The probe harness from the issue #11 investigation is kept as a runnable script that
reproduces the evidence table in section 5.

Its one non-negotiable design rule: **the harness pins a target window handle and refuses
to inject anything unless that window currently has focus**, re-asserting focus if it
drifted and aborting if it cannot. During the issue #11 work, an earlier version aimed at
whatever `GetForegroundWindow()` returned, and a Notepad window that failed to come to
the front meant a sequence of pastes and select-alls went into an unrelated application.
An input-injecting test harness that trusts ambient focus is a hazard, not a test.

**Regression coverage of the retrospective log.** Each solved issue should map to either
a unit test or a documented probe step. Issues #4, #9, #11 and the `FR-C*` family are the
candidates; #1–#3 and #10 are packaging defects that the build either produces or does
not.

---

## 9. Explicitly not changing

The audio pipeline (`Recorder`, pre-roll, idle release), model parameters, the CUDA DLL
resolution logic, `build_portable.py`, the installer, and the elevation model all stay as
they are. They work, they are covered by the retrospective log, and touching them would
put `NFR-6`/`NFR-7` back in play for no gain.

---

## 10. Sequencing

1. Split into `app/ptt/` with entry-point shims; add `pyproject.toml`. Behaviour-neutral.
2. Add the unit tests and the pinned-window probe harness.
3. Build the picker dialog and the safety classifier on top of the settled config layer.
