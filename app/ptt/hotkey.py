"""
Push-to-talk chord definition, parsing, detection and safety classification.

Detection polls `GetAsyncKeyState` rather than installing a keyboard hook.
Windows silently unregisters low-level hooks after UAC prompts, screen locks,
sleep, and USB HID hotplug, which stopped the hotkey responding entirely
(FR-C2, retrospective issues #7 and #8). The `keyboard` library is retained
only as an exception-path fallback.

The settings window's key picker polls the same way, through `poll_vks`, and
for the same reason -- so the picker and the detector share one code path and
one failure mode. Qt key events would have been the obvious alternative and are
the wrong tool: they arrive only for the focused window, so a key released after
focus moved elsewhere would stay shaded forever.

Because detection does not suppress the keypress, the chord must consist of
keys that do nothing on their own (FR-C3). See `docs/design.md` section 6.

This module imports nothing from `ptt.config`: the chord is always passed in as
an argument, never read from a global. That is what lets the engine re-read the
setting on every poll iteration so the chord can be changed without a restart,
and what keeps this module testable without a config file.
"""

import ctypes
from typing import NamedTuple

import keyboard


class Key(NamedTuple):
    """
    One name the chord vocabulary accepts.

    `vks` is every virtual-key that satisfies the name, not one. That plural is
    the whole point of the field: `ctrl`, `shift` and `alt` have real unsided
    virtual keys that the OS reports for either side, but **Windows has no
    unsided Win key** -- `0x5B` is `VK_LWIN`. Before this table existed,
    `"win"` mapped to `0x5B` alone, so a chord of `["win"]` claimed to match
    either side and silently detected the left one only.

    `family` groups the three names that mean the same physical key, and is ""
    for a key that has no sides. `bindable` marks the physical keys the picker
    may offer; the unsided aliases are valid in config.json but are not caps on
    a keyboard, so they are never drawn as bindable.
    """
    name: str
    vks: tuple
    label: str
    bindable: bool
    family: str = ""
    prints: bool = False


#: The chord vocabulary, declared once. `VK_MAP`, `KEY_LABELS`, `BINDABLE_KEYS`
#: and the rest are derived from it below, so adding a key here is the only edit
#: needed to make it configurable, detectable and offerable in the picker.
#:
#: The order is also the canonical chord order (see `canonical`), which is why
#: it stays grouped Ctrl, Shift, Alt, Win, Space rather than sorted.
KEYS = (
    Key("ctrl",   (0x11,),      "Ctrl",        False, "ctrl"),
    Key("lctrl",  (0xA2,),      "Left Ctrl",   True,  "ctrl"),
    Key("rctrl",  (0xA3,),      "Right Ctrl",  True,  "ctrl"),
    Key("shift",  (0x10,),      "Shift",       False, "shift"),
    Key("lshift", (0xA0,),      "Left Shift",  True,  "shift"),
    Key("rshift", (0xA1,),      "Right Shift", True,  "shift"),
    Key("alt",    (0x12,),      "Alt",         False, "alt"),
    Key("lalt",   (0xA4,),      "Left Alt",    True,  "alt"),
    Key("ralt",   (0xA5,),      "Right Alt",   True,  "alt"),
    Key("win",    (0x5B, 0x5C), "Win",         False, "win"),
    Key("lwin",   (0x5B,),      "Left Win",    True,  "win"),
    Key("rwin",   (0x5C,),      "Right Win",   True,  "win"),
    Key("space",  (0x20,),      "Space",       True,  "",    True),
)

_BY_NAME = {k.name: k for k in KEYS}
_ORDER = {k.name: i for i, k in enumerate(KEYS)}

#: Virtual-key codes for every key that may take part in the chord, kept as the
#: public name -> primary-VK view because `README.md` and `design.md` describe
#: the vocabulary in these terms. Detection reads `Key.vks`, not this: for
#: `"win"` the two differ, and that difference is the issue described on `Key`.
VK_MAP = {k.name: k.vks[0] for k in KEYS}

#: Human-readable labels for the tray menu, the console banner and the picker.
KEY_LABELS = {k.name: k.label for k in KEYS}

#: The keys the settings window may bind: physical, side-specific caps plus
#: Space. The picker derives what it may offer from here rather than keeping a
#: list of its own, so a key added to `KEYS` becomes bindable in the UI with no
#: edit to the panel.
BINDABLE_KEYS = tuple(k.name for k in KEYS if k.bindable)

#: Physical virtual-key -> the bindable chord name for it. The picker draws a
#: keyboard from virtual-key codes -- it needs them anyway, to shade keys the OS
#: reports down -- so this answers "may this cap be bound?" from the table above
#: rather than from nine names transcribed into the UI by hand.
BINDABLE_BY_VK = {k.vks[0]: k.name for k in KEYS if k.bindable}

#: Chord name -> its family, for the names that have sides.
FAMILY = {k.name: k.family for k in KEYS if k.family}

#: Family -> the unsided name that matches either physical side.
EITHER_SIDE = {k.family: k.name for k in KEYS if k.family and not k.bindable}

#: Family -> its side-specific names, in `KEYS` order.
SIDES = {
    family: tuple(k.name for k in KEYS if k.family == family and k.bindable)
    for family in EITHER_SIDE
}

