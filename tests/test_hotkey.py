"""
The chord vocabulary, its parsing, and the safety classifier.

`hotkey.py` imports nothing from `ptt.config` and reaches Win32 only through
`_key_state()`, so everything here runs without a keyboard, a config file or an
event loop. That is not an accident of the tests -- the module's docstring says
it is what keeps the module testable.
"""

import pytest

from ptt import hotkey


# -- a stand-in for GetAsyncKeyState ---------------------------------------

def fake_keys(*down):
    """A `_key_state` replacement reporting exactly `down` as held.

    Returns the 0x8000 bit set, which is what the real call returns for a key
    that is currently down. Bit 0x0001 is deliberately never set: it means
    "pressed since the last call" and is cleared per caller, so anything reading
    it would race the engine's own polling.
    """
    held = set(down)

    def _state(vk):
        return -0x8000 if vk in held else 0

    return _state


# -- parse_chord ------------------------------------------------------------

def test_parse_chord_accepts_a_list():
    assert hotkey.parse_chord(["rctrl"]) == (("rctrl",), None)


def test_parse_chord_accepts_a_tuple():
    assert hotkey.parse_chord(("lctrl", "lshift")) == (("lctrl", "lshift"), None)


def test_parse_chord_normalises_case_and_whitespace():
    chord, reason = hotkey.parse_chord(["  RCtrl ", "LSHIFT"])
    assert chord == ("rctrl", "lshift")
    assert reason is None


def test_parse_chord_rejects_empty():
    assert hotkey.parse_chord([]) == (None, "empty")


@pytest.mark.parametrize("value", ["rctrl", {"a": 1}, None, 7])
def test_parse_chord_rejects_a_non_list(value):
    chord, reason = hotkey.parse_chord(value)
    assert chord is None
    assert reason == "not a list"


def test_parse_chord_names_the_unknown_keys_in_its_reason():
    chord, reason = hotkey.parse_chord(["rctrl", "banana"])
    assert chord is None
    assert "banana" in reason


def test_parse_chord_rejects_a_chord_that_is_only_partly_valid():
    assert hotkey.parse_chord(["rctrl", "f13"])[0] is None


def test_every_name_in_vk_map_parses():
    for name in hotkey.VK_MAP:
        assert hotkey.parse_chord([name]) == ((name,), None)


# -- the KEYS table and everything derived from it --------------------------

def test_vk_map_and_labels_derive_from_keys():
    assert set(hotkey.VK_MAP) == {k.name for k in hotkey.KEYS}
    assert set(hotkey.KEY_LABELS) == {k.name for k in hotkey.KEYS}
    for key in hotkey.KEYS:
        assert hotkey.VK_MAP[key.name] == key.vks[0]
        assert hotkey.KEY_LABELS[key.name] == key.label
        assert key.label, f"{key.name} has no label"


def test_bindable_keys_are_the_bindable_entries():
    assert hotkey.BINDABLE_KEYS == tuple(k.name for k in hotkey.KEYS if k.bindable)


def test_bindable_by_vk_has_no_colliding_virtual_keys():
    bindable = [k for k in hotkey.KEYS if k.bindable]
    assert len(hotkey.BINDABLE_BY_VK) == len(bindable)
    for key in bindable:
        assert hotkey.BINDABLE_BY_VK[key.vks[0]] == key.name


def test_unsided_aliases_are_never_bindable():
    """They are not physical keys, so the picker has no cap to draw for them."""
    for name in hotkey.EITHER_SIDE.values():
        assert name not in hotkey.BINDABLE_KEYS


def test_every_family_has_one_unsided_name_and_two_sided_ones():
    families = {k.family for k in hotkey.KEYS if k.family}
    assert families == set(hotkey.EITHER_SIDE) == set(hotkey.SIDES)
    for family in families:
        members = [k for k in hotkey.KEYS if k.family == family]
        unsided = [k for k in members if not k.bindable]
        sided = [k for k in members if k.bindable]
        assert len(unsided) == 1, family
        assert len(sided) == 2, family
        assert hotkey.EITHER_SIDE[family] == unsided[0].name
        assert hotkey.SIDES[family] == tuple(k.name for k in sided)


def test_space_has_no_family_and_is_the_only_printing_key():
    assert hotkey.FAMILY.get("space") is None
    assert [k.name for k in hotkey.KEYS if k.prints] == ["space"]


