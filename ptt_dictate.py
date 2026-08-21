"""
ptt_dictate.py — Push-to-talk local dictation for Windows 11.

Hold Right Ctrl -> records mic. Release -> transcribes on the GPU (faster-whisper,
fp16) and pastes the text at the current cursor location.

Built for an RTX 5090 (Blackwell / sm_120):
  * compute_type = "float16"  (int8 crashes on sm_120 with CUBLAS_STATUS_NOT_SUPPORTED)
  * ctranslate2 >= 4.5.0       (first version with sm_120 support)
  * cuDNN 9 DLLs added to the search path at startup (see _add_nvidia_dll_dirs)

Install (native Windows Python 3.11/3.12 venv, NOT WSL):
    python -m venv .venv && .venv\\Scripts\\activate
    pip install -U "faster-whisper" "ctranslate2>=4.5.0" sounddevice numpy keyboard pyperclip
    pip install -U nvidia-cudnn-cu12 nvidia-cublas-cu12   # provides cuDNN9/cuBLAS DLLs

Run (Administrator terminal — the `keyboard` hook needs it for a global hotkey,
and to inject into elevated windows the process must itself be elevated):
    python ptt_dictate.py
"""

import os
import sys
import time
import threading
import socket
import re
import pyperclip

# --- Make pip-installed CUDA/cuDNN DLLs discoverable before importing CT2 -----
def _add_nvidia_dll_dirs():
    if not sys.platform.startswith("win"):
        return
    try:
        import nvidia  # the meta-namespace from the nvidia-*-cu12 wheels
    except ImportError:
        return
    
    # nvidia is a namespace package, so __file__ might be None.
    # We inspect its __path__ list instead.
    paths = getattr(nvidia, "__path__", None)
    if not paths:
        file_path = getattr(nvidia, "__file__", None)
        if file_path:
            paths = [os.path.dirname(file_path)]
        else:
            return

    for base in paths:
        for sub in ("cudnn", "cublas", "cuda_nvrtc"):
            d = os.path.join(base, sub, "bin")
            if os.path.isdir(d):
                try:
                    os.add_dll_directory(d)
                    os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]
                except Exception:
                    pass

_add_nvidia_dll_dirs()

import numpy as np
import sounddevice as sd
sd._terminate()  # Terminate initial auto-initialization to prevent sleep blocking
import keyboard
from faster_whisper import WhisperModel
import ctypes

class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("dwTime", ctypes.c_uint)
    ]

def get_idle_duration():
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        tick_count = ctypes.windll.kernel32.GetTickCount()
        millis = (tick_count - lii.dwTime) & 0xFFFFFFFF
        return millis / 1000.0
    return 0.0

# ----------------------------- Configuration ---------------------------------
# Desktop (darklord) gets the full large-v3; laptops/other hosts get large-v3-turbo
IS_DESKTOP   = socket.gethostname().lower() == "darklord"
MODEL_SIZE   = "large-v3-turbo"

DEVICE       = "cuda"
COMPUTE_TYPE = "float16"       # REQUIRED on Blackwell; do NOT use int8
SAMPLE_RATE  = 16_000          # Whisper's native rate
LANGUAGE     = "en"            # set None for autodetect
HOTKEY_MODS  = ("rctrl",)      # push-to-talk chord; a lone modifier, so no
                               # character, no scroll, no menu activation
POLL_SEC     = 0.02            # 20 ms hotkey polling
# ------------------------------------------------------------------------------


