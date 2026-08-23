"""
config.json: validation, fallbacks, round trips, and the atomic write.

Two things are checked together throughout. A fallback must produce the right
value **and** say why in the log: `OBS-3` exists because a configuration that
silently reverts is indistinguishable from one that was never applied, and the
return value alone cannot tell those apart. The `log_lines` fixture is what makes
the second half checkable.
"""

import json
import os

import pytest

from ptt import config, transcribe


def load(path):
    return config.load(path)


def written(path):
    return json.loads(open(path, encoding="utf-8").read())


# -- the file is missing or unusable ----------------------------------------

def test_missing_file_uses_defaults(tmp_path, log_lines):
    settings = load(str(tmp_path / "nope.json"))
    assert settings.use_gpu is True
    assert settings.hotkey == config.hotkey_mod.DEFAULT_HOTKEY
    assert settings.model == transcribe.DEFAULT_MODEL
    assert settings.benchmarks == {}
    assert any("not found" in line for line in log_lines())


def test_malformed_json_uses_defaults(tmp_path, log_lines):
    path = tmp_path / "config.json"
    path.write_text("{ this is not json", encoding="utf-8")
    settings = load(str(path))
    assert settings.model == transcribe.DEFAULT_MODEL
    assert any("Failed to read" in line for line in log_lines())


def test_a_top_level_array_uses_defaults(config_file, log_lines):
    settings = load(config_file(["rctrl"]))
    assert settings.hotkey == config.hotkey_mod.DEFAULT_HOTKEY
    assert any("not an object" in line for line in log_lines())


# -- round trip and unknown keys --------------------------------------------

def test_round_trip(config_file):
    path = config_file({"version": 1, "use_gpu": False, "hotkey": ["lalt", "lshift"],
                        "model": "small.en"})
    first = load(path)
    first.save()
    second = load(path)
    assert (second.use_gpu, second.hotkey, second.model) == \
           (first.use_gpu, first.hotkey, first.model)


def test_unknown_keys_survive_a_round_trip(config_file):
    """Acceptance criterion 8: a newer build's settings survive a rollback."""
    path = config_file({"future_setting": 42, "version": 1,
                        "use_gpu": True, "hotkey": ["rctrl"]})
    settings = load(path)
    assert settings.extra == {"future_setting": 42}
    settings.hotkey = ("rshift",)
    settings.save()
    assert written(path)["future_setting"] == 42
    assert written(path)["hotkey"] == ["rshift"]


def test_a_known_key_wins_a_collision_with_a_preserved_unknown_one(config_file):
    """Known keys are serialised last, so a rolled-back build cannot shadow one."""
    settings = load(config_file({"use_gpu": True, "hotkey": ["rctrl"]}))
    settings.extra = {"use_gpu": "nonsense"}
    assert settings.to_dict()["use_gpu"] is True


def test_version_is_written_back_on_save_not_on_read(config_file):
    """Migration is lazy: loading a v0 file must not rewrite it."""
    path = config_file({"use_gpu": True})
    before = open(path, encoding="utf-8").read()
    settings = load(path)
    assert open(path, encoding="utf-8").read() == before
    settings.save()
    assert written(path)["version"] == config.CONFIG_VERSION


# -- version -----------------------------------------------------------------

def test_a_non_integer_version_falls_back(config_file, log_lines):
    settings = load(config_file({"version": "one"}))
    assert settings.version == config.CONFIG_VERSION
    assert any("version is not an integer" in line for line in log_lines())


# -- use_gpu -----------------------------------------------------------------

def test_use_gpu_round_trips(config_file):
    assert load(config_file({"use_gpu": False})).use_gpu is False


def test_use_gpu_as_a_string_falls_back_and_logs(config_file, log_lines):
    """
    "false" is a truthy string. Read naively it forces GPU on a machine that
    may not have one, which is why every field is checked by type.
    """
    settings = load(config_file({"use_gpu": "false"}))
    assert settings.use_gpu is True
    assert any("use_gpu is not a boolean" in line for line in log_lines())


@pytest.mark.parametrize("value", [1, 0, None, [], "true"])
def test_use_gpu_rejects_every_non_boolean(config_file, value):
    assert load(config_file({"use_gpu": value})).use_gpu is True


# -- hotkey ------------------------------------------------------------------

def test_a_valid_hotkey_loads(config_file):
    assert load(config_file({"hotkey": ["lalt", "lshift"]})).hotkey == ("lalt", "lshift")


def test_an_unsided_hotkey_loads_unchanged(config_file):
    """A hand-written unsided name must survive being read."""
    assert load(config_file({"hotkey": ["ctrl"]})).hotkey == ("ctrl",)


@pytest.mark.parametrize("value, reason", [
    (["banana"], "unknown key names"),
    ([], "empty"),
    ("rctrl", "not a list"),
])
def test_an_invalid_hotkey_falls_back_and_logs_the_reason(
        config_file, log_lines, value, reason):
    settings = load(config_file({"hotkey": value}))
    assert settings.hotkey == config.hotkey_mod.DEFAULT_HOTKEY
    assert any(reason in line for line in log_lines()), reason


def test_a_four_key_hotkey_is_accepted(config_file):
    """The three-key cap is the picker's rule, not the file format's."""
    chord = ["lctrl", "lshift", "lalt", "space"]
    assert load(config_file({"hotkey": chord})).hotkey == tuple(chord)


