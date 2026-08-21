# PTT Local Dictation Utility: Retrospective Log

This document is optimized for LLM parser consumption. It records solved issues: the
symptom observed, the underlying cause, and the fix applied. Entries are appended, not
rewritten.

## 📌 Scope of this document

This is the **append-only retrospective log**: symptoms, causes, and fixes, kept so that
solved problems stay solved. It is deliberately narrow.

* What the utility must do, and the constraints these issues produced -> [requirements.md](requirements.md)
* How it is built — configuration matrix, module layout, injection contract -> [design.md](design.md)

Those sections used to live here and drifted out of date (this file once documented a
`build_dist.py` that no longer existed). They are now maintained next to the code they
describe.

## 🐛 DLL & Resource Resolution Rules

When packaging using PyInstaller (`--onedir` mode):
1. **CUDA DLLs:** Must collect dynamic binaries from pip packages `nvidia-cublas-cu12`, `nvidia-cudnn-cu12`, and `nvidia-cuda-nvrtc-cu12`.
2. **Directory Mapping:** DLL directories (`cublas\bin`, `cudnn\bin`, `cuda_nvrtc\bin`) must be copied to `nvidia/sub/bin` inside the PyInstaller output distribution directory.
3. **DLL Discovery Path:** `app/ptt_tray.py` uses `sys._MEIPASS` when frozen to programmatically locate and add these directories to the Windows search path via `os.add_dll_directory()`.
4. **VAD Model Assets:** Must pass `--collect-data=faster_whisper` to package `silero_vad_v6.onnx` models correctly.

## 📖 Solved Issues & Retrospective Log

### 1. Slow Transcription / CPU Fallback in Frozen Mode
* **Symptom:** Packaged binary transcribes in 30+ seconds instead of 1-2 seconds; GPU is not used.
* **Cause:** PyInstaller doesn't load implicit namespace packages correctly, failing to add CUDA DLL folders to the path.
* **Fix:** Programmatically search `sys._MEIPASS` for `nvidia` subdirectories and run `os.add_dll_directory()`.

### 2. Silero VAD Asset Error
* **Symptom:** `NoSuchFile: Load model from .../faster_whisper/assets/... failed` on execution.
* **Fix:** Bundled `faster-whisper` static assets into the build outputs via `--collect-data=faster_whisper`.

### 3. WhisperModel Missing Attribute
* **Symptom:** Crash Toast: `'WhisperModel' object has no attribute 'device'`.
* **Fix:** Replaced references to `model.device` with a tracking state variable `current_device`.

### 4. Transcription Cutoff and Character Repeat (dots)
* **Symptom:** Saying "testing one two three" typed `Testing .......`.
* **Cause:** `large-v3` hallucinations on trailing silence combined with `condition_on_previous_text=True`.
* **Fix:** Switched model to `large-v3-turbo` (faster, less prone to loops), set `condition_on_previous_text=False`, and added regex filter to strip consecutive periods `re.sub(r'\.{2,}', '', text)`.

### 5. Keystroke Injection Shortcut Conflicts
* **Symptom:** Typing cut off in Notepad (e.g. `Testing 1, 2, 3, ` instead of `Testing 1, 2, 3, 4, 5.`).
* **Cause:** `keyboard.write()` types character-by-character. If the user was still physically releasing the `Ctrl` modifier key, simulated keys were sent as shortcuts (e.g., `4` became `Ctrl+4` which switches Notepad tabs; `c` became `Ctrl+C` which triggers copy).
* **Fix:** Switched from `keyboard.write` character simulation to clipboard-based paste using `Shift + Insert` which is instant, single-event, and doesn't conflict with lingering physical `Ctrl` keys. Temporarily preserves and restores the user's previous clipboard contents.

### 6. Hotkey Recording Wake-up Delay (Headset Latency)
* **Symptom:** A ~1-second delay when starting to record after pressing `Ctrl+Space` (often with headset chime/clicks).
* **Cause:** PortAudio audio streams were started and stopped on every key press and release, triggering hardware wake-up latency.
* **Fix:** Kept the audio stream in the `started` state continuously while active. Implemented callback-level filtering via a `self.recording` flag and added a 200ms pre-roll buffer (`self._preroll`) to prevent clipping early words. Increased idle timeout from 120s to 240s to preserve low-power states when inactive.