def test_default_hotkey_is_valid_and_safe():
    assert hotkey.parse_chord(list(hotkey.DEFAULT_HOTKEY))[0] == hotkey.DEFAULT_HOTKEY
    assert hotkey.classify(hotkey.DEFAULT_HOTKEY) == []


# -- issue #12: the unsided Win key -----------------------------------------

def test_win_carries_both_virtual_keys():
    """
    Regression for retrospective issue #12.

    `VK_MAP["win"]` is 0x5B, which is VK_LWIN -- Windows has no unsided Win
    virtual key. Before the `KEYS` table each name carried one code, so a chord
    of `["win"]` claimed to match either side and silently detected the left one.
    """
    by_name = {k.name: k for k in hotkey.KEYS}
    assert by_name["win"].vks == (0x5B, 0x5C)
    assert by_name["lwin"].vks == (0x5B,)
    assert by_name["rwin"].vks == (0x5C,)


@pytest.mark.parametrize("side", [0x5B, 0x5C])
def test_win_matches_either_side(monkeypatch, side):
    monkeypatch.setattr(hotkey, "_key_state", lambda: fake_keys(side))
    assert hotkey.chord_held(("win",)) is True


def test_sided_win_does_not_match_the_other_side(monkeypatch):
    monkeypatch.setattr(hotkey, "_key_state", lambda: fake_keys(0x5C))
    assert hotkey.chord_held(("lwin",)) is False
    assert hotkey.chord_held(("rwin",)) is True


@pytest.mark.parametrize("family", ["ctrl", "shift", "alt"])
def test_the_other_unsided_names_have_a_real_unsided_virtual_key(monkeypatch, family):
    """Unlike Win, these three are reported by the OS for either side."""
    by_name = {k.name: k for k in hotkey.KEYS}
    assert len(by_name[family].vks) == 1
    monkeypatch.setattr(hotkey, "_key_state", lambda: fake_keys(by_name[family].vks[0]))
    assert hotkey.chord_held((family,)) is True


# -- chord_held and poll_vks -------------------------------------------------

def test_chord_held_needs_every_key_down(monkeypatch):
    monkeypatch.setattr(hotkey, "_key_state", lambda: fake_keys(0xA2))
    assert hotkey.chord_held(("lctrl",)) is True
    assert hotkey.chord_held(("lctrl", "lshift")) is False


def test_chord_held_is_order_independent(monkeypatch):
    monkeypatch.setattr(hotkey, "_key_state", lambda: fake_keys(0xA2, 0xA0))
    assert hotkey.chord_held(("lctrl", "lshift")) is True
    assert hotkey.chord_held(("lshift", "lctrl")) is True


def test_chord_held_falls_back_to_the_keyboard_library(monkeypatch):
    """
    The fallback exists because `ctypes.windll` is not always reachable.

    It has no side-aware names, so it strips the l/r prefix -- which is why the
    stub below is asked about "ctrl" rather than "lctrl".
    """
    def boom():
        raise OSError("no user32 here")

    asked = []
    monkeypatch.setattr(hotkey, "_key_state", boom)
    monkeypatch.setattr(
        hotkey, "keyboard",
        type("stub", (), {"is_pressed": staticmethod(lambda k: asked.append(k) or True)})
    )
    assert hotkey.chord_held(("lctrl",)) is True
    assert asked == ["ctrl"]


def test_poll_vks_returns_only_the_keys_that_are_down(monkeypatch):
    monkeypatch.setattr(hotkey, "_key_state", lambda: fake_keys(0xA0, 0x20))
    assert hotkey.poll_vks([0xA0, 0xA1, 0x20, 0x41]) == {0xA0, 0x20}


def test_poll_vks_is_empty_rather_than_raising_without_win32(monkeypatch):
    """An unshaded board beats taking the settings window down over cosmetics."""
    def boom():
        raise OSError("no user32 here")

    monkeypatch.setattr(hotkey, "_key_state", boom)
    assert hotkey.poll_vks([0xA0, 0x20]) == set()


# -- canonical ---------------------------------------------------------------

def test_canonical_uses_the_keys_table_order():
    assert hotkey.canonical(("space", "rshift", "lctrl")) == ("lctrl", "rshift", "space")


def test_canonical_removes_duplicates():
    assert hotkey.canonical(("rctrl", "rctrl")) == ("rctrl",)


def test_canonical_is_idempotent():
    once = hotkey.canonical(("space", "lalt", "rctrl"))
    assert hotkey.canonical(once) == once


