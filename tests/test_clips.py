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


# -- deleting the newest clip (the double-tap) ----------------------------------

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


def _left(root, month="2026-11"):
    folder = root / "darklord" / month
    records = [json.loads(line)["id"] for line in
               (folder / "recordings.jsonl").read_text(encoding="utf-8").splitlines()]
    wavs = sorted(p.stem for p in folder.glob("*.wav"))
    return records, wavs


def test_deleting_removes_the_newest_record_and_its_wav(tmp_path):
    first, second, third = _save_at(tmp_path, 1, 2, 3)
    assert clips.delete_last(str(tmp_path), "darklord") == third
    assert _left(tmp_path) == ([first, second], [first, second])


def test_deleting_again_walks_back_one_clip_at_a_time(tmp_path):
    first, second, third = _save_at(tmp_path, 1, 2, 3)
    assert clips.delete_last(str(tmp_path), "darklord") == third
    assert clips.delete_last(str(tmp_path), "darklord") == second
    assert _left(tmp_path) == ([first], [first])
    assert clips.delete_last(str(tmp_path), "darklord") == first
    assert _left(tmp_path) == ([], [])
    assert clips.delete_last(str(tmp_path), "darklord") is None


def test_deleting_crosses_into_the_previous_month(tmp_path):
    (october,) = _save_at(tmp_path, 1, month=10)
    (november,) = _save_at(tmp_path, 1)
    assert clips.delete_last(str(tmp_path), "darklord") == november
    assert clips.delete_last(str(tmp_path), "darklord") == october
    assert _left(tmp_path, "2026-10") == ([], [])


def test_there_is_nothing_to_delete_in_a_missing_folder(tmp_path):
    assert clips.delete_last(str(tmp_path / "nothing"), "darklord") is None


def test_a_record_whose_wav_is_already_gone_is_still_deleted(tmp_path):
    first, second = _save_at(tmp_path, 1, 2)
    os.remove(tmp_path / "darklord" / "2026-11" / f"{second}.wav")
    assert clips.delete_last(str(tmp_path), "darklord") == second
    assert _left(tmp_path) == ([first], [first])


def test_the_saver_deletes_after_the_saves_queued_before_it(tmp_path, log_lines):
    """A double-tap straight after a dictation deletes that dictation."""
    (first,) = _save_at(tmp_path, 1)
    saver = clips.ClipSaver()
    samples = np.zeros(1600, np.float32)
    record = _record(samples)
    saver.submit(str(tmp_path), record, samples)
    saver.delete_last(str(tmp_path), "darklord")
    saver.flush()

    assert (saver.saved, saver.deleted) == (1, 1)
    assert _left(tmp_path) == ([first], [first])
    assert any(f"deleted {record['id']}" in line for line in log_lines())


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
    assert config.Settings().clips_folder == r"%USERPROFILE%\tools\ptt-clips"
    assert paths.user_folder(paths.DEFAULT_CLIPS_FOLDER) == \
        os.path.abspath(r"C:\Users\someone\tools\ptt-clips")


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