### 7. Keyboard Hook Loss / Unresponsiveness after System Transitions
* **Symptom:** PTT stops responding to the `Ctrl+Space` hotkey entirely (the icon remains green), even though the process is running and responding.
* **Cause:** The Python `keyboard` library relies on Windows low-level keyboard hooks (`SetWindowsHookEx`), which Windows silently disables after UAC prompts, screen locks, or sleep timeouts.
* **Fix:** Replaced hook-based polling with the Win32 `GetAsyncKeyState` API in `chord_held()` to query keyboard driver states directly from the OS. This makes hotkey detection completely immune to hook unregistrations.

### 8. Pasting Failure After USB HID (Jabra) Connection & Zombie Process Accumulation
* **Symptom:** The application records audio and transcribes successfully (visible in the debug logs), but no text is pasted at the cursor when the hotkey is released. Additionally, multiple duplicate zombie `ptt_dictate.exe` instances accumulate in memory on restart.
* **Cause:** 
  1. **Hook Thread Failure**: Connecting or disconnecting USB HID devices (like Jabra headsets with physical call control buttons) resets the Windows keyboard hook chain. This silently invalidates the Python `keyboard` library's hook thread, causing simulated inputs (like `keyboard.press_and_release("shift+insert")`) to fail.
  2. **UWP Scancode Requirement**: Modern Windows 11 Notepad (a UWP application) rejects simulated virtual keys (like `VK_INSERT`) if they do not contain valid hardware scan codes and the `KEYEVENTF_EXTENDEDKEY` (0x01) flag (since the physical `Insert` key is in the extended navigation block).
  3. **UIPI Security Blocks**: Windows User Interface Privilege Isolation (UIPI) blocks simulated inputs sent from non-elevated scripts to other privilege contexts or UWP containers.
  4. **Zombie Processes**: Python's interpreter blocks exiting if background threads spawned by CTranslate2 thread pools or the `keyboard` listener hook remain alive, leaving zombie processes in memory.
* **Fix:** 
  1. **Native Input Injection**: Replaced the `keyboard` library's pasting with direct, native Win32 `keybd_event` calls via `ctypes`.
  2. **Hardware Scancodes & Extended Flag**: Resolved scan codes via `MapVirtualKeyW` and explicitly flagged `VK_INSERT` as extended (`0x01`), allowing UWP Notepad and command-line terminals to accept the simulated `Shift+Insert` keystroke.
  3. **Elevation Wrapper**: Standardized launcher and installers to self-elevate to Administrator, bypassing UIPI.
  4. **Forced Process Termination**: Added `os._exit(0)` directly inside the `__main__` entry point of both `ptt_dictate.py` and `ptt_tray.py` to immediately terminate the process and all background threads at the OS level upon exiting.

