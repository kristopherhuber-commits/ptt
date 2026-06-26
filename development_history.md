# PTT Local Dictation Utility: Machine-Readable System State

This document is optimized for LLM parser consumption. It records system properties, environment details, codebase structure, packaging specifications, resolved issues, and roadmap status.

## ⚙️ System Configuration Matrix

```yaml
system:
  target_os: "Windows 11"
  python_version: "3.14.2"
  dependencies:
    - name: "faster-whisper"
      version: "1.2.1"
      purpose: "speech-to-text inference engine"
    - name: "ctranslate2"
      version: "4.7.2"
      purpose: "execution engine supporting CUDA float16 on Blackwell architectures"
    - name: "sounddevice"
      version: "0.5.5"
      purpose: "audio microphone stream capture"
    - name: "numpy"
      version: "2.4.6"
      purpose: "numerical array audio flattening"
    - name: "keyboard"
      version: "0.13.5"
      purpose: "global hotkey capture and keystroke simulation"
    - name: "pyperclip"
      version: "1.11.0"
      purpose: "clipboard copy/paste integration"
    - name: "pystray"
      version: "0.19.5"
      purpose: "headless system tray icon integration"
    - name: "pillow"
      version: "12.2.0"
      purpose: "programmatic status tray icon rendering"
    - name: "pyinstaller"
      version: "6.20.0"
      purpose: "binary compilation and standalone packaging"

active_hotkey:
  mods: ["ctrl", "space"]
  trigger_behavior: "Press and hold to record; release to transcribe and paste."

model_parameters:
  default_model: "large-v3-turbo"
  device: "cuda"
  compute_type: "float16"
  language: "en"
  beam_size: 5
  vad_filter: true
  condition_on_previous_text: false

persistence:
  config_file: "config.json"
  saved_keys:
    - use_gpu: boolean
```

## 📂 Codebase Map

* `ptt_dictate.py`: Command-line developer version of the utility.
* `app/ptt_tray.py`: Headless system tray version (teal mic = idle, red = recording, yellow = transcribing, blue = loading).
* `build_dist.py`: Automated PyInstaller compilation script that resolves CUDA binaries and builds `ptt_dictate_dist.zip`.
* `run_tray.bat`: UAC-elevating native launcher that bypasses Windows Smart App Control (SAC) blocks using `pythonw.exe`.
* `C:\Users\huber\.local\bin\ptt.bat`: Elevated global shell alias mapped to user PATH to run `ptt_dictate.py` natively.

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
.venv\Scripts\python.exe build_dist.py
```
