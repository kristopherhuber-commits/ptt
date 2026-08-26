"""
The Concierge panel's view model, and the saved-transcript store.

`V-CG-101` … `V-CG-114`. No `QApplication` and no widget is instantiated:
defining a QWidget subclass needs neither, so this file imports the panel module
and exercises `ConciergeView`, the narration functions and `Row` -- the half of
`qt_concierge.py` that decides *what* is on screen, which is the half a
screenshot could not check anyway.

The rules under test are the three that suppress a row, and each of them exists
because the naive rendering is wrong in a specific way:

- a streamed bubble is provisional, because in grammar mode the content deltas
  are the JSON decision envelope and not an answer;
- a live progress line is replaced by the settled call, because `run_benchmark`
  narrates itself twice and the second line has the measurement in it;
- a change chip is the narration of the call that produced it, because
  `set_config` emits both and the chip is the one with the Undo on it.

And the one that adds a row: **a refused tool call is rendered as a refusal**
(FR-CG-11). Gate 2.5 made that ordinary rather than exotic -- `tools.Registry`
now rejects an `update_memory` that copies text out of `read_log`
(`development_history.md` #24) -- so an implementation that handled
`{"ok": true}` and let everything else fall off the screen would lose the
refusal the harness was changed to produce.
"""

import json

import pytest

from ptt import config
from ptt.concierge import sessions as sessions_mod
from ptt.concierge import state as state_mod
from ptt.concierge import tools as tools_mod
from ptt.ui import qt_concierge as panel_mod
from ptt.ui import qt_window as window_mod
from ptt.ui.qt_concierge import ConciergeView, Row


@pytest.fixture
def view():
    return ConciergeView(state=state_mod.READY, model_label="Gemma 4 12B")


def kinds(view):
    return [row.kind for row in view.rows]


# -- V-CG-101: the streamed bubble is provisional -----------------------------

def test_tokens_coalesce_into_one_bubble(view):
    view.add_user("hello")
    for token in ("Hi", " there", "."):
        view.add_token(token)
    assert kinds(view) == [panel_mod.USER, panel_mod.AGENT]
    assert view.rows[-1].text == "Hi there."


def test_the_settled_reply_replaces_whatever_was_streamed(view):
    """
    In grammar mode the content deltas are the decision envelope, so the live
    text can be JSON. `Turn.reply` is the transcript of record either way.
    """
    view.add_user("hello")
    view.add_token('{"decision":"reply","reply":"Hi ')
    view.add_token('there"}')
    view.close_turn("Hi there")
    assert kinds(view) == [panel_mod.USER, panel_mod.AGENT]
    assert view.rows[-1].text == "Hi there"


def test_a_cancelled_turn_leaves_no_half_answer(view):
    """
    Design 2: a new send cancels the current generation. The user who
    interrupted is looking at their next question, not at two thirds of an
    answer to the previous one.
    """
    view.add_user("first")
    view.add_token("I was saying")
    view.close_turn("", forced="cancelled")
    assert kinds(view) == [panel_mod.USER]


def test_a_tool_call_discards_the_partial_stream(view):
    view.add_user("what is the hotkey?")
    view.add_token('{"decision":"tool","tool":"get_state"')
    view.add_tool("get_state", {}, {"state": "idle"})
    assert kinds(view) == [panel_mod.USER, panel_mod.TOOL]


def test_an_empty_reply_leaves_no_empty_bubble(view):
    view.add_user("hello")
    view.add_token("...")
    view.close_turn("")
    assert kinds(view) == [panel_mod.USER]


# -- V-CG-102: a refused tool call is a refusal -------------------------------

def test_a_refused_memory_note_is_rendered_as_a_refusal(view):
    """
    The headline requirement of this panel, and the one a success-only
    implementation loses. Gate 2.5's guard makes this an ordinary outcome.
    """
    result = {"error": True,
              "reason": "that text was copied out of the log, and the log "
                        "carries content this application only observed",
              "hint": "write what you concluded, in your own words"}
    row = view.add_tool("update_memory", {"text": "..."}, result)
    assert row.kind == panel_mod.REFUSAL
    assert "copied out of the log" in row.detail
    assert "your own words" in row.detail
    assert kinds(view) == [panel_mod.REFUSAL]


