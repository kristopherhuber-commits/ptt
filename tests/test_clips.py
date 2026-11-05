"""
Saving dictations for training (v4, FR-11): ids, layout, audio format, failures.

The engine's side -- that a dictation reaches the saver only while saving is on
-- is in test_engine.py.
"""

import datetime
import json
import os
import wave

import numpy as np

from ptt import clips, config, paths, transcribe


STARTED = datetime.datetime(2026, 11, 4, 15, 30, 12, 345678, tzinfo=datetime.timezone.utc)


def _record(samples, machine="darklord"):
    transcript = transcribe.Transcript(" cloud code", "Claude Code", ("cloud code -> Claude Code",), ())
    return clips.build_record(
        machine=machine, started=STARTED, samples=samples, transcript=transcript,
        latency_s=0.4567, mic="Microphone (USB)", app_version="4.0.0.dev1",
        model="mobiuslabsgmbh/faster-whisper-large-v3-turbo", model_revision="abc",
        compute_type="float16", beam_size=5, vad=True,
    )


def test_the_id_is_the_machine_and_the_utc_time_to_the_millisecond():
    assert clips.clip_id("darklord", STARTED) == "darklord-20261104T153012.345Z"


def test_the_record_holds_every_field_the_data_plan_lists():
    record = _record(np.zeros(24_000, np.float32))
    assert list(record) == [
        "id", "machine", "mic", "app_version", "model", "model_revision",
        "compute_type", "beam_size", "vad", "hint", "duration_s", "latency_s",
        "raw_text", "pasted_text", "rules_fired", "segments",
    ]
    assert record["duration_s"] == 1.5
    assert record["latency_s"] == 0.46
    assert record["raw_text"] == " cloud code"
    assert record["pasted_text"] == "Claude Code"
    json.dumps(record)


