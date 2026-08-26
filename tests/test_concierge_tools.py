"""
The eight tools: dispatch, refusal, and the 16 KiB fetch-time cap.

`V-CG-10` … `V-CG-19`. Every seam is a fake, so nothing here opens an audio
device, reads the real log, or touches a GPU. Two properties are checked
repeatedly because they are the ones a later change would break silently: a
result never exceeds the cap, and a refused write changes nothing.
"""

import json
import os

import pytest

from ptt import config, transcribe
from ptt.concierge import tools as tools_mod
from ptt.concierge.agent import Journal


class FakeDevice:
    def __init__(self, index, name, hostapi="MME"):
        self.index = index
        self.name = name
        self.hostapi = hostapi


@pytest.fixture
def settings(tmp_path):
    return config.Settings(path=str(tmp_path / "config.json"))


@pytest.fixture
def memory(tmp_path):
    return tools_mod.MemoryNote(str(tmp_path / "note.txt"),
                                str(tmp_path / "note.prev.txt"))


@pytest.fixture
def registry(settings, tmp_path, memory):
    log = tmp_path / "debug_log.txt"
    prev = tmp_path / "debug_log.prev.txt"
    log.write_text("current line one\ncurrent line two\n", encoding="utf-8")
    prev.write_text("previous line one\n", encoding="utf-8")
    return tools_mod.Registry(
        settings,
        state_provider=lambda: {"state": "idle", "status_text": "Ready",
                                "detail": "model resident on CUDA",
                                "hotkey": "Right Ctrl", "model": "large-v3-turbo",
                                "device": "cuda", "microphone": "Jabra",
                                "last": "0.57 s"},
        devices=lambda: (FakeDevice(0, "Microphone Array"), FakeDevice(3, "Jabra")),
        benchmark=lambda model: {"seconds": 1.181, "device": "cuda"},
        memory=memory,
        journal=Journal(settings=settings, memory=memory),
        log_path=str(log),
        previous_log_path=str(prev),
        llm_resident=lambda: True,
        installed_sizes=lambda: {"large-v3-turbo": 1_700_000_000},
    )


# -- the registry itself ------------------------------------------------------

def test_there_are_exactly_eight_tools(registry):
    """`concierge_handoff.md` section 4. Eight, named, in a stable order."""
    assert registry.names() == (
        "get_config", "set_config", "get_state", "list_audio_devices",
        "list_models", "run_benchmark", "read_log", "update_memory")


def test_only_the_two_writing_tools_are_marked_as_writing(registry):
    """
    FR-CG-3 says "every Concierge-made change", which is not "every setting
    change" -- so `update_memory` is in this set (Q22), and nothing else is.
    """
    assert {t.name for t in registry.tools() if t.writes} == {
        "set_config", "update_memory"}


def test_an_unregistered_tool_is_refused_with_the_list(registry):
    result = registry.call("delete_everything", {})
    assert result["error"] is True
    assert "not a registered tool" in result["reason"]
    assert "get_config" in result["hint"]


def test_a_missing_required_argument_is_refused(registry):
    result = registry.call("set_config", {"key": "use_gpu"})
    assert result["error"] is True and "needs 'value'" in result["reason"]


def test_an_unknown_argument_is_refused(registry):
    result = registry.call("get_state", {"verbose": True})
    assert result["error"] is True and "no argument 'verbose'" in result["reason"]


def test_an_out_of_range_argument_is_refused(registry):
    result = registry.call("read_log", {"tail_lines": 99999})
    assert result["error"] is True and "is above 2000" in result["reason"]


def test_a_raising_tool_becomes_an_error_not_an_exception(settings):
    """
    A tool that throws must not take the agent loop with it. The seam is the
    boundary: what crosses it is always a result.
    """
    registry = tools_mod.Registry(settings, benchmark=lambda m: 1 / 0)
    result = registry.call("run_benchmark", {"model": "tiny.en"})
    assert result["error"] is True and "ZeroDivisionError" in result["reason"]


# -- the 16 KiB cap (Q16) -----------------------------------------------------

def test_a_result_under_the_cap_is_untouched():
    body = tools_mod.cap({"a": 1, "items": [1, 2, 3]}, bulk_key="items")
    assert body == {"a": 1, "items": [1, 2, 3]}
    assert "truncated" not in body


