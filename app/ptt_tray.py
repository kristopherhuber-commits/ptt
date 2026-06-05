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
import json
import traceback
import re

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

# --- Make pip-installed CUDA/cuDNN DLLs discoverable before importing CT2 -----
def _add_nvidia_dll_dirs():
    if not sys.platform.startswith("win"):
        log_debug("Not on Windows, skipping DLL dir additions.")
        return
        
    base_paths = []
    
    # 1. Check if running under PyInstaller and check the bundle root sys._MEIPASS
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        pyi_nvidia_path = os.path.join(sys._MEIPASS, "nvidia")
        log_debug(f"PyInstaller detected. Adding PyInstaller bundle search path: {pyi_nvidia_path}")
        base_paths.append(pyi_nvidia_path)
        
    # 2. Try standard import as fallback
    try:
        import nvidia
        paths = getattr(nvidia, "__path__", None)
        if paths:
            log_debug(f"nvidia package found via import. Paths: {paths}")
            base_paths.extend(paths)
        else:
            file_path = getattr(nvidia, "__file__", None)
            if file_path:
                log_debug(f"nvidia package __file__ found: {file_path}")
                base_paths.append(os.path.dirname(file_path))
    except ImportError as e:
        log_debug(f"nvidia package import failed/skipped: {str(e)}")
        
    # Deduplicate paths
    unique_base_paths = []
    for p in base_paths:
        if p not in unique_base_paths:
            unique_base_paths.append(p)
            
    # 3. Add DLL directories to Search Path
    log_debug(f"Resolving CUDA DLLs for paths: {unique_base_paths}")
    for base in unique_base_paths:
        for sub in ("cudnn", "cublas", "cuda_nvrtc"):
            d = os.path.join(base, sub, "bin")
            log_debug(f"Checking directory: {d}")
            if os.path.isdir(d):
                try:
                    os.add_dll_directory(d)
                    os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]
                    log_debug(f"Added DLL directory: {d}")
                except Exception as ex:
                    log_debug(f"Error adding DLL directory {d}: {str(ex)}")
            else:
                log_debug(f"Directory does not exist: {d}")

_add_nvidia_dll_dirs()

# Standard imports
try:
    log_debug("Importing system and audio libraries...")
    import numpy as np
    import sounddevice as sd
    import keyboard
    log_debug("Importing ctranslate2/faster-whisper...")
    from faster_whisper import WhisperModel
    log_debug("Importing GUI and tray libraries...")
    import pystray
    from pystray import MenuItem as item
    from PIL import Image, ImageDraw
    log_debug("Imports completed successfully.")
except Exception as e:
    log_debug(f"CRITICAL: Failed to import dependencies: {str(e)}")
    log_debug(traceback.format_exc())
    sys.exit(1)

# ----------------------------- Configuration ---------------------------------
IS_DESKTOP   = socket.gethostname().lower() == "darklord"
MODEL_SIZE   = "large-v3-turbo"
SAMPLE_RATE  = 16_000
LANGUAGE     = "en"
HOTKEY_MODS  = ("ctrl", "space")
POLL_SEC     = 0.02
CONFIG_FILE  = os.path.join(app_dir, "config.json")

log_debug(f"IS_DESKTOP: {IS_DESKTOP}")
log_debug(f"MODEL_SIZE: {MODEL_SIZE}")
log_debug(f"CONFIG_FILE: {CONFIG_FILE}")

# Dynamic global state variables
model = None
use_gpu = True
is_cuda_supported = False
app_status = "Initializing..."
running = True
reload_model_event = threading.Event()
icon = None

# -------------------------- Helper Functions ---------------------------------

def check_cuda_availability():
    """Verify if CTranslate2 can see an NVIDIA GPU."""
    try:
        import ctranslate2
        count = ctranslate2.get_cuda_device_count()
        log_debug(f"ctranslate2 detected CUDA devices count: {count}")
        return count > 0
    except Exception as e:
        log_debug(f"ctranslate2 CUDA check raised exception: {str(e)}")
        log_debug(traceback.format_exc())
        return False

def load_config():
    """Load CPU/GPU preference from config.json."""
    global use_gpu
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)
                use_gpu = cfg.get("use_gpu", True)
                log_debug(f"Loaded config.json: use_gpu={use_gpu}")
        except Exception as e:
            log_debug(f"Failed to read config.json: {str(e)}")
            use_gpu = True
    else:
        log_debug("config.json not found, using default (use_gpu=True)")
        use_gpu = True

def save_config():
    """Save CPU/GPU preference to config.json."""
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump({"use_gpu": use_gpu}, f)
            log_debug(f"Saved config.json: use_gpu={use_gpu}")
    except Exception as e:
        log_debug(f"Failed to save config.json: {str(e)}")

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
    global use_gpu
    if not use_gpu:
        use_gpu = True
        save_config()
        reload_model_event.set()

