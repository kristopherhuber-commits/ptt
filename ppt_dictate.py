"""
ppt_dictate.py — Push-to-talk local dictation for Windows 11.

Hold Ctrl+Alt -> records mic. Release -> transcribes on the GPU (faster-whisper,
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
    python ppt_dictate.py
"""

import os
import sys
import time
import threading

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
import keyboard
import pyperclip
from faster_whisper import WhisperModel

# ----------------------------- Configuration ---------------------------------
MODEL_SIZE   = "large-v3"      # swap "large-v3-turbo" (recent FW) for lower latency
DEVICE       = "cuda"
COMPUTE_TYPE = "float16"       # REQUIRED on Blackwell; do NOT use int8
SAMPLE_RATE  = 16_000          # Whisper's native rate
LANGUAGE     = "en"            # set None for autodetect
HOTKEY_MODS  = ("ctrl", "space") # the push-to-talk chord
POLL_SEC     = 0.02            # 20 ms hotkey polling
# ------------------------------------------------------------------------------


class Recorder:
    """Captures mono float32 audio while active, into an in-memory buffer."""
    def __init__(self, samplerate):
        self.samplerate = samplerate
        self._frames = []
        self._stream = None

    def _callback(self, indata, frames, time_info, status):
        if status:
            print(f"[audio] {status}", file=sys.stderr)
        self._frames.append(indata.copy())

    def start(self):
        self._frames = []
        self._stream = sd.InputStream(
            samplerate=self.samplerate, channels=1,
            dtype="float32", callback=self._callback,
        )
        self._stream.start()

    def stop(self):
        if self._stream is None:
            return np.empty(0, dtype=np.float32)
        self._stream.stop()
        self._stream.close()
        self._stream = None
        if not self._frames:
            return np.empty(0, dtype=np.float32)
        return np.concatenate(self._frames, axis=0).flatten()


def paste_text(text):
    """Insert text at the cursor by simulating typing (highly compatible)."""
    if not text:
        return
    # Add a tiny sleep to make sure keys are fully released before typing starts
    time.sleep(0.05)
    keyboard.write(text)


def chord_held():
    return all(keyboard.is_pressed(k) for k in HOTKEY_MODS)


def main():
    print(f"Loading {MODEL_SIZE} on {DEVICE} ({COMPUTE_TYPE}) ...")
    model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
    print(f"Ready. Hold {'+'.join(HOTKEY_MODS).upper()} to dictate, release to type. "
          f"Ctrl+C to quit.")

    rec = Recorder(SAMPLE_RATE)
    recording = False

    try:
        while True:
            held = chord_held()
            if held and not recording:
                recording = True
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
                    audio, language=LANGUAGE, beam_size=5, vad_filter=True,
                )
                text = "".join(s.text for s in segments).strip()
                if text:
                    print(f"  -> {text}")
                    paste_text(text)
                else:
                    print("  (no speech detected)")
            time.sleep(POLL_SEC)
    except KeyboardInterrupt:
        if recording:
            rec.stop()
        print("\nBye.")


if __name__ == "__main__":
    main()
