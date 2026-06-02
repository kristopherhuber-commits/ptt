# Push-to-Talk Local Dictation for Windows 11

A local, low-latency dictation utility that records audio when you hold a hotkey, transcribes it on your GPU using `faster-whisper`, and types the text directly at your cursor.

---

## ⚡ Quick Start (Windows Run)

You can launch this application directly using the Windows Run dialog:

1. Press **`Win + R`** to open the Run dialog.
2. Type **`ppt`** and press **Enter**.
3. Click **Yes** on the Windows User Account Control (UAC) prompt.
4. A PowerShell window running the script will open in the background. **Hold `Ctrl + Space`** to record audio, and release the keys to transcribe and type the text.

---

## 💻 Manual Execution

If you want to run the script manually from a command prompt:

1. Open PowerShell or Command Prompt **as Administrator** (required for global key hooks).
2. Navigate to the project directory:
   ```powershell
   cd "C:\Users\huber\git\ppt"
   ```
3. Run the script using the local virtual environment:
   ```powershell
   .venv\Scripts\python.exe ppt_dictate.py
   ```

---

## ⚙️ Configuration & Customization

All settings are configured at the top of [ppt_dictate.py](file:///c:/Users/huber%20(windows)/git/ppt/ppt_dictate.py):

* **Hotkey:** To change the recording hotkey, update `HOTKEY_MODS` (line 70).
* **Model Size:** Change `MODEL_SIZE` (line 65) to change transcription accuracy vs. speed (e.g., `"large-v3"` or `"large-v3-turbo"`).
* **Hardware Mode:** Default is set to GPU (`"cuda"`) and `float16` compute type to support Blackwell/Ampere architectures.

---

## 📄 Development Details
For detailed environment setup, script locations, and the project roadmap, see [development_history.md](file:///C:/Users/huber/git/ppt/development_history.md).
