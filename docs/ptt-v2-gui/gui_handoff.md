# PTT Dictation — GUI handoff

For Claude Code. This document specifies a PySide6 front end for the existing
engine. The engine, hotkey detection, audio, transcription and injection code
**does not change** except where a section below says so explicitly.

Design reference: `PTT Dictation UI v1 (standalone).html` (open in a browser) or
`PTT Dictation UI.dc.html` in this project. Turn 4 in that file is the PySide6
rendering; turn 3 is the same design with every tab built out; turn 2 is the
three-layer interaction wired together.

**Status.** All three layers and all six panels are built. What remains is §10's
acceptance pass and packaging, and the `+` registration marks in §9. Sections
marked **As built** record where the shipped code differs from what this document
originally specified and why; §12's rule applies — the code is right, and these
notes are this document being brought into line with it. Nothing specified here
has been dropped, and where a divergence would have cost something, the code was
changed rather than the requirement.

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
    qt_app.py          # QApplication owner, wiring, the engine-thread bridge.
    qt_tray.py         # QSystemTrayIcon + state→icon map + context menu.
    qt_popover.py      # Frameless read-only state panel (layer 2).
    qt_statusview.py   # The state display itself, embedded in both the popover
                       #   and the window's banner — §5 requires one class.
    qt_window.py       # QMainWindow with the tab widget (layer 3).
    qt_theme.py        # Font registration and style.qss, applied to the app.
    panels/
        __init__.py    # InstantApplyPanel — the write → save → notify sequence
                       #   every control routes through. See §6.
        hotkey.py      # Keyboard diagram.                  BUILT
        model.py       # Model table.                       BUILT
        audio.py       # Device picker + level meter.       BUILT
        vocabulary.py  # Replacement rules table.           BUILT
        advanced.py    # Engine constants.                  BUILT
        diagnostics.py # Log tail + probe readouts.         BUILT

app/assets/
    style.qss          # The whole visual layer (see §9).
    fonts/             # Barlow + Barlow Condensed, with both OFL.txt licences.
    benchmark_sample.wav
