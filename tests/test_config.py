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

from ptt import config, transcribe, vocabulary


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


def test_the_defaults_are_the_behaviour_of_the_build_before_this_one(tmp_path):
    """
    Acceptance criterion 8's other half. Every setting added for the Audio and
    Vocabulary panels defaults to what the application did before those panels
    existed, so a config.json written by any earlier build -- which says nothing
    about any of them -- behaves identically after an upgrade.
    """
    settings = load(str(tmp_path / "nope.json"))
    assert settings.audio_device is None          # follow the Windows default
    assert settings.keep_stream_warm is True      # NFR-2, NFR-4
    assert settings.ignore_short_holds is True    # FR-3
    assert settings.start_click is False          # a new capability, off
    assert settings.vocabulary == ()


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


def test_an_unknown_key_survives_beside_every_setting_this_build_owns(config_file):
    """
    Acceptance criterion 8, against the whole schema rather than three keys of
    it. Each session adds fields to `_KNOWN_KEYS`, and a key omitted from that
    tuple is silently duplicated -- once from `extra` and once from `to_dict` --
    so this checks that the file that comes back out has one of each.
    """
    path = config_file({
        "future_setting": 42,
        "version": 1, "use_gpu": True, "hotkey": ["rctrl"], "model": "small.en",
        "audio_device": 3, "keep_stream_warm": False, "ignore_short_holds": False,
        "start_click": True,
        "vocabulary": [{"heard": "w s l", "typed": "WSL", "scope": "always"}],
    })
    settings = load(path)
    assert settings.extra == {"future_setting": 42}
    settings.save()

    raw = written(path)
    assert raw["future_setting"] == 42
    assert raw["audio_device"] == 3
    assert raw["keep_stream_warm"] is False
    assert raw["start_click"] is True
    assert raw["vocabulary"] == [{"heard": "w s l", "typed": "WSL", "scope": "always"}]


def test_a_file_from_the_pre_gui_build_loads_and_saves_unchanged_in_meaning(config_file):
    """
    The other direction of acceptance criterion 8: a config.json written before
    any of this existed names none of the new keys, and loading it must not
    change what the application does.
    """
    path = config_file({"version": 1, "use_gpu": True, "hotkey": ["rctrl"]})
    settings = load(path)
    settings.save()

    raw = written(path)
    assert raw["audio_device"] is None
    assert raw["keep_stream_warm"] is True
    assert raw["ignore_short_holds"] is True
    assert raw["start_click"] is False
    assert raw["vocabulary"] == []


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


# -- audio_device ------------------------------------------------------------

def test_a_device_index_round_trips(config_file):
    assert load(config_file({"audio_device": 3})).audio_device == 3


def test_a_null_device_means_the_system_default(config_file, log_lines):
    """
    The value every configuration written before this build carries by
    omission, so it must load silently rather than being treated as a fallback.
    """
    settings = load(config_file({"audio_device": None}))
    assert settings.audio_device is None
    assert not any("audio_device" in line and "default" in line
                   for line in log_lines())


def test_device_zero_is_a_real_device_and_not_a_falsy_none(config_file):
    """PortAudio numbers from zero, so a truthiness test here loses a device."""
    assert load(config_file({"audio_device": 0})).audio_device == 0


@pytest.mark.parametrize("value", ["2", 2.5, [], {}, "default"])
def test_a_non_integer_device_falls_back_and_logs(config_file, log_lines, value):
    settings = load(config_file({"audio_device": value}))
    assert settings.audio_device is None
    assert any("audio_device is not an integer" in line for line in log_lines())


def test_a_boolean_device_falls_back_and_logs(config_file, log_lines):
    """`True` is an `int` in Python and would otherwise be accepted as device 1."""
    settings = load(config_file({"audio_device": True}))
    assert settings.audio_device is None
    assert any("audio_device is not an integer" in line for line in log_lines())


def test_a_negative_device_falls_back_and_logs(config_file, log_lines):
    settings = load(config_file({"audio_device": -1}))
    assert settings.audio_device is None
    assert any("audio_device is negative" in line for line in log_lines())


# -- the audio behaviour booleans --------------------------------------------