def test_a_clip_is_saved_in_the_machine_and_month_folder(tmp_path):
    samples = np.linspace(-1.0, 0.999, 16_000, dtype=np.float32)
    record = _record(samples)
    folder = clips.save(str(tmp_path), record, samples)

    assert folder == os.path.join(str(tmp_path), "darklord", "2026-11")
    lines = (tmp_path / "darklord" / "2026-11" / "recordings.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [record]

    wav_path = tmp_path / "darklord" / "2026-11" / "darklord-20261104T153012.345Z.wav"
    with wave.open(str(wav_path), "rb") as f:
        assert (f.getnchannels(), f.getsampwidth(), f.getframerate()) == (1, 2, 16_000)
        back = np.frombuffer(f.readframes(f.getnframes()), np.int16).astype(np.float32) / 32768.0
    assert np.max(np.abs(back - samples)) <= 1 / 32768


def test_the_recordings_file_is_only_appended_to(tmp_path):
    samples = np.zeros(1600, np.float32)
    clips.save(str(tmp_path), _record(samples), samples)
    clips.save(str(tmp_path), _record(samples, machine="darklord"), samples)
    lines = (tmp_path / "darklord" / "2026-11" / "recordings.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_full_scale_audio_does_not_wrap_around():
    pcm = clips.to_pcm16(np.array([-1.5, -1.0, 0.0, 1.0, 1.5], np.float32))
    assert pcm.tolist() == [-32768, -32768, 0, 32767, 32767]


def test_the_saver_writes_on_its_own_thread_and_logs_a_failure(tmp_path, log_lines):
    saver = clips.ClipSaver()
    samples = np.zeros(1600, np.float32)
    blocker = tmp_path / "blocker"
    blocker.write_text("not a folder", encoding="utf-8")

    saver.submit(str(tmp_path / "good"), _record(samples), samples)
    saver.submit(str(blocker), _record(samples), samples)
    saver.flush()

    assert (saver.saved, saver.failed) == (1, 1)
    assert any("darklord-20261104T153012.345Z not saved" in line for line in log_lines())


# -- flagging the newest clip (the double-tap, FR-13) ---------------------------

def _save_at(root, *minutes, month=11):
    """Save one clip per minute given, in that order; return their ids."""
    samples = np.zeros(1600, np.float32)
    ids = []
    for minute in minutes:
        record = _record(samples)
        started = datetime.datetime(2026, month, 4, 15, minute, 0, tzinfo=datetime.timezone.utc)
        record["id"] = clips.clip_id("darklord", started)
        clips.save(str(root), record, samples)
        ids.append(record["id"])
    return ids


def _flags(root, month="2026-11"):
    path = root / "darklord" / month / "flags.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_flag_marks_the_newest_clip_and_keeps_every_clip(tmp_path):
    first, second, third = _save_at(tmp_path, 1, 2, 3)
    assert clips.flag_last(str(tmp_path), "darklord") == (third, True)

    (flag,) = _flags(tmp_path)
    assert flag["id"] == third
    assert flag["flagged_at"].endswith("Z")
    folder = tmp_path / "darklord" / "2026-11"
    assert sorted(p.stem for p in folder.glob("*.wav")) == [first, second, third]
    assert len((folder / "recordings.jsonl").read_text(encoding="utf-8").splitlines()) == 3


def test_flagging_again_adds_nothing(tmp_path):
    (only,) = _save_at(tmp_path, 1)
    clips.flag_last(str(tmp_path), "darklord")
    assert clips.flag_last(str(tmp_path), "darklord") == (only, False)
    assert len(_flags(tmp_path)) == 1


def test_a_new_clip_is_the_next_one_flagged(tmp_path):
    (first,) = _save_at(tmp_path, 1)
    clips.flag_last(str(tmp_path), "darklord")
    (second,) = _save_at(tmp_path, 2)
    assert clips.flag_last(str(tmp_path), "darklord") == (second, True)
    assert [f["id"] for f in _flags(tmp_path)] == [first, second]


def test_the_newest_clip_may_be_in_an_earlier_month(tmp_path):
    (october,) = _save_at(tmp_path, 1, month=10)
    (tmp_path / "darklord" / "2026-11").mkdir()
    assert clips.flag_last(str(tmp_path), "darklord") == (october, True)
    assert [f["id"] for f in _flags(tmp_path, "2026-10")] == [october]


def test_there_is_nothing_to_flag_in_a_missing_folder(tmp_path):
    assert clips.flag_last(str(tmp_path / "nothing"), "darklord") is None


def test_the_saver_flags_after_the_saves_queued_before_it(tmp_path, log_lines):
    """A double-tap straight after a dictation flags that dictation."""
    _save_at(tmp_path, 1)
    saver = clips.ClipSaver()
    samples = np.zeros(1600, np.float32)
    record = _record(samples)
    saver.submit(str(tmp_path), record, samples)
    saver.flag_last(str(tmp_path), "darklord")
    saver.flush()

    assert (saver.saved, saver.flagged) == (1, 1)
    assert [f["id"] for f in _flags(tmp_path)] == [record["id"]]
    assert any(f"flagged {record['id']} as wrong" in line for line in log_lines())


# -- the two settings ---------------------------------------------------------

def test_saving_is_off_until_switched_on():
    assert config.Settings().save_clips is False
    assert config.FIELDS["save_clips"].default is False


def test_the_concierge_cannot_switch_saving_on_or_move_the_clips():
    """A clips folder moved somewhere synced would break CON-6."""
    assert "save_clips" not in config.WRITABLE_KEYS
    assert "clips_folder" not in config.WRITABLE_KEYS


def test_the_clips_folder_is_stored_unexpanded_and_expanded_when_used(monkeypatch):
    monkeypatch.setenv("USERPROFILE", r"C:\Users\someone")
    assert config.Settings().clips_folder == r"%USERPROFILE%\tools\ptt-v4-training\ptt-clips"
    assert paths.user_folder(paths.DEFAULT_CLIPS_FOLDER) == \
        os.path.abspath(r"C:\Users\someone\tools\ptt-v4-training\ptt-clips")


def test_an_empty_clips_folder_is_refused(tmp_path):
    settings = config.Settings(path=str(tmp_path / "config.json"))
    ok, reason = settings.set("clips_folder", "  ")
    assert not ok and reason == "clips_folder is empty"
    assert settings.clips_folder == paths.DEFAULT_CLIPS_FOLDER


def test_both_settings_survive_a_save_and_load(tmp_path):
    settings = config.Settings(path=str(tmp_path / "config.json"))
    assert settings.set("save_clips", True) == (True, None)
    assert settings.set("clips_folder", r"D:\clips") == (True, None)
    loaded = config.load(str(tmp_path / "config.json"))
    assert (loaded.save_clips, loaded.clips_folder) == (True, r"D:\clips")


def test_the_machine_name_is_lower_case(monkeypatch):
    monkeypatch.setenv("COMPUTERNAME", "DARKLORD")
    assert paths.machine_name() == "darklord"