```

**As built:** one module was added outside `ui/` — `app/ptt/vocabulary.py`, holding
the `Rule` type, its validation and the substitution itself. It is pure and has no
Qt import, for the reason §6.4 gives: the matching rules are a fact about the
application rather than about the widgets that edit them, and putting them beside
the panel would make them untestable without an event loop. It sits next to
`hotkey.py`, which is the same shape for the same reason.

**As built:** `style.qss` and the fonts live under `app/assets/`, not inside
`app/ptt/ui/`. `design.md` §4 makes `paths.py` the only module allowed to compute
a directory, so they are reached through `paths.asset_path()`; `build_portable.py`
walks `app/` wholesale, so they ship with no change to the build script.
`tray.py` and `pystray` were deleted in session 1, per §11.

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
Exit. `Settings…` opens layer 3. Exit must keep the current
non-joining behaviour — `engine.stop()` then quit; do **not** join the engine
thread, or Exit hangs for an in-flight CPU transcription.

**`Pause` was declined.** Earlier drafts of this section listed it between
`Settings…` and `Exit`, and `stage0_review.md` §3.2 pointed out that nothing in
the engine could implement it. It was struck in session 5 as a deliberate
decision, not an omission: exiting and relaunching is the pause, and a menu item
that only suspends the hotkey would need a fifth tray icon, a status string the
engine cannot supply because the pause lives in the frontend, and a ruling on
whether it survives a restart — three decisions for a case a right-click already
covers. Should it ever be wanted, **it needs no engine change**:
`Engine.__init__` takes a `chord_held` seam, so a frontend passing
`lambda chord: (not self._paused) and hotkey_mod.chord_held(chord)` gets it, and
those three decisions are what to design rather than the mechanism.

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

**As built: the rows elide.** Every value and the derived detail line are
`ElidedLabel`, not `QLabel`. Two reasons, both found the hard way. The detail
line used to word-wrap, and a wrapping label inside a nested layout in a
`QGridLayout` reports a one-line height — so at the popover's fixed 340 px the
grid allocated one line and the wrapped second line was **drawn over the
headline**. And a real device name is 72 characters, which simply ran off the
right-hand edge with nothing to say it had. Eliding fixes both, and it is the
honest failure: an ellipsis says text was cut where clipping says nothing.
Nothing is lost — this same widget in the window is wide enough to show the
whole string, which is exactly the glance-versus-detail split this section
already draws between the two layers.

## 6. Layer 3 — settings window

`QMainWindow`, not frameless — Windows draws the title bar. Width ~880 px,
resizable, minimum size around 820×620. Structure top to bottom:

**As built:** it opens at 880×800 and each tab sits in a `QScrollArea`. The
minimum stays 820×620 and is honoured; the scroll area is what makes it safe. The
banner alone is 254 px because §5 requires it to be the popover's layout verbatim,
the keyboard is 188 px, and the model table shows six tiers at once — a window
that opened already scrolled would hide the compatibility warnings on one tab and
the Measure button on the other. Squeezing a `QHBoxLayout` below its widgets'
minimum overlaps them rather than clipping them, so without the scroll area a
keyboard drawn at the minimum size renders on top of itself.

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

**What it shows.** A full 104-key keyboard of checkable `QPushButton`s:
function row, number row, QWERTY rows, modifier row, the navigation cluster and
the numeric keypad. Keycaps are 28 px tall and 28 px per unit with a 4 px gap, so
an *n*-unit cap is `32n - 4` px wide (see the mockup for relative widths).

**As built:** the main block is `QHBoxLayout` rows inside a `QVBoxLayout` rather
than one `QGridLayout`. A grid aligns columns across rows and a keyboard's main
block deliberately does not — 1.5u and 1.75u caps stagger every row against the
one above — so a grid could only express it by giving every cap a span in some
fine unit, which is a worse way to write the same pixels. The **keypad** *is* a
`QGridLayout`, because its cells do align and its `+` and `Enter` are two rows
tall. The mockup draws no keypad; this document says 104 keys and §12 makes this
document right, so the keypad is drawn.

The keypad's digits report `VK_NUMPAD0`–`9` only while Num Lock is on; with it
off the same physical keys report `VK_HOME`, `VK_END` and the rest, so keypad 7
shades `Home` on the main block instead. Both `Enter` keys share `VK_RETURN` —
Windows separates them with an extended-key flag that `GetAsyncKeyState` does not
carry — so they shade together. Both are the hardware being reported accurately
and neither is worth hiding.

**Which keys are bindable.** Exactly the side-specific physical keys plus
`space` — the nine: `lctrl`, `rctrl`, `lshift`, `rshift`, `lalt`, `ralt`, `lwin`,
`rwin`, `space`. Every other key on the board is drawn but **disabled and dimmed
to ~30% opacity**. Do not hard-code this list in the panel; adding a key to the
engine must add it to the UI.

**As built:** that set is not derivable from `VK_MAP` alone, which was this
document's instruction and is impossible as written — `VK_MAP` has thirteen
entries, four of them unsided aliases, and carries no attribute separating a
physical key from an alias (stage 0 review §3.4). `hotkey.py` therefore gained
`KEYS`, one declarative table that `VK_MAP`, `KEY_LABELS`, `BINDABLE_KEYS` and
`BINDABLE_BY_VK` are all derived from.

The panel uses `BINDABLE_BY_VK`: a cap may be bound iff the engine's table has a
bindable name for that cap's **virtual key**, which the cap already carries for
the live shading. The panel names no keys at all, so adding one to `KEYS` lights
up the matching cap with no edit to the UI, and removing one dims it.

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
release when focus moves. Nothing may stay shaded once the window is not in front.

**As built:** the polling goes through `hotkey.poll_vks`, so the picker and the
detector share one Win32 call site and one failure mode. Only bit `0x8000` is
tested; bit `0x0001` is "pressed since the last call" and is cleared per caller,
so reading it here would race the engine's own polling.

Clearing is done by the poll itself — `isVisible() and isActiveWindow()`, checked
on every tick — not by `focusOutEvent`. `focusOutEvent` never fires for this
widget: focus lives on the caps and the checkbox, never on the panel. Asking the
two questions on a timer that is already running cannot miss a case; alt-tab,
minimise, switching tabs and closing the window all land in the same branch.
`hideEvent` also stops the timer and clears, so a key held at the moment the tab
changed is not still shaded when it comes back.

Only the caps whose state actually changed are re-polished. Qt resolves a
`[held="true"]` selector when a widget is polished and not again, and restyling
104 widgets 33 times a second is not free.

**How binding is committed.** Clicking a bindable key toggles it in the chord.
Clicking the only bound key leaves it bound (never allow an empty chord —
`hotkey.parse_chord` rejects empty and the engine would fall back). Chords of up
to three keys are allowed; a fourth click replaces the chord with just that key.
Write through as `settings.hotkey = (…)` — a whole-tuple rebind, never a mutation.
`config.py`'s docstring explains why: the engine re-reads `settings.hotkey` on
every poll iteration, and an attribute rebind is atomic where a list mutation is
not. This is what lets the new chord take effect with no restart, so **do not add
a lock and do not make `Settings` frozen.**

**As built**, four cases this document left open (stage 0 review §5.7–§5.9):

- **Order.** A chord the panel builds is put in `hotkey.KEYS` order by
  `hotkey.canonical()`. Without it the same three keys chosen in a different order
  would rewrite `config.json` and relabel the tray menu for no change in
  behaviour.
- **A chord already in `config.json` is never rewritten on the user's behalf** —
  not reordered, not re-spelled, not truncated. Only a chord the user builds is
  canonicalised, so a four-key chord or a hand-written unsided name survives being
  looked at. The three-key cap applies to what a *click* may build.
- **An unsided name lights both of its caps**, because both really are bound —
  `chord_held` fires on either one.
- **Clicking one cap of an unsided binding narrows it to the other side** rather
  than unbinding the pair. `ctrl` + a click on Left Ctrl becomes `rctrl`. Nothing
  is lost, the chord keeps its length, and it cannot produce an empty chord.

**Compatibility warning box.** A live panel beside the chord readout, driven by
the chosen chord.

**As built:** the rules live in `hotkey.classify()`, which returns a *list* of
warnings, with `hotkey.SAFE_NOTE` shown when that list is empty. It is pure — no
Win32, no Qt — so every rule is testable without a keyboard, and the panel renders
what it returns instead of restating the rules beside the widgets. `design.md` §6
holds the table and is the authority; this document's inline version differed from
it in three ways and lost on all three. The `alt` and `space` rules this document
listed are unchanged and still fire, word for word; the rule set only grew:

- **The layout-switch rule is `Alt+Shift` or `Ctrl+Shift` specifically**, not "any
  multi-key combination including a shift". Warning on `Win+Shift` or
  `Ctrl+Alt+Shift` cries wolf and trains the user to ignore the box. No chord
  loses its only warning to this narrowing — those two examples warn for Win and
  for Alt respectively.
- **A lone unsided `ctrl` or `shift` warns** that it fires during ordinary typing.
  This document dropped that row, and it is the one rule guarding the exact
  configuration the "match either side" checkbox exists to create.
- **A chord containing `win` warns** that it opens the Start menu on release.
  Neither table had this. `inject.suppress_alt_menu` neutralises the Alt case and
  has no Win equivalent, so without it the box prints "Safe: … activates no menu
  bar" over Left Win, which is false.

Warnings never block a save. The user may know better than the classifier; the
point is that they choose knowing — which is also why an unsafe chord is not
shown in the same grey as a safe one. Each warning is prefixed `Warning:` and the
box outlines itself and switches to amber, so the state is legible from across
the panel rather than only once the paragraph is being read. The box is 420 px
wide, which fits the two-warning chords (any Alt combination, the common way to
pick a bad hotkey) without the panel scrolling.

The chord chip and the `"hotkey": [...]` readout are on **separate lines**, not
side by side as the mockup draws them: a three-key chord makes that one line
about 540 px wide, which competes with the box for the panel's width and forces a
horizontal scrollbar. A settings window that scrolls sideways is worse than one
that stacks two short lines.

**"Match either side" checkbox.** When checked, a side-specific binding is
written unsided (`rctrl` → `ctrl`), which resolves to either side. Show the
resulting JSON (`"hotkey": ["rctrl"]`) next to the chord so the user can see
exactly what lands in `config.json`.

**As built**, with the two corrections the stage 0 review (§3.5, §4.4) required
before this control could ship:

- **`win` was broken and is fixed.** `VK_MAP["win"]` was `0x5B`, which is
  `VK_LWIN` — Windows has no unsided Win virtual key — so `["win"]` claimed to
  match either side and detected only the left one. Each `hotkey.KEYS` entry now
  carries *every* virtual key that satisfies its name and `chord_held` tests
  `any`, so the claim is true. Ticking this box with Right Win bound no longer
  silently stops the hotkey responding.
- **The classifier runs on the chord as written, not as clicked.** This is the
  one control that can turn a safe binding into a hazardous one, and the box has
  to say so at the moment it happens rather than describing the chord the user no
  longer has. Ticking it with `rctrl` bound immediately replaces "Safe" with the
  lone-unsided-modifier warning.
- The box is checked when every sided key in the chord is already unsided, and is
  disabled for a chord with no sided key (`space` alone). Clearing it has to pick
  a side, and picks the **right-hand** one: ordinary typing reaches for the
  left-hand Ctrl, Shift and Alt for every `Ctrl+C` and every capital letter, which
  is the same reason the shipped default is Right Ctrl.

### 6.2 Model panel

A `QTableView` over a small model, one row per Whisper size tier: `tiny.en`,
`base.en`, `small.en`, `medium.en`, `large-v3`, `large-v3-turbo`. Columns:

| Column | Source |
| --- | --- |
| Model + parameter count | static table |
| Disk size | static table; actual bytes when present locally |
| Character | one short static phrase, e.g. "fastest, least accurate" |
| Measured | latency in seconds on this machine, or `—` if never measured |
| State | `Downloaded` / `Not on disk` — see the note below |

The bars are a `QStyledItemDelegate`, not widgets in cells.

**As built:** the State column checks **both** `paths.local_model_dir(name)` and
the Hugging Face cache. `local_model_dir` only ever finds a model *bundled* beside
the app, and `resolve_model_path` falls back to letting faster-whisper fetch by
name — so reading only the first printed "Not on disk" beside the 1.6 GB model the
app was running at that moment. The cache is **scanned** rather than addressed by
a constructed repository name: the repo a tier resolves to is not derivable from
the tier (`large-v3-turbo` comes from `mobiuslabsgmbh/`, the smaller tiers from
`Systran/`), so the repo id is matched by suffix, which needs no guess about who
publishes what. The Disk column shows the real byte count for anything found and
a `~`-prefixed estimate otherwise, so the two are never confused.

**Decided: measure on demand, and show nothing until measured.** The speed and
accuracy bars in the mockup are invented placeholders and must not ship as fact.
Instead:

- A `Measure on this machine` button transcribes the bundled
  `app/assets/benchmark_sample.wav` (30 s, 16 kHz mono, recorded once with
  `docs/ptt-v2-gui/record_sample.py`) with the selected model and records the
  wall time. Store results under a new `benchmarks` key in `config.json`, keyed by
  model name and device, with a timestamp.

  **As built:** the button calls `Engine.request_benchmark()`, which is serviced
  at the top of the poll loop and times the model **already resident**. Selecting
  a row loads that model, so the selected model and the loaded model are the same
  one and no second `WhisperModel` is ever allocated. That was the stage 0 review's
  §4.6 objection and it is not theoretical: `large-v3` measured while
  `large-v3-turbo` is resident is 3.1 GB plus 1.6 GB of float16 weights on one
  card before activations, and an allocation failure during a *measurement* must
  not be able to take down the model dictation depends on. It also makes the
  number mean something — a latency measured while another model holds VRAM is not
  comparable to one measured beside it.

  The trade-off, stated plainly: measuring a tier means selecting it first, which
  loads it. Dictation pauses for the measurement, which the banner says throughout
  (`transcribing` / `Measuring <model>...` — a new status string, but no new state,
  so §7's table needs no new row). The result travels back through the same
  `EngineBridge` hop as every other engine callback; nothing writes `config.json`
  off the GUI thread.

  `transcribe.load_benchmark_clip()` converts the 16-bit WAV to the float32 buffer
  inference wants, so the measured path is byte-for-byte the dictation path from
  `transcribe_audio` inwards. It raises rather than guessing if the clip is not
  mono 16-bit 16 kHz.

  Each stored entry is `{"seconds": float, "at": ISO-8601, "clip": digest}`, keyed
  `"<model>|<device>"`. The clip digest is not decoration: `record_sample.py` says
  the clip must never be re-recorded because that invalidates every cached figure,
  and nothing enforced it. Measurements taken against a different clip no longer
  match and are simply not shown.
- The `Measured` column shows that latency, or `—` for models never measured.
  Draw the relative bar only across rows that have real numbers.
- Do **not** show an accuracy column. Word error rate cannot be measured without
  a labelled corpus, and quoting published WER figures for a different dataset
  would be misleading in a settings window. The `Character` column carries the
  qualitative tradeoff instead, which is honest and is what the choice actually
  turns on.
- Measuring downloads the model if absent. Say so on the button's tooltip.

  **As built:** the warning moved to where the download now happens. Because the
  measured model is the resident one, it is **selecting** a tier that fetches it,
  not measuring it — by the time Measure is pressed the model is already loaded.
  The panel's blurb therefore carries the note ("Selecting one loads it
  immediately … and downloads it first if it is not already on disk") and the
  Measure tooltip carries the one that belongs to it ("Dictation pauses while it
  runs"). Nothing is unsaid; it is said next to the control that does it.

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

**As built:** it was four changes, not one (stage 0 review §3.6). The constant was
read in three places — `resolve_model_path()` closed over it and now takes a
parameter; `load_model_with_fallback()`'s **CPU fallback path** re-read it
directly, which was a latent bug where a bundled local model was used for the CUDA
attempt and then re-downloaded by name for the fallback, and now reuses the
resolved path; and `ptt_tray.py`'s startup log line, which OBS-3 covers, moved
below `config.load()`. The fourth is `Engine.request_benchmark` above.

`transcribe.MODELS` now holds the tier table — name, parameter count, disk
estimate, character phrase — and `config.py` validates `model` against
`MODEL_NAMES` derived from it, so the panel's rows and the accepted values cannot
drift apart. An unrecognised name falls back to `DEFAULT_MODEL` with a logged
reason rather than being handed to faster-whisper, which would try to fetch it
from Hugging Face by that name.

One further change this document did not call for, in `config.py`:
`Settings.save()` now writes a temp file and `os.replace()`s it into place under a
lock. Instant-apply turns a handful of writes per process into one per click, and
gives the file two writers — the GUI thread on every control and the engine thread
on a CUDA fallback. `"w"` truncates first, so the old code's failure mode was a
zero-byte `config.json`, which `load()` handles correctly by falling back, meaning
the user's symptom is their settings silently resetting (stage 0 review §4.3). The
lock guards the **file**; it is not the field lock `Settings`' docstring forbids,
and the engine's live re-read stays lock-free.

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

**As built.** Four notes, two of them corrections this document needs.

*The two behaviour checkboxes gate their constants; they do not write zero into
them.* Stage 0 §4.2 is right that `IDLE_THRESHOLD_SEC` and `MIN_RECORD_SEC` are
durations, and that a checkbox writing 0 would switch off `NFR-4` and `FR-3` —
a zero idle threshold closes and reopens the device on every poll iteration,
which is issue #6 at 50 Hz. It is also right that what the user wants to say
about them is on or off. Both are true at once, so `keep_stream_warm` and
`ignore_short_holds` are booleans that decide whether their constant applies,
the constants keep their values, and §6.5's table shows each value **and says
when it is currently bypassed**. Turning the warm stream off means the stream is
opened by `rec.start()` when the chord goes down and released when the recording
ends — the real cost, paid deliberately, rather than a constant set to nonsense.

*The click is implemented.* §6.3 says to skip it "if it needs new audio-output
code". It needs ten lines of `winsound.MessageBeep`, which is Windows stdlib and
so is neither a dependency nor a build change, and it is asynchronous — measured
at 80 µs, so it is safe on the poll thread, where a blocking beep would delay
`rec.start()` and eat the first word. It is off by default and the panel says
why: it plays through the Windows output device, so an open desktop microphone
can hear it and it lands in the recording.

*The meter polls; nothing signals it.* Stage 0 §4.5's objection is accepted in
full. `Recorder._callback` computes a peak and rebinds a float; the panel reads
it on a 30 Hz `QTimer`. Emitting a Qt signal from PortAudio's realtime callback
allocates, locks and wakes the GUI thread several hundred times a second, and a
callback that overruns its deadline drops audio — which in a dictation app means
dropped words. It is also the shape the chord's live re-read already uses.
The scale is dB rather than linear amplitude: speech peaks around 0.05–0.2, which
on a linear bar is a twitch at the left-hand end and reads as a broken microphone.

*The picker shows a filtered list, and the filter is measured rather than
chosen.* PortAudio reports every device **once per Windows audio API**. On the
development laptop that is fourteen rows for one array microphone: two
placeholders meaning "the default device", two `PC Speaker` **outputs** whose
kernel-streaming pins advertise input channels, a `Stereo Mix` loopback, and one
entry that cannot be opened at all. A user cannot be asked to choose between
fourteen rows describing one microphone, so `audio.pickable_devices` offers the
copies from a single host API. Which one was decided by opening each:

| Host API | At 16 kHz | Device name |
| --- | --- | --- |
| Windows DirectSound | opens | full, 72 characters |
| MME | opens | **truncated at 31 characters** |
| Windows WASAPI | **fails** — `Invalid sample rate [PaErrorCode -9997]` | full |
| Windows WDM-KS | mixed; one entry refuses outright | raw pin names |

WASAPI is the modern API and the tempting default, and it is unusable here:
PortAudio opens it in shared mode, where the stream must match the device's mix
format, and this microphone's is 48 kHz. `SAMPLE_RATE` is Whisper's 16 kHz and is
not negotiable, so a WASAPI row is a row that cannot record. DirectSound is
preferred, MME is the fallback, and WDM-KS is never offered.

MME's truncation is where the popover's `Microphone` row got
`Microphone Array (Intel® Smart `, cut off mid-word — the default device resolves
through MME. `audio._expand_name` recovers the full name from another API's copy
of the same device, and only for names at exactly the 31-character limit, so it
cannot lengthen a name that was simply short.

Nothing is hidden silently: the whole enumeration is written to debug_log.txt
once per run with every index and a `[hidden]` marker, `audio_device` accepts any
of those numbers, and a device set that way is shown in the picker with its host
API named so it is not mistaken for a row the picker offered.

*A device is opened defensively, and the saved index is never rewritten.* A
PortAudio index is not a stable identifier — the numbering shifts when a device
is plugged in or removed — so a saved index is checked before it is used, and
falls back to the Windows default with a logged reason if it no longer names an
input. That is not sufficient on its own: **PortAudio lists devices it cannot
open.** Several WDM-KS entries on the development machine advertise two input
channels and then fail with `Invalid device [PaErrorCode -9996]`, and without a
second fallback, picking one from the combo box left the application with no
stream at all — the hotkey doing nothing, nothing on screen to say why. So the
open falls back to the default too, the same shape as
`transcribe.load_model_with_fallback`. The setting is not rewritten either way:
an unplugged headset comes back, and forgetting the user's choice because it was
missing once is the worse failure. The device the stream actually opened on is
logged every time and shown on the panel and in the popover's `Microphone` row.

### 6.4 Vocabulary panel

New capability. A `QTableView` of replacement rules: heard → typed, plus a scope
column (Always / specific app classes). Applied to the transcript **after**
`transcribe.clean_text` and **before** `inject.paste_text`. Whole-word,
case-insensitive matching. Store as a list of objects under a new `vocabulary`
key in `config.json`, validated field by field like everything else.

Keep the substitution pure and in its own function so it is unit-testable without
a model, matching how `clean_text` and `parse_chord` are already written.

**As built.** The substitution runs **inside `transcribe.transcribe_audio`**,
immediately after `clean_text`. Stage 0 §3.7 is right that there is no seam
between the two functions this section names: `clean_text` is called from inside
`transcribe_audio`, not by the engine, so "after one and before the other" has
exactly one place it can mean. `engine.py` changes by one argument rather than
gaining a concept, and the question §3.7 also raises answers itself — `on_text`
receives the substituted string, so a console frontend cannot print one thing and
paste another.

Four things this section left open, settled in `ptt/vocabulary.py`'s docstring and
pinned by `tests/test_vocabulary.py`:

- **One pass.** All rules compile to a single alternation, so a replacement is
  never itself replaced. Rules cannot chain and two rules that map into each
  other terminate.
- **The longest phrase wins**, ties by position in the list. Without it, adding
  `w s l two` beside an existing `w s l` would silently never fire, and there is
  no reordering control in this pass that would let the user find out why.
- **The replacement is literal.** The mockup's `new paragraph` → `\n\n` row does
  not work: interpreting it needs an escape language and a rule for a literal
  backslash, which is a language rather than a setting.
- **Scope.** §11 puts per-application behaviour rules out of scope, so the column
  is drawn, the field is stored and validated, and one value is accepted. A rule
  carrying any other scope is **dropped** on load with a logged reason rather
  than being widened to Always — every other fallback in this codebase restores
  a default that does less, and coercing a scoped rule into an unscoped one would
  do more.

Deleting offers an undo rather than a prompt, which is what this document's §6
asks for "where practical"; restoring one row to the index it came from is
practical. The panel also carries a preview line, which is not in the mockup: it
is the only way to see what a rule does without dictating, and a feature whose
effect is invisible until it has already altered your text needs one.

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

**As built: nothing on this panel is editable, and the warning is therefore not
needed.** The paragraph above is the argument for that rather than against it.
`BEAM_SIZE` and `LANGUAGE` change what the model produces and nothing else in
this window would explain a transcript that got worse; `MIN_RECORD_SEC` and
`IDLE_THRESHOLD_SEC` are bounded at both ends by issue #6 on one side and `FR-3`
and `NFR-4` on the other, and what a user actually wants to say about them is on
or off, which is what §6.3's two checkboxes now are; and `Shift+Insert` is where
most of what this application types ends up working at all. Exposing any of them
is a `Settings` field with validation and a logged fallback, and none of them has
one yet.

Two consequences:

- Each of the two gated rows shows its value **and** says when the Audio tab has
  switched it off — `240 s · bypassed from the Audio tab`. Without that the two
  panels would disagree about what is in force, which is the thing this window
  exists to make impossible.
- Every value is read from the module that owns it rather than transcribed
  beside it, so a change to `transcribe.BEAM_SIZE` appears here with no edit to
  the panel. That needed one small change: `vad_filter` was a literal in
  `transcribe_audio`'s call and is now `transcribe.VAD_FILTER`, because a panel
  reporting "On" from a hard-coded string is reporting nothing.

**"Start with Windows" is a readout, not a checkbox.** This section says it
*reflects* whether the shortcut is present, which is a file-existence check, and
stage 0 §5.13 is right that *setting* it means creating a `.lnk` through
`WScript.Shell` COM and re-applying `install.ps1`'s run-as-admin byte patch —
installer logic duplicated inside the app. A checkbox that cannot be clicked is
worse than a value: Windows delivers no mouse events to a disabled widget, so its
tooltip never appears and it explains nothing, which is the lesson the Model
panel's Delete button already recorded. The path is reached through
`paths.startup_shortcut_path()`, since `paths` owns every application-relative
path.

The mockup's **Restore defaults** button is not drawn. A read-only page has
nothing to restore, and wiring it to the Audio tab's checkboxes would be a
button on one page that changes another.

### 6.6 Diagnostics panel

- Three readouts: CUDA device count and compute type, median transcription
  latency, last paste target window class (all already logged today).
- Tail of `debug_log.txt` in a monospace read-only view, newest at the bottom.
- Buttons: open log folder, reload model.

Read the log through `paths` — do not construct the path independently.

**As built.** The three readouts come from the **engine**, not from parsing the
log. Stage 0 §5.10 poses the choice and the answer is the second one: `OBS-4`
guarantees `debug_log.txt` is plain text, not that any line in it has a stable
format, so a median rebuilt by regex over `Transcription finished in ...` would
break silently the first time that message was reworded — and a diagnostics
panel that lies is worse than one that says "nothing dictated yet". The engine
already computed both figures on the way to logging them, so it keeps them:
`Engine.median_latency()` over the last twenty dictations (median, not mean —
the first transcription after a model load carries warm-up cost that is not
representative) and `Engine.last_paste_target`. Benchmarks are excluded, because
a 30-second clip is not a dictation. The CUDA readout is
`transcribe.cuda_device_count()`, split out of `cuda_available()` so the number
is returned rather than read back out of the line that logs it.

The log is read through `paths.debug_log_path()` and the folder is opened at
`paths.APP_DIR`, not at `os.path.dirname` of the log — taking the directory of a
path `paths` returned would still be this panel computing one. The tail is read
from a window at the **end** of the file rather than the whole of it, since it
refreshes every 1.5 seconds while the tab is on screen, and the partial first
line a byte-offset seek produces is dropped: half a log entry reads as a
corrupted log rather than as a window into a long one.

Two side effects of the engine keeping those figures, both of which close §5
rows that have shown an em dash since session 2: the popover's `Microphone` row
now names the device the stream is actually open on, and its `Last` row shows the
duration and word count of the last transcription. §5's table asks for both and
nothing could supply them until this session.

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
| Compatibility warning text | `#b45309`, 13 px, weight 600 |
| Compatibility warning box border | `#d97706` |

The two warning colours are the tray's amber ramp, not the accent ramp, and
deliberately so: §7 keeps the tray colours outside the design system because they
are functional signalling rather than decoration, and a hotkey that will eat the
user's keystrokes is the same kind of signal. `#d97706` is the tray's own
`transcribing` outline; the text is one step darker because `#d97706` on the light
ground is about 3.4:1, which is not enough for body text.

Everything is square — `border-radius: 0` on buttons, inputs, tabs, cells and
frames. One exception: the primary button is the only solid accent fill in the UI.

Two things QSS cannot do, needing a `paintEvent` override:

- the `+` registration marks at panel corners (`.blueprint` in the reference);
- the level meter and the table bars.

**As built:** the model table's bars and state chips are painted by
`QStyledItemDelegate`s, and a delegate is not a widget, so no style-sheet selector
can reach one. They read their colours off the **view**, which carries them as Qt
properties written from `style.qss` with `qproperty-` — the same indirection
`StatusDot` uses, and for the same reason: no colour lives in Python, not even one
a paint method draws.

**As built:** the `+` registration marks are `ptt/ui/qt_marks.py`, a mixin the two
ground surfaces inherit — `StatusView`, so they appear on the popover *and* the
window banner, and `InstantApplyPanel`, so they appear on all six tabs. The
reference's 11 px box and 1 px arms are reproduced exactly. Their colour is a
`markColour` Qt property written from `style.qss`, so this file defines none —
and it takes **two** values where `industry.css` has one, because the reference's
"body text at 55%" is right on the light ground and invisible on the dark one.

**One deliberate deviation.** `.blueprint` offsets each mark by −6 px so it hangs
half outside the box; CSS allows that with `overflow: visible` on the parent. Qt
does not: a `QPainter` on a widget is clipped to that widget's rectangle, so a
mark drawn at a negative coordinate is never rasterised at all. The alternative
is painting each frame from its parent, which would make every panel depend on
what contains it. **The marks are inset instead**, by `MARGIN_PX`. They read the
same, and `mark_centres` returns nothing at all rather than four overlapping
crossings when a widget is too small to hold them.

**Controls are native Qt widgets — no custom-drawn switches.** Every on/off
setting is a `QCheckBox`; an either/or choice with a fixed, known set of options
is a pair of `QRadioButton`s (GPU vs CPU is the only one); a list whose length is
not known at design time is a `QComboBox` (the microphone picker). **Never mix
radio buttons and checkboxes in the same panel** — that was a specific review
note. The design was revised to avoid skinning a switch: colour the indicators
from the table above via QSS (`::indicator`, `::indicator:checked`) and stop
there. Do not write a custom switch widget.

## 10. Acceptance criteria

These are the criteria. **Their status is tracked in
[verification.md](../verification.md) section 6**, not here — a spec that records its own
results goes stale the moment either changes independently.

1. Tray icon behaves exactly as today: four colours, four glyphs, same tooltip
   format, same right-click items, same non-hanging Exit.
2. Hovering the tray icon raises the popover without stealing keyboard focus from
   the focused application. Typing in another window while it is up is unaffected.
   It is also **in front**, including over an always-on-top window from another
   process: the panel is never activated, and `raise_()` cannot cross out of the
   ordinary Z-order band, so it carries `WindowStaysOnTopHint`. Topmost is a
   Z-order property and not an activation one, so this does not cost the focus
   guarantee above — verify both together.
3. Clicking the popover opens the settings window; the banner shows identical
   content to the popover it replaced.
4. Pressing any key while the Hotkey panel is visible shades that key within
   ~50 ms and unshades it on release, on the keypad as well as the main block.
   Alt-tabbing away clears all shading, as does switching tabs and closing the
   window.
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
