# Push-to-Talk Local Dictation for Windows 11

A local, low-latency dictation utility that records audio when you hold a hotkey, transcribes it on your GPU using `faster-whisper`, and types the text directly at your cursor.

This repository includes both a **command-line developer version** and a **compiled standalone System Tray application** that runs headless without a console window.

---

## 📦 Distribution & Installation Directions (For Target PCs)

The application is fully portable and requires **no Python installation** on target PCs.

### 1. Installation
1. Copy the **`ptt_dictate_dist.zip`** archive to the target computer.
2. Extract the ZIP file to a folder of your choice (e.g., `C:\Users\<Username>\Documents` or Desktop).
3. Open the extracted folder and locate **`ptt_dictate.exe`**.
4. **Create a Shortcut:** Right-click `ptt_dictate.exe` -> select **Show more options** -> **Create shortcut** (or Send to -> Desktop).
5. **Configure Administrator Privileges:**
   * Right-click the newly created shortcut and select **Properties**.
   * On the **Shortcut** tab, click the **Advanced...** button.
   * Check the box for **"Run as administrator"**.
   * Click **OK**, then click **Apply**.
   * *(Note: Administrator privileges are required for global key interception and to simulate typing into other elevated applications).*

---

## ⚡ Run Directions

### 1. Launching
1. Double-click the shortcut you created (or `ptt_dictate.exe` directly).
2. Click **Yes** on the Windows User Account Control (UAC) elevation prompt.
3. A **Teal Microphone** icon will appear in the Windows System Tray (notification area, usually bottom right).

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
2. Run:
   ```powershell
   .venv\Scripts\python.exe app\ptt_tray.py
   ```

### 3. Rebuild the Standalone Executable
To compile changes under `app/` and generate a new ZIP package:
1. Ensure the running executable is closed (right-click the tray icon and select **Exit**).
2. Open PowerShell and run:
   ```powershell
   .venv\Scripts\python.exe build_dist.py
   ```
3. This will rebuild the executable under `dist/` and overwrite `ptt_dictate_dist.zip`.

---

## 📄 Development Details
For detailed environment setup, packaging summaries, and the project roadmap, see:
* [agent_project_summary.md](file:///c:/Users/huber/git/ptt/agent_project_summary.md) (Packaging technical notes)
* [development_history.md](file:///C:/Users/huber/git/ptt/development_history.md) (Environment setup and roadmap)
