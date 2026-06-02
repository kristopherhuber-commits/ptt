# Development History & Project Status

This file tracks the setup, current configuration, and future roadmap of the **Push-to-Talk (PTT) Local Dictation Utility** (`ppt`). This allows future AI agents or developers to quickly understand the current state of the project.

---

## 📋 Project Overview
The project is a local, low-latency push-to-talk dictation utility for Windows 11. It records audio from the microphone when a hotkey is held, transcribes the speech locally using a `faster-whisper` model on the GPU, and automatically types/injects the text at the current cursor position.

* **Core Script:** [ppt_dictate.py](file:///c:/Users/huber%20(windows)/git/ppt/ppt_dictate.py)
* **Target Platforms/Hardware:**
  * Developed for modern Windows 11 systems.
  * Optimized for high-end NVIDIA GPUs (RTX 5090, RTX 3080 Ti) using FP16 compute mode to avoid crashes on Blackwell/Ampere architectures.

---

## ⚙️ Current Configuration & Setup

### 1. Active Hotkey
* **Chord:** `Ctrl + Space`
* **Variable:** `HOTKEY_MODS = ("ctrl", "space")` in [ppt_dictate.py](file:///c:/Users/huber%20(windows)/git/ppt/ppt_dictate.py#L70)
* **Behavior:** Hold both keys to record, release to transcribe and paste.

### 2. Python Virtual Environment
* **Location:** `C:\Users\huber\git\ppt\.venv`
* **Python Version:** 3.14.2
* **Dependencies Installed:**
  * `faster-whisper` (speech-to-text engine)
  * `ctranslate2>=4.5.0` (inference engine)
  * `sounddevice` (microphone audio capture)
  * `numpy` (audio array processing)
  * `keyboard` (global key hooks and simulation)
  * `pyperclip` (clipboard operations)
  * `nvidia-cudnn-cu12` & `nvidia-cublas-cu12` (provides cuDNN 9 / cuBLAS DLLs directly inside the venv)

### 3. Windows Run Alias (`ppt`)
To allow quick launch without manual command prompt navigation, a launch helper is set up:
* **Batch Script Path:** [ppt.bat](file:///C:/Users/huber/.local/bin/ppt.bat)
* **Location in PATH:** `C:\Users\huber\.local\bin\ppt.bat`
* **Behavior:** 
  1. Triggered by typing `ppt` in the Windows Run dialog (`Win + R`).
  2. Automatically requests Administrator elevation (UAC prompt).
  3. Launches a new elevated PowerShell console, navigates to the repository, and executes the script using the local virtual environment.

---

## 🛠️ Execution & Maintenance Commands

* **To run the script directly (as Admin):**
  ```powershell
  .venv\Scripts\python.exe ppt_dictate.py
  ```
* **To install new packages to the environment:**
  ```powershell
  .venv\Scripts\python.exe -m pip install <package_name>
  ```

---

## 🚀 Future Roadmap & Next Steps

1. **CPU / GPU Rendering Toggle:**
   * Modify the startup flow to show a lightweight selection dialog (e.g., using `tkinter` or `customtkinter`) where the user can toggle between CPU and GPU rendering modes.
2. **Headless Background Process (No Console Window):**
   * Transition the utility to run in the background (headless/no persistent PowerShell terminal) and minimize it to the Windows System Tray.
3. **Compile to Standalone Executable:**
   * Package the application using PyInstaller so it can run as a standalone `.exe` without requiring a local Python installation:
     ```powershell
     .venv\Scripts\pip install pyinstaller
     .venv\Scripts\pyinstaller --noconsole --onefile ppt_dictate.py
     ```
