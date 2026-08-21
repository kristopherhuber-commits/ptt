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
from dataclasses import dataclass, field

from ptt import hotkey as hotkey_mod
from ptt import paths
from ptt.logging_setup import log_debug

#: Bumped only when a migration is needed. Files with no `version` key predate
#: versioning and are treated as v1; the key is written on the next save.
CONFIG_VERSION = 1

#: Keys this build owns. Anything else found in the file is preserved verbatim
#: so a newer build's settings survive a rollback.
_KNOWN_KEYS = ("version", "use_gpu", "hotkey")


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
    """
    use_gpu: bool = True
    hotkey: tuple = hotkey_mod.DEFAULT_HOTKEY
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
        }

    def save(self):
        """Write config.json. Never raises -- a read-only disk must not take the
        application down mid-dictation."""
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, indent=2)
            log_debug(f"Saved config.json: use_gpu={self.use_gpu}, hotkey={self.hotkey}")
        except Exception as e:
            log_debug(f"Failed to save config.json: {str(e)}")


def load(path=None):
    """
    Read config.json, falling back to defaults field by field. Never raises.

    Every fallback is logged with the reason that caused it (OBS-3).
    """
    if path is None:
        path = paths.config_path()

    if not os.path.exists(path):
        s = Settings(path=path)
        log_debug(f"config.json not found, using defaults (use_gpu={s.use_gpu}, hotkey={s.hotkey})")
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

    s.extra = {k: v for k, v in raw.items() if k not in _KNOWN_KEYS}

    log_debug(
        f"Loaded config.json: use_gpu={s.use_gpu}, hotkey={s.hotkey}, "
        f"version={s.version}, unknown_keys={sorted(s.extra)}"
    )
    return s
