"""
Push-to-talk chord definition, parsing and detection.

Detection polls `GetAsyncKeyState` rather than installing a keyboard hook.
Windows silently unregisters low-level hooks after UAC prompts, screen locks,
sleep, and USB HID hotplug, which stopped the hotkey responding entirely
(FR-C2, retrospective issues #7 and #8). The `keyboard` library is retained
only as an exception-path fallback.

Because detection does not suppress the keypress, the chord must consist of
keys that do nothing on their own (FR-C3). See `docs/design.md` section 6.

This module imports nothing from `ptt.config`: the chord is always passed in as
an argument, never read from a global. That is what lets the engine re-read the
setting on every poll iteration so the chord can be changed without a restart,
and what keeps this module testable without a config file.
"""

import ctypes

import keyboard

#: Virtual-key codes for every key that may take part in the chord. Left/right
#: variants are listed separately so a single side can be bound; the unsided
#: names ("ctrl", "alt", ...) match either side.
VK_MAP = {
    "ctrl":  0x11, "lctrl":  0xA2, "rctrl":  0xA3,
    "shift": 0x10, "lshift": 0xA0, "rshift": 0xA1,
    "alt":   0x12, "lalt":   0xA4, "ralt":   0xA5,
    "win":   0x5B, "lwin":   0x5B, "rwin":   0x5C,
    "space": 0x20,
}

#: Human-readable labels for the tray menu and console banner.
KEY_LABELS = {
    "ctrl": "Ctrl", "lctrl": "Left Ctrl", "rctrl": "Right Ctrl",
    "shift": "Shift", "lshift": "Left Shift", "rshift": "Right Shift",
    "alt": "Alt", "lalt": "Left Alt", "ralt": "Right Alt",
    "win": "Win", "lwin": "Left Win", "rwin": "Right Win",
    "space": "Space",
}

#: Right Ctrl alone: a lone modifier, so no character, no scroll and no menu
#: activation (FR-C3). Also sidesteps the Alt+Shift input-language switch that
#: made the previous default hazardous (issues #9, #11).
DEFAULT_HOTKEY = ("rctrl",)


def parse_chord(value):
    """
    Validate a configured chord.

    Returns ``(chord, None)`` on success or ``(None, reason)`` on failure.

    Deliberately pure: it never logs and never raises, so it can be unit-tested
    without a filesystem, and so `ptt.config` remains the only module that
    writes the OBS-3 line explaining a fallback.
    """
    if not isinstance(value, (list, tuple)):
        return None, "not a list"
    if not value:
        return None, "empty"

    chord = tuple(str(k).strip().lower() for k in value)
    unknown = [k for k in chord if k not in VK_MAP]
    if unknown:
        return None, f"unknown key names: {unknown}"
    return chord, None


def chord_label(chord):
    """Render a chord for display, e.g. ``('rctrl',)`` -> ``Right Ctrl``."""
    return " + ".join(KEY_LABELS.get(k, k.title()) for k in chord)


def chord_held(chord):
    """True while every key in `chord` is reported down (FR-C2)."""
    try:
        return all(
            (ctypes.windll.user32.GetAsyncKeyState(VK_MAP[k]) & 0x8000) != 0
            for k in chord
        )
    except Exception:
        # The keyboard library has no side-aware names: strip the l/r prefix.
        return all(keyboard.is_pressed(k.lstrip("lr")) for k in chord)