### 9. Space-Bar Leakage Moving the Cursor / Scrolling During Recording
* **Symptom:** Holding the `Ctrl+Space` hotkey would sometimes type a literal space or scroll the focused window (cursor "moving forward") instead of just starting a recording.
* **Cause:** `chord_held()` detects the hotkey via `GetAsyncKeyState` polling rather than a suppressing keyboard hook (see issue #7), so the physical keypress is never blocked from reaching the focused application. `Space` is a printable/actionable key, so every hold also delivered a real spacebar press to whatever had focus (typing a space, or scrolling in browsers/PDF viewers). `Ctrl` alone doesn't cause this because it has no character or default scroll action.
* **Fix:** Changed the hotkey chord from `Ctrl+Space` to `Shift+Alt` — two pure modifier keys that produce no character and no scroll action on their own, eliminating the leakage. Updated in both `ptt_dictate.py` and `app/ptt_tray.py` (`HOTKEY_MODS`); `VK_MAP` already contained entries for both keys so no new Win32 plumbing was needed.
* **Caveat:** `Alt+Shift` is Windows' default "switch input/keyboard language" hotkey when more than one input language is installed (Settings → Time & Language → Language). On machines with a second layout installed, this should be checked/disabled to avoid the hotkey also cycling keyboard layouts.

### 10. `pip.exe` Self-Upgrade Failure During Portable Build
* **Symptom:** `build_portable.py` failed with `ERROR: To modify pip, please run the following command: ...python.exe -m pip install --upgrade pip` when upgrading pip inside the fresh `.venv`.
* **Cause:** On Windows, `pip.exe` cannot overwrite its own running executable file during a self-upgrade.
* **Fix:** Changed the upgrade step in `build_portable.py` to invoke `python.exe -m pip install --upgrade pip` instead of calling `pip.exe` directly.

### 11. Dictation Silently Failing in Notepad (and every other menu-bar app)
* **Symptom:** Holding the hotkey in Windows 11 Notepad records and transcribes correctly - `debug_log.txt` shows a clean result - but no text ever appears at the cursor.
* **Cause:** Confirmed by direct Win32 probing against a live Notepad window. Windows activates a window's menu bar - or, in WinUI apps like Windows 11 Notepad, the access-key layer - when `Alt` goes **up with no other key pressed in between**. Activation moves keyboard focus off the document: `GetGUIThreadInfo` reports `GUI_CARETBLINKING` dropping to 0, i.e. the caret is gone. Every subsequently injected keystroke is discarded. Two separate triggers were present:
  1. **The user's own release.** `Shift` does not count as an intervening key, so releasing the `Shift+Alt` chord is a bare `Alt` tap.
  2. **The app's own injection.** `paste_text()` unconditionally injected a synthetic `Alt` keyup as its "release stuck modifiers" step, firing a second activation while `Alt` was still physically held.
* **Measured evidence:** with a bare `Alt` down/up before pasting, the caret dies and both `Shift+Insert` **and** `Ctrl+V` are swallowed - so this was never a paste-mechanism or UWP-scancode problem (contrast issue #8). Tapping `Esc` first restores the caret and the paste lands. A bare `Alt` **keyup alone**, with no preceding keydown, is harmless - activation requires the full press. End-to-end check against a pinned Notepad window: Right Ctrl pastes, `Shift+Alt` with the guard pastes (caret alive), `Shift+Alt` without it is swallowed (caret dead).
* **Fix:**
  1. **Default hotkey changed to `Right Ctrl`** (`HOTKEY_MODS = ("rctrl",)`): a lone modifier with no character, no scroll, and no menu activation. It also sidesteps the `Alt+Shift` language-switch caveat from issue #9. `VK_MAP` gained left/right variants so a single side can be bound.
  2. **`suppress_alt_menu()`**: taps `VK_NONAME` (0xFC - reserved and unassigned, so it produces no character and no command) while `Alt` is still held, supplying the missing intervening keypress that renders the release inert. Called on record start (covers the user's physical release) and again inside `paste_text()` (covers the app's synthetic one).
  3. **Conditional, side-aware modifier release**: only modifiers actually reported down are released, and both `VK_LCONTROL` and `VK_RCONTROL` are released explicitly - injecting the unsided `VK_CONTROL` release leaves the right-hand key state set.
  4. **Hotkey made configurable** via `config.json`, validated against `VK_MAP`, with the active chord shown in the tray menu.
  5. **Paste is now logged**: `target_accepts_keys()` checks for a caret before pasting and logs a warning when it is missing, along with the target window class. Previously a swallowed paste left no trace - the log recorded a successful transcription either way, which is why this went undiagnosed.
* **Scope:** not Notepad-specific. Any window with a menu bar or access keys - Explorer, VS Code, Office, Firefox - behaves identically.

## 🛠️ Maintenance & Execution Protocols

### Native Terminal Execution
```powershell
.venv\Scripts\python.exe ptt_dictate.py
```

### Native Headless Tray Execution
```powershell
.venv\Scripts\pythonw.exe app\ptt_tray.py
```

### Clean Recompile & Rebuild
```powershell
python build_portable.py
```