def test_a_long_list_is_cut_and_says_so():
    items = [{"line": "x" * 200} for _ in range(500)]
    body = tools_mod.cap({"items": items}, bulk_key="items")
    assert body["truncated"] is True
    assert 0 < len(body["items"]) < 500
    assert body["returned_bytes"] <= tools_mod.RESULT_CAP_BYTES
    assert body["available_bytes"] > body["returned_bytes"]
    assert body["hint"]


def test_the_cap_is_never_exceeded_whatever_it_holds():
    """
    The counters are written *after* the size is measured, so a naive
    implementation lands a few bytes over its own bound. The overhead estimate
    is deliberately generous for that reason.
    """
    for count in (1, 17, 200, 5000):
        items = [{"line": "y" * 300, "n": n} for n in range(count)]
        body = tools_mod.cap({"items": items}, bulk_key="items")
        assert len(tools_mod.encoded(body)) <= tools_mod.RESULT_CAP_BYTES


def test_items_are_kept_from_the_front():
    """
    Which is why `read_log` puts the current log first: the file the user is
    asking about survives the cut, and the previous session's is what goes.
    """
    items = [{"n": n, "pad": "z" * 500} for n in range(200)]
    body = tools_mod.cap({"items": items}, bulk_key="items")
    kept = [i["n"] for i in body["items"]]
    assert kept == list(range(len(kept)))


def test_a_map_is_shortened_and_names_what_it_dropped():
    values = {f"key{n}": "w" * 400 for n in range(200)}
    body = tools_mod.cap({"settings": values}, bulk_key="settings")
    assert body["truncated"] is True
    assert body["omitted"]
    assert set(body["omitted"]).isdisjoint(body["settings"])


def test_an_oversized_result_with_nothing_to_drop_is_an_error():
    """
    Not an over-cap body with a flag on it. Design 4.4 is explicit that the
    context budget cannot rescue a turn whose single tool result is larger than
    the window, so the bound has to actually bind.
    """
    body = tools_mod.cap({"blob": "q" * 40000})
    assert body["error"] is True
    assert "over the" in body["reason"]


def test_every_tool_result_goes_through_the_cap(registry, tmp_path):
    """
    The belt to each implementation's braces: dispatch caps, so the bound is a
    property of calling a tool rather than of each tool remembering.
    """
    huge = "\n".join("x" * 1000 for _ in range(4000))
    (tmp_path / "debug_log.txt").write_text(huge, encoding="utf-8")
    for name, args in [("get_config", {}), ("get_state", {}),
                       ("list_audio_devices", {}), ("list_models", {}),
                       ("read_log", {"tail_lines": 2000})]:
        result = registry.call(name, args)
        assert len(tools_mod.encoded(result)) <= tools_mod.RESULT_CAP_BYTES, name


# -- get_config / set_config --------------------------------------------------

def test_get_config_returns_one_key(registry):
    assert registry.call("get_config", {"key": "model"}) == {
        "key": "model", "value": transcribe.DEFAULT_MODEL}


def test_get_config_returns_everything_when_asked_for_nothing(registry):
    result = registry.call("get_config", {})
    assert set(result["settings"]) == set(config.READABLE_KEYS)


def test_get_config_refuses_a_key_that_is_not_a_setting(registry):
    result = registry.call("get_config", {"key": "turbo"})
    assert result["error"] is True


def test_a_setting_reads_back_as_something_set_config_would_accept(registry, settings):
    """
    A round trip the model should never have to guess at: tuples come back as
    lists, and a rule comes back as its own JSON shape.
    """
    settings.set("hotkey", ["ralt", "rshift"])
    value = registry.call("get_config", {"key": "hotkey"})["value"]
    assert value == ["ralt", "rshift"]
    assert registry.call("set_config", {"key": "hotkey", "value": value})["ok"]


def test_set_config_writes_through_settings_set(registry, settings):
    result = registry.call("set_config", {"key": "model", "value": "small.en"})
    assert result["ok"] is True
    assert result["old"] == transcribe.DEFAULT_MODEL and result["new"] == "small.en"
    assert settings.model == "small.en"


def test_set_config_refuses_a_bad_value_and_reports_the_reason(registry, settings):
    """FR-CG-11, end to end through dispatch."""
    result = registry.call("set_config", {"key": "use_gpu", "value": "false"})
    assert result["error"] is True
    assert "is not a boolean" in result["reason"]
    assert settings.use_gpu is True


