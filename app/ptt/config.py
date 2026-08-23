"""
Settings persistence: config.json, next to the application (FR-8).

This is the only module that reads or writes the file (design.md section 7).
Nothing else opens it, and nothing else decides what a missing or malformed
value means.

Every field is validated and every fallback is logged with its reason (OBS-3).
A configuration that silently reverts to a default is indistinguishable from one
that was never applied, which is the class of failure the observability
requirements exist to close.
"""

import json
import os
import threading
from dataclasses import dataclass, field

from ptt import hotkey as hotkey_mod
from ptt import paths
from ptt import transcribe
from ptt.logging_setup import log_debug

#: Bumped only when a migration is needed. Files with no `version` key predate
#: versioning and are treated as v1; the key is written on the next save.
CONFIG_VERSION = 1

#: Keys this build owns. Anything else found in the file is preserved verbatim
#: so a newer build's settings survive a rollback.
_KNOWN_KEYS = ("version", "use_gpu", "hotkey", "model", "benchmarks")

#: Serialises writers of config.json. See `Settings.save`.
_save_lock = threading.Lock()


@dataclass
class Settings:
    """
    The live settings object. Exactly one exists per process.

    **Deliberately not frozen, and every field is an immutable value.**

    The engine holds this instance and re-reads `hotkey` on every poll
    iteration, so the chord can be changed while it runs. That is safe only
    because writes are whole-value rebinds -- `settings.hotkey = ("rshift",)`,
    never `settings.hotkey.append(...)`. An attribute rebind is a single
    bytecode, so a reader on another thread sees either the old tuple or the new
    one, never a half-built one. No lock is needed and none should be added.

    Making this frozen, which is the natural instinct, breaks the live re-read
    outright.

    `benchmarks` is a dict rather than a tuple, and the same rule covers it: a
    new measurement builds a **new** dict and rebinds the attribute. Mutating
    the existing one in place would reintroduce exactly the half-built read the
    rebind rule exists to prevent.

    The lock inside `save` is a different thing entirely and is not the lock
    this docstring forbids: it guards the **file**, which two threads can now
    reach -- every control in the settings window applies instantly, and
    `Engine._persist_cpu_fallback` writes from the engine thread. It never
    covers a field read or write, so the live re-read stays lock-free.
    """
    use_gpu: bool = True
    hotkey: tuple = hotkey_mod.DEFAULT_HOTKEY
    model: str = transcribe.DEFAULT_MODEL
    benchmarks: dict = field(default_factory=dict)
    version: int = CONFIG_VERSION
    extra: dict = field(default_factory=dict, repr=False)
    path: str = field(default_factory=paths.config_path, repr=False)

    def to_dict(self):
        """Serialise, preserving unknown keys. Known keys are written last, so
        they win if a rolled-back build left a colliding value behind."""
        return {
            **self.extra,
            "version": CONFIG_VERSION,
            "use_gpu": bool(self.use_gpu),
            "hotkey": list(self.hotkey),
            "model": str(self.model),
            "benchmarks": dict(self.benchmarks),
        }

    def save(self):
        """
        Write config.json. Never raises -- a read-only disk must not take the
        application down mid-dictation.

        Written to a temporary file and moved into place under a lock, rather
        than opened `"w"` and dumped into. Both halves of that matter now that
        every control in the settings window saves the moment it is touched:

        - `"w"` truncates *first*, so a process that died between truncate and
          dump left a zero-byte config.json. `load` handles the garbage
          correctly -- it logs and falls back -- which means the user's symptom
          is their settings silently resetting, the exact failure OBS-3 exists
          to make impossible. `os.replace` is atomic on NTFS: the file is either
          entirely the old contents or entirely the new ones.
        - Two threads can reach this now. The GUI thread writes on every click;
          the engine thread writes on a CUDA fallback. Interleaved `json.dump`
          calls into one handle produce a file that is neither version.
        """
        tmp = self.path + ".tmp"
        try:
            with _save_lock:
                try:
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(self.to_dict(), f, indent=2)
                    os.replace(tmp, self.path)
                except Exception:
                    # A half-written temp file is no use to anyone and would sit
                    # next to config.json looking like a real one.
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                    raise
            log_debug(
                f"Saved config.json: use_gpu={self.use_gpu}, hotkey={self.hotkey}, "
                f"model={self.model}"
            )
        except Exception as e:
            log_debug(f"Failed to save config.json: {str(e)}")


