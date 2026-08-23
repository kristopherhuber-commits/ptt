# PTT Dictation — GUI handoff

For Claude Code. This document specifies a PySide6 front end for the existing
engine. The engine, hotkey detection, audio, transcription and injection code
**does not change** except where a section below says so explicitly.

Design reference: `PTT Dictation UI v1 (standalone).html` (open in a browser) or
`PTT Dictation UI.dc.html` in this project. Turn 4 in that file is the PySide6
rendering; turn 3 is the same design with every tab built out; turn 2 is the
three-layer interaction wired together.

---

## 1. What is being built

Three layers of UI over the existing engine.

| Layer | Surface | Interactive? |
| --- | --- | --- |
| 1 | Tray icon — existing behaviour, unchanged | Hover and click only |
| 2 | Hover popover — a read-only state display | No controls at all |
| 3 | Settings window — tabs over panels | Yes; the only interactive surface |

Design rule that drives the colour scheme: **dark steel surfaces are read-only,
light surfaces are interactive.** The popover and the window's banner are dark.
Everything below the tab bar is light. Do not invert this — the user chose it
deliberately after seeing the alternative.

## 2. Toolkit

PySide6 (LGPL, so commercial closed-source distribution is fine). Not PyQt6.

Add to `requirements.txt`:

```
PySide6-Essentials
```

`PySide6-Essentials` rather than the full `PySide6`, to keep WebEngine,
Multimedia, Charts and 3D out of the distribution. `build_portable.py` needs no
structural change; verify the resulting zip still extracts and runs, and check
which Qt DLLs the bundle actually pulls in.

Qt replaces `pystray` for the tray icon (`QSystemTrayIcon`). It does **not**
replace `PIL` if you keep drawing the icons programmatically — but see §4.

## 3. Module layout

The existing rule from `docs/design.md` §4 holds: **the engine must not import
the UI.** It reports state through the `on_state` callback its caller supplies.
Keep it that way.

```
app/ptt/ui/
    tray.py        # EXISTS (pystray). Replaced by qt_tray.py, or kept for fallback.
    qt_app.py      # NEW. QApplication owner, wiring, the engine thread bridge.
    qt_tray.py     # NEW. QSystemTrayIcon + state→icon map + context menu.
    qt_popover.py  # NEW. Frameless read-only state panel (layer 2).
    qt_window.py   # NEW. QMainWindow with the tab widget (layer 3).
    panels/
        hotkey.py      # NEW. Keyboard diagram.
        model.py       # NEW. Model table.
        audio.py       # NEW. Device picker + level meter.
        vocabulary.py  # NEW. Replacement rules table.
        advanced.py    # NEW. Engine constants.
        diagnostics.py # NEW. Log tail + probe readouts.
    style.qss      # NEW. The whole visual layer (see §9).
```

`app/ptt_tray.py` becomes a thin entry point over `qt_app.py`, matching how it
currently sits over `TrayApp`.

### Threading — read this before writing any UI code

The engine's `on_state` callback is invoked **from the engine thread, not the UI
thread** (`engine.py` module docstring, contract point 2). `pystray` tolerated
cross-thread setter calls. **Qt does not.** Touching a widget from a non-GUI
thread is undefined behaviour and will crash or corrupt paint state.

Therefore: `on_state` must do nothing but emit a Qt signal. Connect it with
`Qt.QueuedConnection` (the default for cross-thread connections) so delivery
happens on the GUI thread.

```python
class EngineBridge(QObject):
    state_changed = Signal(str, str)     # state, status_text
    text_ready = Signal(str)
    level = Signal(float)                # optional, see §6

    def on_state(self, state, status_text=None):
        self.state_changed.emit(state, status_text or "")
```

Pass `bridge.on_state` to `Engine(...)`. Nothing else in the engine changes.
The contract's other points still apply: the callback must not block, must not
raise, and may fire before the UI is fully built — a signal emission satisfies
all three.

## 4. Layer 1 — tray icon

Existing behaviour is liked and must be preserved exactly. The state→icon map in
`tray.py::create_icon_image` is the specification:

