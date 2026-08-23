# Push-to-Talk Local Dictation for Windows 11

A local, low-latency dictation utility that records audio when you hold a hotkey, transcribes it with `faster-whisper`, and pastes the text directly at your cursor. Everything runs on your own machine — no audio ever leaves it.

This application is built for compatibility and stability across different environments, including standard text editors, command lines, **WSL/Linux terminals**, and is fully immunized against device changes (such as connecting Jabra or other USB HID headsets).

---

## 1. 🚀 Features & Architecture

* **Zero-Latency Audio**: PortAudio streams remain active continuously in low-power states to prevent audio hardware wake-up delays.
* **Runs on CPU or GPU**: Transcribes on an NVIDIA GPU (CUDA, `float16`) when one is available and on the CPU (`int8`) when it is not. **A GPU is not required.** Switch between them at any time from the tray menu without restarting, and if a GPU load ever fails the application falls back to CPU on its own and remembers the choice. The GPU is substantially faster — measured on one machine, the same utterance took 0.5 s on GPU against 5.5 s on CPU.
* **Focus-Safe Hotkey**: Defaults to `Right Ctrl` — a lone modifier that types no character, scrolls nothing, and never activates a menu bar. Configurable via `config.json`.
* **Universal Clipboard Pasting**: Pastes using `Shift+Insert` to ensure full compatibility with WSL terminals and bash command prompts, while preserving and restoring your original clipboard content.
* **Native Input Injection**: Bypasses the fragile Python `keyboard` library for pasting, using native Win32 `keybd_event` calls. This guarantees that your pasting never breaks when Windows resets the hook chain (e.g., when plugging in USB headsets like Jabra).
* **Smart App Control Compatible**: Uses a launcher signed by the Python Software Foundation to bypass Windows security blocks natively.
* **System Tray Interface**: Features a dynamic tray icon showing application status (Teal = Ready, Red = Recording, Yellow = Transcribing, Blue = Loading).

---

## 2. 📦 Installation

There are two ways to install. **Most people want Option 1.**

| | Who it is for | What you need |
|---|---|---|
| **Option 1** | Anyone who just wants to use the app | A browser. Nothing else. |
| **Option 2** | Developers, or anyone modifying the code | Git and a Python 3.14 installation |

Both produce the same application. The published archive is built from the tagged commit it is attached to, so the two are equivalent.

**Requirements either way:** Windows 11, and a microphone. An NVIDIA GPU is optional and only affects speed (see section 1). The first launch downloads the speech model (~1.6 GB) and takes a few minutes; every launch after that takes a few seconds.

### Option 1 — Install the ready-made release *(recommended)*

No Python, no developer tools, no command line.