@pytest.mark.parametrize("key, default", [
    ("keep_stream_warm", True),
    ("ignore_short_holds", True),
    ("start_click", False),
])
def test_each_behaviour_flag_round_trips(config_file, key, default):
    assert getattr(load(config_file({key: not default})), key) is (not default)
    assert getattr(load(config_file({key: default})), key) is default


@pytest.mark.parametrize("key, default", [
    ("keep_stream_warm", True),
    ("ignore_short_holds", True),
    ("start_click", False),
])
@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, []])
def test_a_non_boolean_flag_falls_back_and_logs(
        config_file, log_lines, key, default, value):
    """
    "false" is a truthy string. Read naively, `ignore_short_holds` would be
    switched *on* by a file trying to switch it off -- or, worse for the two
    that default to True, off by a `0` someone meant as False and got right
    only by accident.
    """
    settings = load(config_file({key: value}))
    assert getattr(settings, key) is default
    assert any(f"{key} is not a boolean" in line for line in log_lines())


# -- vocabulary ---------------------------------------------------------------

RULE = {"heard": "see translate two", "typed": "ctranslate2", "scope": "always"}


def test_a_valid_rule_loads(config_file):
    settings = load(config_file({"vocabulary": [RULE]}))
    assert settings.vocabulary == (
        vocabulary.Rule("see translate two", "ctranslate2", "always"),
    )


def test_the_vocabulary_is_a_tuple_not_a_list(config_file):
    """
    `Settings` holds values that are replaced wholesale; the engine reads this
    one on the transcription path while the panel writes it. A list would make
    `settings.vocabulary.append(...)` look reasonable, which is the mutation
    `config.Settings`' docstring forbids.
    """
    assert isinstance(load(config_file({"vocabulary": [RULE]})).vocabulary, tuple)


def test_a_non_list_vocabulary_is_ignored_and_logged(config_file, log_lines):
    settings = load(config_file({"vocabulary": {"heard": "x"}}))
    assert settings.vocabulary == ()
    assert any("vocabulary is not a list" in line for line in log_lines())


@pytest.mark.parametrize("entry, reason", [
    ("see translate two",                      "not an object"),
    ({"typed": "ctranslate2"},                 "heard is not a string"),
    ({"heard": "", "typed": "x"},              "heard is empty"),
    ({"heard": "  ", "typed": "x"},            "heard is empty"),
    ({"heard": 7, "typed": "x"},               "heard is not a string"),
    ({"heard": "x", "typed": 7},               "typed is not a string"),
    ({"heard": "x", "typed": "y", "scope": "editors"}, "not one of"),
])
def test_a_bad_rule_is_dropped_and_logged(config_file, log_lines, entry, reason):
    settings = load(config_file({"vocabulary": [entry]}))
    assert settings.vocabulary == ()
    assert any("vocabulary[0]" in line and reason in line for line in log_lines())


def test_a_bad_rule_does_not_take_the_good_ones_with_it(config_file):
    """Validated per entry, like benchmarks: one bad row is not a corrupt list."""
    settings = load(config_file({"vocabulary": [{"heard": ""}, RULE]}))
    assert [r.heard for r in settings.vocabulary] == ["see translate two"]


def test_an_unknown_scope_is_dropped_rather_than_widened_to_always(
        config_file, log_lines):
    """
    The rule is not applied at all. A fallback that made a rule scoped to one
    application fire everywhere would be a behaviour change dressed as a
    default, which is the opposite of what every other fallback here does.
    """
    settings = load(config_file({"vocabulary": [
        {"heard": "x", "typed": "y", "scope": "editors"}]}))
    assert settings.vocabulary == ()
    assert any("scope 'editors'" in line for line in log_lines())


def test_a_rule_missing_its_scope_loads_as_always(config_file):
    settings = load(config_file({"vocabulary": [{"heard": "x", "typed": "y"}]}))
    assert settings.vocabulary[0].scope == vocabulary.SCOPE_ALWAYS


def test_the_vocabulary_survives_a_round_trip(config_file):
    path = config_file({"vocabulary": [RULE, {"heard": "w s l", "typed": "WSL"}]})
    settings = load(path)
    settings.save()
    assert load(path).vocabulary == settings.vocabulary


