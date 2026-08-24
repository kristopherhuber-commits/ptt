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
from ptt import vocabulary as vocabulary_mod
from ptt.logging_setup import log_debug

#: Bumped only when a migration is needed. Files with no `version` key predate
#: versioning and are treated as v1; the key is written on the next save.
CONFIG_VERSION = 1

#: Keys this build owns. Anything else found in the file is preserved verbatim
#: so a newer build's settings survive a rollback.
_KNOWN_KEYS = (
    "version", "use_gpu", "hotkey", "model", "benchmarks",
    "audio_device", "keep_stream_warm", "ignore_short_holds", "start_click",
    "vocabulary",
)

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
    rebind rule exists to prevent. `vocabulary` is a tuple of
    `vocabulary.Rule`, which is a NamedTuple, so the same discipline is
    enforced by the type rather than only by this docstring -- editing a rule
    builds a new tuple and rebinds it, and the engine's transcription path
    reads whichever tuple is current.

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

    #: PortAudio input-device index, or None for "follow the Windows default
    #: device". None is the default and is what every configuration written
    #: before this build says by omission, so existing installations keep the
    #: behaviour they have today.
    audio_device: int | None = None

    #: Hold the input stream open between recordings (NFR-2, NFR-4). True is
    #: the shipped behaviour: the stream stays open while the user is at the
    #: machine and is released after `engine.IDLE_THRESHOLD_SEC` of inactivity.
    #: False closes it as soon as each recording ends, which costs the hardware
    #: wake-up latency and the headset chime issue #6 exists to avoid.
    keep_stream_warm: bool = True

    #: Discard a hold shorter than `engine.MIN_RECORD_SEC` as an accidental tap
    #: (FR-3). True is the shipped behaviour.
    ignore_short_holds: bool = True

    #: Play a short system sound when recording starts. Off by default: it goes
    #: to the Windows output device, so an open desktop microphone can hear it.
    start_click: bool = False

    #: Replacement rules, applied to the transcript before it is pasted. A
    #: tuple of `vocabulary.Rule`; see this class's docstring for why it is a
    #: tuple and not a list.
    vocabulary: tuple = ()

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
            "audio_device": (
                None if self.audio_device is None else int(self.audio_device)
            ),
            "keep_stream_warm": bool(self.keep_stream_warm),
            "ignore_short_holds": bool(self.ignore_short_holds),
            "start_click": bool(self.start_click),
            "vocabulary": vocabulary_mod.to_json(self.vocabulary),
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
                f"model={self.model}, audio_device={self.audio_device}, "
                f"vocabulary={len(self.vocabulary)}"
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


def _load_vocabulary(raw_value, path_note="config.json"):
    """
    Validate the replacement rules, dropping the ones that make no sense.

    Per entry, like `_load_benchmarks` and for the same reason: one malformed
    rule someone hand-edited must not throw away the twenty beside it. The
    validation itself lives in `vocabulary.parse_rule`, which is pure and never
    logs, so this is the only place a rejected rule is explained (OBS-3).
    """
    if not isinstance(raw_value, list):
        log_debug(f"{path_note} vocabulary is not a list ({raw_value!r}); ignoring it.")
        return ()

    kept = []
    for index, entry in enumerate(raw_value):
        rule, reason = vocabulary_mod.parse_rule(entry)
        if rule is None:
            log_debug(f"{path_note} vocabulary[{index}] is invalid ({reason}); dropping it.")
            continue
        kept.append(rule)
    return tuple(kept)


def _load_bool(raw, key, default, path_note="config.json"):
    """
    One boolean setting, validated by type rather than by truthiness.

    A bare truthiness test accepts the string "false" as True, which is how a
    hand-edited config.json silently turns a safety default off. Shared by all
    four booleans so the message shape -- and therefore the OBS-3 evidence a
    user sees -- is identical for every one of them.
    """
    value = raw.get(key, default)
    if isinstance(value, bool):
        return value
    log_debug(f"{path_note} {key} is not a boolean ({value!r}); using default {default}.")
    return default


def _load_audio_device(raw_value, path_note="config.json"):
    """
    Validate the input-device index. `None` means "follow the Windows default".

    Only the type is checked here. Whether the index still names a live input
    device is a question about the machine rather than about the file, and it
    is answered at stream-open time by `audio.Recorder._resolve_device` -- a
    device that is unplugged while the app is closed must not cause the saved
    choice to be forgotten, and a device that is missing right now may be back
    before the next recording.

    `bool` is excluded explicitly: `True` is an `int` in Python and would
    otherwise be accepted as device 1.
    """
    if raw_value is None:
        return None
    if isinstance(raw_value, bool) or not isinstance(raw_value, int):
        log_debug(
            f"{path_note} audio_device is not an integer ({raw_value!r}); "
            f"using the system default."
        )
        return None
    if raw_value < 0:
        log_debug(
            f"{path_note} audio_device is negative ({raw_value!r}); "
            f"using the system default."
        )
        return None
    return raw_value


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

    # The booleans. A bare truthiness test would accept the string "false" as
    # True -- forcing GPU on a machine that cannot do it, or switching FR-3's
    # minimum hold off -- so every one of them is checked by type.
    s.use_gpu = _load_bool(raw, "use_gpu", s.use_gpu)
    s.keep_stream_warm = _load_bool(raw, "keep_stream_warm", s.keep_stream_warm)
    s.ignore_short_holds = _load_bool(raw, "ignore_short_holds", s.ignore_short_holds)
    s.start_click = _load_bool(raw, "start_click", s.start_click)

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

    if "audio_device" in raw:
        s.audio_device = _load_audio_device(raw["audio_device"])

    if "vocabulary" in raw:
        s.vocabulary = _load_vocabulary(raw["vocabulary"])

    s.extra = {k: v for k, v in raw.items() if k not in _KNOWN_KEYS}

    log_debug(
        f"Loaded config.json: use_gpu={s.use_gpu}, hotkey={s.hotkey}, "
        f"model={s.model}, benchmarks={len(s.benchmarks)}, "
        f"audio_device={s.audio_device}, keep_stream_warm={s.keep_stream_warm}, "
        f"ignore_short_holds={s.ignore_short_holds}, start_click={s.start_click}, "
        f"vocabulary={len(s.vocabulary)}, "
        f"version={s.version}, unknown_keys={sorted(s.extra)}"
    )
    return s