1. Download **`ptt_dictate_dist.zip`** (~1.35 GB) from the [**Releases page**](https://github.com/kristopherhuber-commits/ptt/releases/latest).
2. Extract the ZIP file completely.
3. Double-click **`install.bat`** inside the extracted folder.
4. Click **Yes** on the User Account Control (UAC) prompt. The batch script will automatically self-elevate to Administrator to complete the setup.

The application is distributed as a portable Python environment, so no pre-existing Python installation or library configuration is required on the target computer.

#### What the installer does

* Terminate any running PTT Dictation processes to prevent file locks.
* Copy the application files to `C:\Users\<Username>\AppData\Local\Programs\ptt_dictate\`.
* Create a **PTT Dictation** shortcut on the Desktop, pre-configured to **Run as Administrator** (required to type into elevated Windows apps) and styled with the built-in Windows **microphone icon**.
* Create a **PTT Dictation** shortcut in the Windows Startup folder to launch automatically when you log in.
* Relaunch the application immediately.

### Option 2 — Build it yourself from source

For developers, or if you pull the repository onto a new computer and want to rebuild from scratch. Requires a local Python 3.14 installation.

#### Build the distributable

1. Ensure any running executable is closed (right-click the tray icon and select **Exit**).
2. Run the build script using your local Python installation:
   ```powershell
   python build_portable.py
   ```

This script will automatically:
* Create a virtual environment (`.venv`) if it does not exist.
* Upgrade pip and install all package requirements from `requirements.txt`.
* Configure the virtual environment with the signed Python interpreter DLLs (renaming `pythonw.exe` to `ptt_dictate.exe` so it shows up correctly on the GPU).
* Bundle everything cleanly into `ptt_dictate_dist.zip`.

The resulting archive is byte-for-byte the same kind of package as the one on the Releases page. Install it exactly as described in Option 1, starting from step 2.

#### Or run directly from the source tree

Once `.venv` exists, you can run either frontend without building or installing anything.

**The system tray application** — the one that ships. Open PowerShell **as Administrator**, then run headlessly:
```powershell
.venv\Scripts\pythonw.exe app\ptt_tray.py
```
*Or, if you want to see console print output for debugging:*
```powershell
.venv\Scripts\python.exe app\ptt_tray.py
```

**The console frontend** — a developer-facing frontend that prints state to the terminal instead of drawing a tray icon. It runs the same engine and reads the same `config.json` as the tray, so a hotkey or device chosen in either applies to both. Open PowerShell **as Administrator**, navigate to the project directory, then run:
```powershell
.venv\Scripts\python.exe ptt_dictate.py
```

Administrator is required in both cases: Windows UIPI blocks input injected from a non-elevated process into an elevated one.

---

## 3. ⚡ Run Directions

### 3.1 Launching

* **Normal Usage**: Double-click the **PTT Dictation** shortcut on your Desktop, or let it start automatically on login.
* **UAC Prompt**: Click **Yes** on the Windows User Account Control (UAC) elevation prompt.
* A **Teal Microphone** icon will appear in the Windows System Tray (notification area, bottom right). If it is hidden, click the **`^`** chevron next to the clock and drag the microphone icon to the main taskbar.

### 3.2 Usage

* **Record**: **Hold `Right Ctrl`** and speak. The tray icon will turn **Red**.
* **Transcribe**: **Release the keys**. The tray icon will turn **Yellow** while it transcribes and automatically pastes the text directly at your cursor.
* **Settings**: Right-click the system tray icon to:
  * Check the current state (`Status: Ready (CUDA)`, `Status: Recording...`, etc.) and the active `Hotkey:`.
  * Toggle between **`Use GPU (CUDA)`** and **`Use CPU`** modes. On a machine without a supported GPU the CUDA option is unavailable and CPU is used automatically.
  * **Exit** the application.
* **Persistence**: The application creates a local `config.json` file in its directory to remember your CPU/GPU preference and hotkey across restarts. Settings it does not recognise are preserved, so a newer build's config survives a rollback.
* **Changing the hotkey**: Add a `hotkey` entry to `config.json` and restart. It takes a list of key names, all of which must be held together:

  ```json
  { "version": 1, "use_gpu": true, "hotkey": ["rctrl"] }
  ```

  Valid names: `ctrl`, `lctrl`, `rctrl`, `shift`, `lshift`, `rshift`, `alt`, `lalt`, `ralt`, `win`, `lwin`, `rwin`, `space`. Unsided names (`ctrl`) match either side. An unrecognised name falls back to the default and is noted in `debug_log.txt`.

  Two cautions when choosing your own, both learned the hard way (see [docs/development_history.md](docs/development_history.md)): keys that produce a **character or scroll** (`space`) leak into the focused window while you hold them, and chords containing **`alt`** activate the target window's menu bar on release, which steals keyboard focus and silently discards the paste. The app now disarms the Alt case automatically, but `Alt+Shift` and `Ctrl+Shift` remain Windows' input-language and keyboard-layout switches when a second layout is installed.

---

## 4. 📄 Documentation

* [docs/requirements.md](docs/requirements.md) — what the utility must do, and the compatibility constraints that earlier bugs produced.
* [docs/design.md](docs/design.md) — how it is built: configuration matrix, module layout, the keystroke-injection contract, and the hotkey design.
* [docs/development_history.md](docs/development_history.md) — the retrospective log of solved issues.

The implementation lives in `app/ptt/`; `ptt_dictate.py` and `app/ptt_tray.py` are thin
entry points over it. See [docs/design.md](docs/design.md) section 4 for the module layout.
