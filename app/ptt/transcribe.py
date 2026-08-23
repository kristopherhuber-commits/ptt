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
import wave
from typing import NamedTuple

import numpy as np

from ptt import paths
from ptt.logging_setup import log_debug


class ModelInfo(NamedTuple):
    """One Whisper size tier, as the Model panel presents it."""
    name: str
    params: str
    disk: str
    character: str


#: The size tiers this build offers, smallest first. `config.py` validates the
#: `model` setting against these names, and the Model panel renders these rows,
#: so the two cannot drift apart.
#:
#: `disk` is an estimate, not a measurement: a CTranslate2 float16 conversion is
#: about two bytes per weight, which is where each figure comes from. The panel
#: replaces it with the real directory size for any model actually on disk, and
#: marks the estimate with a leading `~` so the two are never confused.
#:
#: `character` is a qualitative phrase, deliberately. There is no accuracy
#: column: word error rate cannot be measured without a labelled corpus, and
#: quoting a published figure for a different dataset in a settings window would
#: be presenting someone else's benchmark as this machine's (gui_handoff 6.2).
MODELS = (
    ModelInfo("tiny.en",        "39M params",   "~75 MB",  "fastest, least accurate"),
    ModelInfo("base.en",        "74M params",   "~145 MB", "fast, loose on names"),
    ModelInfo("small.en",       "244M params",  "~484 MB", "even trade"),
    ModelInfo("medium.en",      "769M params",  "~1.5 GB", "accurate, slower"),
    ModelInfo("large-v3",       "1550M params", "~3.1 GB", "most accurate, slowest"),
    ModelInfo("large-v3-turbo", "809M params",  "~1.6 GB", "near-large accuracy, half the time"),
)

#: The names `config.py` accepts for the `model` setting.
MODEL_NAMES = tuple(m.name for m in MODELS)

#: What ships, and what an unrecognised `model` in config.json falls back to.
DEFAULT_MODEL = "large-v3-turbo"

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


def resolve_model_path(model_size):
    """Prefer a model bundled next to the app; otherwise let faster-whisper fetch it."""
    local_model_path = paths.local_model_dir(model_size)
    if os.path.isdir(local_model_path):
        log_debug(f"Using local bundled model directory: {local_model_path}")
        return local_model_path
    log_debug(f"Using on-demand model name: {model_size}")
    return model_size


def load_model_with_fallback(model_size, use_gpu, cuda_supported, on_fallback=None):
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

    # Resolved once, outside the try, so the CPU fallback below reuses whatever
    # the CUDA attempt used. It previously re-read the module constant instead,
    # which meant a bundled local model directory was used for the CUDA attempt
    # and then re-downloaded by name for the fallback.
    model_path = resolve_model_path(model_size)

    try:
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
            log_debug(f"Attempting to load fallback model '{model_path}' on CPU ({CPU_COMPUTE_TYPE})...")
            model = WhisperModel(model_path, device="cpu", compute_type=CPU_COMPUTE_TYPE)
            log_debug("Fallback model loaded successfully on CPU.")
            return model, "cpu", "Ready (CPU Fallback)"
        except Exception as e2:
            log_debug(f"ERROR: Fallback CPU model load failed: {str(e2)}")
            log_debug(traceback.format_exc())
            return None, "cpu", "Error loading model"


def _directory_size(path):
    """Total bytes under `path`, or 0 if it is not there."""
    if not os.path.isdir(path):
        return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def installed_sizes():
    """
    Bytes each known model occupies on this machine, keyed by name.

    Two places count, and reporting only the first was the tempting mistake.
    `resolve_model_path` prefers a model bundled next to the app in `models/`;
    everything else faster-whisper has fetched lives in the Hugging Face cache.
    A panel that looked only at `paths.local_model_dir` would tell the user
    "Not on disk" about the 1.6 GB model the app is running at that moment.

    The cache is **scanned**, not addressed by a constructed repository name.
    The repo a size tier resolves to is not derivable from the tier -- this
    machine's `large-v3-turbo` comes from
    `mobiuslabsgmbh/faster-whisper-large-v3-turbo` while the smaller tiers come
    from `Systran/` -- so the repo id is matched by suffix, which needs no
    guess about who publishes what. `large-v3` does not match
    `faster-whisper-large-v3-turbo`, which is the one collision worth checking.

    Never raises: a missing or unreadable cache logs and reports nothing found,
    which shows as "Not on disk" rather than taking the panel down.
    """
    sizes = {}
    for info in MODELS:
        bundled = _directory_size(paths.local_model_dir(info.name))
        if bundled:
            sizes[info.name] = bundled

    try:
        from huggingface_hub import scan_cache_dir
        for repo in scan_cache_dir().repos:
            if repo.repo_type != "model":
                continue
            leaf = repo.repo_id.rsplit("/", 1)[-1]
            for info in MODELS:
                if leaf.endswith(info.name):
                    sizes.setdefault(info.name, repo.size_on_disk)
    except Exception as e:
        log_debug(f"Could not scan the Hugging Face cache: {e}")

    return sizes


_clip_id = None


def benchmark_clip_id():
    """
    A short digest of the bundled sample clip, stored beside each measurement.

    `record_sample.py` says the clip must never be re-recorded, because a new
    recording invalidates every cached measurement -- but nothing enforced that,
    so a re-record would have left old numbers on screen looking comparable to
    new ones. Storing this with each result makes the cache self-invalidating:
    measurements taken against a different clip simply stop matching and are not
    displayed.

    Returns "" if the clip cannot be read, which reads as "unknown" and shows
    nothing rather than taking the panel down.
    """
    global _clip_id
    if _clip_id is None:
        import hashlib
        try:
            with open(paths.asset_path("benchmark_sample.wav"), "rb") as f:
                _clip_id = hashlib.sha256(f.read()).hexdigest()[:12]
        except Exception as e:
            log_debug(f"Could not digest the benchmark clip: {e}")
            _clip_id = ""
    return _clip_id


def load_benchmark_clip():
    """
    Read the bundled sample clip as the float32 buffer inference wants.

    `benchmark_sample.wav` is 16-bit PCM, because that is what a WAV file is;
    `Recorder.stop()` hands the engine float32 in [-1, 1). Something has to
    divide by 32768 and this is it, so the measured path is byte-for-byte the
    dictation path from `transcribe_audio` inwards.

    Raises if the clip is missing or is not 16 kHz mono 16-bit -- a benchmark
    that silently measured the wrong audio would be worse than no benchmark.
    """
    path = paths.asset_path("benchmark_sample.wav")
    with wave.open(path, "rb") as f:
        channels, width, rate, frames = (
            f.getnchannels(), f.getsampwidth(), f.getframerate(), f.getnframes()
        )
        if (channels, width, rate) != (1, 2, 16_000):
            raise ValueError(
                f"{path} is {channels}ch/{width * 8}-bit/{rate} Hz; "
                f"the benchmark clip must be mono 16-bit 16 kHz"
            )
        raw = f.readframes(frames)

    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    log_debug(f"Loaded benchmark clip: {path} ({frames / rate:.1f}s)")
    return audio


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