class Recorder:
    """Captures mono float32 audio while active, into an in-memory buffer."""
    def __init__(self, samplerate):
        self.samplerate = samplerate
        self._frames = []
        self._preroll = []
        self._stream = None
        self.recording = False

    def _callback(self, indata, frames, time_info, status):
        if status:
            print(f"[audio] {status}", file=sys.stderr)
        if self.recording:
            self._frames.append(indata.copy())
        else:
            self._preroll.append(indata.copy())
            while len(self._preroll) > 4:
                self._preroll.pop(0)

    def open_stream(self):
        if self._stream is not None:
            return
        try:
            sd._initialize()
            self._frames = []
            self._preroll = []
            self._stream = sd.InputStream(
                samplerate=self.samplerate, channels=1,
                dtype="float32", callback=self._callback,
            )
            self._stream.start()
        except Exception as e:
            print(f"[audio] Failed to open stream: {e}", file=sys.stderr)

    def close_stream(self):
        self.recording = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
            try:
                sd._terminate()
            except Exception:
                pass
        self._frames = []
        self._preroll = []

    def start(self):
        if self._stream is None:
            self.open_stream()
        # Seed main frames with pre-roll data to capture early speech
        self._frames = list(self._preroll)
        self._preroll = []
        self.recording = True

    def stop(self):
        self.recording = False
        self._preroll = []
        if not self._frames:
            return np.empty(0, dtype=np.float32)
        audio_data = np.concatenate(self._frames, axis=0).flatten()
        self._frames = []
        return audio_data


def paste_text(text):
    """Insert text at the cursor by copying to clipboard and simulating Shift+Insert via native Win32 API."""
    if not text:
        return
        
    # Save original clipboard contents
    try:
        old_clipboard = pyperclip.paste()
    except Exception:
        old_clipboard = None
        
    # Copy the transcribed text to clipboard
    try:
        pyperclip.copy(text)
    except Exception:
        # Fallback to direct typing if clipboard copy fails
        try:
            keyboard.write(text)
        except Exception:
            pass
        return

    # Neutralise any modifier still physically held, so the paste chord is not
    # reinterpreted as a shortcut by the target window. Alt is disarmed first:
    # releasing it bare would activate the window's menu and steal focus.
    try:
        suppress_alt_menu()
        for vk in NEUTRALISE_VKS:
            if ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000:
                _send_key(vk, keyup=True)
    except Exception:
        for key in ("ctrl", "alt", "win"):
            try:
                keyboard.release(key)
            except Exception:
                pass

    # Simulate Shift+Insert to paste via native Win32 keybd_event with scan codes and extended key flags
    try:
        time.sleep(0.01)
        _send_key(0x10)                              # Shift down
        _send_key(0x2D, extended=True)               # Insert down (extended)
        _send_key(0x2D, keyup=True, extended=True)   # Insert up (extended)
        _send_key(0x10, keyup=True)                  # Shift up
    except Exception:
        try:
            keyboard.press_and_release("shift+insert")
        except Exception:
            pass

    # Wait for Windows to process the paste before restoring clipboard
    time.sleep(0.1)
    
    # Restore original clipboard contents
    if old_clipboard is not None:
        try:
            pyperclip.copy(old_clipboard)
        except Exception:
            pass


# --------------------------- Hotkey key tables -------------------------------
# Virtual-key codes for every key that may take part in the push-to-talk chord.
# Left/right variants are listed separately so a single side can be bound; the
# unsided names ("ctrl", "alt", ...) match either side.
VK_MAP = {
    "ctrl":  0x11, "lctrl":  0xA2, "rctrl":  0xA3,
    "shift": 0x10, "lshift": 0xA0, "rshift": 0xA1,
    "alt":   0x12, "lalt":   0xA4, "ralt":   0xA5,
    "win":   0x5B, "lwin":   0x5B, "rwin":   0x5C,
    "space": 0x20,
}

# Human-readable chord labels for the tray menu and console banner.
KEY_LABELS = {
    "ctrl": "Ctrl", "lctrl": "Left Ctrl", "rctrl": "Right Ctrl",
    "shift": "Shift", "lshift": "Left Shift", "rshift": "Right Shift",
    "alt": "Alt", "lalt": "Left Alt", "ralt": "Right Alt",
    "win": "Win", "lwin": "Left Win", "rwin": "Right Win",
    "space": "Space",
}

ALT_VKS = (0x12, 0xA4, 0xA5)