def test_a_refused_write_is_rendered_as_a_refusal(view):
    row = view.add_tool("set_config", {"key": "use_gpu", "value": "false"},
                        {"error": True, "reason": "use_gpu is not a boolean"})
    assert row.kind == panel_mod.REFUSAL
    assert "not a boolean" in row.detail


def test_a_refusal_with_no_reason_still_says_something(view):
    row = view.add_tool("read_log", {}, {"error": True})
    assert row.kind == panel_mod.REFUSAL
    assert row.detail


def test_a_refusal_survives_a_chip_immediately_before_it(view):
    """
    The chip-absorbs-the-narration rule must not absorb a *refusal*. The two
    can only ever be adjacent across separate calls, and dropping the second
    would report a rejection as a success -- what FR-CG-11 forbids in the one
    place it is easiest to do by accident.
    """
    view.add_change(1, "config", "use_gpu", False, True)
    row = view.add_tool("set_config", {"key": "model"},
                        {"error": True, "reason": "no such model"})
    assert row.kind == panel_mod.REFUSAL
    assert kinds(view) == [panel_mod.CHANGE, panel_mod.REFUSAL]


# -- V-CG-103: the chip is the narration --------------------------------------

def test_a_successful_write_shows_the_chip_and_nothing_else(view):
    view.add_change(1, "config", "use_gpu", False, True)
    view.add_progress("changed use_gpu to True")
    assert view.add_tool("set_config", {"key": "use_gpu"}, {"ok": True}) is None
    assert kinds(view) == [panel_mod.CHANGE]


def test_the_chip_reads_the_way_the_handoff_writes_it():
    assert panel_mod.chip_text("config", "use_gpu", False, True) == \
        "use_gpu: false → true"


def test_a_memory_chip_carries_lengths_rather_than_the_note():
    text = panel_mod.chip_text("memory", "memory_note", "a" * 400, "b" * 380)
    assert text == "memory note: 400 → 380 characters"
    assert "aaaa" not in text


def test_a_long_value_is_shortened_in_a_chip():
    text = panel_mod.chip_text("config", "vocabulary", "x" * 400, "y")
    assert len(text) < 120 and "…" in text


# -- V-CG-104: live progress becomes one settled line -------------------------

def test_progress_lines_are_replaced_by_the_settled_call(view):
    view.add_progress("measuring medium.en against the bundled 30-second clip")
    view.add_progress("measured medium.en: 2.34 s")
    row = view.add_tool("run_benchmark", {"model": "medium.en"},
                        {"seconds": 2.34, "device": "cuda"})
    assert kinds(view) == [panel_mod.TOOL]
    assert "measuring medium.en" in row.text
    assert row.detail == "2.34 s"


def test_a_progress_line_stands_while_the_call_is_still_running(view):
    view.add_progress("measuring medium.en against the bundled 30-second clip")
    assert kinds(view) == [panel_mod.TOOL]
    assert view.rows[0].text == ("Measuring medium.en against the bundled "
                                 "30-second clip…")


def test_a_past_tense_progress_line_is_still_a_sentence(view):
    """
    `tools.py` emits both tenses. Gluing them behind "Concierge is" makes a
    sentence of one and nonsense of the other, which is why the harness's own
    words are used and only the first letter is touched.
    """
    view.add_progress("changed use_gpu to True")
    assert view.rows[0].text == "Changed use_gpu to True…"


# -- V-CG-105: every tool gets a sentence -------------------------------------

def test_every_registered_tool_has_its_own_description(tmp_path):
    """
    Derived from the registry rather than from a list here, so a ninth tool
    fails this test instead of quietly narrating as `running <name>`.
    """
    registry = tools_mod.Registry(
        config.Settings(path=str(tmp_path / "config.json")))
    for name in registry.names():
        described = panel_mod.describe_tool(name, {})
        assert described and not described.startswith("running "), name


def test_an_unregistered_tool_still_narrates_something():
    assert panel_mod.describe_tool("teleport", {}) == "running teleport"


def test_a_named_argument_reaches_the_narration():
    assert "medium.en" in panel_mod.describe_tool("run_benchmark",
                                                  {"model": "medium.en"})
    assert "use_gpu" in panel_mod.describe_tool("set_config", {"key": "use_gpu"})


# -- V-CG-106: undo -----------------------------------------------------------