#: Right Ctrl alone: a lone modifier, so no character, no scroll and no menu
#: activation (FR-C3). Also sidesteps the Alt+Shift input-language switch that
#: made the previous default hazardous (issues #9, #11).
DEFAULT_HOTKEY = ("rctrl",)

#: What the picker shows when `classify` returns nothing, and what it puts in
#: front of each warning when it does. Both live here beside the rules so the
#: whole classifier -- including the case where it has nothing to say -- is one
#: testable table rather than strings scattered through a panel.
#:
#: The prefix is not baked into `classify`'s return values: a warning is a fact
#: about the chord, and the word "Warning" is a presentation choice a console
#: frontend might make differently.
SAFE_NOTE = "Safe: types no character, scrolls nothing, activates no menu bar."
WARNING_PREFIX = "Warning: "


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


def canonical(chord):
    """
    Put a chord in `KEYS` order and drop duplicates.

    `chord_held` is order-independent but `chord_label` and `config.json` are
    not, so the picker rebuilds every chord through here. Without it the same
    three keys chosen in a different order would rewrite the file and relabel
    the tray menu for no change in behaviour.

    Applied only to chords the picker builds. A chord loaded from config.json is
    never reordered on the user's behalf, because rewriting a setting nobody
    touched is exactly the churn this avoids.
    """
    return tuple(sorted(set(chord), key=lambda name: _ORDER.get(name, len(KEYS))))


def chord_label(chord):
    """Render a chord for display, e.g. ``('rctrl',)`` -> ``Right Ctrl``."""
    return " + ".join(KEY_LABELS.get(k, k.title()) for k in chord)


def classify(chord):
    """
    Warnings for a candidate chord, worst first (design.md section 6).

    Pure -- no Win32, no logging, no Qt -- so every row of the table is testable
    without a keyboard, and so the picker renders what this returns rather than
    reimplementing the rules beside the widgets.

    Returns a list, empty when there is nothing to say; the picker shows
    `SAFE_NOTE` then. Warnings never block a save: the user may know better than
    this function, and the point is that they choose knowing.

    An empty chord returns nothing. It is rejected by `parse_chord`, not warned
    about, and the picker will not produce one.
    """
    warnings = []
    families = {FAMILY.get(k, k) for k in chord}

    if any(_BY_NAME[k].prints for k in chord if k in _BY_NAME):
        warnings.append(
            "Space types a character into whatever has focus while you hold it, "
            "and scrolls a browser or a PDF viewer (issue #9)."
        )

    if "alt" in families:
        warnings.append(
            "Alt chords activate the focused window's menu bar on release, which "
            "steals focus and can discard the paste. The app disarms this "
            "automatically, but a lone modifier is safer (issue #11)."
        )

    # Not in design.md's table, and it should be: `inject.suppress_alt_menu`
    # neutralises the Alt case and has no equivalent for Win, so a Win chord
    # really does open the Start menu on every release. Without this the picker
    # would call Left Win "Safe: ... activates no menu bar", which is false.
    if "win" in families:
        warnings.append(
            "Win opens the Start menu when it is released on its own, taking "
            "focus off whatever you were dictating into."
        )

    # Windows' layout switch is specifically Alt+Shift and Ctrl+Shift. Warning
    # on any shift combination -- Win+Shift, Ctrl+Alt+Shift -- would cry wolf.
    if families in ({"alt", "shift"}, {"ctrl", "shift"}):
        warnings.append(
            "Ctrl+Shift and Alt+Shift are Windows' keyboard-layout switches when "
            "a second input language is installed."
        )

    if len(chord) == 1 and chord[0] in ("ctrl", "shift"):
        warnings.append(
            f"{KEY_LABELS[chord[0]]} on its own matches either side, so this "
            f"fires constantly during ordinary typing."
        )

    return warnings


def _key_state():
    """
    Resolve `GetAsyncKeyState` once per poll.

    Deliberately not resolved at module scope: `ctypes.windll` does not exist
    off Windows, and `design.md` section 8 wants this module importable in unit
    tests that have no Win32 at all.
    """
    fn = ctypes.windll.user32.GetAsyncKeyState
    fn.argtypes = [ctypes.c_int]
    fn.restype = ctypes.c_short
    return fn


def poll_vks(vks):
    """
    Which of `vks` the OS reports down right now, as a set.

    Used by the picker to shade the keyboard diagram live, including keys that
    can never be bound -- the board reads as real hardware, so the user can see
    the app is registering their keyboard at all.

    Only bit `0x8000` ("currently down") is tested. Bit `0x0001` is "pressed
    since the last call" and is cleared per caller, so reading it here would
    race the engine's own polling.

    Returns an empty set rather than raising if Win32 is unavailable, which
    leaves the board unshaded instead of taking the window down over cosmetics.
    """
    try:
        get_state = _key_state()
        return {vk for vk in vks if (get_state(vk) & 0x8000) != 0}
    except Exception:
        return set()


def chord_held(chord):
    """True while every key in `chord` is reported down (FR-C2)."""
    try:
        get_state = _key_state()
        return all(
            any((get_state(vk) & 0x8000) != 0 for vk in _BY_NAME[k].vks)
            for k in chord
        )
    except Exception:
        # The keyboard library has no side-aware names: strip the l/r prefix.
        return all(keyboard.is_pressed(k.lstrip("lr")) for k in chord)
