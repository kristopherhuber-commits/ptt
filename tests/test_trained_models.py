"""
Models trained on the user's speech, listed beside the stock tiers (v4, FR-17).

The engine's side -- loading one from its folder, and falling back to stock when
it is missing -- is in test_engine.py.
"""

import json
import os

from ptt import config, paths, transcribe


def _model(folder, name, meta=None):
    path = folder / name
    path.mkdir(parents=True)
    (path / "model.bin").write_bytes(b"\0" * 10)
    if meta is not None:
        (path / transcribe.TRAINED_META).write_text(json.dumps(meta), encoding="utf-8")
    return path


def test_each_folder_with_a_model_is_listed_by_name(tmp_path):
    _model(tmp_path, "lora-b", {"created_utc": "2026-11-04T12:00:00Z", "clips_trained": 40})
    _model(tmp_path, "lora-a")
    (tmp_path / "half-converted").mkdir()
    (tmp_path / "notes.txt").write_text("not a model", encoding="utf-8")

    found = transcribe.trained_models(str(tmp_path))
    assert [m.name for m in found] == ["trained:lora-a", "trained:lora-b"]
    assert found[0].meta == {}
    assert found[1].meta["clips_trained"] == 40


def test_a_folder_that_is_not_there_has_no_models(tmp_path):
    assert transcribe.trained_models(str(tmp_path / "missing")) == ()


def test_a_trained_name_resolves_to_its_folder_or_to_nothing(tmp_path):
    path = _model(tmp_path, "lora-a")
    assert transcribe.trained_model_dir(str(tmp_path), "trained:lora-a") == str(path)
    assert transcribe.trained_model_dir(str(tmp_path), "trained:lora-z") is None


def test_a_model_directory_is_used_as_given_and_never_looked_up_by_name(tmp_path):
    path = _model(tmp_path, "lora-a")
    assert transcribe.resolve_model_path(str(path)) == str(path)


def test_a_trained_model_is_identified_by_its_name_and_training_time(tmp_path):
    _model(tmp_path, "lora-a", {"created_utc": "2026-11-04T12:00:00Z"})
    assert transcribe.model_identity("trained:lora-a", str(tmp_path)) == \
        ("trained:lora-a", "2026-11-04T12:00:00Z")


# -- the settings --------------------------------------------------------------

def test_the_model_setting_takes_a_trained_name(tmp_path):
    settings = config.Settings(path=str(tmp_path / "config.json"))
    assert settings.set("model", "trained:lora-20261104-2105") == (True, None)


def test_the_model_setting_still_refuses_an_unknown_name(tmp_path):
    settings = config.Settings(path=str(tmp_path / "config.json"))
    for bad in ("large-v9", "trained:", "trained:../escape"):
        ok, reason = settings.set("model", bad)
        assert not ok and "is not one of" in reason and "trained:<name>" in reason


def test_the_trained_models_folder_is_not_the_concierges_to_move():
    assert "trained_models_folder" not in config.WRITABLE_KEYS
    assert config.Settings().trained_models_folder == r"%USERPROFILE%\tools\ptt-train\models"


def test_the_trained_models_folder_expands_to_the_users_tools(monkeypatch):
    monkeypatch.setenv("USERPROFILE", r"C:\Users\someone")
    assert paths.user_folder(paths.DEFAULT_TRAINED_MODELS_FOLDER) == \
        os.path.abspath(r"C:\Users\someone\tools\ptt-train\models")
