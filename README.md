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
* **Three-layer interface**, all built with Qt (PySide6):
  * a **tray icon** showing application status (Teal = Ready, Red = Recording, Yellow = Transcribing, Blue = Loading), with the same right-click menu it has always had;
  * a **hover popover** — glance at the tray icon and a small panel shows the state, the hotkey, the model, the microphone and the last transcription. It never takes keyboard focus, so you can keep typing into whatever you were typing into, and it draws over always-on-top windows;
  * a **settings window** with six tabs, opened by clicking the popover or choosing **Settings…** from the tray menu. **Every control applies the moment you touch it** — there is no OK, Apply or Cancel, and nothing needs a restart.

The tray was previously drawn by `pystray`. It is now a `QSystemTrayIcon`; the icons themselves are the same drawing code, pixel for pixel. `pystray` has been removed from the project.

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

1. Download **`ptt_dictate_dist.zip`** (~1.45 GB) from the [**Releases page**](https://github.com/kristopherhuber-commits/ptt/releases/latest).
2. Extract the ZIP file completely.
3. Double-click **`install.bat`** inside the extracted folder.
4. Click **Yes** on the User Account Control (UAC) prompt. The batch script will automatically self-elevate to Administrator to complete the setup.

The application is distributed as a portable Python environment, so no pre-existing Python installation or library configuration is required on the target computer.

#### What the installer does

* Terminate any running PTT Dictation processes to prevent file locks.
* Copy the application files to `C:\Users\<Username>\AppData\Local\Programs\ptt_dictate\`.
* Create a **PTT Dictation** shortcut on the Desktop, pre-configured to **Run as Administrator** (required to type into elevated Windows apps) and styled with the built-in Windows **microphone icon**.
* Create a **PTT Dictation** shortcut in the Windows Startup folder. **This does not currently start the application at log-in** — see [Known limitation](#known-limitation-it-does-not-start-itself-after-a-reboot) below.
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

* **Normal Usage**: Double-click the **PTT Dictation** shortcut on your Desktop. You need to do this once after each reboot.
* **UAC Prompt**: Click **Yes** on the Windows User Account Control (UAC) elevation prompt.
* A **Teal Microphone** icon will appear in the Windows System Tray (notification area, bottom right). If it is hidden, click the **`^`** chevron next to the clock and drag the microphone icon to the main taskbar.

#### Known limitation: it does not start itself after a reboot

The installer puts a shortcut in the Windows Startup folder, but the application
does **not** come up on its own when you log in. You have to launch it once per
boot from the Desktop shortcut.

The cause is the elevation the app needs in order to type into other windows
(`FR-C5`): the Startup shortcut is marked **Run as administrator**, and Windows
does not silently auto-launch Startup-folder shortcuts that request elevation.
Fixing it properly means registering a Task Scheduler task with "Run with highest
privileges" instead of using the Startup folder, which is a change to
`install.ps1`. That has not been done yet.

### 3.2 Usage

* **Record**: **Hold `Right Ctrl`** and speak. The tray icon will turn **Red**.
* **Transcribe**: **Release the keys**. The tray icon will turn **Yellow** while it transcribes and automatically pastes the text directly at your cursor.
* **Check the state**: **Hover** the tray icon. A panel appears next to it showing the current state, the bound hotkey, the model and the device, the microphone in use, and how long the last transcription took and how many words it produced. It has no controls — it is a display — and it will not steal focus from what you are typing into.
* **Tray menu**: Right-click the system tray icon to:
  * Check the current state (`Status: Ready (CUDA)`, `Status: Recording...`, etc.) and the active `Hotkey:`.
  * Toggle between **`Use GPU (CUDA)`** and **`Use CPU`** modes. On a machine without a supported GPU the CUDA option is unavailable and CPU is used automatically.
  * Open **`Settings…`**.
  * **Exit** the application.
* **Settings window**: click the popover, or choose **Settings…** from the tray menu. Six tabs, and **every control applies immediately** — there is no OK button, no Apply, no Cancel, and no restart. Each change is written to `config.json` straight away and confirmed in the status bar.

  | Tab | What it does |
  |---|---|
  | **Hotkey** | A full keyboard diagram. Click a key to bind it; only keys that type nothing on their own can be bound. Every key you press shades as you press it. A compatibility panel warns about chords that will interfere with ordinary typing. |
  | **Model** | The Whisper size tiers, and the GPU/CPU choice. `Measure on this machine` times one transcription of a bundled 30-second clip. |
  | **Audio** | The microphone picker, a live level meter, and three recording-behaviour switches. |
  | **Vocabulary** | Replacement rules applied to the transcript before it is pasted — for names and jargon the model mishears. |
  | **Advanced** | The engine's tuning constants, read live from the modules that own them. A readout, not a form. |
  | **Diagnostics** | CUDA state, median latency, where the last paste went, and the tail of `debug_log.txt`. |
* **Persistence**: The application creates a local `config.json` file in its directory to remember your CPU/GPU preference, hotkey and model across restarts. Settings it does not recognise are preserved, so a newer build's config survives a rollback.
* **Changing the hotkey**: Open **Settings…** from the tray menu and click a key on the **Hotkey** tab's keyboard. It applies immediately — there is no OK button and no restart. Every key you press shades on the diagram as you press it, so you can see the app is reading your keyboard.

  It can still be set by hand. Add a `hotkey` entry to `config.json` and restart; it takes a list of key names, all of which must be held together:

  ```json
  { "version": 1, "use_gpu": true, "hotkey": ["rctrl"], "model": "large-v3-turbo" }
  ```

  Valid names: `ctrl`, `lctrl`, `rctrl`, `shift`, `lshift`, `rshift`, `alt`, `lalt`, `ralt`, `win`, `lwin`, `rwin`, `space`. Unsided names (`ctrl`) match either side. An unrecognised name falls back to the default and is noted in `debug_log.txt`.

  Two cautions when choosing your own, both learned the hard way (see [docs/development_history.md](docs/development_history.md)): keys that produce a **character or scroll** (`space`) leak into the focused window while you hold them, and chords containing **`alt`** activate the target window's menu bar on release, which steals keyboard focus and silently discards the paste. The app now disarms the Alt case automatically, but `Alt+Shift` and `Ctrl+Shift` remain Windows' input-language and keyboard-layout switches when a second layout is installed. The Hotkey tab shows these warnings for whatever chord you pick, so you no longer have to remember them.

* **Changing the model**: The **Model** tab lists the Whisper size tiers and switches between them immediately, reloading the engine. `Measure on this machine` times one transcription of a bundled 30-second clip so the latency column is a figure from your hardware rather than a published number for someone else's. Downloading and deleting models are not implemented yet; selecting a model that is not on disk fetches it as part of loading it.

---

## 4. 📄 Documentation

* [docs/requirements.md](docs/requirements.md) — what the utility must do, and the compatibility constraints that earlier bugs produced.
* [docs/design.md](docs/design.md) — how it is built: configuration matrix, module layout, the keystroke-injection contract, and the hotkey design.
* [docs/verification.md](docs/verification.md) — the tests: what each one verifies, which part of the design it traces to, and the results.
* [docs/development_history.md](docs/development_history.md) — the retrospective log of solved issues.
* [docs/ptt-v2-gui/gui_handoff.md](docs/ptt-v2-gui/gui_handoff.md) — the PySide6 GUI specification: the three UI layers (tray icon, hover popover, settings window), panel-by-panel behaviour, and the acceptance criteria. `docs/ptt-v2-gui/ptt_dictation_ui_mockups.html` is the visual reference and `claude_code_prompt.md` is the staged build plan.

The implementation lives in `app/ptt/`; `ptt_dictate.py` and `app/ptt_tray.py` are thin
entry points over it. See [docs/design.md](docs/design.md) section 4 for the full module
layout.

### Where the interface lives

The three layers described in section 1 are `app/ptt/ui/`, one module per piece:

| Module | Layer |
|---|---|
| `ui/qt_app.py` | Owns the `QApplication`, and holds `EngineBridge` — the boundary between the engine's thread and the GUI thread. |
| `ui/qt_tray.py` | Layer 1: the `QSystemTrayIcon`, its menu, and the state-to-icon map. Replaces the deleted `ui/tray.py`, which used `pystray`. |
| `ui/qt_popover.py` | Layer 2: the frameless, non-activating hover panel. |
| `ui/qt_window.py` | Layer 3: the `QMainWindow`, the tab bar and the status bar. |
| `ui/qt_statusview.py` | The read-only state display — built once and embedded twice, as the popover's body and as the window's banner, so the two cannot drift apart. |
| `ui/qt_theme.py` | Registers the bundled fonts and applies `style.qss`. |
| `ui/qt_marks.py` | The small `+` registration marks at the corners of the status panel and every tab — the crosshair a printer uses to align colour plates, and the motif that makes the window read as a technical drawing. |
| `ui/qt_threadcheck.py` | `THREAD-CHECK`: the one line each queued hop writes to `debug_log.txt` at its first emission, which is the evidence that the hop is real. Two threads must be named and they must differ. |
| `ui/qt_concierge.py` | Layer 4 (v3.0): the Concierge chat panel, docked right of the tabs. Holds a `ConciergeView` — a plain object with no Qt in it that decides what the transcript contains — and renders it. A chat is one of five things it can be showing: `gate_for` picks between the first-run card, the model download, the no-CUDA and switched-off cards and the chat, from the state machine plus the two opt-in keys. |
| `ui/qt_concierge_worker.py` | The thread adapter between that panel and the Qt-free harness in `ptt/concierge/`. `ConciergeWorker` owns the harness on a worker thread; `ConciergeController` lives on the GUI thread and is the only place a cross-thread connection is made. |
| `ui/panels/` | One module per tab: `hotkey`, `model`, `audio`, `vocabulary`, `advanced`, `diagnostics`. `panels/__init__.py` holds `InstantApplyPanel`, the one write-then-save-then-tell-the-engine sequence every control routes through. |

**The engine never imports the UI.** It reports state through a callback the frontend
supplies, which is what lets one core drive both the tray application and the console
frontend.

### Bundled fonts

The interface uses **Barlow** and **Barlow Condensed** — Copyright 2017 The Barlow
Project Authors (https://github.com/jpt/barlow), licensed under the
**SIL Open Font License, Version 1.1**. The font files live in
`app/assets/fonts/` and ship in the distribution together with their `OFL.txt` licence
files, as the licence requires. If they fail to register the interface falls back to
Segoe UI and says so in `debug_log.txt`.