def test_a_successful_undo_marks_the_chip_and_clears_it_from_pending(view):
    view.add_change(3, "config", "model", "large-v3-turbo", "medium.en")
    view.mark_undone(3, True, "")
    assert view.rows[0].undone is True
    assert view.pending_changes() == ()


def test_a_refused_undo_stays_pending_and_says_why(view):
    """
    `V-CG-40`…`V-CG-45`'s rule, on the panel's side: a chip that greyed itself
    out after a failed undo would claim a restore that did not happen.
    """
    view.add_change(3, "config", "model", "a", "b")
    view.mark_undone(3, False, "the value is no longer valid")
    assert view.rows[0].undone is False
    assert len(view.pending_changes()) == 1
    assert view.rows[-1].kind == panel_mod.NOTICE
    assert "no longer valid" in view.rows[-1].text


def test_an_undo_for_a_change_that_is_not_shown_is_not_an_error(view):
    view.mark_undone(99, True, "")
    assert view.rows == []


# -- V-CG-107: the states are the machine's, verbatim -------------------------

def test_every_state_the_machine_declares_has_a_caption():
    for state in state_mod.STATES:
        assert panel_mod.state_caption(state, "")


def test_the_machines_own_detail_wins_over_the_default_caption():
    detail = "llama-server exited with code 1"
    assert panel_mod.state_caption(state_mod.STOPPED, detail) == detail


def test_a_message_can_be_sent_only_when_the_machine_says_so_or_while_answering():
    """
    The `ready` half is `state.can_serve`, not a second opinion about it.
    `generating` is added deliberately: design 2 says a new send cancels the
    current generation rather than queueing behind it.
    """
    allowed = {s for s in state_mod.STATES if panel_mod.can_send(s)}
    assert allowed == {state_mod.READY, state_mod.GENERATING}


def test_every_state_says_something_in_the_empty_input_box():
    for state in state_mod.STATES:
        assert panel_mod.placeholder(state, "")


# -- V-CG-108: the status bar segment -----------------------------------------

def test_the_segment_names_the_model_and_the_residency():
    text = panel_mod.status_segment(state_mod.READY, "Gemma 4 12B", 5)
    assert text == "Concierge: Gemma 4 12B resident · unloads after 5 min idle"


def test_zero_minutes_reads_as_unloading_on_close():
    text = panel_mod.status_segment(state_mod.GENERATING, "Gemma 4 12B", 0)
    assert text.endswith("unloads on close")


@pytest.mark.parametrize("state", [state_mod.DISABLED, state_mod.NOT_DOWNLOADED,
                                   state_mod.STOPPED])
def test_the_segment_is_absent_when_no_vram_is_held(state):
    """
    Handoff section 7 says "absent when not downloaded/disabled"; `stopped`
    joins them, because the segment exists to say VRAM is being held and in all
    three of those states none is.
    """
    assert panel_mod.status_segment(state, "Gemma 4 12B", 5) == ""


# -- V-CG-109: rows survive being saved and read back -------------------------

def test_a_row_round_trips_through_the_saved_shape():
    row = Row(panel_mod.TOOL, "Concierge is reading the log…", detail="118 lines")
    assert Row.from_dict(row.to_dict()) == row


def test_an_unknown_row_kind_reads_back_as_a_notice():
    """A transcript written by a later build must not vanish; it degrades."""
    row = Row.from_dict({"kind": "hologram", "text": "hello"})
    assert row.kind == panel_mod.NOTICE and row.text == "hello"


def test_a_saved_payload_is_json_serialisable(view):
    view.add_user("hello")
    view.add_change(1, "config", "use_gpu", False, True)
    view.add_tool("read_log", {}, {"lines": []})
    json.dumps(view.save_payload())


def test_a_new_session_clears_the_transcript_but_not_the_note(view):
    view.memory_text = "prefers the medium model"
    view.add_user("hello")
    view.add_token("hi")
    view.clear()
    assert view.rows == []
    assert view.memory_text == "prefers the medium model"
    view.add_token("fresh")
    assert kinds(view) == [panel_mod.AGENT]


# -- V-CG-109b: the window gives back what expanding took ---------------------

def test_collapsing_returns_the_window_to_where_it_started():
    """
    Reported in session 3's hand test: closing the panel left the window one
    panel wider than it started, every time.
    """
    assert window_mod.restored_width(880, 1240) == 880


def test_a_resize_made_while_the_panel_was_open_survives_the_close():
    assert window_mod.restored_width(880, 1440) == 1080
    assert window_mod.restored_width(880, 1200) == 840


