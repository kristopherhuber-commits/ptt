"""
ptt_tray.py -- entry point for the system tray frontend.

Runs without a console window. The implementation lives in app/ptt/; this file
only wires it together. The invocation path is unchanged: run_tray.bat still
calls `.venv\\Scripts\\ptt_dictate.exe app\\ptt_tray.py`, and install.ps1 still
copies app/ wholesale.
"""

import os
import sys
import traceback

# Running this as a script already puts app/ on sys.path, so `import ptt`
# resolves. The insert is belt and braces for other invocations (-m, a different
# cwd, a future frozen build) and costs nothing.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from ptt import logging_setup, paths, runtime, transcribe


def main():
    logging_setup.init()

    # Register the CUDA/cuDNN DLL directories before CTranslate2 is ever
    # imported. transcribe.py defers that import into its functions, so this is
    # the only ordering guarantee needed; see its docstring for why it matters.
    transcribe.ensure_cuda_dll_dirs()

    # Heavy imports go inside main(), inside a guard. At module scope a failure
    # would traceback into pythonw.exe's void with nothing in the log.
    try:
        logging_setup.log_debug("Importing system and audio libraries...")
        from ptt import engine as engine_mod
        logging_setup.log_debug("Importing GUI and tray libraries...")
        from ptt.ui.tray import TrayApp
        from ptt import config
        logging_setup.log_debug("Imports completed successfully.")
    except Exception as e:
        logging_setup.log_debug(f"CRITICAL: Failed to import dependencies: {str(e)}")
        logging_setup.log_debug(traceback.format_exc())
        sys.exit(1)

    logging_setup.log_debug(f"MODEL_SIZE: {transcribe.MODEL_SIZE}")
    logging_setup.log_debug(f"CONFIG_FILE: {paths.config_path()}")

    # 1. Detect if CUDA is available on this system
    cuda_supported = transcribe.cuda_available()
    logging_setup.log_debug(f"Initial check_cuda_availability: {cuda_supported}")

    # 2. Load settings
    # The engine applies the no-CUDA override; see its constructor.
    settings = config.load()

    # 3. Two-phase wiring: the tray needs the engine to drive it, and the engine
    #    needs the tray's callback to report to. The engine never imports the UI.
    tray = TrayApp(settings, cuda_supported)
    engine = engine_mod.Engine(settings, cuda_supported, on_state=tray.on_state)
    tray.attach(engine)

    # 4. pystray owns the main thread; it starts the engine on a daemon thread.
    tray.run()


if __name__ == "__main__":
    runtime.main_guard(main)
