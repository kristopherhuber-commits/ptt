# Push-to-Talk Local Dictation for Windows 11

A local, low-latency dictation utility that records audio when you hold a hotkey, transcribes it on your GPU using `faster-whisper`, and pastes the text directly at your cursor.

This application is built for compatibility and stability across different environments, including standard text editors, command lines, **WSL/Linux terminals**, and is fully immunized against device changes (such as connecting Jabra or other USB HID headsets).

---

## 🚀 Features & Architecture

* **Zero-Latency Audio**: PortAudio streams remain active continuously in low-power states to prevent audio hardware wake-up delays.
* **Universal Clipboard Pasting**: Pastes using `Shift+Insert` to ensure full compatibility with WSL terminals and bash command prompts, while preserving and restoring your original clipboard content.
* **Native Input Injection**: Bypasses the fragile Python `keyboard` library for pasting, using native Win32 `keybd_event` calls. This guarantees that your pasting never breaks when Windows resets the hook chain (e.g., when plugging in USB headsets like Jabra).
* **Smart App Control Compatible**: Uses a launcher signed by the Python Software Foundation to bypass Windows security blocks natively.
* **System Tray Interface**: Features a dynamic tray icon showing application status (Teal = Ready, Red = Recording, Yellow = Transcribing, Blue = Loading).

---

## 📦 Distribution & Installation (For Target PCs)

The application is distributed as a portable Python environment. No pre-existing Python installation or library configuration is required on the target computers.

### 1. Installation Steps
1. Copy the **`ptt_dictate_dist.zip`** archive to the target computer.
2. Extract the ZIP file completely.
3. Double-click **`install.bat`** inside the extracted folder.
4. Click **Yes** on the User Account Control (UAC) prompt. The batch script will automatically self-elevate to Administrator to complete the setup.

### 2. What the Installer Does
The installation script will automatically:
* Terminate any running PTT Dictation processes to prevent file locks.
* Copy the application files to `C:\Users\<Username>\AppData\Local\Programs\ptt_dictate\`.
* Create a **PTT Dictation** shortcut on the Desktop, pre-configured to **Run as Administrator** (required to type into elevated Windows apps) and styled with the built-in Windows **microphone icon**.
* Create a **PTT Dictation** shortcut in the Windows Startup folder to launch automatically when you log in.
* Relaunch the application immediately.

---

## ⚡ Run Directions

### 1. Launching
* **Normal Usage**: Double-click the **PTT Dictation** shortcut on your Desktop, or let it start automatically on login.
* **UAC Prompt**: Click **Yes** on the Windows User Account Control (UAC) elevation prompt.
* A **Teal Microphone** icon will appear in the Windows System Tray (notification area, bottom right). If it is hidden, click the **`^`** chevron next to the clock and drag the microphone icon to the main taskbar.

### 2. Usage
* **Record**: **Hold `Shift + Alt`** and speak. The tray icon will turn **Red**.
* **Transcribe**: **Release the keys**. The tray icon will turn **Yellow** while it transcribes and automatically pastes the text directly at your cursor.
* **Settings**: Right-click the system tray icon to:
  * Check the current state (`Status: Ready (CUDA)`, `Status: Recording...`, etc.).
  * Toggle between **`Use GPU (CUDA)`** and **`Use CPU`** modes.
  * **Exit** the application.
* **Persistence**: The application creates a local `config.json` file in its directory to remember your CPU/GPU preference across restarts.

---

## 💻 Developer Directions

If you want to run the python scripts directly or rebuild the executable:

### 1. Run the Command-Line Script
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

### 3. Build & Package the Portable Distribution
If you pull the repository on a new computer (or need to rebuild it from scratch):
1. Ensure the running executable is closed (right-click the tray icon and select **Exit**).
2. Run the build script using your local Python installation:
   ```powershell
   python build_portable.py
   ```
This script will automatically:
* Create a virtual environment (`.venv`) if it does not exist.
* Upgrade pip and install all package requirements from `requirements.txt`.
* Configure the virtual environment with the signed Python interpreter DLLs (renaming `pythonw.exe` to `ptt_dictate.exe` so it shows up correctly on the GPU).
* Bundle everything cleanly into `ptt_dictate_dist.zip`.

---

## 📄 Development Details
For detailed environment setup, packaging summaries, and the project roadmap, see:
* [development_history.md](file:///C:/Users/huber/git/ptt/development_history.md) (Environment setup and roadmap)