| Engine state | Fill | Outline | Glyph |
| --- | --- | --- | --- |
| `idle` | `#0d9488` teal | `#0f766e` | microphone |
| `recording` | `#ef4444` red | `#b91c1c` | filled circle |
| `transcribing` | `#f59e0b` amber | `#d97706` | rounded square |
| `loading` | `#3b82f6` blue | `#1d4ed8` | 270° arc |

Keep the drawing code. The simplest port is to keep `create_icon_image`
unchanged (it returns a PIL image), convert to `QPixmap` via `QImage` on the raw
bytes, and set that as the `QIcon`. Alternatively re-draw with `QPainter` — but
only if the result is pixel-comparable at 16, 20, 24 and 32 px. The user
explicitly values these icons; do not restyle them.

Tooltip stays as today: `PTT Dictation (<status text>)`.

Two states have no icon today, because the engine reports them as `idle` with a
different status string: **CPU fallback** and **no microphone**. The mockup
proposes a fifth glyph (dark red `#8c3a2e`, exclamation) for hard failures. Treat
that as **optional and out of scope for the first pass** — ship the four existing
icons and raise it separately.

Right-click menu keeps the current items, since the popover has no controls:
Status (disabled), Hotkey (disabled), Use GPU / Use CPU (checkable), Settings…,
Pause, Exit. `Settings…` opens layer 3. Exit must keep the current
non-joining behaviour — `engine.stop()` then quit; do **not** join the engine
thread, or Exit hangs for an in-flight CPU transcription.

## 5. Layer 2 — hover popover

A `QSystemTrayIcon` tooltip is plain text only, so this is a separate widget.

- `QWidget` with `Qt.Tool | Qt.FramelessWindowHint`, `Qt.WA_TranslucentBackground`
  not required (the panel is opaque), `setAttribute(Qt.WA_ShowWithoutActivating)`
  so raising it does not steal focus from whatever the user is typing into.
- Position from `QSystemTrayIcon.geometry()`, clamped to
  `QScreen.availableGeometry()` so it never lands off-screen or under the taskbar.
- Show on hover. `QSystemTrayIcon` has no hover signal, so poll cursor position
  against the icon geometry on a ~150 ms `QTimer`, or install an event filter —
  whichever proves reliable on Windows 11. Hide on mouse-leave after a ~400 ms
  grace period so the pointer can travel from icon to panel without it vanishing.
- `mousePressEvent` anywhere on the panel opens layer 3 and hides the popover.
- **No buttons, no toggles, no links.** It is a display. This is a hard
  requirement from the user.

Content, in this exact order (a `QGridLayout` of `QLabel`s):

| Row | Label | Value |
| --- | --- | --- |
| Header | — | status dot + `PTT Dictation` + engine state code tag |
| 1 | `State` | status text (large) + one-line detail (small, muted) |
| 2 | `Hotkey` | `hotkey.chord_label(settings.hotkey)` |
| 3 | `Model` | model name + `CUDA`/`CPU` tag |
| 4 | `Microphone` | active device name |
| 5 | `Last` | duration + word count of the last transcription |

Footer: `Click anywhere to configure →`.

The status text is the engine's own `status_text` (`Ready (CUDA)`,
`Ready (CPU Fallback)`, `Recording...`, `Transcribing...`, `Loading Model...`,
`Error loading model`). Do not invent new strings; the detail line may be
derived, but the headline must be what the engine reported.

**Layer 3's banner is this same widget's layout, verbatim.** Build it once and
embed the same class in both places. The user specifically asked for the
transition from popover to window to feel like the same object growing.

## 6. Layer 3 — settings window

`QMainWindow`, not frameless — Windows draws the title bar. Width ~880 px,
resizable, minimum size around 820×620. Structure top to bottom:

1. **Banner** — the §5 widget on a dark ground. Read-only, updates live.
2. **`QTabWidget`** — Hotkey · Model · Audio · Vocabulary · Advanced · Diagnostics.
3. **Panel** — the active tab's content, light ground.
4. **`QStatusBar`** — one muted line: state · model · hotkey, plus a transient
   "Saved" confirmation (see below).