def test_canonical_does_not_raise_on_an_unknown_name():
    """It is only ever called with validated chords, but must not explode."""
    assert hotkey.canonical(("banana", "rctrl")) == ("rctrl", "banana")


# -- chord_label -------------------------------------------------------------

def test_chord_label_renders_one_key():
    assert hotkey.chord_label(("rctrl",)) == "Right Ctrl"


def test_chord_label_joins_a_chord():
    assert hotkey.chord_label(("lalt", "lshift")) == "Left Alt + Left Shift"


def test_chord_label_falls_back_for_an_unknown_name():
    assert hotkey.chord_label(("banana",)) == "Banana"


# -- classify: docs/design.md section 6, row by row --------------------------

def test_a_lone_sided_modifier_is_safe():
    assert hotkey.classify(("rctrl",)) == []
    assert hotkey.classify(("lctrl",)) == []
    assert hotkey.classify(("rshift",)) == []


def test_space_warns_that_it_types_a_character():
    warnings = hotkey.classify(("space",))
    assert len(warnings) == 1
    assert "Space types a character" in warnings[0]


def test_any_alt_warns_about_the_menu_bar():
    for chord in (("lalt",), ("ralt",), ("alt",), ("rctrl", "lalt")):
        assert any("menu bar" in w for w in hotkey.classify(chord)), chord


def test_any_win_warns_about_the_start_menu():
    """`inject.suppress_alt_menu` neutralises Alt and has no Win equivalent."""
    for chord in (("lwin",), ("rwin",), ("win",)):
        assert any("Start menu" in w for w in hotkey.classify(chord)), chord


@pytest.mark.parametrize("chord", [
    ("lalt", "lshift"), ("ralt", "rshift"), ("lctrl", "lshift"), ("rctrl", "rshift"),
])
def test_alt_shift_and_ctrl_shift_warn_about_the_layout_switch(chord):
    assert any("keyboard-layout" in w for w in hotkey.classify(chord)), chord


@pytest.mark.parametrize("chord", [
    ("lwin", "lshift"),                    # Win+Shift is not a layout switch
    ("lctrl", "lalt", "lshift"),           # nor is Ctrl+Alt+Shift
    ("lshift", "rshift"),                  # nor is Shift+Shift
])
def test_other_shift_combinations_do_not_warn_about_the_layout_switch(chord):
    """
    The narrowing that stopped the box crying wolf.

    gui_handoff section 6.1 broadened this rule to "a multi-key combination
    including a shift", which fires on chords Windows does nothing special with
    and trains the user to ignore the panel. design.md section 6 is specific and
    is the authority.
    """
    assert not any("keyboard-layout" in w for w in hotkey.classify(chord)), chord


def test_none_of_those_chords_is_left_with_no_warning_at_all():
    """Narrowing the layout rule must not have silenced a genuinely bad chord."""
    assert hotkey.classify(("lwin", "lshift"))           # warns about Win
    assert hotkey.classify(("lctrl", "lalt", "lshift"))  # warns about Alt


@pytest.mark.parametrize("name", ["ctrl", "shift"])
def test_a_lone_unsided_common_modifier_warns_about_ordinary_typing(name):
    """
    The row gui_handoff dropped, and the only one guarding the configuration
    the "match either side" checkbox exists to create.
    """
    warnings = hotkey.classify((name,))
    assert any("ordinary typing" in w for w in warnings)


def test_an_unsided_modifier_in_a_chord_does_not_warn_about_ordinary_typing():
    """Ctrl on its own fires constantly; Ctrl held with something else does not."""
    warnings = hotkey.classify(("ctrl", "rshift"))
    assert not any("ordinary typing" in w for w in warnings)


def test_a_chord_can_collect_several_warnings():
    warnings = hotkey.classify(("lalt", "lshift"))
    assert len(warnings) == 2
    assert any("menu bar" in w for w in warnings)
    assert any("keyboard-layout" in w for w in warnings)


def test_an_empty_chord_is_rejected_rather_than_warned():
    assert hotkey.classify(()) == []


def test_every_warning_is_a_non_empty_string():
    for chord in (("space",), ("lalt",), ("lwin",), ("ctrl",), ("lalt", "lshift")):
        for warning in hotkey.classify(chord):
            assert isinstance(warning, str) and warning.strip()


def test_the_panel_facing_strings_exist():
    assert hotkey.SAFE_NOTE.strip()
    assert hotkey.WARNING_PREFIX.strip()