def test_a_refused_write_hints_the_shape_a_retry_needs(registry, settings):
    """
    `V-CG-13`. The repair loop's other half, and it is not decoration.

    Design 4.3 makes a structured error the mechanism that turns a wrong first
    attempt into a right second one, and section 6's threshold assumes it works.
    A hint the model cannot act on is a repair loop that cannot repair: session
    2's first suite run measured Gemma 4 12B sending the *string* `"['ralt']"`
    for `hotkey`, being told "read the setting's type before writing it", and
    sending the identical value again. So the hint carries the field's type and
    its current value -- a worked example of the shape, in the field's own
    units.
    """
    result = registry.call("set_config", {"key": "hotkey", "value": "['ralt']"})
    assert result["error"] is True
    assert "array" in result["hint"]
    # The current value, which is what the retry should look like.
    assert '["rctrl"]' in result["hint"]
    assert settings.hotkey == ("rctrl",)


def test_the_retry_hint_is_derived_from_fields_not_written_per_key(registry):
    """
    Every constraint in the hint comes from the `FIELDS` entry (D-CG-13).

    Choices and bounds appear because the rule carries them, not because
    `tools.py` knows anything about these particular settings -- the same
    derivation `V-CF-16` pins for the schema's `key` enum.
    """
    choices = registry.call(
        "set_config", {"key": "model", "value": "whisper-ultra"})
    assert "large-v3-turbo" in choices["hint"]

    bounded = registry.call(
        "set_config", {"key": "concierge.idle_unload_minutes", "value": 120})
    assert "between 0 and 30" in bounded["hint"]

    plain = registry.call("set_config", {"key": "use_gpu", "value": "false"})
    assert "boolean" in plain["hint"]
    # No bounds and no choices on a bool, so neither clause appears.
    assert "between" not in plain["hint"] and "one of" not in plain["hint"]


def test_set_config_cannot_reach_the_vocabulary(registry, settings):
    """
    The scope exclusion, enforced. The requirement states it as prose and prose
    does not stop a tool call (review section 1.2).
    """
    result = registry.call(
        "set_config",
        {"key": "vocabulary", "value": [{"heard": "a", "typed": "b"}]})
    assert result["error"] is True
    assert "is not one of" in result["reason"]
    # The refusal has to say where the thing lives, or the model is left to
    # guess whether the setting exists at all (design 4.5 part 2).
    assert "Vocabulary tab" in result["hint"]
    assert settings.vocabulary == ()


def test_the_allowlist_is_enforced_twice(registry, settings):
    """
    Belt and braces, and both are load-bearing. The argument enum refuses the
    call before it is dispatched; `_set_config` refuses it again on the way in,
    so a future caller that reaches dispatch by another route -- a native-mode
    tool call the schema did not constrain, say -- still cannot write a key the
    allowlist excludes.
    """
    result = registry._set_config("vocabulary", [])
    assert result["error"] is True
    assert "may change" in result["reason"]
    assert settings.vocabulary == ()


def test_set_config_cannot_reach_the_benchmark_cache(registry):
    result = registry.call("set_config", {"key": "benchmarks", "value": {}})
    assert result["error"] is True


def test_set_config_refuses_an_object_as_a_value(registry):
    """
    Design 4.1's scalar union is where the schema deliberately stops. An object
    is not in it, so dispatch refuses it rather than handing it to `Settings`.
    """
    result = registry.call("set_config", {"key": "model", "value": {"name": "x"}})
    assert result["error"] is True and "is an object" in result["reason"]


def test_a_successful_write_is_journalled_and_announced(registry, settings):
    seen = []
    registry._on_applied = lambda key, old, new: seen.append((key, old, new))
    registry.call("set_config", {"key": "start_click", "value": True})
    assert seen == [("start_click", False, True)]
    assert [(c.kind, c.key, c.old, c.new) for c in registry._journal.changes()] == [
        ("config", "start_click", False, True)]


def test_a_refused_write_is_not_journalled(registry):
    registry.call("set_config", {"key": "model", "value": "enormous"})
    assert registry._journal.changes() == ()


# -- get_state (Q26) ----------------------------------------------------------

def test_get_state_returns_exactly_the_declared_keys(registry):
    """
    The harness declares the shape; the Qt adapter fills it. It may not import
    `UiState`, whose module imports PySide6 at column 0 (CON-CG-6).
    """
    result = registry.call("get_state", {})
    assert set(result) == set(tools_mod.STATE_KEYS)