There is no OK/Apply/Cancel button box. **Every control applies instantly.**

**Instant-apply semantics.** A control's `valueChanged`/`toggled`/`clicked`
handler does three things, in this order: write the field on `Settings`, call
`Settings.save()`, and ask the engine to act if the change needs it
(`request_model_reload()` for model or device changes; nothing at all for hotkey
or vocabulary, which the engine re-reads on its own). The status bar shows
`Saved · 14:22:07` for a few seconds after each write. No confirmation dialogs
except for deleting a vocabulary rule or a downloaded model, which offer an undo
rather than a prompt where practical.

`Settings.save()` is documented never to raise — a read-only disk logs and
continues rather than taking the app down. Instant-apply relies on that, so do
not add exception handling that surfaces a modal on every keystroke.

### 6.1 Hotkey panel — the behaviour to get right

This is the part a screenshot cannot convey, so it is spelled out.

**What it shows.** A full 104-key keyboard as a `QGridLayout` of checkable
`QPushButton`s. Layout: function row, number row, QWERTY rows, modifier row, plus
the navigation cluster. Keycaps are 28 px tall; modifiers are wider (see the
mockup for relative widths).

**Which keys are bindable.** Exactly the keys in `hotkey.VK_MAP` that are
side-specific physical keys, i.e. the nine: `lctrl`, `rctrl`, `lshift`, `rshift`,
`lalt`, `ralt`, `lwin`, `rwin`, `space`. Every other key on the board is drawn
but **disabled and dimmed to ~30% opacity**. Do not hard-code this list in the
panel — derive it from `hotkey.VK_MAP` so adding a key to the engine adds it to
the UI.

**Three visual states per key**, all three visible at once:

| State | Appearance | Meaning |
| --- | --- | --- |
| Bound | solid accent fill, light text | part of the current chord |
| Held now | accent tint + 2 px inset accent border | the OS reports this key down |
| Bindable | pale accent tint, hairline accent border | can be clicked to bind |
| Not bindable | dimmed, disabled | drawn for orientation only |

**The live "held now" shading is a requirement.** While the window is open, every
key the user physically presses shades on the diagram the instant it goes down
and unshades on release — including non-bindable keys, so the board reads as live
hardware and the user can see the app is registering their keyboard. Implement it
by polling `GetAsyncKeyState` on a ~30 ms `QTimer` for the keys on the board
(the same mechanism `hotkey.chord_held` already uses, and for the same reason:
Windows silently unregisters low-level hooks after UAC prompts, screen locks,
sleep and USB hotplug — see `docs/development_history.md`, issues #7 and #8). Do
**not** use `keyPressEvent`; it only fires for the focused window and misses the
release when focus moves. Clear all held states on `focusOutEvent` and on window
hide so nothing gets stuck shaded.

**How binding is committed.** Clicking a bindable key toggles it in the chord.
Clicking the only bound key leaves it bound (never allow an empty chord —
`hotkey.parse_chord` rejects empty and the engine would fall back). Chords of up
to three keys are allowed; a fourth click replaces the chord with just that key.
Write through as `settings.hotkey = (…)` — a whole-tuple rebind, never a mutation.
`config.py`'s docstring explains why: the engine re-reads `settings.hotkey` on
every poll iteration, and an attribute rebind is atomic where a list mutation is
not. This is what lets the new chord take effect with no restart, so **do not add
a lock and do not make `Settings` frozen.**

**Compatibility warning box.** A live panel beside the chord readout, driven by
the chosen chord. The rules, all learned the hard way:

- chord contains `alt` → "Alt chords activate the focused window's menu bar on
  release, which steals focus and can discard the paste." (The engine already
  disarms this via `inject.suppress_alt_menu`, so this is a caution, not a block.)
- chord contains `space` → "Space types a character into whatever has focus while
  you hold it."
- chord is a multi-key combination including a shift → "Ctrl+Shift and Alt+Shift
  are Windows' keyboard-layout switches when a second layout is installed."
- otherwise → "Safe: types no character, scrolls nothing, activates no menu bar."