def test_rule_order_survives_a_round_trip(config_file):
    """
    Order is not cosmetic: two phrases of the same length are applied in the
    order they appear, so reordering them on save would change what is typed.
    """
    rules = [{"heard": "abc", "typed": "first"}, {"heard": "xyz", "typed": "second"}]
    path = config_file({"vocabulary": rules})
    settings = load(path)
    settings.save()
    assert [r.typed for r in load(path).vocabulary] == ["first", "second"]


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


# -- FIELDS: the declarative schema, and its three consumers (D-CG-13) --------
#
# `V-CF-15` and `V-CF-16`. The point of these is not that validation works --
# every test above already checks that -- but that there is exactly **one**
# declaration of it. `load()`, `Settings.set()` and the Concierge's generated
# tool schema all read `FIELDS`, and the mutation recorded in
# `verification.md` section 4.1 is what proves a private copy of a rule fails.

def test_every_settings_field_has_a_fields_entry():
    """
    A field on the dataclass with no rule is a field nothing validates.

    Both directions, because either gap is a real defect: an entry with no field
    is a key `Settings.set` would accept and `to_dict` would drop.
    """
    declared = set(config.FIELDS)
    on_object = set()
    for name in config.Settings.__dataclass_fields__:
        if name in ("extra", "path"):
            continue
        if name == "concierge":
            on_object |= {
                "concierge." + sub
                for sub in config.ConciergeSettings.__dataclass_fields__
                if sub != "extra"
            }
            continue
        on_object.add(name)
    assert declared == on_object


def test_the_fields_defaults_are_the_dataclass_defaults():
    """
    The table's `default` is what the log line quotes when a value is rejected.

    A table that says one thing and a dataclass that does another produces the
    worst possible OBS-3 line: a reason, and the wrong value beside it.
    """
    fresh = config.Settings(path="unused.json")
    for key, rule in config.FIELDS.items():
        assert fresh.get(key) == rule.default, key


def test_load_and_set_reject_the_same_value_for_the_same_reason():
    """
    The two consumers, on one declaration.

    `load` falls back and logs; `set` refuses and reports. Both must be reading
    the same rule, which is checkable by giving each the same bad value and
    comparing the words that come back.
    """
    settings = config.Settings(path="unused.json")
    for key, bad in [("use_gpu", "false"), ("model", "enormous"),
                     ("audio_device", -1), ("concierge.opt_in", "maybe"),
                     ("concierge.idle_unload_minutes", 45)]:
        rule = config.FIELDS[key]
        _, load_defect = rule.check(bad, note="config.json")
        ok, set_reason = settings.set(key, bad)
        assert not ok, key
        assert load_defect and load_defect in set_reason, key


def test_the_settable_allowlist_excludes_vocabulary_and_benchmarks():
    """
    The scope exclusion is an allowlist, not a sentence.

    `concierge_requirements.md` section 5 puts "editing vocabulary rules" out of
    scope for v3.0, and `set_config("vocabulary", ...)` reaches them unless the
    registry says otherwise (review section 1.2). `benchmarks` is excluded from
    the other side for the same reason: it is a measurement cache the harness
    writes, not a preference anyone states.
    """
    assert "vocabulary" not in config.WRITABLE_KEYS
    assert "benchmarks" not in config.WRITABLE_KEYS
    assert "version" not in config.WRITABLE_KEYS
    assert "vocabulary" in config.READABLE_KEYS
    assert set(config.WRITABLE_KEYS) <= set(config.READABLE_KEYS)


def test_every_documented_field_documents_itself():
    """
    The pack's per-setting half is generated from this prose, so a field with
    none produces a pack entry that says nothing -- and FR-CG-1 is scored on
    exactly that half.
    """
    for key, rule in config.FIELDS.items():
        if rule.internal:
            continue
        assert rule.does, key
        assert rule.when, key
        assert rule.risk, key


def test_set_writes_the_value_and_persists_it(tmp_path, log_lines):
    settings = config.Settings(path=str(tmp_path / "config.json"))
    ok, reason = settings.set("model", "small.en")
    assert (ok, reason) == (True, None)
    assert settings.model == "small.en"
    assert config.load(str(tmp_path / "config.json")).model == "small.en"
    assert any("Set model" in line for line in log_lines())


