"""
The settings panels' pure parts: the keyboard board tables and two formatters.

Only module-level data and free functions are touched. Defining a QWidget
subclass needs no QApplication -- instantiating one does -- so these import the
panels without an event loop.

The board is worth pinning because it is data, not code: a mistyped virtual key
produces a cap that never shades and a chord that cannot be bound, and neither
raises anything.
"""

import inspect

from ptt import config, engine as engine_mod, hotkey, inject, paths, transcribe
from ptt.ui import qt_marks as marks
from ptt.ui.panels import advanced as advanced_panel
from ptt.ui.panels import audio as audio_panel
from ptt.ui.panels import diagnostics as diagnostics_panel
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


# -- the audio panel's level meter -------------------------------------------

def test_silence_reads_as_the_floor_rather_than_minus_infinity():
    """`20*log10(0)` is not a number a readout can print."""
    assert audio_panel.to_dbfs(0.0) == audio_panel.METER_FLOOR_DB
    assert audio_panel.to_dbfs(-1.0) == audio_panel.METER_FLOOR_DB


def test_full_scale_is_zero_dbfs():
    """Digital full scale is 0 dB, so every real reading is negative."""
    assert audio_panel.to_dbfs(1.0) == 0.0
    assert audio_panel.to_dbfs(0.5) < 0.0


def test_a_quiet_signal_is_floored_rather_than_reported_precisely():
    """Below the floor the microphone is reporting its own noise."""
    assert audio_panel.to_dbfs(0.000001) == audio_panel.METER_FLOOR_DB


def test_the_meter_is_dark_only_in_silence():
    """
    Anything audible lights at least one bar. A meter that showed nothing for a
    quiet-but-working microphone reads as a broken microphone.
    """
    assert audio_panel.meter_fill(0.0) == 0
    assert audio_panel.meter_fill(0.001) >= 1


def test_the_meter_is_full_at_full_scale():
    assert audio_panel.meter_fill(1.0) == audio_panel.METER_BARS


def test_ordinary_speech_lands_in_the_middle_of_the_meter():
    """
    Why the scale is dB and not linear amplitude: speech peaks around 0.05-0.2,
    which on a linear bar is a twitch at the left-hand end.
    """
    bars = audio_panel.METER_BARS
    for peak in (0.05, 0.1, 0.2):
        assert bars * 0.25 <= audio_panel.meter_fill(peak) <= bars * 0.85, peak


def test_the_meter_never_overflows_its_bars():
    for peak in (0.0, 0.5, 1.0, 2.0):
        assert 0 <= audio_panel.meter_fill(peak) <= audio_panel.METER_BARS


def test_the_default_device_entry_is_not_an_index():
    """
    `None` is what every configuration written before this build carries by
    omission, so it has to stay the meaning of "follow the Windows default".
    """
    assert config.Settings(path="x").audio_device is None


# -- the advanced panel's table ----------------------------------------------

def advanced_rows(**overrides):
    settings = config.Settings(path="x", **overrides)
    return {row.name: row for row in advanced_panel.rows(settings)}


def test_every_advanced_row_reports_the_live_constant():
    """
    The whole point of the panel: a value transcribed beside a constant drifts
    away from it silently, and this is the page a user consults precisely when
    they doubt what is in force.
    """
    rows = advanced_rows()
    assert rows["Beam size"].value == str(transcribe.BEAM_SIZE)
    assert rows["Language"].value == transcribe.LANGUAGE
    assert rows["Paste method"].value == inject.PASTE_CHORD_LABEL
    assert rows["Minimum hold"].value.startswith(f"{engine_mod.MIN_RECORD_SEC:.2f}")
    assert rows["Release microphone when idle"].value.startswith(
        f"{engine_mod.IDLE_THRESHOLD_SEC:.0f}")


def test_the_voice_activity_filter_row_reports_the_flag_inference_uses():
    """`vad_filter` was a literal in the call; the panel needs it to be a value."""
    assert transcribe.VAD_FILTER is True
    assert advanced_rows()["Voice activity filter"].value == "On"


def test_a_constant_the_audio_tab_has_switched_off_says_so():
    """
    The two panels cannot be allowed to disagree about what is in force. The
    value has not changed -- it is simply not being applied -- so the row shows
    both facts rather than hiding one.
    """
    bypassed = advanced_rows(ignore_short_holds=False, keep_stream_warm=False)
    assert advanced_panel.BYPASSED in bypassed["Minimum hold"].value
    assert advanced_panel.BYPASSED in bypassed["Release microphone when idle"].value

    applied = advanced_rows()
    assert advanced_panel.BYPASSED not in applied["Minimum hold"].value
    assert advanced_panel.BYPASSED not in applied["Release microphone when idle"].value


def test_every_advanced_row_says_what_it_is_for():
    """A constant with no explanation is a number the user cannot act on."""
    for row in advanced_panel.rows(config.Settings(path="x")):
        assert row.name and row.note and row.value


def test_the_startup_row_reads_the_shortcut_through_paths(monkeypatch, tmp_path):
    """
    `paths` owns every application-relative path, including this one -- the
    panel must not assemble `%APPDATA%` for itself.
    """
    shortcut = tmp_path / "PTT Dictation.lnk"
    monkeypatch.setattr(paths, "startup_shortcut_path", lambda: str(shortcut))
    assert advanced_rows()["Start with Windows"].value == "Not present"

    shortcut.write_text("", encoding="utf-8")
    assert advanced_rows()["Start with Windows"].value == "Present"


# -- the diagnostics panel's log tail ----------------------------------------