**"Match either side" checkbox.** When checked, a side-specific binding is
written unsided (`rctrl` → `ctrl`), which `hotkey.VK_MAP` resolves to either
side. Show the resulting JSON (`"hotkey": ["rctrl"]`) next to the chord so the
user can see exactly what lands in `config.json`.

### 6.2 Model panel

A `QTableView` over a small model, one row per Whisper size tier: `tiny.en`,
`base.en`, `small.en`, `medium.en`, `large-v3`, `large-v3-turbo`. Columns:

| Column | Source |
| --- | --- |
| Model + parameter count | static table |
| Disk size | static table; actual bytes when present locally |
| Character | one short static phrase, e.g. "fastest, least accurate" |
| Measured | latency in seconds on this machine, or `—` if never measured |
| State | `Downloaded` / `Not on disk`, from `paths.local_model_dir(name)` |

The bars are a `QStyledItemDelegate`, not widgets in cells.

**Decided: measure on demand, and show nothing until measured.** The speed and
accuracy bars in the mockup are invented placeholders and must not ship as fact.
Instead:

- A `Measure on this machine` button transcribes the bundled
  `app/assets/benchmark_sample.wav` (30 s, 16 kHz mono, recorded once with
  `docs/gui_handoff/record_sample.py`) with the selected model and records the
  wall time. Store results under a new `benchmarks` key in `config.json`, keyed by
  model name and device, with a timestamp.
- The `Measured` column shows that latency, or `—` for models never measured.
  Draw the relative bar only across rows that have real numbers.
- Do **not** show an accuracy column. Word error rate cannot be measured without
  a labelled corpus, and quoting published WER figures for a different dataset
  would be misleading in a settings window. The `Character` column carries the
  qualitative tradeoff instead, which is honest and is what the choice actually
  turns on.
- Measuring downloads the model if absent. Say so on the button's tooltip.

GPU/CPU choice is a pair of `QRadioButton`s beside the table, replacing the tray
menu's checkboxes as the primary control (keep the menu items too). On change: `settings.use_gpu = …`,
`settings.save()`, `engine.request_model_reload()`. Selecting a different model
does the same. Both must respect the existing rule that **hardware has the last
word** — if `cuda_supported` is False the engine forces `use_gpu = False`, so the
toggle must be disabled and show why, not silently spring back.

Requires one engine change: `transcribe.MODEL_SIZE` is currently a module
constant. Make the model name a `Settings` field (`model: str =
"large-v3-turbo"`, validated against the known list, falling back with a logged
reason like every other field) and pass it into `load_model_with_fallback`.
Follow `config.py`'s existing validation pattern exactly — validate the value,
log the reason for any fallback (OBS-3), and preserve unknown keys.

Download management (fetching a model not on disk, showing progress, deleting
one) is **out of scope for the first pass** unless the user asks. The buttons are
in the mockup; wire them to a stub that reports "not yet implemented" rather than
half-implementing a download.

### 6.3 Audio panel

- Device list from PortAudio, via the existing `audio` module, in a `QComboBox`.
  The list includes a "Follow the Windows default device" entry (the current
  behaviour, and the default). A combo box rather than radio buttons for two
  reasons: the device count is unknown at design time, and it keeps this panel to
  one control type — checkboxes — instead of mixing radios and checkboxes in one
  view.
- Live input level meter — a custom `QWidget.paintEvent`, fed by a signal from
  the audio thread. Never read the audio buffer from the GUI thread.
- Three checkboxes: keep the stream warm while active (maps to
  `IDLE_THRESHOLD_SEC`), ignore holds shorter than 0.30 s (maps to
  `MIN_RECORD_SEC`), and an optional start-of-recording click (new; skip if it
  needs new audio-output code).

Selecting a specific device is a new capability — the recorder currently takes
the default. Add a device index to `Settings` and to `audio.Recorder`, keeping
`None` meaning "system default" so existing configs behave exactly as now.

### 6.4 Vocabulary panel

