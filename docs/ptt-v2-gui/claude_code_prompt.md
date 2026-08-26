# Claude Code — session plan

Six sessions. Each has a **setup** (for you) and a **prompt** (for Claude Code).
Copy the prompt block verbatim as your first message of that session.

**One model and one effort level per session, set before you start and never
changed mid-session** — switching invalidates the prompt cache and re-sends the
whole conversation at full price.

| Session | Work | Model | Effort |
| --- | --- | --- | --- |
| 0 | Read everything, report back, no code | Opus 5 | **Max** |
| 1 | Qt tray + engine→UI thread bridge | Opus 5 | **Max** |
| 2 | Window shell, banner, popover | Opus 5 | Extra (xhigh) |
| 3 | Hotkey + Model panels | Opus 5 | **Max** |
| 4 | Audio, Vocabulary, Advanced, Diagnostics | Opus 5 | Extra (xhigh) |
| 5 | Acceptance criteria + packaging | Opus 5 | **Max** |

Everything outside the fenced blocks below is for you, not for Claude Code.

**The unit suite arrived in session 3**, alongside the two panels — `docs/design.md`
section 10 step 2 had been outstanding since the package split. The tests, what each one
verifies and their results are in `docs/verification.md`. Run it before and after any
session that touches `app/ptt/`:

```powershell
uvx --with-requirements requirements-dev.txt pytest
```

## Why Opus 5 and not Fable 5

Anthropic recommends Opus 5 as the starting choice for complex agentic coding,
reserving Fable 5 for the highest-capability, longest-horizon autonomous work. On
published coding and agent evaluations Opus 5 leads or ties Fable at half the
price, and at max effort lands within half a point of Fable's CursorBench result
at less than half the cost per task. Fable's advantage grows with task length and
autonomy — multi-day unsupervised runs across large migrations. This job is the
opposite shape: nine modules, a written spec, five bounded stages, human review
between each.

**Escalate to Fable 5 only if** Opus 5 at Max, with all context present, gets the
same thing wrong twice in a row. Failing by skipping a file or stopping halfway is
an effort problem, not a model problem. Failing because the spec is ambiguous is a
spec problem — fix the handoff instead.

Sonnet 5 and Haiku 4.5 are the wrong tier for this work. Never for sessions 1 or 3.

## Why Max on 0, 1, 3 and 5

Effort controls how many files get read, how many tools run, and how many steps
happen before Claude checks back with you — not just thinking time. Default is
high; the Claude Code team's guidance is to start coding at xhigh and keep max for
hard problems. Max is used where a mistake is expensive *and* invisible:

- **0** — the entire point is exhaustive reading. Effort decides whether it reads
  all six documents or four. Tiny output, so the cheapest Max session you'll run.
- **1** — cross-thread widget access produces code that works on your machine and
  corrupts paint state on someone else's. It will not show up in your testing.
- **3** — the hotkey panel has three documented traps that are easy to skim past.
- **5** — verification work, where low effort means "claims the criteria pass".

Sessions 2 and 4 are mechanical with an explicit spec. Extra is right and cheaper.

---

# Session 0 — orientation, no code

**Setup:** `cd` to the repo, run `claude`, then `/model` → Opus (1M context),
`/effort` → Max.

```
I want to add a GUI to this application. Do not write any code in this session.

Read these in this order, completely:

1. README.md
2. docs/requirements.md
3. docs/design.md — especially section 4 (module layout), section 6 (hotkey design), section 7 (config ownership)
4. docs/development_history.md — the retrospective. Issues #1, #4, #7, #8, #9 and #11 constrain what you are about to build.
5. docs/gui_handoff/gui_handoff.md — the GUI specification
6. Every module in app/ptt/, plus app/ptt_tray.py, ptt_dictate.py, build_portable.py and install.ps1

docs/gui_handoff/ptt_dictation_ui_mockups.html is the visual reference — one self-contained file, open it in a browser. Turn 4 shows the design mapped onto PySide6 widgets with a widget-by-widget note beside it. Turn 3 shows every settings tab built out. Turn 2 shows the three-layer interaction: tray icon, hover popover, settings window. The keyboard diagrams and model tables in it are live — click them, and press keys while a keyboard diagram is on screen.

Then report back, in writing, before touching anything:

- The threading constraint between the engine and Qt, in your own words, and what specifically goes wrong if it is violated.
- Every place the handoff document contradicts the code as it actually exists.
- Anything in the handoff you think is a bad idea, and why. I want disagreement here, not compliance.
- Anything in the handoff that is underspecified — where you would have to guess.
- Your proposed file-by-file plan for session 1 only, and where you expect trouble.

Write your findings to docs/gui_handoff/stage0_review.md so the next session can read them.
```

