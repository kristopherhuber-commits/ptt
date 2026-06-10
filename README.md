# Push-to-Talk Local Dictation for Windows 11

A local, low-latency dictation utility that records audio when you hold a hotkey, transcribes it on your GPU using `faster-whisper`, and types the text directly at your cursor.

This repository includes both a **command-line developer version** and a **compiled standalone System Tray application** that runs headless without a console window.

---

## 📦 Distribution & Installation Directions (For Target PCs)

The application is distributed as a portable Python environment. No pre-existing Python installation or library configuration is required on the target computers.

> [!NOTE]
> **Windows Smart App Control (SAC) Compatibility:** Because the custom launcher (`ptt_dictate.exe`) is a copy of the official Python interpreter signed by the Python Software Foundation, Windows Smart App Control and SmartScreen fully trust it and will not block execution.

### 1. Installation
1. Copy the **`ptt_dictate_dist.zip`** archive to the target computer.
2. Extract the ZIP file completely.
3. Double-click **`install.bat`** inside the extracted folder.
4. Click **Yes** on the User Account Control (UAC) prompt to allow the installer script to configure the shortcuts.

The installation script will automatically:
* Copy the application to `C:\Users\<Username>\AppData\Local\Programs\ptt_dictate\`.
* Create a **PTT Dictation** shortcut on the Desktop, pre-configured to **Run as Administrator** (required to intercept global hotkeys and type into other elevated windows).
* Create a **PTT Dictation** shortcut in the Windows Startup folder to launch the app automatically on login.
* Relaunch the app immediately.

---

## ⚡ Run Directions

### 1. Launching
* **Normal Usage:** Double-click the **PTT Dictation** shortcut on your Desktop, or let it start automatically on login.
* **First Run:** Click **Yes** on the Windows User Account Control (UAC) elevation prompt.
* A **Teal Microphone** icon will appear in the Windows System Tray (notification area, bottom right). If it is hidden, click the **`^`** chevron next to the clock and drag the microphone icon to the main taskbar.

### 2. Usage
* **Record:** **Hold `Ctrl + Space`** to record audio. The tray icon will turn **Red**.
* **Transcribe:** **Release the keys**. The tray icon will turn **Yellow** while it transcribes and automatically type the text directly at your cursor.
* **Settings:** Right-click the system tray icon to:
  * Check the current state (`Status: Ready (CUDA)`, `Status: Recording...`, etc.).
  * Toggle between **`Use GPU (CUDA)`** and **`Use CPU`** modes.
  * **Exit** the application.
* **Persistence:** The application creates a local `config.json` file in its directory to remember your CPU/GPU preference across restarts.

---

## 💻 Developer Directions

If you want to run the python scripts directly or rebuild the executable:

### 1. Run the Command-Line Script (Untouched)
To run the original command-line utility from PowerShell:
1. Open PowerShell **as Administrator**.
2. Navigate to the project directory and run:
   ```powershell
   .venv\Scripts\python.exe ptt_dictate.py
   ```

### 2. Run the System Tray Script
To run the tray icon script natively:
1. Open PowerShell **as Administrator**.
2. Run headlessly (recommended):
   ```powershell
   .venv\Scripts\pythonw.exe app\ptt_tray.py
   ```
   *Or, if you want to see console print output for debugging, run:*
   ```powershell
   .venv\Scripts\python.exe app\ptt_tray.py
   ```

### 3. Repackage the Portable Distribution
To bundle changes under `app/` or dependency updates into a new distribution ZIP package:
1. Ensure the running executable is closed (right-click the tray icon and select **Exit**).
2. Open PowerShell and run:
   ```powershell
   .venv\Scripts\python.exe package_portable.py
   ```
3. This will rebuild and overwrite `ptt_dictate_dist.zip` with your latest files and virtual environment.

---

## 📄 Development Details
For detailed environment setup, packaging summaries, and the project roadmap, see:
* [agent_project_summary.md](file:///c:/Users/huber/git/ptt/agent_project_summary.md) (Packaging technical notes)
* [development_history.md](file:///C:/Users/huber/git/ptt/development_history.md) (Environment setup and roadmap)
