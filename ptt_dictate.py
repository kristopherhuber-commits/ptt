"""
ptt_dictate.py -- entry point for the console frontend.

Hold the configured chord (Right Ctrl by default) to record; release to
transcribe and paste at the cursor. State is printed to stdout; press Ctrl+C to
quit.

This is the developer-facing frontend. It runs the same engine as the tray
(app/ptt/engine.py) and reads the same app/config.json, so a chord or device
chosen in one applies to the other. The only difference is that state is printed
rather than drawn on a tray icon.

Run from an Administrator terminal -- Windows UIPI blocks input injected from a
non-elevated process into an elevated one (FR-C5):

    .venv\\Scripts\\python.exe ptt_dictate.py
"""

import os
import sys

# This file lives at the repo root, where `ptt` is not importable -- the package
# is under app/. The tray shim gets its directory on sys.path for free by virtue
# of being inside app/; this one has to say so. pyproject.toml's
# `pythonpath = ["app"]` only helps pytest, not the runtime.
_APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app")
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from ptt import logging_setup, runtime, transcribe


def main():
    # echo=True mirrors every log line to stdout, which is how this frontend
    # gets the audio-stream diagnostics the tray writes only to the file.
    logging_setup.init(echo=True)

    # Register the CUDA/cuDNN DLL directories before CTranslate2 is imported;
    # see ptt/transcribe.py for why the ordering matters.
    transcribe.ensure_cuda_dll_dirs()

    from ptt import config, hotkey
    from ptt.engine import Engine

    cuda_supported = transcribe.cuda_available()
    # The engine applies the no-CUDA override; see its constructor.
    settings = config.load()

    print(f"Hold {hotkey.chord_label(settings.hotkey)} to dictate, release to type. "
          f"Ctrl+C to quit.", flush=True)

    engine = Engine(
        settings, cuda_supported,
        on_state=lambda state, status: print(f"[{state}] {status}", flush=True),
        on_text=lambda text: print(f"  -> {text}", flush=True),
    )

    try:
        # The console has no tray loop, so the engine owns the main thread.
        engine.run()
    except KeyboardInterrupt:
        # run() has already released the microphone in its finally block.
        engine.stop()
        print("\nBye.", flush=True)


if __name__ == "__main__":
    runtime.main_guard(main)