# Modifiers neutralised before pasting. Both sides are listed explicitly:
# releasing the unsided VK_CONTROL leaves the right-hand key state set.
NEUTRALISE_VKS = (0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C)

# Reserved, unassigned virtual key. Produces no character and no command, so it
# is safe to inject purely to break up an Alt press (see suppress_alt_menu).
VK_NONAME = 0xFC


def _send_key(vk, keyup=False, extended=False):
    """Inject one key event carrying a real hardware scan code (UWP apps reject bare VKs)."""
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    flags = (0x01 if extended else 0) | (0x02 if keyup else 0)
    ctypes.windll.user32.keybd_event(vk, scan, flags, 0)


def _alt_is_down():
    return any((ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000) != 0 for vk in ALT_VKS)


def suppress_alt_menu():
    """
    Stop an Alt release from opening the focused window's menu bar.

    Windows activates the menu -- or, in WinUI apps like Windows 11 Notepad, the
    access-key layer -- when Alt goes up and no other key was pressed in between.
    That moves keyboard focus off the document: the caret disappears and every
    subsequent injected keystroke, Shift+Insert and Ctrl+V alike, is discarded.
    Tapping a reserved unassigned key while Alt is still held supplies the
    missing intervening keypress, so the release becomes inert.
    """
    if not _alt_is_down():
        return
    try:
        _send_key(VK_NONAME)
        _send_key(VK_NONAME, keyup=True)
    except Exception:
        pass


def hotkey_label(chord):
    return " + ".join(KEY_LABELS.get(k, k.title()) for k in chord)


def chord_held():
    """Check if all keys in the hotkey chord are pressed using GetAsyncKeyState."""
    try:
        return all((ctypes.windll.user32.GetAsyncKeyState(VK_MAP[k]) & 0x8000) != 0 for k in HOTKEY_MODS)
    except Exception:
        # The keyboard library has no side-aware names: strip the l/r prefix.
        return all(keyboard.is_pressed(k.lstrip("lr")) for k in HOTKEY_MODS)


def main():
    print(f"Loading {MODEL_SIZE} on {DEVICE} ({COMPUTE_TYPE}) ...")
    model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
    print(f"Ready. Hold {hotkey_label(HOTKEY_MODS)} to dictate, release to type. "
          f"Ctrl+C to quit.")

    rec = Recorder(SAMPLE_RATE)
    recording = False
    stream_open = False
    IDLE_THRESHOLD_SEC = 240.0

    try:
        while True:
            idle = get_idle_duration()
            if idle < IDLE_THRESHOLD_SEC:
                if not stream_open:
                    rec.open_stream()
                    stream_open = True
            else:
                if stream_open and not recording:
                    rec.close_stream()
                    stream_open = False

            held = chord_held()
            if held and not recording:
                recording = True
                # Break up the Alt press now, while it is still held: once the
                # user releases it the menu has already taken focus.
                suppress_alt_menu()
                rec.start()
                print("> recording", end="", flush=True)
            elif not held and recording:
                recording = False
                audio = rec.stop()
                print(" ... transcribing", flush=True)
                if audio.size < SAMPLE_RATE * 0.3:   # ignore < 0.3 s blips
                    print("  (too short, skipped)")
                    continue
                segments, _ = model.transcribe(
                    audio,
                    language=LANGUAGE,
                    beam_size=5,
                    vad_filter=True,
                    condition_on_previous_text=False
                )
                text = "".join(s.text for s in segments).strip()
                text = re.sub(r'\.{2,}', '', text).strip()
                if text:
                    print(f"  -> {text}")
                    paste_text(text)
                else:
                    print("  (no speech detected)")
            time.sleep(POLL_SEC)
    except KeyboardInterrupt:
        if recording:
            rec.stop()
        rec.close_stream()
        print("\nBye.")


if __name__ == "__main__":
    try:
        main()
        import os
        os._exit(0)
    except Exception as e:
        import os
        os._exit(1)
