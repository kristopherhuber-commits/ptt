# PTT Dictation — Requirements

What this utility must do, and why. Each requirement has a stable ID so that
[design.md](design.md), commit messages, and tests can cite it.

Requirements marked **(retro)** were not designed up front. They were extracted from
[development_history.md](development_history.md), where they appear as the *cause* of a
solved bug. They are recorded here so the constraint is stated once rather than
rediscovered by breaking it again.

---

## 1. Purpose and non-goals

Local, low-latency push-to-talk dictation for Windows 11. Hold a key, speak, release, and
the transcribed text appears at the cursor in whatever application has focus.

**Non-goals:**

- No cloud transcription. Audio never leaves the machine.
- No always-on listening. The microphone is only sampled while the chord is held.
- Not a voice-command system. The output is text, never actions.
- Not cross-platform. See `CON-1`.

---

## 2. Functional requirements

| ID | Requirement |
|---|---|
| `FR-1` | Holding the configured chord starts recording; releasing it stops recording and begins transcription. |
| `FR-2` | Transcribed text is inserted at the caret of the currently focused window, without the user changing focus. |
| `FR-3` | Recordings shorter than 0.3 s are discarded without transcription, so an accidental tap is not a dictation. |
| `FR-4` | The push-to-talk chord is user-selectable and persists across restarts. |
| `FR-5` | The user can switch between GPU (CUDA) and CPU inference at runtime, without restarting. |
| `FR-6` | If the GPU model fails to load, the application falls back to CPU automatically and records that it did so. |
| `FR-7` | The tray icon distinguishes four states at a glance: loading, idle/ready, recording, transcribing. |
| `FR-8` | Preferences persist in a `config.json` next to the application. |
| `FR-9` | The application exits fully on request, leaving no background process behind. **(retro — issue #8)** |

---

## 3. Compatibility requirements

These are the expensive ones. Each cost at least one bug report.

### `FR-C1` — Text insertion must work in every target class **(retro — issues #5, #8)**

Win32 desktop apps, UWP/WinUI apps (Windows 11 Notepad), and WSL/Linux terminals all
accept text differently. Insertion must therefore:

- Use the clipboard plus a single paste keystroke, never per-character typing. Typing
  character-by-character turns into shortcuts if a modifier is still physically held
  (`4` became `Ctrl+4`, switching Notepad tabs).
- Use `Shift+Insert` rather than `Ctrl+V`, because WSL and terminal targets accept the
  former.
- Carry a real hardware scan code from `MapVirtualKeyW`. UWP targets reject synthetic
  virtual keys that have none.
- Set `KEYEVENTF_EXTENDEDKEY` on navigation-block keys such as `Insert`.

### `FR-C2` — Hotkey detection must survive system transitions **(retro — issues #7, #8)**

Detection must remain live across UAC prompts, screen lock, sleep, and USB HID hotplug
(headsets with call-control buttons reset the keyboard hook chain).

- Poll `GetAsyncKeyState`. Windows silently unregisters low-level keyboard hooks, so
  `SetWindowsHookEx` — and any library built on it — must not be relied on for detection.
- Inject input through native Win32 calls, not through a hook-based library, for the same
  reason.

### `FR-C3` — Holding the chord must not disturb the focused window **(retro — issues #9, #11)**

Detection is by polling, not by a suppressing hook, so the physical keypress always
reaches the focused application. The chord must therefore consist of keys that do nothing
on their own:

- No printable or scrolling keys. `Space` typed a literal space and scrolled browsers and
  PDF viewers while held.
- No bare `Alt` release. Windows activates a window's menu bar — or, in WinUI apps, the
  access-key layer — when `Alt` goes up with no other key pressed in between. That moves
  keyboard focus off the document and every subsequent injected keystroke is discarded.
  Where a chord containing `Alt` is chosen anyway, the application must neutralise the
  release.

### `FR-C4` — The clipboard must be left as it was found **(retro — issue #5)**

Insertion goes via the clipboard, so the user's clipboard contents must be captured
before and restored after every paste.

### `FR-C5` — Text insertion must reach elevated targets

The application must be able to run elevated, since Windows UIPI blocks input injected
from a non-elevated process into an elevated one.

---

## 4. Non-functional requirements

| ID | Requirement |
|---|---|
| `NFR-1` | Transcription completes in under 2 s for a typical utterance on GPU. |
| `NFR-2` | Recording starts instantly. No audio-hardware wake-up delay at the moment the chord is pressed. **(retro — issue #6)** |
| `NFR-3` | Speech at the very start of a recording is not clipped. A pre-roll buffer covers the gap between intent and detection. **(retro — issue #6)** |
| `NFR-4` | While the user is idle, the application releases the audio device so the machine can enter low-power states. |
| `NFR-5` | Transcription output is free of the repetition and trailing-punctuation artefacts that the model produces on silence. **(retro — issue #4)** |
| `NFR-6` | The distribution runs on a target PC with no pre-existing Python installation and no library setup. |
| `NFR-7` | The launcher is not blocked by Windows Smart App Control. |

---

## 5. Observability requirements

New in this revision, and the direct lesson of issue #11: a paste that Windows discarded
looked identical in the log to a paste that worked. The transcription was logged as a
success either way, so a total failure of the application's primary function left no
trace at all. It went undiagnosed for months as a result.

| ID | Requirement |
|---|---|
| `OBS-1` | Every step that can fail silently must log its outcome, not merely its attempt. Insertion is the specific case: log that text was inserted, and into which window. |
| `OBS-2` | Conditions known to cause silent failure must be detected and logged as warnings — in particular, a focused window with no caret at the moment of pasting. |
| `OBS-3` | Configuration actually in force at startup must be logged, including the resolved chord and the reason for any fallback to a default. |
| `OBS-4` | The log must be a plain text file next to the application, readable without tooling. It is rotated to `debug_log.prev.txt` at startup rather than truncated: both frontends write the same file, so starting one would otherwise destroy the other's log — and a crash log must survive the restart that follows it. |

---

## 6. Constraints

| ID | Constraint |
|---|---|
| `CON-1` | Windows 11 only. The implementation is built directly on Win32 APIs. |
| `CON-2` | Distributed as a portable environment built around a Python Software Foundation-signed interpreter (`NFR-6`, `NFR-7`). |
| `CON-3` | The GUI is built on **PySide6-Essentials** (LGPL). *Revised.* This was originally constrained to `tkinter` on the grounds that it is already in the portable environment and so adds no dependency and no distribution size. That trade was re-taken when the GUI grew from a single hotkey-capture dialog into three layers and six panels. Measured cost: +76.9 MB compressed on a 1.35 GB distribution. `NFR-6` and `NFR-7` are unaffected — the wheel bundles its own MSVC runtime (`msvcp140*.dll`, `vcruntime140*.dll`) and its own Windows platform plugin (`qwindows.dll`), so a target PC needs no redistributable, and the launcher is still the PSF-signed interpreter. The wheel is `cp310-abi3`, i.e. stable-ABI, so it loads on 3.14 without a version-specific build. |
| `CON-4` | Inference runs through `faster-whisper`/CTranslate2 with `float16` on CUDA. `int8` is not usable on Blackwell GPUs. |
| `CON-5` | The application must keep working when it is the only thing that has changed — i.e. no requirement here may be met by asking the user to reconfigure Windows. |

---

## 7. Traceability

Requirements above trace back to the retrospective log. The reverse direction, for
review: every issue in [development_history.md](development_history.md) should either
appear as a requirement here or be a pure implementation defect.

| Issue | Becomes |
|---|---|
| #1, #2, #3 | Implementation defects (packaging, asset bundling, attribute rename). No requirement. |
| #4 | `NFR-5` |
| #5 | `FR-C1`, `FR-C4` |
| #6 | `NFR-2`, `NFR-3`, `NFR-4` |
| #7 | `FR-C2` |
| #8 | `FR-C1`, `FR-C2`, `FR-C5`, `FR-9` |
| #9 | `FR-C3` |
| #10 | Build-script defect. No requirement. |
| #11 | `FR-C3`, `FR-4`, `OBS-1`, `OBS-2` |
