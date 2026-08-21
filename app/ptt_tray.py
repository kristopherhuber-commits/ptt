"""
ptt_tray.py — Push-to-talk local dictation for Windows 11 as a system tray app.

This version runs without a console window. It creates a system tray icon that:
- Reflects the application state (Teal = Ready, Red = Recording, Yellow = Transcribing, Blue = Loading).
- Allows toggling between CPU and GPU mode.
- Remembers preferences in a config.json.
- Checks if CUDA is available at startup.
- Logs initialization details to debug_log.txt.
"""

import os
import sys
import time
import threading
import socket
import traceback

# Determine application directory for saving config and finding local models
if getattr(sys, 'frozen', False):
    app_dir = os.path.dirname(sys.executable)
else:
    app_dir = os.path.dirname(os.path.abspath(__file__))

debug_log_path = os.path.join(app_dir, "debug_log.txt")

def log_debug(msg):
    """Log messages to debug_log.txt for easy inspection."""
    try:
        with open(debug_log_path, "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass

# Clear debug log at startup
try:
    if os.path.exists(debug_log_path):
        os.remove(debug_log_path)
except Exception:
    pass

log_debug("=== App Started ===")
log_debug(f"sys.frozen: {getattr(sys, 'frozen', False)}")
log_debug(f"sys.executable: {sys.executable}")
log_debug(f"app_dir: {app_dir}")

# Register the CUDA/cuDNN DLL directories before CTranslate2 is ever imported.
# ptt.transcribe imports only paths + logging_setup at module scope, so this is
# cheap and pulls in nothing heavy; see its docstring for why the order matters.
from ptt import paths, transcribe
transcribe.ensure_cuda_dll_dirs()

# Standard imports
try:
    log_debug("Importing system and audio libraries...")
    # Keeps its original slot: importing the engine pulls in ptt.audio, and so
    # sounddevice and its _terminate() (issue #6), at the same point the tray
    # used to import sounddevice directly.
    from ptt import engine as engine_mod
    log_debug("Importing GUI and tray libraries...")
    import pystray
    from pystray import MenuItem as item
    from PIL import Image, ImageDraw
    from ptt import config, hotkey as hotkey_mod
    log_debug("Imports completed successfully.")
except Exception as e:
    log_debug(f"CRITICAL: Failed to import dependencies: {str(e)}")
    log_debug(traceback.format_exc())
    sys.exit(1)

# ----------------------------- Configuration ---------------------------------
IS_DESKTOP   = socket.gethostname().lower() == "darklord"
MODEL_SIZE   = transcribe.MODEL_SIZE   # "large-v3-turbo"; see ptt/transcribe.py

log_debug(f"IS_DESKTOP: {IS_DESKTOP}")
log_debug(f"MODEL_SIZE: {MODEL_SIZE}")
log_debug(f"CONFIG_FILE: {paths.config_path()}")

# Dynamic global state variables
is_cuda_supported = False
app_status = "Initializing..."
icon = None

# Both are assigned once in main(), before the tray icon or the engine thread
# exist. Declared rather than set to None so a premature read raises NameError
# instead of a confusing AttributeError on NoneType.
settings: "config.Settings"
engine: "engine_mod.Engine"

# -------------------------- Icon Generation ----------------------------------

def create_icon_image(state):
    """Draw a status icon programmatically without needing external image assets."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    
    if state == "recording":
        d.ellipse((4, 4, 60, 60), fill=(239, 68, 68), outline=(185, 28, 28), width=4)
        d.ellipse((20, 20, 44, 44), fill=(255, 255, 255))
    elif state == "transcribing":
        d.ellipse((4, 4, 60, 60), fill=(245, 158, 11), outline=(217, 119, 6), width=4)
        d.rounded_rectangle((22, 22, 42, 42), radius=4, fill=(255, 255, 255))
    elif state == "loading":
        d.ellipse((4, 4, 60, 60), fill=(59, 130, 246), outline=(29, 78, 216), width=4)
        d.arc((16, 16, 48, 48), start=0, end=270, fill=(255, 255, 255), width=5)
    else:  # "idle"
        d.ellipse((4, 4, 60, 60), fill=(13, 148, 136), outline=(15, 118, 110), width=4)
        d.rounded_rectangle((26, 16, 38, 36), radius=6, fill=(255, 255, 255))
        d.arc((20, 22, 44, 38), start=0, end=180, fill=(255, 255, 255), width=3)
        d.line((32, 38, 32, 46), fill=(255, 255, 255), width=3)
        d.line((24, 46, 40, 46), fill=(255, 255, 255), width=3)
        
    return img

# -------------------------- Tray Menu Actions --------------------------------

def set_state(state, status_text=None):
    """Update icon image, tooltip, and status menu item dynamically."""
    global app_status, icon
    if status_text:
        app_status = status_text
    else:
        app_status = state.capitalize()
    
    if icon:
        icon.icon = create_icon_image(state)
        icon.title = f"PTT Dictation ({app_status})"
        icon.menu = create_menu()

def set_device_gpu(icon_obj, item_obj):
    if not settings.use_gpu:
        settings.use_gpu = True
        settings.save()
        engine.request_model_reload()

def set_device_cpu(icon_obj, item_obj):
    if settings.use_gpu:
        settings.use_gpu = False
        settings.save()
        engine.request_model_reload()

def on_exit(icon_obj, item_obj):
    log_debug("Exit requested by user.")
    engine.stop()
    # Do NOT join the engine thread here. It is a daemon and the process
    # exits via os._exit immediately after icon.run() returns; joining would
    # block for an in-flight transcription -- up to 30 s on CPU -- turning
    # "Exit" into "hang".
    icon_obj.stop()

def create_menu():
    """Build the dynamic tray icon menu."""
    status_label = f"Status: {app_status}"
    
    return pystray.Menu(
        item(status_label, lambda icon_obj, item_obj: None, enabled=False),
        item(f"Hotkey: {hotkey_mod.chord_label(settings.hotkey)}", lambda icon_obj, item_obj: None, enabled=False),
        pystray.Menu.SEPARATOR,
        item(
            "Use GPU (CUDA)",
            set_device_gpu,
            checked=lambda item_obj: settings.use_gpu,
            enabled=lambda item_obj: is_cuda_supported
        ),
        item(
            "Use CPU",
            set_device_cpu,
            checked=lambda item_obj: not settings.use_gpu
        ),
        pystray.Menu.SEPARATOR,
        item("Exit", on_exit)
    )

# -------------------------- Main App Loop ------------------------------------

# -------------------------- Application Entry ---------------------------------

def main():
    global icon, is_cuda_supported, settings, engine
    
    # 1. Detect if CUDA is available on this system
    is_cuda_supported = transcribe.cuda_available()
    log_debug(f"Initial check_cuda_availability: {is_cuda_supported}")
    
    # 2. Load settings
    settings = config.load()
    if not is_cuda_supported:
        log_debug("CUDA not supported on this hardware. Overriding config to use CPU.")
        settings.use_gpu = False
        
    # 3. Build the engine. It reports state through set_state and never
    #    imports the UI; see ptt/engine.py for the callback contract.
    engine = engine_mod.Engine(settings, is_cuda_supported, on_state=set_state)

    # 4. Create the tray icon
    icon = pystray.Icon(
        "ptt_dictate",
        create_icon_image("loading"),
        "PTT Dictation (Initializing...)",
        menu=create_menu()
    )
    
    # 5. Start the engine on a daemon thread; pystray owns the main thread
    def setup_tray(icon_obj):
        icon_obj.visible = True
        log_debug("System Tray icon made visible. Starting background thread...")
        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        
    log_debug("Starting tray icon event loop...")
    icon.run(setup_tray)

if __name__ == "__main__":
    try:
        main()
        log_debug("App main execution finished. Forcing process exit.")
        import os
        os._exit(0)
    except Exception as e:
        log_debug(f"Unhandled crash in __main__: {str(e)}")
        log_debug(traceback.format_exc())
        import os
        os._exit(1)