**Before continuing:** read the review, particularly the "bad idea" and
"underspecified" sections. This is the cheapest moment in the project to catch a
wrong assumption. If it contradicts the spec, fix the spec first.

---

# Session 1 — tray icon and the thread bridge

**Setup:** new session (`/clear` or a fresh terminal). `/model` → Opus,
`/effort` → Max.

```
Read docs/gui_handoff/gui_handoff.md sections 3, 4 and 7, then docs/gui_handoff/stage0_review.md, app/ptt/ui/tray.py, app/ptt/engine.py and app/ptt_tray.py.

Implement session 1 only: replace the pystray tray with QSystemTrayIcon, and build the engine-to-UI signal bridge. No settings window, no popover, no panels. The application must be fully usable at the end of this session — dictation works, the icon changes colour, the menu works.

Hard constraints:

- The engine is not modified. It calls on_state from the engine thread, so on_state must do nothing but emit a Qt signal delivered to the GUI thread. Read the Engine docstring's state-callback contract before you write it.
- The four existing tray icons are preserved exactly — teal microphone, red circle, amber square, blue arc, with the same fills and outlines as create_icon_image. Keep that drawing code and convert PIL to QPixmap. I value these icons specifically; do not restyle them.
- Exit keeps its current behaviour: engine.stop() then quit, never join the engine thread. Read the comment explaining why.
- PySide6-Essentials in requirements.txt, not full PySide6.
- Delete app/ptt/ui/tray.py and remove pystray from requirements.txt once the Qt tray works. We are not keeping both.

Add a development-time assertion that the state handler runs on the GUI thread (QThread.currentThread() == qApp.thread()), and tell me how to verify it fires on the right thread rather than asserting that it does.

Then tell me exactly what to test by hand before I move on.
```

---

# Session 2 — window shell, banner, popover

**Setup:** new session. `/model` → Opus, `/effort` → Extra.

```
Read docs/gui_handoff/gui_handoff.md sections 5 and 6, and the code from session 1.

Implement the settings window shell and the hover popover. No panel content yet — six empty tabs.

- The banner in the settings window and the hover popover are the same widget class, embedded twice. Build it once. Row order is specified in section 5 and must match exactly: header, then State, Hotkey, Model, Microphone, Last.
- The popover is a frameless tool window that does not steal keyboard focus — Qt.WA_ShowWithoutActivating. Shown on tray hover, hidden on leave after a short grace period so the pointer can travel to it, and it opens the settings window on click. It has no controls of any kind. It is a display.
- Position it from QSystemTrayIcon.geometry(), clamped to QScreen.availableGeometry() so it never lands off-screen or under the taskbar.
- Dark ground for the popover and the banner, light for everything below the tab bar. That split means read-only versus interactive and is deliberate — do not invert it.
- Register the fonts from app/assets/fonts/ before creating any widget, resolving the path via paths, not the working directory. Section 8 lists the four files to register.
- Start style.qss now, with the colour table from section 9. Every colour lives there, not in Python.

Verify before you finish: hovering the tray icon raises the popover while I am typing in another window, and my typing is unaffected.
```

---

# Session 3 — Hotkey and Model panels

**Setup:** new session. `/model` → Opus, `/effort` → Max.

```
Read docs/gui_handoff/gui_handoff.md sections 6.1 and 6.2, app/ptt/hotkey.py, app/ptt/config.py including the whole Settings docstring, app/ptt/transcribe.py, and docs/development_history.md issues #7, #8, #9 and #11.

Implement the Hotkey and Model panels.

The Hotkey panel is the highest-risk piece in this project. Specifically:

- Live key shading: poll GetAsyncKeyState on a roughly 30 ms timer. Do not use keyPressEvent — it only fires for the focused window and misses releases when focus moves. This is the same reason the engine polls instead of installing a hook; read the hotkey.py module docstring.
- Derive the bindable key set from hotkey.VK_MAP. Do not hard-code nine key names.
- Chord writes are whole-tuple rebinds, settings.hotkey = ("rshift",), never a mutation. The Settings docstring explains why this is load-bearing and why adding a lock or freezing the dataclass breaks it.
- Clear all shading on focus loss and on window hide, so nothing gets stuck shaded.
- Never allow an empty chord.
- Three visual states are simultaneously visible: bound, held-now, and bindable, plus dimmed for non-bindable. The mockup's Hotkey tab shows all four.

The Model panel needs one engine change: transcribe.MODEL_SIZE becomes a validated Settings field. Follow the existing validation pattern in config.py exactly — validate the value, log the reason for any fallback, and preserve unknown keys. Do not invent a different pattern.

Every control applies instantly: write the field, call Settings.save(), then request_model_reload() if the change needs it. No OK/Apply/Cancel anywhere.

Model downloading is out of scope — stub those buttons so they say so.

When you are done, tell me how to verify that rebinding to Right Shift takes effect without restarting the app.
```

