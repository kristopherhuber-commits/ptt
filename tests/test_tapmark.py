"""
Where the double-tap's red mark goes (v4).

Windows reports the caret in physical pixels and Qt places windows in logical
ones; `to_logical` is the mapping. The window itself needs a QApplication, so
it is checked by hand (verification.md section 5.7).
"""

from ptt.ui.qt_tapmark import to_logical


def test_at_100_percent_a_point_is_unchanged():
    assert to_logical((500, 300), [(0, 0, 1920, 1080, 1.0)]) == (500, 300)


def test_at_150_percent_a_point_scales_from_the_screens_corner():
    # A 3840 x 2160 panel at 150 % is 2560 x 1440 logical.
    assert to_logical((3000, 1500), [(0, 0, 2560, 1440, 1.5)]) == (2000, 1000)


def test_a_point_on_the_second_screen_uses_that_screens_scale():
    screens = [(0, 0, 2560, 1440, 1.5), (3840, 0, 1920, 1080, 1.0)]
    assert to_logical((4000, 100), screens) == (4000, 100)
    assert to_logical((300, 150), screens) == (200, 100)


def test_a_point_on_no_screen_uses_the_first():
    assert to_logical((-30, -30), [(0, 0, 1920, 1080, 2.0)]) == (-15, -15)