def _load_benchmarks(raw_value, path_note="config.json"):
    """
    Validate the measured-latency cache, dropping entries that make no sense.

    Kept out of `load` only because it is the one field with per-entry
    validation; the rule is the same as every other field's -- check the type,
    log what was rejected and why, never raise.

    Shape: ``{"<model>|<device>": {"seconds": float, "at": str, "clip": str}}``.
    `clip` is a digest of the benchmark WAV, so re-recording the clip
    invalidates the numbers taken against the old one instead of silently
    comparing measurements of two different recordings.
    """
    if not isinstance(raw_value, dict):
        log_debug(f"{path_note} benchmarks is not an object ({raw_value!r}); ignoring it.")
        return {}

    kept = {}
    for key, entry in raw_value.items():
        if not isinstance(entry, dict):
            log_debug(f"{path_note} benchmarks[{key!r}] is not an object; dropping it.")
            continue
        seconds = entry.get("seconds")
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
            log_debug(
                f"{path_note} benchmarks[{key!r}] has no positive numeric "
                f"'seconds' ({seconds!r}); dropping it."
            )
            continue
        kept[str(key)] = {
            "seconds": float(seconds),
            "at": str(entry.get("at", "")),
            "clip": str(entry.get("clip", "")),
        }
    return kept


def load(path=None):
    """
    Read config.json, falling back to defaults field by field. Never raises.

    Every fallback is logged with the reason that caused it (OBS-3).
    """
    if path is None:
        path = paths.config_path()

    if not os.path.exists(path):
        s = Settings(path=path)
        log_debug(
            f"config.json not found, using defaults (use_gpu={s.use_gpu}, "
            f"hotkey={s.hotkey}, model={s.model})"
        )
        return s

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        log_debug(f"Failed to read config.json: {str(e)}; using defaults.")
        return Settings(path=path)

    if not isinstance(raw, dict):
        log_debug(f"config.json is a {type(raw).__name__}, not an object; using defaults.")
        return Settings(path=path)

    s = Settings(path=path)

    # version: absent means this file predates versioning, i.e. v1.
    version = raw.get("version", CONFIG_VERSION)
    if not isinstance(version, int):
        log_debug(f"config.json version is not an integer ({version!r}); treating as {CONFIG_VERSION}.")
        version = CONFIG_VERSION
    s.version = version

    # use_gpu: a bare truthiness test would accept the string "false" as True
    # and silently force GPU on a machine that cannot do it.
    use_gpu = raw.get("use_gpu", s.use_gpu)
    if isinstance(use_gpu, bool):
        s.use_gpu = use_gpu
    else:
        log_debug(f"config.json use_gpu is not a boolean ({use_gpu!r}); using default {s.use_gpu}.")

    # hotkey
    if "hotkey" in raw:
        chord, reason = hotkey_mod.parse_chord(raw["hotkey"])
        if chord is None:
            log_debug(f"config.json hotkey invalid ({reason}); using default {s.hotkey}.")
        else:
            s.hotkey = chord

    # model: validated against the tiers this build knows how to present, so an
    # unrecognised name loads the default rather than being handed to
    # faster-whisper, which would try to fetch it from Hugging Face by name.
    if "model" in raw:
        model = raw["model"]
        if not isinstance(model, str):
            log_debug(f"config.json model is not a string ({model!r}); using default {s.model}.")
        elif model not in transcribe.MODEL_NAMES:
            log_debug(
                f"config.json model {model!r} is not one of "
                f"{list(transcribe.MODEL_NAMES)}; using default {s.model}."
            )
        else:
            s.model = model

    if "benchmarks" in raw:
        s.benchmarks = _load_benchmarks(raw["benchmarks"])

    s.extra = {k: v for k, v in raw.items() if k not in _KNOWN_KEYS}

    log_debug(
        f"Loaded config.json: use_gpu={s.use_gpu}, hotkey={s.hotkey}, "
        f"model={s.model}, benchmarks={len(s.benchmarks)}, "
        f"version={s.version}, unknown_keys={sorted(s.extra)}"
    )
    return s