New capability. A `QTableView` of replacement rules: heard → typed, plus a scope
column (Always / specific app classes). Applied to the transcript **after**
`transcribe.clean_text` and **before** `inject.paste_text`. Whole-word,
case-insensitive matching. Store as a list of objects under a new `vocabulary`
key in `config.json`, validated field by field like everything else.

Keep the substitution pure and in its own function so it is unit-testable without
a model, matching how `clean_text` and `parse_chord` are already written.

### 6.5 Advanced panel

Read-mostly list of the engine's constants, with their current values:

| Setting | Constant | Value |
| --- | --- | --- |
| Beam size | `transcribe.BEAM_SIZE` | 5 |
| Voice activity filter | `vad_filter` in `transcribe_audio` | On |
| Minimum hold | `engine.MIN_RECORD_SEC` | 0.30 s |
| Release microphone when idle | `engine.IDLE_THRESHOLD_SEC` | 240 s |
| Paste method | `inject.paste_text` | Shift+Insert |
| Language | `transcribe.LANGUAGE` | en |

Plus a "Start with Windows" checkbox reflecting whether the Startup-folder
shortcut the installer creates is present.

Every one of these values fixed a specific documented failure. If you make one
editable, it becomes a `Settings` field with validation and a logged fallback —
not a raw write. Shift+Insert in particular is load-bearing for WSL and bash
terminals; if you expose it, warn on change.

### 6.6 Diagnostics panel

- Three readouts: CUDA device count and compute type, median transcription
  latency, last paste target window class (all already logged today).
- Tail of `debug_log.txt` in a monospace read-only view, newest at the bottom.
- Buttons: open log folder, reload model.

Read the log through `paths` — do not construct the path independently.

## 7. State → UI contract

One table, used by all three layers. `state` is the engine's own value.

| `state` | Dot | Popover headline | Tray icon |
| --- | --- | --- | --- |
| `loading` | amber | `Loading Model...` | blue arc |
| `idle` | steel | `Ready (CUDA)` / `Ready (CPU Fallback)` | teal mic |
| `recording` | red | `Recording...` | red circle |
| `transcribing` | amber | `Transcribing...` | amber square |
| error paths | dark red | `Error loading model` / `Error: …` | teal mic (today) |

The dot colours are UI-only and come from the design system's ramp. The tray icon
colours are the existing ones in §4 and are not part of the design system —
that is deliberate: they are functional signalling the user already knows.

## 8. Fonts

Barlow and Barlow Condensed are Google Fonts (SIL Open Font License — free to
bundle and redistribute, including commercially). They will not be on target PCs,
so the TTFs are committed to the repo. **They are already in place**, as
downloaded from Google Fonts, with their `OFL.txt` licences:

```
app/assets/fonts/Barlow/Barlow-Regular.ttf
app/assets/fonts/Barlow/Barlow-Medium.ttf
app/assets/fonts/Barlow/Barlow-Bold.ttf
app/assets/fonts/Barlow_Condensed/BarlowCondensed-SemiBold.ttf
```

Those four are the ones actually used — Barlow 400/500/700 for body text and
values, Barlow Condensed 600 for headings and the small-caps labels. The
directories also contain the other weights and every italic; leave them in the
repo but do not register them, and make sure `build_portable.py` either ships the
whole `assets/fonts` tree or is told to include exactly these four files. Keep
both `OFL.txt` files wherever the fonts end up in the distribution — that is the
licence condition.

Register them at startup, before any widget is created:

```python
QFontDatabase.addApplicationFont("app/assets/fonts/Barlow/Barlow-Regular.ttf")
```

Resolve the path relative to the application directory via `paths`, not the
working directory — the app is launched from a desktop shortcut and from the
Startup folder, so the CWD is not predictable.

Barlow Condensed for headings and small caps labels, Barlow for body, and a
monospace face (Consolas is on every Windows 11 box) for values, key names, JSON
and log lines. If registration fails, fall back to Segoe UI rather than letting
Qt pick — and log it.

## 9. Styling

One `style.qss` applied to the `QApplication`, holding every colour. Take the
values from `industry.css` in this project — the design system's token sheet.
Do not invent colours.

