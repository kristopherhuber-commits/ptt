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

- No cloud transcription. Audio never leaves the user's own machines.
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
| `FR-10` | When the shared vocabulary file `~/agent-configs/vocabulary.json` exists, it is the only store of the replacement rules: the Vocabulary tab reads and writes it, and an edit made to it outside the application applies to the next dictation without a restart. When it does not exist, the rules in `config.json` are used. A broken or missing file never empties the vocabulary. *(Added in v3.1. The file is shared across the user's machines through the `agent-configs` git repo, and Claude Code adds rules to it as it notices mis-transcriptions.)* |

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

---

## 8. v4 — Training the model on the user's own speech

*Added for v4. Background: [personalization.md](background/personalization.md) and
[training-data-plan.md](background/training-data-plan.md).*

On short replies the model often types what it expects to hear rather than what was said,
such as "cloud code" for "Claude Code": a short clip gives it little sound to go on. v4
saves the user's dictation, gets each clip's correct text for as little of the user's effort
as possible, fine-tunes the dictation model (`large-v3-turbo`) on it, and ships the result
only if it measurably beats the stock model.

Each *threshold* below is set once the stock model has been measured on the user's own
clips. *Held-out clips* are kept out of training and used only to measure.

| ID | Need | Judged by |
|---|---|---|
| `FR-11` | When saving is on, every dictation is saved: its audio, what the model typed, what was pasted, and the settings in force. They go to a clips folder set per machine, outside this repo and the install folder. | Dictate ten clips: ten audio files and ten records appear, each matching what was pasted. |
| `FR-12` | Saving can be paused from the tray. While paused, dictation works as usual and nothing is saved. | Pause, dictate three clips, resume, dictate one: only the last is saved. |
| `FR-13` | Double-tapping the dictation key marks the last clip as wrong. The taps type nothing (`FR-3`) and make no sound; a red mark over the terminal's text cursor confirms the flag for half a second. | After a wrong paste in the terminal, double-tap: the mark shows on the cursor, nothing is typed, and the clip is flagged. |
| `FR-14` | A review window gives clips their correct text, flagged clips first. It plays the clip and shows what was typed with a suggested correction; Enter saves the suggestion, an edit replaces it, and a clip can be discarded. | Review ten flagged clips: each ends with correct text or discarded, most with one key press. |
| `FR-15` | Errors the user didn't flag are found without their effort: on Darklord, clips get a second, slower transcription overnight, and a clip where it disagrees with what was pasted joins the review queue. | After a week, the review queue holds unflagged clips whose pasted text was wrong. |
| `FR-16` | A read-aloud mode shows one line of a script at a time and records the user reading it, so the clip's correct text is known. | Record a ten-line script: ten clips, each with its line as its correct text. |
| `FR-17` | The user can train a model from the clips collected so far. The result is a separate choice in the Model tab on both machines; the stock model stays one click away. | Run a training: the new model appears in the Model tab on both machines, and switching between it and the stock model works. |
| `FR-18` | A report compares two models on the held-out clips, for each measure in `NFR-8` to `NFR-13`, with how certain each difference is. | The report for the stock and trained models shows every measure and whether each difference is real or within chance. |
| `NFR-8` | Short replies (1 to 7 words) are typed as said. | The share of held-out short replies typed exactly right, ignoring capitals and punctuation, beats the stock model by the threshold. In a week of real use, flags per 100 clips fall. |
| `NFR-9` | Vocabulary terms (Claude Code, MIND Technology, Seamap, GunLink, ...) are typed right without the vocabulary rules. | The share of terms typed right in the read-aloud test beats the stock model by the threshold. In a week of real use, the rules fire less often per 100 words. |
| `NFR-10` | No over-correction: everyday words that sound like a term ("cloud", "boy", "mind") are still typed when said. | In the read-aloud test, terms typed where they weren't said stay within the threshold. |
| `NFR-11` | Fewer errors overall. | The word error rate on held-out clips is lower than the stock model's. |
| `NFR-12` | General English no worse. | The word error rate on a public English test set rises by no more than the threshold. |
| `NFR-13` | No slower: `NFR-1` still holds on both machines. | Latency on the benchmark clip on Nightlord, the slower machine, stays within the threshold of the stock model's. |
| `NFR-14` | Saving, the overnight pass and training never delay dictation. | Dictate during an overnight pass and during training: `NFR-1` holds. |
| `OBS-5` | A clip that fails to save is logged and shown, never silent. | Make the clips folder unwritable and dictate: the failure is shown. |

| ID | Constraint |
|---|---|
| `CON-6` | Saved clips stay on the user's own machines: never on the NAS or a cloud service, and never in this repo, which is public. They hold whatever was dictated, including MIND's non-public information. Each machine keeps its own clips; when it's time to train, Nightlord's are moved to Darklord, where training runs. |
| `CON-7` | The trained model is `large-v3-turbo`, fine-tuned and converted to run under `CON-4`. |

**Out of scope for v4:** training anywhere but Darklord; a dictation model other than
`large-v3-turbo`; the confirming mark outside the terminal (the flag itself works
anywhere); recording which application the text went to; capturing edits made after
pasting; retraining on a schedule.