def set_device_cpu(icon_obj, item_obj):
    global use_gpu
    if use_gpu:
        use_gpu = False
        save_config()
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
        pystray.Menu.SEPARATOR,
        item(
            "Use GPU (CUDA)",
            set_device_gpu,
            checked=lambda item_obj: use_gpu,
            enabled=lambda item_obj: is_cuda_supported
        ),
        item(
            "Use CPU",
            set_device_cpu,
            checked=lambda item_obj: not use_gpu
        ),
        pystray.Menu.SEPARATOR,
        item("Exit", on_exit)
    )

# --------------------------- Recorder & Hotkey -------------------------------

class Recorder:
    """Captures mono float32 audio from microphone into an in-memory buffer."""
    def __init__(self, samplerate):
        self.samplerate = samplerate
        self._frames = []
        self._stream = None

    def _callback(self, indata, frames, time_info, status):
        if status:
            log_debug(f"Audio stream status warning: {status}")
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
    """Insert text at the cursor using simulated keyboard typing."""
    if not text:
        return
    time.sleep(0.05)  # Let keys release fully
    keyboard.write(text)


def chord_held():
    """Check if all keys in the hotkey chord are pressed."""
    return all(keyboard.is_pressed(k) for k in HOTKEY_MODS)

# -------------------------- Main App Loop ------------------------------------

def transcription_loop(icon_obj):
    """Runs in a background thread to manage model lifecycle and key monitoring."""
    global model, use_gpu, is_cuda_supported, running
    
    # Run initial config load & model build
    reload_model_event.set()
    rec = Recorder(SAMPLE_RATE)
    recording = False
    current_device = "cpu"
    
    while running:
        # Check if we need to reload the transcription model
        if reload_model_event.is_set():
            reload_model_event.clear()
            set_state("loading", "Loading Model...")
            
            # Deallocate old model
            model = None
            
            target_device = "cuda" if (use_gpu and is_cuda_supported) else "cpu"
            target_compute_type = "float16" if target_device == "cuda" else "int8"
            
            try:
                # Check for locally packaged model folder first
                local_model_path = os.path.join(app_dir, "models", MODEL_SIZE)
                if os.path.isdir(local_model_path):
                    model_path = local_model_path
                    log_debug(f"Using local bundled model directory: {model_path}")
                else:
                    model_path = MODEL_SIZE
                    log_debug(f"Using on-demand model name: {model_path}")
                
                log_debug(f"Attempting to load model '{model_path}' on '{target_device.upper()}' ({target_compute_type})...")
                model = WhisperModel(model_path, device=target_device, compute_type=target_compute_type)
                current_device = target_device
                log_debug(f"Model successfully loaded on '{target_device.upper()}'.")
                set_state("idle", f"Ready ({target_device.upper()})")
            except Exception as e:
                log_debug(f"ERROR: Failed to load model on '{target_device.upper()}': {str(e)}")
                log_debug(traceback.format_exc())
                
                # If CUDA failed to load, automatically fall back to CPU
                if target_device == "cuda":
                    log_debug("Initiating auto CPU fallback...")
                    use_gpu = False
                    save_config()
                    try:
                        log_debug(f"Attempting to load fallback model '{MODEL_SIZE}' on CPU (int8)...")
                        model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
                        current_device = "cpu"
                        log_debug("Fallback model loaded successfully on CPU.")
                        set_state("idle", "Ready (CPU Fallback)")
                    except Exception as e2:
                        log_debug(f"ERROR: Fallback CPU model load failed: {str(e2)}")
                        log_debug(traceback.format_exc())
                        set_state("idle", "Error loading model")
                else:
                    set_state("idle", "Error loading model")
        
        # Monitor the hotkey for recording/transcribing
        if model is not None:
            try:
                held = chord_held()
                if held and not recording:
                    recording = True
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
                    segments, _ = model.transcribe(
                        audio,
                        language=LANGUAGE,
                        beam_size=5,
                        vad_filter=True,
                        condition_on_previous_text=False
                    )
                    text = "".join(s.text for s in segments).strip()
                    text = re.sub(r'\.{2,}', '', text).strip()
                    t1 = time.time()
                    log_debug(f"Transcription finished in {t1-t0:.2f}s. Result: '{text}'")
                    
                    if text:
                        paste_text(text)
                    set_state("idle", f"Ready ({current_device.upper()})")
            except Exception as e:
                log_debug(f"ERROR inside main processing loop: {str(e)}")
                log_debug(traceback.format_exc())
                set_state("idle", f"Error: {str(e)}")
                
        time.sleep(POLL_SEC)
        
    # Cleanup on exit
    if recording:
        rec.stop()
    log_debug("Transcription background loop finished.")

# -------------------------- Application Entry ---------------------------------

def main():
    global icon, is_cuda_supported, use_gpu
    
    # 1. Detect if CUDA is available on this system
    is_cuda_supported = check_cuda_availability()
    log_debug(f"Initial check_cuda_availability: {is_cuda_supported}")
    
    # 2. Load settings
    load_config()
    if not is_cuda_supported:
        log_debug("CUDA not supported on this hardware. Overriding config to use CPU.")
        use_gpu = False
        
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
    except Exception as e:
        log_debug(f"Unhandled crash in __main__: {str(e)}")
        log_debug(traceback.format_exc())