def write_log(tmp_path, lines):
    path = tmp_path / "debug_log.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def test_the_tail_returns_the_last_lines_in_file_order(tmp_path):
    path = write_log(tmp_path, [f"line {n}" for n in range(50)])
    assert diagnostics_panel.tail_lines(path, limit=3) == \
        ["line 47", "line 48", "line 49"]


def test_a_short_log_is_returned_whole(tmp_path):
    path = write_log(tmp_path, ["only one"])
    assert diagnostics_panel.tail_lines(path, limit=200) == ["only one"]


def test_the_tail_reads_from_the_end_rather_than_the_whole_file(tmp_path):
    """
    A session that dictated all day reaches megabytes, and this runs every 1.5
    seconds while the tab is open.
    """
    path = write_log(tmp_path, [f"line {n:04d}" for n in range(5000)])
    lines = diagnostics_panel.tail_lines(path, limit=5, window=512)
    assert lines == [f"line {n}" for n in range(4995, 5000)]


def test_a_partial_first_line_is_dropped(tmp_path):
    """
    Seeking to a byte offset lands in the middle of a line, and half a log
    entry reads as a corrupted log rather than as a window into a long one.
    """
    path = write_log(tmp_path, ["x" * 400, "the whole line", "and another"])
    lines = diagnostics_panel.tail_lines(path, limit=50, window=64)
    assert all(not line.startswith("x") for line in lines), lines


def test_a_missing_log_is_empty_rather_than_an_exception(tmp_path):
    """The log being absent is itself worth seeing; it must not take the window down."""
    assert diagnostics_panel.tail_lines(str(tmp_path / "nope.txt")) == []


def test_an_undecodable_byte_does_not_lose_the_line(tmp_path):
    """
    `log_debug` writes UTF-8, but a log truncated by a crash mid-character
    still has to be readable -- this panel is where you look after a crash.
    """
    path = tmp_path / "debug_log.txt"
    path.write_bytes(b"good line\n\xff\xfe broken\n")
    assert len(diagnostics_panel.tail_lines(str(path))) == 2


# -- the + registration marks ------------------------------------------------
#
# Only the geometry. Whether the arms are the right colour is style.qss's
# business and whether they are visible is a screenshot's; where the four
# crossings land is arithmetic, and arithmetic is what a unit test can hold.

def test_four_marks_one_per_corner():
    centres = marks.mark_centres(400, 300)
    assert len(centres) == 4
    assert len(set(centres)) == 4


def test_the_marks_are_inset_from_every_edge():
    """
    The reference hangs each mark half outside the box; Qt clips a paintEvent to
    the widget, so these are inset instead. Every arm must therefore land inside
    the widget, or it is silently cropped -- which looks like a half-drawn mark
    rather than an error.
    """
    w, h = 400, 300
    half = marks.MARK_PX // 2
    for x, y in marks.mark_centres(w, h):
        assert 0 <= x - half and x + half < w
        assert 0 <= y - half and y + half < h


def test_the_marks_sit_at_the_corners_not_the_middle():
    w, h = 400, 300
    xs = {x for x, _ in marks.mark_centres(w, h)}
    ys = {y for _, y in marks.mark_centres(w, h)}
    assert len(xs) == 2 and len(ys) == 2
    assert min(xs) < w // 4 and max(xs) > 3 * w // 4
    assert min(ys) < h // 4 and max(ys) > 3 * h // 4


def test_the_layout_is_symmetric():
    """The gap at the left equals the gap at the right, and likewise vertically."""
    w, h = 400, 300
    (left, top), (right, _), _, (_, bottom) = marks.mark_centres(w, h)
    assert left == (w - 1) - right
    assert top == (h - 1) - bottom


def test_a_widget_too_small_gets_no_marks_rather_than_a_smear():
    """
    Every panel lives in a QScrollArea and the popover is resized as its content
    changes, so a widget smaller than four marks is reachable. It must draw
    nothing: overlapping crossings read as a rendering fault, and this is
    decoration -- there is no case where drawing it matters more than the window
    looking broken.
    """
    span = 2 * (marks.MARGIN_PX + marks.MARK_PX)
    assert marks.mark_centres(span - 1, 300) == ()
    assert marks.mark_centres(400, span - 1) == ()
    assert marks.mark_centres(0, 0) == ()
    assert marks.mark_centres(span, span) != ()


def test_the_crossing_lands_on_a_whole_pixel():
    """
    An odd mark size is deliberate: an even one puts the crossing between two
    pixels, and a 1 px hairline drawn there is rendered as two grey ones.
    """
    assert marks.MARK_PX % 2 == 1
    for x, y in marks.mark_centres(400, 300):
        assert x == int(x) and y == int(y)


def test_no_colour_is_defined_in_the_module():
    """
    The same rule the status dot follows: every colour lives in style.qss. The
    default is transparent so a stylesheet that failed to load draws no marks
    rather than black ones.
    """
    source = inspect.getsource(marks)
    assert "#" not in source.replace("#:", "").replace("# ", "")
    assert marks.RegistrationMarks._mark_colour.alpha() == 0


def test_both_hosts_use_the_mixin():
    """
    The marks exist on exactly the two surfaces section 9 asks for: the dark
    read-only display, which is both the popover body and the window banner, and
    the light tab panel every settings tab derives from.
    """
    from ptt.ui import qt_statusview
    from ptt.ui import panels

    assert issubclass(qt_statusview.StatusView, marks.RegistrationMarks)
    assert issubclass(panels.InstantApplyPanel, marks.RegistrationMarks)
    for panel in (hotkey_panel.HotkeyPanel, model_panel.ModelPanel,
                  audio_panel.AudioPanel, advanced_panel.AdvancedPanel,
                  diagnostics_panel.DiagnosticsPanel):
        assert issubclass(panel, marks.RegistrationMarks)
