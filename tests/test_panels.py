"""
The settings panels' pure parts: the keyboard board tables and two formatters.

Only module-level data and free functions are touched. Defining a QWidget
subclass needs no QApplication -- instantiating one does -- so these import the
panels without an event loop.

The board is worth pinning because it is data, not code: a mistyped virtual key
produces a cap that never shades and a chord that cannot be bound, and neither
raises anything.
"""

from ptt import hotkey
from ptt.ui.panels import hotkey as hotkey_panel
from ptt.ui.panels import model as model_panel


def board_vks():
    """Every virtual key drawn on the board, keypad included, with duplicates."""
    main = [cap.vk for row in hotkey_panel.ROWS for cap in row if cap.vk]
    return main + [pad.vk for pad in hotkey_panel.NUMPAD]


# -- the board ---------------------------------------------------------------

def test_the_board_is_a_full_104_key_keyboard():
    assert len(board_vks()) == 104


def test_only_the_two_enter_keys_share_a_virtual_key():
    """
    Windows reports VK_RETURN for both and separates them with an extended-key
    flag GetAsyncKeyState does not carry, so they shade together. Any other
    duplicate is a typo.
    """
    vks = board_vks()
    duplicated = {vk for vk in vks if vks.count(vk) > 1}
    assert duplicated == {0x0D}
    assert vks.count(0x0D) == 2


def test_every_bindable_key_appears_exactly_once_on_the_board():
    """
    A bindable key with no cap could never be chosen, and one with two caps
    would light in two places at once.
    """
    vks = board_vks()
    for vk, name in hotkey.BINDABLE_BY_VK.items():
        assert vks.count(vk) == 1, f"{name} appears {vks.count(vk)} times"


def test_the_board_carries_no_virtual_key_of_zero():
    """Zero marks a spacer, which is a plain widget rather than a keycap."""
    assert 0 not in board_vks()


def test_every_cap_has_a_label():
    for row in hotkey_panel.ROWS:
        for cap in row:
            if cap.vk:
                assert cap.label, hex(cap.vk)
    for pad in hotkey_panel.NUMPAD:
        assert pad.label, hex(pad.vk)


def test_nine_caps_are_bindable_and_the_rest_are_not():
    bindable = [vk for vk in board_vks() if vk in hotkey.BINDABLE_BY_VK]
    assert len(bindable) == len(hotkey.BINDABLE_KEYS) == 9


# -- geometry ----------------------------------------------------------------

def test_cap_width_spans_the_caps_and_the_gaps_between_them():
    """An n-unit cap covers n caps and the n-1 gaps: 32n - 4."""
    assert hotkey_panel.cap_width(1) == 28
    assert hotkey_panel.cap_width(2) == 60
    assert hotkey_panel.cap_width(0.5) == 12
    for units in (1, 1.5, 1.75, 2, 2.25, 2.5, 4, 4.5):
        assert hotkey_panel.cap_width(units) == round(32 * units) - 4


def test_a_prefix_of_a_row_always_ends_on_the_same_pixel_boundary():
    """
    What keeps the nav cluster above the arrows and the keypad rows level: the
    width of a run depends only on its total units, never on how it is split.
    """
    for units in (4, 6.5, 15.5):
        halves = hotkey_panel.cap_width(units / 2) * 2 + hotkey_panel.GAP_PX
        assert halves == hotkey_panel.cap_width(units)


def test_the_keypad_grid_has_no_overlapping_cells():
    occupied = set()
    for pad in hotkey_panel.NUMPAD:
        for row in range(pad.row, pad.row + pad.rowspan):
            for col in range(pad.col, pad.col + pad.colspan):
                assert (row, col) not in occupied, f"{pad.label} overlaps at {row},{col}"
                occupied.add((row, col))


def test_the_keypad_is_four_columns_wide():
    assert max(p.col + p.colspan for p in hotkey_panel.NUMPAD) == 4


def test_the_chord_cap_matches_the_engines_limit():
    assert hotkey_panel.MAX_CHORD_KEYS == 3


def test_the_preferred_side_is_the_right_hand_one():
    """
    Clearing "match either side" has to choose a side. Ordinary typing reaches
    for the left-hand modifiers, so the right is the safer expansion -- and is
    the same reason the shipped default is Right Ctrl.
    """
    for family, sides in hotkey.SIDES.items():
        assert sides[hotkey_panel.PREFERRED_SIDE].startswith("r"), family


# -- the model panel's formatters -------------------------------------------

def test_benchmark_key_names_the_model_and_the_device():
    """
    A CPU figure and a CUDA figure for one model are different numbers about
    different hardware; showing one where the other belongs would misreport.
    """
    assert model_panel.benchmark_key("tiny.en", "cuda") == "tiny.en|cuda"
    assert model_panel.benchmark_key("tiny.en", "cpu") != \
           model_panel.benchmark_key("tiny.en", "cuda")


def test_format_bytes_uses_megabytes_below_a_gigabyte():
    assert model_panel._format_bytes(75 * 1024 * 1024) == "75 MB"


def test_format_bytes_switches_to_gigabytes_at_the_boundary():
    assert model_panel._format_bytes(1024 * 1024 * 1024) == "1.0 GB"
    assert model_panel._format_bytes(1023 * 1024 * 1024) == "1023 MB"


def test_a_measured_size_is_not_marked_as_an_estimate():
    """The `~` prefix belongs to the catalogue's estimate and nothing else."""
    assert not model_panel._format_bytes(1024 * 1024).startswith("~")