def test_a_refused_write_changes_nothing_and_saves_nothing(tmp_path, log_lines):
    """
    FR-CG-11's shape. The path this replaced accepted the value, wrote it to
    disk, and reverted it at the next start -- a rejection reported as success.
    """
    path = tmp_path / "config.json"
    settings = config.Settings(path=str(path))
    settings.set("model", "small.en")
    before = path.read_text(encoding="utf-8")

    ok, reason = settings.set("model", "enormous")
    assert ok is False
    assert "is not one of" in reason
    assert settings.model == "small.en"
    assert path.read_text(encoding="utf-8") == before
    assert any("Rejected write" in line for line in log_lines())


def test_the_spikes_own_case_is_refused(tmp_path):
    """
    set_config with key use_gpu and the **string** "false" -- from spike C2's
    30/30, recorded there as a clean call. config.py exists to reject it.
    """
    settings = config.Settings(path=str(tmp_path / "config.json"))
    ok, reason = settings.set("use_gpu", "false")
    assert ok is False
    assert "is not a boolean" in reason
    assert settings.use_gpu is True


def test_an_unknown_key_is_refused_rather_than_created(tmp_path):
    settings = config.Settings(path=str(tmp_path / "config.json"))
    ok, reason = settings.set("turbo_mode", True)
    assert ok is False and "not a setting" in reason
    assert not hasattr(settings, "turbo_mode")


def test_the_version_key_is_not_writable(tmp_path):
    settings = config.Settings(path=str(tmp_path / "config.json"))
    ok, reason = settings.set("version", 99)
    assert ok is False and "not writable" in reason


def test_set_rebinds_whole_values_rather_than_mutating_them(tmp_path):
    """
    design.md section 7's field discipline, now binding tool code too.

    The engine reads `benchmarks` and `vocabulary` from another thread without a
    lock, which is safe only because a write is a rebind. This asserts the
    object the caller passed is not the object that ends up on `Settings`, so an
    in-place mutation of the caller's dict cannot reach a reader mid-write.
    """
    settings = config.Settings(path=str(tmp_path / "config.json"))
    incoming = {"tiny.en|cpu": {"seconds": 2.0, "at": "now", "clip": "abc"}}
    assert settings.set("benchmarks", incoming)[0] is True
    assert settings.benchmarks is not incoming
    incoming["tiny.en|cpu"]["seconds"] = 99.0
    assert settings.benchmarks["tiny.en|cpu"]["seconds"] == 2.0


def test_a_strict_write_refuses_a_partly_bad_collection(tmp_path):
    """
    The one place load and write legitimately differ, and it is a disposition
    rather than a second rule: reading a hand-edited file drops the bad rule and
    keeps the good ones (V-CF-13); writing is all or nothing, because a
    partially applied write reported as success is what FR-CG-11 forbids.
    """
    settings = config.Settings(path=str(tmp_path / "config.json"))
    good = {"heard": "w s l", "typed": "WSL", "scope": "always"}
    bad = {"heard": "", "typed": "x", "scope": "always"}
    ok, reason = settings.set("vocabulary", [good, bad])
    assert ok is False and "rule 1" in reason
    assert settings.vocabulary == ()


def test_override_validates_but_does_not_persist(tmp_path):
    """
    Hardware having the last word (FR-6) is not a save. A driver that is broken
    this morning must not cost the user the preference they chose.
    """
    path = tmp_path / "config.json"
    settings = config.Settings(path=str(path))
    settings.set("use_gpu", True)
    settings.override("use_gpu", False)
    assert settings.use_gpu is False
    assert config.load(str(path)).use_gpu is True

    ok, reason = settings.override("use_gpu", "false")
    assert ok is False and "is not a boolean" in reason


def test_benchmark_key_is_owned_by_config(tmp_path):
    """
    Moved here from the Model panel because the Concierge reports the same
    measurements and may not import a module that imports Qt (CON-CG-6). Two
    copies of a key format is the drift FIELDS exists to prevent, one level
    down.
    """
    assert config.benchmark_key("large-v3-turbo", "cuda") == "large-v3-turbo|cuda"