# -- model -------------------------------------------------------------------

def test_a_known_model_loads(config_file):
    assert load(config_file({"model": "small.en"})).model == "small.en"


def test_an_unknown_model_falls_back_and_logs(config_file, log_lines):
    """
    Handing an unrecognised name through would make faster-whisper try to fetch
    it from Hugging Face by that name.
    """
    settings = load(config_file({"model": "enormous-v9"}))
    assert settings.model == transcribe.DEFAULT_MODEL
    assert any("is not one of" in line for line in log_lines())


@pytest.mark.parametrize("value", [7, None, ["small.en"], {"name": "small.en"}])
def test_a_non_string_model_falls_back_and_logs(config_file, log_lines, value):
    settings = load(config_file({"model": value}))
    assert settings.model == transcribe.DEFAULT_MODEL
    assert any("model is not a string" in line for line in log_lines())


def test_every_catalogue_name_is_accepted(config_file):
    for name in transcribe.MODEL_NAMES:
        assert load(config_file({"model": name})).model == name


# -- benchmarks --------------------------------------------------------------

GOOD = {"seconds": 1.18, "at": "2026-08-23T14:22:07", "clip": "1b00eade0c24"}


def test_a_valid_benchmark_loads(config_file):
    settings = load(config_file({"benchmarks": {"large-v3-turbo|cuda": GOOD}}))
    entry = settings.benchmarks["large-v3-turbo|cuda"]
    assert entry["seconds"] == 1.18
    assert entry["clip"] == "1b00eade0c24"


def test_a_non_object_benchmarks_value_is_ignored_and_logged(config_file, log_lines):
    settings = load(config_file({"benchmarks": ["1.18"]}))
    assert settings.benchmarks == {}
    assert any("benchmarks is not an object" in line for line in log_lines())


@pytest.mark.parametrize("entry", [
    "1.18",                                   # not an object
    {},                                       # no seconds
    {"seconds": 0},                           # not positive
    {"seconds": -2.0},                        # not positive
    {"seconds": True},                        # a bool is an int in Python
    {"seconds": "1.18"},                      # a string that looks numeric
])
def test_a_bad_benchmark_entry_is_dropped_and_logged(config_file, log_lines, entry):
    settings = load(config_file({"benchmarks": {"tiny.en|cpu": entry}}))
    assert settings.benchmarks == {}
    assert any("benchmarks[" in line for line in log_lines())


def test_a_bad_entry_does_not_take_the_good_ones_with_it(config_file):
    """Entries are validated individually; one bad row is not a corrupt cache."""
    settings = load(config_file({"benchmarks": {
        "tiny.en|cpu": {"seconds": -1},
        "large-v3-turbo|cuda": GOOD,
    }}))
    assert list(settings.benchmarks) == ["large-v3-turbo|cuda"]


def test_benchmarks_survive_a_round_trip(config_file):
    path = config_file({"benchmarks": {"large-v3-turbo|cuda": GOOD}})
    settings = load(path)
    settings.save()
    assert load(path).benchmarks == settings.benchmarks


# -- save --------------------------------------------------------------------

def test_save_never_raises_on_an_unwritable_path(tmp_path, log_lines):
    """
    A read-only disk must not take the application down mid-dictation, and with
    instant-apply this runs on every click.
    """
    settings = config.Settings(path=str(tmp_path))      # a directory, not a file
    settings.save()                                     # must not raise
    assert any("Failed to save" in line for line in log_lines())


def test_a_save_that_fails_mid_write_leaves_the_previous_file_intact(tmp_path):
    """
    The atomic write, tested where it actually matters.

    `"w"` truncates on open, so a serialisation failure part-way through left a
    truncated config.json behind. `load` handles that correctly by falling back,
    which means the user's symptom is not a crash -- it is their settings
    silently resetting, the exact failure OBS-3 exists to prevent.

    An unserialisable value in `extra` is the cheapest way to fail *after* the
    point where the old code had already truncated the target. Failing on a
    missing directory does not exercise this: that raises at `open`, before any
    truncation, and passes against either implementation.
    """
    path = tmp_path / "config.json"
    settings = config.Settings(path=str(path))
    settings.hotkey = ("rshift",)
    settings.save()
    original = path.read_text(encoding="utf-8")

    settings.extra = {"unserialisable": object()}
    settings.save()                                  # must not raise

    assert path.read_text(encoding="utf-8") == original
    assert config.load(str(path)).hotkey == ("rshift",)


def test_a_failed_save_leaves_no_temporary_file_behind(tmp_path):
    """A half-written temp file would sit beside config.json looking like one."""
    settings = config.Settings(path=str(tmp_path / "config.json"))
    settings.save()
    settings.extra = {"unserialisable": object()}
    settings.save()
    assert [f for f in os.listdir(tmp_path) if f.endswith(".tmp")] == []


def test_a_successful_save_leaves_no_temporary_file_behind(tmp_path):
    """The temp file is moved into place, not copied and abandoned."""
    settings = config.Settings(path=str(tmp_path / "config.json"))
    settings.save()
    assert (tmp_path / "config.json").exists()
    assert [f for f in os.listdir(tmp_path) if f.endswith(".tmp")] == []