def test_a_key_the_adapter_forgot_reads_as_unknown(settings):
    """
    Dropped keys are worse than empty ones: a field that vanishes is a field the
    model invents a value for.
    """
    registry = tools_mod.Registry(settings, state_provider=lambda: {"state": "idle"})
    result = registry.call("get_state", {})
    assert set(result) == set(tools_mod.STATE_KEYS)
    assert result["microphone"] == "unknown"


def test_an_adapter_that_supplies_extra_keys_does_not_widen_the_result(registry):
    registry._state_provider = lambda: {"state": "idle", "secret": "x"}
    assert "secret" not in registry.call("get_state", {})


# -- list_audio_devices / list_models ----------------------------------------

def test_list_audio_devices_reports_the_selection_and_what_it_means(registry, settings):
    result = registry.call("list_audio_devices", {})
    assert result["selected_index"] is None
    assert "Windows default" in result["selected_means"]
    assert [d["index"] for d in result["devices"]] == [0, 3]

    settings.set("audio_device", 3)
    assert registry.call("list_audio_devices", {})["selected_index"] == 3


def test_list_models_reports_the_catalogue_with_any_measurement(registry, settings):
    settings.set("benchmarks", {
        config.benchmark_key("large-v3-turbo", "cuda"): {
            "seconds": 1.18, "at": "2026-08-25T10:00:00", "clip": "abc",
            "llm_resident": True}})
    result = registry.call("list_models", {})
    assert result["current"] == transcribe.DEFAULT_MODEL
    assert [m["name"] for m in result["models"]] == list(transcribe.MODEL_NAMES)
    row = next(m for m in result["models"] if m["name"] == "large-v3-turbo")
    assert row["measured_seconds"] == 1.18
    assert row["measured_with_llm_resident"] is True
    assert row["installed_bytes"] == 1_700_000_000


def test_an_unmeasured_model_says_so_rather_than_guessing(registry):
    row = next(m for m in registry.call("list_models", {})["models"]
               if m["name"] == "tiny.en")
    assert row["measured_seconds"] is None
    assert row["measured_at"] is None


# -- run_benchmark (Q23) ------------------------------------------------------

def test_run_benchmark_progress_comes_from_the_harness(registry):
    """
    Q23. Progress the *model* generates is progress produced by decoding, and
    spike C5 measured a 1.46x Whisper penalty during decode -- which is the
    number being taken. So the harness emits it and the LLM stays idle.
    """
    seen = []
    registry._progress = seen.append
    registry.call("run_benchmark", {"model": "tiny.en"})
    assert any("measuring tiny.en" in line for line in seen)
    assert any("measured tiny.en" in line for line in seen)


def test_a_benchmark_records_whether_the_llm_was_resident(registry):
    result = registry.call("run_benchmark", {"model": "tiny.en"})
    assert result["llm_resident"] is True
    assert result["seconds"] == 1.181
    assert "in VRAM" in result["note"]


def test_run_benchmark_refuses_a_model_that_is_not_in_the_catalogue(registry):
    result = registry.call("run_benchmark", {"model": "enormous"})
    assert result["error"] is True and "is not one of" in result["reason"]


def test_benchmarking_without_an_engine_says_so(settings):
    registry = tools_mod.Registry(settings)
    result = registry.call("run_benchmark", {"model": "tiny.en"})
    assert result["error"] is True and "not available" in result["reason"]


# -- read_log (Q21) -----------------------------------------------------------

def test_read_log_reads_both_files_labelled(registry):
    """
    Q21. `OBS-4` rotates at every startup precisely so a crash log survives the
    restart -- and someone asking for a diagnosis has almost always restarted,
    so the log they want is the one the old specification could not reach.
    """
    result = registry.call("read_log", {})
    assert [f["label"] for f in result["files"]] == ["current", "previous"]
    labels = {line["file"] for line in result["lines"]}
    assert labels == {"current", "previous"}


def test_the_previous_log_can_be_left_out(registry):
    result = registry.call("read_log", {"include_previous": False})
    assert [f["label"] for f in result["files"]] == ["current"]
    assert {line["file"] for line in result["lines"]} == {"current"}


def test_the_current_log_comes_first_and_survives_the_cut(registry, tmp_path):
    """
    One budget across both files. When it binds, the previous session's lines
    are what go.
    """
    (tmp_path / "debug_log.txt").write_text(
        "\n".join(f"current {n} " + "c" * 200 for n in range(300)), encoding="utf-8")
    (tmp_path / "debug_log.prev.txt").write_text(
        "\n".join(f"previous {n} " + "p" * 200 for n in range(300)), encoding="utf-8")
    result = registry.call("read_log", {"tail_lines": 300})
    assert result["truncated"] is True
    assert result["lines"][0]["file"] == "current"
    assert all(line["file"] == "current" for line in result["lines"])


