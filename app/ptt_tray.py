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
    from ptt import audio as audio_mod   # imports sounddevice; see ptt/audio.py
    log_debug("Importing GUI and tray libraries...")
    import pystray
    from pystray import MenuItem as item
    from PIL import Image, ImageDraw
    from ptt import config, hotkey as hotkey_mod, inject
    log_debug("Imports completed successfully.")
except Exception as e:
    log_debug(f"CRITICAL: Failed to import dependencies: {str(e)}")
    log_debug(traceback.format_exc())
    sys.exit(1)

# ----------------------------- Configuration ---------------------------------
IS_DESKTOP   = socket.gethostname().lower() == "darklord"
MODEL_SIZE   = transcribe.MODEL_SIZE   # "large-v3-turbo"; see ptt/transcribe.py
SAMPLE_RATE  = audio_mod.SAMPLE_RATE   # 16_000; see ptt/audio.py
POLL_SEC     = 0.02

log_debug(f"IS_DESKTOP: {IS_DESKTOP}")
log_debug(f"MODEL_SIZE: {MODEL_SIZE}")
log_debug(f"CONFIG_FILE: {paths.config_path()}")

# Dynamic global state variables
model = None
# Assigned once in main(), before the tray icon or the engine thread exist.
# Declared rather than set to None so a premature read is a NameError instead
# of a confusing AttributeError on NoneType.
settings: "config.Settings"
is_cuda_supported = False
app_status = "Initializing..."
running = True
reload_model_event = threading.Event()
icon = None

# -------------------------- Helper Functions ---------------------------------

def _persist_cpu_fallback():
    """Remember that CUDA failed, so the next start does not retry it (FR-6)."""
    settings.use_gpu = False
    settings.save()

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
        reload_model_event.set()

def set_device_cpu(icon_obj, item_obj):
    if settings.use_gpu:
        settings.use_gpu = False
        settings.save()
        reload_model_event.set()

def on_exit(icon_obj, item_obj):
    global running
    log_debug("Exit requested by user.")
    running = False
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

def transcription_loop(icon_obj):
    """Runs in a background thread to manage model lifecycle and key monitoring."""
    global model, is_cuda_supported, running
    
    # Run initial config load & model build
    reload_model_event.set()
    rec = audio_mod.Recorder(SAMPLE_RATE)
    recording = False
    current_device = "cpu"
    stream_open = False
    IDLE_THRESHOLD_SEC = 240.0
    
    while running:
        # Check if we need to reload the transcription model
        if reload_model_event.is_set():
            reload_model_event.clear()
            set_state("loading", "Loading Model...")
            
            # Deallocate the old model before loading its replacement
            model = None

            model, current_device, status_text = transcribe.load_model_with_fallback(
                settings.use_gpu, is_cuda_supported, on_fallback=_persist_cpu_fallback
            )
            set_state("idle", status_text)

        # Check user idle duration
        idle = audio_mod.get_idle_duration()
        if idle < IDLE_THRESHOLD_SEC:
            if not stream_open:
                rec.open_stream()
                stream_open = True
        else:
            if stream_open and not recording:
                rec.close_stream()
                stream_open = False
        
        # Monitor the hotkey for recording/transcribing
        if model is not None:
            try:
                held = hotkey_mod.chord_held(settings.hotkey)
                if held and not recording:
                    recording = True
                    # Break up the Alt press now, while it is still held: once the
                    # user releases it the menu has already taken focus.
                    inject.suppress_alt_menu()
                    rec.start()
                    set_state("recording", "Recording...")
                    log_debug("Recording started...")
                elif not held and recording:
                    recording = False
                    audio = rec.stop()
                    set_state("transcribing", "Transcribing...")
                    log_debug(f"Recording stopped. Audio samples: {audio.size}")
                    
                    if audio.size < SAMPLE_RATE * 0.3:  # skip accidental clicks
                        log_debug("Recording too short, skipping transcription.")
                        set_state("idle", f"Ready ({current_device.upper()})")
                        continue
                    
                    log_debug("Starting transcription...")
                    t0 = time.time()
                    text = transcribe.transcribe_audio(model, audio)
                    t1 = time.time()
                    log_debug(f"Transcription finished in {t1-t0:.2f}s. Result: '{text}'")
                    
                    if text:
                        if not inject.target_accepts_keys():
                            log_debug("WARNING: focused window has no caret; paste may be discarded.")
                        inject.paste_text(text)
                        log_debug(f"Pasted {len(text)} chars into '{inject.foreground_window_class()}'.")
                    set_state("idle", f"Ready ({current_device.upper()})")
            except Exception as e:
                log_debug(f"ERROR inside main processing loop: {str(e)}")
                log_debug(traceback.format_exc())
                set_state("idle", f"Error: {str(e)}")
                
        time.sleep(POLL_SEC)
        
    # Cleanup on exit
    if recording:
        rec.stop()
    rec.close_stream()
    log_debug("Transcription background loop finished.")

# -------------------------- Application Entry ---------------------------------

def main():
    global icon, is_cuda_supported, settings
    
    # 1. Detect if CUDA is available on this system
    is_cuda_supported = transcribe.cuda_available()
    log_debug(f"Initial check_cuda_availability: {is_cuda_supported}")
    
    # 2. Load settings
    settings = config.load()
    if not is_cuda_supported:
        log_debug("CUDA not supported on this hardware. Overriding config to use CPU.")
        settings.use_gpu = False
        
    # 3. Create the tray icon
    icon = pystray.Icon(
        "ptt_dictate",
        create_icon_image("loading"),
        "PTT Dictation (Initializing...)",
        menu=create_menu()
    )
    
    # 4. Start backend loop
    def setup_tray(icon_obj):
        icon_obj.visible = True
        log_debug("System Tray icon made visible. Starting background thread...")
        t = threading.Thread(target=transcription_loop, args=(icon_obj,), daemon=True)
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
