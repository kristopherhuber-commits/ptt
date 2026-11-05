"""
The text cursor's cell, for the double-tap's small mark (FR-13).

The two lookups are Windows calls and are checked by hand (verification.md
section 5.7); what is tested here is how their answers become a cell.
"""

from ptt import caret


def test_the_first_non_empty_rectangle_is_the_cell():
    assert caret.first_rect([0, 0, 0, 0, 61.0, 561.4, 9.0, 19.0]) == (61, 561, 9, 19)


def test_no_rectangle_is_no_cell():
    assert caret.first_rect([]) is None
    assert caret.first_rect(None) is None
    assert caret.first_rect([10, 10, 0, 19]) is None


def test_the_cell_after_the_system_caret_is_a_square_of_its_height():
    assert caret.cell_after_caret(100, 200, 102, 228) == (100, 200, 28, 28)


def test_a_caret_with_no_height_is_no_cell():
    assert caret.cell_after_caret(100, 200, 102, 200) is None


def test_a_lookup_that_does_not_answer_in_time_gives_none(monkeypatch, log_lines):
    import threading
    stall = threading.Event()
    monkeypatch.setattr(caret, "_lookup", lambda: stall.wait(5))
    try:
        assert caret.cursor_cell(timeout=0.05) is None
    finally:
        stall.set()
    assert any("no answer within" in line for line in log_lines())