def test_the_window_is_never_restored_below_its_stated_minimum():
    assert window_mod.restored_width(880, 900) == window_mod.MINIMUM_SIZE[0]


# -- the saved-transcript store (V-CG-110 … V-CG-114) -------------------------

@pytest.fixture
def store(tmp_path):
    return sessions_mod.SessionStore(str(tmp_path / "concierge_sessions.json"),
                                     limit_provider=lambda: 3)


ROWS = [{"kind": "user", "text": "hello", "detail": ""},
        {"kind": "agent", "text": "hi", "detail": ""}]


def test_a_saved_session_can_be_listed_and_read_back(store):
    saved, reason = store.save("Mic setup", ROWS)
    assert reason is None
    listed = store.list()
    assert [s.name for s in listed] == ["Mic setup"]
    assert listed[0].row_count == 2 and listed[0].rows == ()
    loaded = store.load(saved.id)
    assert [r["text"] for r in loaded.rows] == ["hello", "hi"]


def test_an_empty_session_is_refused_with_a_reason(store):
    saved, reason = store.save("nothing", [])
    assert saved is None and "nothing in this session" in reason


def test_the_newest_session_is_first_and_the_limit_is_honoured(store):
    for index in range(5):
        store.save(f"session {index}", ROWS)
    assert [s.name for s in store.list()] == \
        ["session 4", "session 3", "session 2"]


def test_the_limit_is_read_at_every_save_not_captured(tmp_path):
    """
    The same live-re-read discipline the residency timer and the hotkey use:
    lowering `history_limit` has to take effect without a restart.
    """
    limit = [5]
    store = sessions_mod.SessionStore(str(tmp_path / "s.json"),
                                      limit_provider=lambda: limit[0])
    for index in range(4):
        store.save(f"session {index}", ROWS)
    assert len(store.list()) == 4
    limit[0] = 2
    store.save("newest", ROWS)
    assert len(store.list()) == 2


def test_resaving_one_session_replaces_it_rather_than_duplicating_it(store):
    saved, _ = store.save("draft", ROWS)
    again, _ = store.save("draft", ROWS + [{"kind": "user", "text": "more"}],
                          session_id=saved.id)
    assert again.id == saved.id
    assert len(store.list()) == 1
    assert store.load(saved.id).row_count == 3


def test_an_unreadable_store_reads_as_empty_and_says_so(tmp_path, log_lines):
    path = tmp_path / "s.json"
    path.write_text("{ not json", encoding="utf-8")
    store = sessions_mod.SessionStore(str(path))
    assert store.list() == ()
    assert any("could not read saved sessions" in line for line in log_lines())


def test_a_store_that_is_not_a_list_reads_as_empty(tmp_path, log_lines):
    path = tmp_path / "s.json"
    path.write_text('{"id": "x"}', encoding="utf-8")
    assert sessions_mod.SessionStore(str(path)).list() == ()
    assert any("not a list" in line for line in log_lines())


def test_an_oversized_transcript_is_trimmed_from_the_oldest_end():
    rows = [{"kind": "agent", "text": "x" * 1000, "detail": ""} for _ in range(50)]
    rows[-1]["text"] = "the answer"
    fitted, dropped = sessions_mod.fit(rows, limit=5000)
    assert dropped > 0
    assert fitted[-1]["text"] == "the answer"
    assert fitted[0]["kind"] == "notice" and "dropped" in fitted[0]["text"]


def test_a_transcript_that_fits_is_untouched():
    fitted, dropped = sessions_mod.fit(ROWS)
    assert dropped == 0 and fitted == ROWS


def test_a_session_can_be_renamed_and_deleted(store):
    saved, _ = store.save("first name", ROWS)
    assert store.rename(saved.id, "second name") == (True, None)
    assert store.list()[0].name == "second name"
    assert store.rename(saved.id, "  ")[0] is False
    ok, _ = store.delete(saved.id)
    assert ok and store.list() == ()
    assert store.delete(saved.id)[0] is False


def test_a_missing_session_file_is_not_an_error(tmp_path):
    store = sessions_mod.SessionStore(str(tmp_path / "never" / "written.json"))
    assert store.list() == () and store.load("x") is None
    saved, reason = store.save("first", ROWS)
    assert saved is not None and reason is None