| Role | Value |
| --- | --- |
| Light ground (interactive) | `#f2f2f3` |
| Light surface | `#e9e9ea` |
| Text on light | `#1d1f20` |
| Dark ground (read-only) | `#1d2d3d` (accent-900) |
| Text on dark | `#e7eaee` |
| Accent | `#5980a6` |
| Accent on dark | `#94bce3` |
| Hairline border | `#1d1f20` at 16% |
| Bound key fill | `#5980a6`, light text |
| Held key tint | `#d6ebff` with a 2 px `#5980a6` inset border |
| Checked box / radio | `#5980a6` fill, `#f2f2f3` check or centre |

Everything is square — `border-radius: 0` on buttons, inputs, tabs, cells and
frames. One exception: the primary button is the only solid accent fill in the UI.

Two things QSS cannot do, needing a `paintEvent` override:

- the `+` registration marks at panel corners (`.blueprint` in the reference);
- the level meter and the table bars.

**Controls are native Qt widgets — no custom-drawn switches.** Every on/off
setting is a `QCheckBox`; an either/or choice with a fixed, known set of options
is a pair of `QRadioButton`s (GPU vs CPU is the only one); a list whose length is
not known at design time is a `QComboBox` (the microphone picker). **Never mix
radio buttons and checkboxes in the same panel** — that was a specific review
note. The design was revised to avoid skinning a switch: colour the indicators
from the table above via QSS (`::indicator`, `::indicator:checked`) and stop
there. Do not write a custom switch widget.

## 10. Acceptance criteria

1. Tray icon behaves exactly as today: four colours, four glyphs, same tooltip
   format, same right-click items, same non-hanging Exit.
2. Hovering the tray icon raises the popover without stealing keyboard focus from
   the focused application. Typing in another window while it is up is unaffected.
3. Clicking the popover opens the settings window; the banner shows identical
   content to the popover it replaced.
4. Pressing any key while the Hotkey panel is visible shades that key within
   ~50 ms and unshades it on release. Alt-tabbing away clears all shading.
5. Clicking `Right Shift`, then holding Right Shift, starts recording — with no
   restart, because the engine re-read `settings.hotkey` on its next poll.
6. Switching GPU→CPU reloads the model and the banner passes through
   `Loading Model...` then `Ready (CPU)`. `config.json` is written before the
   reload starts, and the status bar confirms the save.
7. On a machine without CUDA, the GPU toggle is disabled with a visible reason,
   and `config.json` shows `use_gpu: false`.
8. `config.json` written by this build is readable by the current build, and a
   `config.json` from the current build loads with no warnings. Unknown keys
   survive a round trip (`future_setting` in the existing file is the test case).
9. No UI object is touched from the engine thread. Verify by asserting
   `QThread.currentThread() == qApp.thread()` in the state handler during
   development.
10. `python build_portable.py` produces a zip that extracts and runs on a clean
    Windows 11 machine, and `install.bat` still creates both shortcuts.

## 11. Out of scope for the first pass

- Model downloading, progress and deletion (stub the buttons).
- The `pystray` tray implementation: **delete** `app/ptt/ui/tray.py` and drop
  `pystray` from `requirements.txt` once stage 1's Qt tray passes its acceptance
  criteria. Do not keep both. Git history is the fallback.
- The proposed fifth tray icon for hard failures.
- Real accuracy figures for the model table (measured latency only — see §6.2).
- Per-application behaviour rules.
- Cloud transcription APIs.
- Multiple hotkey bindings for different actions.

## 12. Source of truth

- `docs/requirements.md` — what the utility must do and why the constraints exist.
- `docs/design.md` §4 module layout, §6 hotkey design, §7 config ownership.
- `docs/development_history.md` — the retrospective. Read issues #1, #4, #7, #8,
  #9 and #11 before touching hotkey, injection or CUDA loading code.
- `industry.css` — the design system's tokens.
- `PTT Dictation UI v1 (standalone).html` — the visual reference.

Where this document and the code disagree, the code is right and this document
needs fixing. Where this document and a screenshot disagree, this document is
right.