def test_a_benchmark_entry_records_whether_the_llm_was_resident(config_file):
    """
    Q23. Spike C5 measured a 1.46x Whisper penalty during active LLM decode, so
    a contended figure sitting in the Model tab beside a clean one looks
    comparable and is not. The condition is recorded with the number.
    """
    entry = {"seconds": 1.18, "at": "2026-08-25T10:00:00", "clip": "abc",
             "llm_resident": True}
    settings = load(config_file({"benchmarks": {"large-v3-turbo|cuda": entry}}))
    assert settings.benchmarks["large-v3-turbo|cuda"]["llm_resident"] is True

    older = {"seconds": 1.18, "at": "", "clip": ""}
    settings = load(config_file({"benchmarks": {"tiny.en|cpu": older}}))
    assert settings.benchmarks["tiny.en|cpu"]["llm_resident"] is False


# -- the concierge block ------------------------------------------------------

def test_a_pre_v3_config_arrives_opted_out_of_nothing(config_file):
    """
    Acceptance criterion v3-8. `enabled: true` cannot express "declined", and a
    file written before v3 must arrive `unset` rather than silently opted in.
    """
    settings = load(config_file({"version": 1, "use_gpu": True, "hotkey": ["rctrl"]}))
    assert settings.concierge.opt_in == "unset"
    assert settings.concierge.enabled is True
    assert settings.concierge.idle_unload_minutes == 5
    # The qualified default, set by gate 2.5 (2026-08-26) and not by hand.
    # It tracks `FIELDS` rather than repeating the literal, so a future
    # qualification run that changes the default does not silently make this
    # assertion a second copy of the rule (issue #12, `V-HK-01`).
    assert settings.concierge.tool_mode == config.FIELDS["concierge.tool_mode"].default
    assert settings.concierge.tool_mode == "native"


def test_the_concierge_block_round_trips(config_file):
    path = config_file({"concierge": {"opt_in": "accepted", "enabled": False,
                                      "idle_unload_minutes": 0}})
    settings = load(path)
    assert settings.concierge.opt_in == "accepted"
    assert settings.concierge.enabled is False
    assert settings.concierge.idle_unload_minutes == 0
    settings.save()
    assert load(path).concierge == settings.concierge


def test_there_is_no_port_key(config_file):
    """
    Q13. The port is pre-bound in Python at every launch and recorded in
    concierge_state.json; a configured one would be a setting that is wrong the
    moment something else takes the port.
    """
    assert not any(k.endswith(".port") for k in config.FIELDS)
    settings = load(config_file({}))
    assert "port" not in settings.to_dict()["concierge"]


def test_an_unknown_concierge_key_survives_a_round_trip(config_file):
    """Criterion 8's guarantee, one level down: a v3.1 key survives a v3 build."""
    path = config_file({"concierge": {"opt_in": "declined", "future_tier": "24gb"}})
    settings = load(path)
    assert settings.concierge.extra == {"future_tier": "24gb"}
    settings.save()
    assert written(path)["concierge"]["future_tier"] == "24gb"
    assert written(path)["concierge"]["opt_in"] == "declined"


def test_a_non_object_concierge_block_falls_back_and_logs(config_file, log_lines):
    settings = load(config_file({"concierge": ["accepted"]}))
    assert settings.concierge.opt_in == "unset"
    assert any("concierge is not an object" in line for line in log_lines())


def test_a_bad_concierge_value_falls_back_field_by_field(config_file, log_lines):
    """One bad key does not take the block with it, exactly as at the top level."""
    settings = load(config_file({"concierge": {"opt_in": "maybe",
                                               "idle_unload_minutes": 99,
                                               "enabled": False}}))
    assert settings.concierge.opt_in == "unset"
    assert settings.concierge.idle_unload_minutes == 5
    assert settings.concierge.enabled is False
    lines = log_lines()
    assert any("concierge.opt_in" in line for line in lines)
    assert any("concierge.idle_unload_minutes is above 30" in line for line in lines)


def test_the_residency_slider_accepts_its_whole_range(tmp_path):
    settings = config.Settings(path=str(tmp_path / "config.json"))
    for minutes in (0, 1, 5, 30):
        assert settings.set("concierge.idle_unload_minutes", minutes)[0] is True
    assert settings.set("concierge.idle_unload_minutes", 31)[0] is False
    assert settings.set("concierge.idle_unload_minutes", -1)[0] is False


