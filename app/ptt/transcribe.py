"""
Speech-to-text: CUDA DLL resolution, model loading, and inference.

**This module must not import faster_whisper or ctranslate2 at module scope.**

CTranslate2 resolves its CUDA and cuDNN DLLs when it is first imported. If that
happens before `ensure_cuda_dll_dirs` has registered the directories the
nvidia-*-cu12 wheels install into, the GPU is simply not found -- no exception,
no warning, just silent CPU inference at roughly ten times the latency
(retrospective issue #1). Both imports therefore live *inside* functions that
call `ensure_cuda_dll_dirs` first, so the ordering is enforced by control flow
and there is no import statement at column 0 for a linter or a well-meaning
refactor to hoist.

`ptt/__init__.py` is empty for the same reason: `import ptt` must not reach
this module.
"""

import os
import re
import sys
import traceback

from ptt import paths
from ptt.logging_setup import log_debug

MODEL_SIZE = "large-v3-turbo"
LANGUAGE = "en"          # None for autodetect
BEAM_SIZE = 5

#: float16 is required on Blackwell (sm_120); int8 crashes there with
#: CUBLAS_STATUS_NOT_SUPPORTED (CON-4).
CUDA_COMPUTE_TYPE = "float16"
CPU_COMPUTE_TYPE = "int8"

_dll_dirs_added = False


def ensure_cuda_dll_dirs():
    """
    Make the pip-installed CUDA/cuDNN DLLs discoverable. Idempotent.

    Must run before CTranslate2 is first imported; see the module docstring.
    """
    global _dll_dirs_added
    if _dll_dirs_added:
        return
    _dll_dirs_added = True

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
        nv_paths = getattr(nvidia, "__path__", None)
        if nv_paths:
            log_debug(f"nvidia package found via import. Paths: {nv_paths}")
            base_paths.extend(nv_paths)
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


def cuda_available():
    """Verify if CTranslate2 can see an NVIDIA GPU."""
    ensure_cuda_dll_dirs()
    try:
        import ctranslate2          # deliberately not at module scope
        count = ctranslate2.get_cuda_device_count()
        log_debug(f"ctranslate2 detected CUDA devices count: {count}")
        return count > 0
    except Exception as e:
        log_debug(f"ctranslate2 CUDA check raised exception: {str(e)}")
        log_debug(traceback.format_exc())
        return False


def _whisper_model_cls():
    """Import WhisperModel, guaranteeing the DLL directories are registered first."""
    ensure_cuda_dll_dirs()
    from faster_whisper import WhisperModel     # deliberately not at module scope
    return WhisperModel


def resolve_model_path():
    """Prefer a model bundled next to the app; otherwise let faster-whisper fetch it."""
    local_model_path = paths.local_model_dir(MODEL_SIZE)
    if os.path.isdir(local_model_path):
        log_debug(f"Using local bundled model directory: {local_model_path}")
        return local_model_path
    log_debug(f"Using on-demand model name: {MODEL_SIZE}")
    return MODEL_SIZE


def load_model_with_fallback(use_gpu, cuda_supported, on_fallback=None):
    """
    Load the model, falling back to CPU if CUDA fails (FR-6).

    Returns ``(model | None, device, status_text)``, where `status_text` is the
    string the tray tooltip shows.

    `on_fallback` is invoked once, after the CUDA attempt has failed and before
    the CPU attempt, so the caller can persist `use_gpu=False`. It is a callback
    rather than a direct write so this module never imports `ptt.config` --
    config.py stays the only module that touches config.json (design.md #7).
    """
    WhisperModel = _whisper_model_cls()

    target_device = "cuda" if (use_gpu and cuda_supported) else "cpu"
    target_compute_type = CUDA_COMPUTE_TYPE if target_device == "cuda" else CPU_COMPUTE_TYPE

    try:
        model_path = resolve_model_path()
        log_debug(f"Attempting to load model '{model_path}' on '{target_device.upper()}' ({target_compute_type})...")
        model = WhisperModel(model_path, device=target_device, compute_type=target_compute_type)
        log_debug(f"Model successfully loaded on '{target_device.upper()}'.")
        return model, target_device, f"Ready ({target_device.upper()})"
    except Exception as e:
        log_debug(f"ERROR: Failed to load model on '{target_device.upper()}': {str(e)}")
        log_debug(traceback.format_exc())

        if target_device != "cuda":
            return None, target_device, "Error loading model"

        # If CUDA failed to load, automatically fall back to CPU
        log_debug("Initiating auto CPU fallback...")
        if on_fallback is not None:
            on_fallback()
        try:
            log_debug(f"Attempting to load fallback model '{MODEL_SIZE}' on CPU ({CPU_COMPUTE_TYPE})...")
            model = WhisperModel(MODEL_SIZE, device="cpu", compute_type=CPU_COMPUTE_TYPE)
            log_debug("Fallback model loaded successfully on CPU.")
            return model, "cpu", "Ready (CPU Fallback)"
        except Exception as e2:
            log_debug(f"ERROR: Fallback CPU model load failed: {str(e2)}")
            log_debug(traceback.format_exc())
            return None, "cpu", "Error loading model"


def clean_text(text):
    """
    Strip the artefacts the model produces on silence (NFR-5, issue #4).

    large-v3 hallucinates runs of full stops on trailing silence; saying
    "testing one two three" could type "Testing .......". Pure, so it is
    unit-testable without a model.
    """
    return re.sub(r'\.{2,}', '', text.strip()).strip()


def transcribe_audio(model, audio):
    """Run inference over a float32 mono buffer and return cleaned text."""
    segments, _ = model.transcribe(
        audio,
        language=LANGUAGE,
        beam_size=BEAM_SIZE,
        vad_filter=True,
        condition_on_previous_text=False
    )
    return clean_text("".join(s.text for s in segments))
