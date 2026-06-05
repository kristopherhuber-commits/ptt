# Agent Project Summary: Push-to-Talk Dictation Packaging

This file summarizes the work completed, the project architecture, the debugging history, and the current status of compiling the Push-to-Talk (PTT) Local Dictation Utility into a Windows standalone executable.

---

## 🎯 Project Goal
To package the push-to-talk GPU-based local dictation utility (using `faster-whisper` and global key hooks) into a portable Windows executable (`.exe`) distributed in a ZIP file. It must run headlessly (without a command console) as a System Tray application, auto-detect GPU/CPU capabilities, allow manual toggles, and require no Python installation on target machines.

---

## 📂 Codebase Structure

* **[ptt_dictate.py](file:///c:/Users/huber/git/ptt/ptt_dictate.py)** (UNTOUCHED): The original command-line version of the utility. Kept intact for native PowerShell testing.
* **[app/](file:///c:/Users/huber/git/ptt/app)** (NEW):
  * [app/ptt_tray.py](file:///c:/Users/huber/git/ptt/app/ptt_tray.py): The GUI-less system tray version.
* **[build_dist.py](file:///c:/Users/huber/git/ptt/build_dist.py)** (NEW): Automated build script that configures and runs PyInstaller, copies CUDA DLLs, and creates the ZIP package.
* **`ptt_dictate_dist.zip`** (NEW): The final compressed standalone distribution package (~1.36 GB).

---

## ⚙️ Architecture & Implementation Details (`app/ptt_tray.py`)

1. **System Tray Integration (`pystray` + `pillow`):**
   * Runs the tray icon message loop in the main thread (essential for Windows message processing and global keyboard hooks).
   * **Dynamic Icon Drawing:** Icons are drawn programmatically at runtime using `PIL.ImageDraw` (no external `.ico` assets needed).
     * **Teal Mic:** Idle / Ready.
     * **Red Circle:** Recording (holding `Ctrl + Space`).
     * **Yellow Circle:** Transcribing.
     * **Blue Circle:** Loading / reloading model.
2. **CPU vs. GPU Execution:**
   * **CUDA Detection:** Startup checks `ctranslate2.get_cuda_device_count() > 0`. If found, defaults to `"cuda"` mode (`float16`), else defaults to `"cpu"` mode (`int8` for fast CPU execution).
   * **Menu Toggle:** Right-clicking the tray icon lets the user toggle between CPU and GPU.
   * **Persistence:** Preference is saved to and loaded from `config.json` inside the executable's directory.
3. **Execution Safety & Logging:**
   * The entire script (including imports) is wrapped in a top-level try-except block that outputs import/syntax crashes to `crash_log.txt`.
   * Model loading, transcription performance, and DLL search logs are written to `debug_log.txt`.

---

## 🐛 Debugging & DLL Resolution History

### The Issue: CPU Fallback / Slow Transcription in Compiled Binary
* **Symptom:** When running the initial executable, transcription took 30+ seconds (hanging).
* **Investigation:** 
  1. We verified that the script `app/ptt_tray.py` ran fast (2-second GPU load) when executed natively via Python.
  2. The packaged executable (`ptt_dictate.exe`) was not showing up in `nvidia-smi` and was consuming ~5.4 GB of RAM (typical for a CPU-bound Python Whisper model).
  3. We discovered that because `nvidia-cu12` packages are implicit namespace packages, `import nvidia` fails or resolves differently inside PyInstaller bundles. The script's DLL loader was returning early, failing to add the CUDA DLL folders (`cublas\bin`, `cudnn\bin`, `cuda_nvrtc\bin`) to the search path.
  4. Deprived of the DLLs, `ctranslate2` failed to load CUDA and silently fell back to CPU execution.

### The Fix: Direct `sys._MEIPASS` Search
* We updated `_add_nvidia_dll_dirs()` in [app/ptt_tray.py](file:///c:/Users/huber/git/ptt/app/ptt_tray.py) to explicitly check `sys._MEIPASS` (the PyInstaller extraction directory) when running in frozen mode:
  ```python
  if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
      base_paths.append(os.path.join(sys._MEIPASS, "nvidia"))
  ```
* This successfully resolves the directories:
  * `_internal\nvidia\cublas\bin`
  * `_internal\nvidia\cudnn\bin`
  * `_internal\nvidia\cuda_nvrtc\bin`
* This correctly adds them to the Windows DLL search path via `os.add_dll_directory()`, restoring full GPU acceleration.

### The Fix: Silero VAD Model Asset Packaging
* **Symptom:** During transcription, the app logged an error: `NoSuchFile: Load model from .../faster_whisper/assets/silero_vad_v6.onnx failed`.
* **Fix:** Added `--collect-data=faster_whisper` to the PyInstaller arguments in [build_dist.py](file:///c:/Users/huber/git/ptt/build_dist.py). This ensures PyInstaller bundles the non-python assets folder (specifically the Silero Voice Activity Detector `.onnx` models) from `faster-whisper` package into the output folder.

### The Fix: `WhisperModel` object has no attribute `'device'`
* **Symptom:** Right after successful typing injection, the app popped up a red notification toast stating `PTT Dictation Error: 'WhisperModel' object has no attribute 'device'`.
* **Fix:** `WhisperModel` does not have a `.device` attribute. We replaced `model.device` in [app/ptt_tray.py](file:///c:/Users/huber/git/ptt/app/ptt_tray.py) with a local string variable `current_device` that is correctly updated when the model is initialized or reloaded.

---

## 🛠️ How to Rebuild and Package

Run this command from the project root:
```powershell
.venv\Scripts\python.exe build_dist.py
```
This script dynamically:
1. Resolves local venv paths.
2. Identifies site-package directories.
3. Passes `--add-binary` options to PyInstaller for the CUDA binaries.
4. Uses `--noconsole` and `--uac-admin` (requests elevation so key injection into elevated windows works).
5. Bundles the app using `--onedir` (highly recommended for instant startup time over `--onefile`).
6. Archives the result into `ptt_dictate_dist.zip`.

---

## 📋 Next Steps

1. Extract `ptt_dictate_dist.zip` and run `ptt_dictate.exe`.
2. Confirm that the system tray icon appears and changes color when you dictate.
3. Inspect `debug_log.txt` in the folder to confirm the model loads on `CUDA` (GPU) and transcribes in 1-2 seconds.
4. Distribute the ZIP to other users.