def test_writing_a_concierge_key_rebinds_the_whole_block(tmp_path):
    """
    The block is a value, replaced wholesale, for the same reason `benchmarks`
    is: the harness reads it from a worker thread while the GUI writes it.
    """
    settings = config.Settings(path=str(tmp_path / "config.json"))
    before = settings.concierge
    settings.set("concierge.enabled", False)
    assert settings.concierge is not before
    assert before.enabled is True
    assert settings.concierge.enabled is False


# -- D-CG-13: one declaration, and a private copy is what must fail ----------
#
# The three tests above check that the rules *agree*. These three check the
# stronger thing the design element actually claims: that there is nowhere else
# for any consumer to read a rule from. Equality is not enough for that -- a
# hand-written tuple with today's values is equal to the derived one and drifts
# the first time a field is added -- so each of these either asserts identity
# with the declaration or changes the declaration and watches the consumer move.
#
# `verification.md` section 4.1 records the mutation these are written against.

def test_load_reads_the_fields_table_itself(monkeypatch, config_file, log_lines):
    """
    Consumer 1. Narrow one rule in `FIELDS` and `load()` must narrow with it.

    A private copy inside `load()` passes every other test in this file and
    fails this one, which is the whole point: the copy is wrong only *later*,
    when the table changes and the copy does not.
    """
    narrowed = dict(config.FIELDS)
    narrowed["model"] = config.FIELDS["model"]._replace(choices=("tiny.en",))
    monkeypatch.setattr(config, "FIELDS", narrowed)

    assert load(config_file({"model": "tiny.en"})).model == "tiny.en"
    settings = load(config_file({"model": "large-v3-turbo"}))
    assert settings.model == transcribe.DEFAULT_MODEL
    assert any("is not one of" in line for line in log_lines())


def test_set_reads_the_fields_table_itself(monkeypatch, tmp_path):
    """Consumer 2, the same way."""
    narrowed = dict(config.FIELDS)
    narrowed["model"] = config.FIELDS["model"]._replace(choices=("tiny.en",))
    monkeypatch.setattr(config, "FIELDS", narrowed)

    settings = config.Settings(path=str(tmp_path / "config.json"))
    assert settings.set("model", "tiny.en")[0] is True
    ok, reason = settings.set("model", "large-v3-turbo")
    assert ok is False and "is not one of" in reason


def test_a_new_setting_reaches_every_consumer_with_no_other_edit(monkeypatch, tmp_path):
    """
    Consumer 3, and the mutation's target. Add a field to the table and it must
    appear in the generated tool schema, in the settable allowlist and in the
    knowledge pack -- with no edit in `tools.py`, `llm.py` or
    `build_knowledge_pack.py`.

    A hand-written enum in any of those passes every equality check on the day
    it is written and fails here, which is the drift `V-HK-01` exists to
    prevent and issue #12 is the recorded cost of.
    """
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import build_knowledge_pack
    from ptt.concierge import llm, tools as tools_mod

    extended = dict(config.FIELDS)
    extended["daydream_mode"] = config.Field(
        "bool", False, does="A field that exists only in this test.",
        when="Never.", risk="Nothing; it is not real.")
    monkeypatch.setattr(config, "FIELDS", extended)
    monkeypatch.setattr(config, "WRITABLE_KEYS",
                        config.WRITABLE_KEYS + ("daydream_mode",))
    monkeypatch.setattr(config, "READABLE_KEYS",
                        config.READABLE_KEYS + ("daydream_mode",))

    registry = tools_mod.Registry(
        config.Settings(path=str(tmp_path / "config.json")))
    branches = llm.grammar_schema(registry)["oneOf"][1]["properties"]["tool"]["oneOf"]
    setter = next(b for b in branches
                  if b["properties"]["name"]["const"] == "set_config")
    assert "daydream_mode" in setter["properties"]["arguments"]["properties"]["key"]["enum"]

    array = llm.tools_array(registry)
    native = next(f for f in array if f["function"]["name"] == "set_config")
    assert "daydream_mode" in native["function"]["parameters"]["properties"]["key"]["enum"]

    assert "`daydream_mode`" in build_knowledge_pack.settings_section()