def test_the_log_tail_is_read_from_the_end(registry, tmp_path):
    (tmp_path / "debug_log.txt").write_text(
        "\n".join(str(n) for n in range(1000)), encoding="utf-8")
    lines = [l["line"] for l in registry.call(
        "read_log", {"tail_lines": 5, "include_previous": False})["lines"]]
    assert lines == ["995", "996", "997", "998", "999"]


def test_a_missing_log_is_empty_rather_than_an_exception(settings, tmp_path):
    registry = tools_mod.Registry(settings,
                                  log_path=str(tmp_path / "nope.txt"),
                                  previous_log_path=str(tmp_path / "nope.prev.txt"))
    result = registry.call("read_log", {})
    assert result["lines"] == []


def test_an_undecodable_byte_does_not_lose_the_line(registry, tmp_path):
    """This is the file you read after a crash. A bad byte is not a reason to stop."""
    (tmp_path / "debug_log.txt").write_bytes(b"good line\nbad \xff byte\n")
    lines = [l["line"] for l in registry.call(
        "read_log", {"include_previous": False})["lines"]]
    assert lines[0] == "good line"
    assert "byte" in lines[1]


def test_the_available_bytes_report_the_whole_file(registry, tmp_path):
    big = "\n".join("x" * 500 for _ in range(500))
    (tmp_path / "debug_log.txt").write_text(big, encoding="utf-8")
    result = registry.call("read_log", {"tail_lines": 500})
    assert result["truncated"] is True
    assert result["available_bytes"] >= len(big)


# -- update_memory (FR-CG-14, Q22) -------------------------------------------

def test_update_memory_writes_the_note_and_journals_it(registry, memory):
    result = registry.call("update_memory", {"text": "prefers the medium model"})
    assert result["ok"] is True and result["characters"] == 24
    assert memory.read() == "prefers the medium model"
    assert [(c.kind, c.key) for c in registry._journal.changes()] == [
        ("memory", "memory_note")]


def test_the_previous_note_is_kept(registry, memory):
    registry.call("update_memory", {"text": "first"})
    registry.call("update_memory", {"text": "second"})
    assert memory.read() == "second"
    assert memory.read_previous() == "first"


def test_a_note_over_the_cap_is_refused(registry, memory):
    result = registry.call(
        "update_memory", {"text": "x" * (tools_mod.MEMORY_NOTE_MAX_CHARS + 1)})
    assert result["error"] is True and "over the" in result["reason"]
    assert memory.read() == ""


def test_a_note_exactly_at_the_cap_is_accepted(registry, memory):
    text = "y" * tools_mod.MEMORY_NOTE_MAX_CHARS
    assert registry.call("update_memory", {"text": text})["ok"] is True
    assert memory.read() == text


def test_a_missing_note_reads_as_empty(memory):
    assert memory.read() == ""
    assert memory.read_previous() == ""


def test_the_note_is_written_atomically(tmp_path):
    memory = tools_mod.MemoryNote(str(tmp_path / "note.txt"),
                                  str(tmp_path / "note.prev.txt"))
    memory.write("hello")
    assert [f for f in os.listdir(tmp_path) if f.endswith(".tmp")] == []


def test_a_non_string_note_is_refused(memory):
    ok, reason = memory.write(42)
    assert ok is False and "not a string" in reason


# -- CON-CG-5: nothing here knows which model is behind it -------------------

def test_the_registry_is_built_from_config_not_from_a_hand_written_list(registry):
    """
    CON-CG-5's floor, one level down: the key enum in `set_config`'s declaration
    is `config.WRITABLE_KEYS`, so a setting added to the application is settable
    by the Concierge with no edit here.
    """
    spec = registry.get("set_config").arg("key")
    # `is`, not `==`. A hand-written tuple carrying today's values is equal to
    # the derived one and drifts the first time a field is added -- which is the
    # mutation `verification.md` section 4.1 records for D-CG-13, and the reason
    # equality was not a strong enough assertion here.
    assert spec.enum is config.WRITABLE_KEYS

    readable = registry.get("get_config").arg("key")
    assert readable.enum is config.READABLE_KEYS