**Outcome.** Both panels shipped. Beyond the prompt: `hotkey.py` gained the `KEYS` table
and `classify()` (`design.md` section 6's classifier, never written until now); the
unsided-`win` defect was found and fixed (retrospective issue #12); `Settings.save()`
became atomic and locked, because instant-apply gave the file two writers;
`Engine.request_benchmark()` was added so measuring never needs a second model in VRAM;
and the hover popover gained `WindowStaysOnTopHint` after a hand-test found it could sit
behind an always-on-top window.

The **unit test suite** (`tests/`, 176 tests) landed here too, with
`requirements-dev.txt`. It was not in the original plan for this session and should have
been step 2 of `design.md` section 10 all along — session 3 added a classifier and two
validated `Settings` fields on top of an untested config layer, which is the wrong order.
The pinned-window probe harness from that step is still outstanding.

---

# Session 4 — remaining panels

**Setup:** new session. `/model` → Opus, `/effort` → Extra.

```
Read docs/gui_handoff/gui_handoff.md sections 6.3 through 6.6, app/ptt/audio.py, app/ptt/inject.py and app/ptt/config.py.

Implement the Audio, Vocabulary, Advanced and Diagnostics panels.

- Audio and Vocabulary add new capabilities — device selection and replacement rules — so both need new validated Settings fields following the existing pattern. A device index of None must keep meaning "system default", so existing configs behave exactly as they do today.
- The microphone picker is a QComboBox. The three behaviour settings are QCheckBoxes. Do not mix radio buttons and checkboxes in one panel.
- Vocabulary substitution runs after transcribe.clean_text and before inject.paste_text. Keep it a pure function in its own module so it is testable without a model, the way clean_text and parse_chord already are.
- Advanced is read-mostly. If you make any value editable it becomes a validated Settings field, not a raw write. Shift+Insert is load-bearing for WSL and bash terminals — if you expose it, warn on change.
- Diagnostics reads the log through paths, never by constructing the path itself.

A config.json written by this build must still load in the pre-GUI build, and unknown keys must survive a round trip. future_setting in the existing file is the test case.

There is a unit suite in tests/, documented in docs/verification.md. Run it with `uvx --with-requirements requirements-dev.txt pytest` before you start and again before you finish. Every new validated Settings field needs its cases added to tests/test_config.py — the fallback value and the OBS-3 log line that explains it, which is what the log_lines fixture is for. The vocabulary substitution function is pure, so it gets its own test module. Add a row to verification.md's traceability matrix for each new design element you verify, and record any manual test you run in its section 5.
```

---

# Session 5 — verify and package

**Setup:** new session. `/model` → Opus, `/effort` → Max.

```
Read docs/gui_handoff/gui_handoff.md section 10, and build_portable.py.

Work through the ten acceptance criteria one at a time. For each, tell me what you did to verify it and the actual result — not whether it should pass. Where a criterion needs hardware or a human, say so and tell me what to do.

Then confirm that app/assets/fonts/ and app/assets/benchmark_sample.wav are included by build_portable.py, run it, and confirm the resulting zip extracts and runs. Both OFL.txt licence files must travel with the fonts into the distribution — that is the font licence condition.

Run the unit suite as part of the acceptance pass: `uvx --with-requirements requirements-dev.txt pytest`. Report the actual count. Confirm that neither tests/ nor requirements-dev.txt appears in the zip. Record every result in docs/verification.md — section 6 is the acceptance-criteria table and section 7 lists what is still unverified, including the pinned-window probe harness.

Finally, update README.md and docs/design.md to describe the new UI module layout, and note that the pystray tray has been removed. The docs are this project's source of truth and are now out of date.
```
