"""
The poll loop: the live re-read, model reloads, benchmarks, and callback isolation.

These drive the real `Engine.run()` on a thread with the model, the microphone
and the keyboard replaced. `Engine.__init__`'s `chord_held` parameter exists for
exactly this -- its docstring calls it "a seam so the loop can be driven without
a keyboard in step 2's tests", and this is step 2.

Nothing here sleeps for a fixed period and hopes. `wait_for` polls a condition
with a deadline, so a slow machine makes the tests slower rather than flaky.
"""

import threading
import time

import numpy as np
import pytest

from ptt import audio as audio_mod
from ptt import config, engine as engine_mod, inject, transcribe, vocabulary


TIMEOUT = 5.0


def wait_for(predicate, timeout=TIMEOUT, what="condition"):
    """Block until `predicate()` is true, or fail saying what never happened."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    pytest.fail(f"timed out after {timeout}s waiting for {what}")


class FakeRecorder:
    """A Recorder that never touches PortAudio."""

    def __init__(self, samplerate=16_000, device=None, samples=None):
        self.samplerate = samplerate
        self.device = device
        self.device_name = ""
        self.level = 0.0
        self.recording = False
        self.opened = False
        self.opens = 0
        self._samples = samples if samples is not None else np.zeros(16_000, np.float32)

    @property
    def is_open(self):
        return self.opened

    def open_stream(self):
        if not self.opened:
            self.opens += 1
        self.opened = True
        self.device_name = f"fake device {self.device}"

    def close_stream(self):
        self.opened = False
        self.device_name = ""

    def start(self):
        # The real Recorder opens the stream itself when one is not already
        # open, which is what the warm-stream checkbox relies on.
        if not self.opened:
            self.open_stream()
        self.recording = True

    def stop(self):
        self.recording = False
        return self._samples


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """
    A running engine with every device replaced. Yields a small control panel.

    The engine thread is a daemon and is stopped in teardown, so a test that
    fails mid-loop cannot leave one running into the next test.
    """
    state = {
        "asked": [],            # every chord the loop has polled for
        "down": set(),          # which keys the fake keyboard reports held
        "states": [],           # every (state, status_text) emitted
        "texts": [],
        "benchmarks": [],
        "loads": [],            # every (model, use_gpu) the loader was asked for
        "rules": [],            # the vocabulary each transcription was given
        "clicks": [],           # every start-of-recording click played
    }
    recorder = FakeRecorder()

    def fake_load(model_size, use_gpu, cuda_supported, on_fallback=None):
        state["loads"].append((model_size, use_gpu))
        return object(), "cuda" if (use_gpu and cuda_supported) else "cpu", "Ready (CPU)"

    def fake_transcribe(model, audio, rules=()):
        state["rules"].append(tuple(rules))
        return "hello there"

    monkeypatch.setattr(transcribe, "load_model_with_fallback", fake_load)
    monkeypatch.setattr(transcribe, "transcribe_audio", fake_transcribe)
    monkeypatch.setattr(transcribe, "load_benchmark_clip",
                        lambda: np.zeros(16_000, np.float32))
    monkeypatch.setattr(audio_mod, "get_idle_duration", lambda: 0.0)
    monkeypatch.setattr(audio_mod, "Recorder",
                        lambda samplerate, device=None: recorder)
    monkeypatch.setattr(audio_mod, "play_start_click",
                        lambda: state["clicks"].append(1))
    monkeypatch.setattr(inject, "suppress_alt_menu", lambda: None)
    monkeypatch.setattr(inject, "target_accepts_keys", lambda: True)
    monkeypatch.setattr(inject, "paste_text", lambda text: state.setdefault(
        "pasted", []).append(text))
    monkeypatch.setattr(inject, "foreground_window_class", lambda: "TestClass")

    settings = config.Settings(path=str(tmp_path / "config.json"))

    def chord_held(chord):
        state["asked"].append(tuple(chord))
        return bool(chord) and all(k in state["down"] for k in chord)

    engine = engine_mod.Engine(
        settings, cuda_supported=False,
        on_state=lambda s, t: state["states"].append((s, t)),
        on_text=state["texts"].append,
        on_benchmark=lambda m, d, s: state["benchmarks"].append((m, d, s)),
        chord_held=chord_held,
    )
    thread = threading.Thread(target=engine.run, daemon=True)
    thread.start()
    wait_for(lambda: state["asked"], what="the loop to start polling")

    state["engine"] = engine
    state["settings"] = settings
    state["recorder"] = recorder
    yield state

    engine.stop()
    thread.join(timeout=TIMEOUT)


# -- the behaviour the whole hotkey feature rests on -------------------------

def test_hotkey_rebind_takes_effect_without_restart(harness):
    """
    Acceptance criterion 5, as a test.

    The loop re-reads `settings.hotkey` on every iteration and never caches it,
    which is what lets the picker rebind the chord while the engine runs. The
    write is a whole-tuple rebind, so a reader on another thread sees either the
    old tuple or the new one -- see `config.Settings`' docstring for why that is
    safe without a lock, and why freezing the dataclass would break it.
    """
    assert harness["asked"][-1] == ("rctrl",)

    harness["settings"].hotkey = ("rshift",)

    wait_for(lambda: harness["asked"][-1] == ("rshift",),
             what="the loop to pick up the new chord")


def test_the_loop_never_caches_the_chord(harness):
    """Rebinding twice in a row is picked up twice, not once."""
    for chord in (("rshift",), ("lctrl", "lalt"), ("space",)):
        harness["settings"].hotkey = chord
        wait_for(lambda c=chord: harness["asked"][-1] == c, what=f"{chord}")


# -- record, transcribe, paste ----------------------------------------------

def test_holding_the_chord_records_and_releasing_transcribes(harness):
    harness["down"].add("rctrl")
    wait_for(lambda: ("recording", "Recording...") in harness["states"],
             what="the recording state")
    assert harness["recorder"].recording is True

    harness["down"].clear()
    wait_for(lambda: harness["texts"] == ["hello there"], what="the transcript")
    assert harness["pasted"] == ["hello there"]
    assert ("transcribing", "Transcribing...") in harness["states"]


def test_a_tap_shorter_than_the_minimum_is_not_transcribed(harness, monkeypatch):
    """
    FR-3. A recording below MIN_RECORD_SEC is an accidental brush of the key,
    and running inference on it wastes seconds and pastes noise.
    """
    short = np.zeros(int(16_000 * engine_mod.MIN_RECORD_SEC / 2), np.float32)
    harness["recorder"]._samples = short

    harness["down"].add("rctrl")
    wait_for(lambda: harness["recorder"].recording, what="recording to start")
    harness["down"].clear()

    wait_for(lambda: harness["states"][-1][0] == "idle", what="a return to idle")
    assert harness["texts"] == []


# -- reload and benchmark ----------------------------------------------------

def test_request_model_reload_rebuilds_the_model(harness):
    before = len(harness["loads"])
    harness["settings"].model = "tiny.en"
    harness["engine"].request_model_reload()

    wait_for(lambda: len(harness["loads"]) > before, what="a reload")
    assert harness["loads"][-1][0] == "tiny.en"
    assert ("loading", "Loading Model...") in harness["states"]


def test_the_model_name_is_read_from_settings_at_reload_time(harness):
    """The same live re-read the chord gets, for the Model panel's selection."""
    harness["settings"].model = "base.en"
    harness["engine"].request_model_reload()
    wait_for(lambda: harness["loads"][-1][0] == "base.en", what="base.en to load")

    harness["settings"].model = "small.en"
    harness["engine"].request_model_reload()
    wait_for(lambda: harness["loads"][-1][0] == "small.en", what="small.en to load")


def test_request_benchmark_times_the_resident_model(harness):
    harness["engine"].request_benchmark()
    wait_for(lambda: harness["benchmarks"], what="a measurement")

    model, device, seconds = harness["benchmarks"][0]
    assert model == harness["settings"].model
    assert device == "cpu"
    assert seconds > 0


def test_benchmarking_reuses_the_transcribing_state(harness):
    """
    gui_handoff section 7's table needs no new row: the state stays
    `transcribing`, because that is what the engine is doing. Only the status
    text is new, and it comes from the engine.
    """
    harness["engine"].request_benchmark()
    wait_for(lambda: harness["benchmarks"], what="a measurement")

    measuring = [s for s in harness["states"] if s[1].startswith("Measuring")]
    assert measuring, harness["states"]
    assert all(state == "transcribing" for state, _ in measuring)


def test_benchmarking_does_not_load_a_second_model(harness):
    """
    Two WhisperModels on one card is a plausible CUDA OOM, and a failure while
    measuring must not be able to take down the model dictation depends on.
    """
    before = len(harness["loads"])
    harness["engine"].request_benchmark()
    wait_for(lambda: harness["benchmarks"], what="a measurement")
    assert len(harness["loads"]) == before


def test_the_engine_returns_to_idle_after_a_benchmark(harness):
    harness["engine"].request_benchmark()
    wait_for(lambda: harness["states"][-1][0] == "idle", what="a return to idle")


# -- the input device --------------------------------------------------------

def test_the_loop_picks_up_a_new_input_device_without_restart(harness):
    """
    The same live re-read the chord gets. Switching microphone must not need a
    restart and must not reload the model -- the Audio panel calls `apply_now`
    with `reload_model=False` and relies entirely on this.
    """
    before = len(harness["loads"])
    harness["settings"].audio_device = 3

    wait_for(lambda: harness["recorder"].device == 3,
             what="the loop to pick up the new device")
    assert len(harness["loads"]) == before, "a device change reloaded the model"


def test_changing_the_device_reopens_the_stream(harness):
    """
    A device chosen while the stream is open has to close it: PortAudio binds
    the device when the stream is created, so a running stream would keep
    recording from the old microphone until something else happened to close it.
    """
    wait_for(lambda: harness["recorder"].opened, what="the stream to open")
    opens = harness["recorder"].opens

    harness["settings"].audio_device = 2
    wait_for(lambda: harness["recorder"].opens > opens, what="a reopen")
    assert harness["recorder"].device == 2


def test_a_device_chosen_mid_recording_applies_to_the_next_one(harness):
    """
    PortAudio binds the device when the stream is created, so a change made
    while the hotkey is held cannot affect the recording in progress. It must
    still be picked up afterwards -- rebinding it during the recording instead
    would leave the two indexes matching and the old stream open for ever.
    """
    wait_for(lambda: harness["recorder"].opened, what="the stream to open")
    harness["down"].add("rctrl")
    wait_for(lambda: harness["recorder"].recording, what="recording to start")

    harness["settings"].audio_device = 1
    opens = harness["recorder"].opens
    assert harness["recorder"].device is None, "the device changed mid-recording"

    harness["down"].clear()
    wait_for(lambda: harness["recorder"].device == 1,
             what="the device to be picked up after the release")
    wait_for(lambda: harness["recorder"].opens > opens, what="a reopen")


# -- the audio behaviour checkboxes ------------------------------------------

def test_the_warm_stream_holds_the_device_open_between_recordings(harness):
    """NFR-2: the stream is open before the user presses anything (issue #6)."""
    wait_for(lambda: harness["recorder"].opened, what="the stream to open")


def test_turning_the_warm_stream_off_releases_the_device_when_idle(harness):
    """
    Off means the microphone is not held: the loop closes it, and `rec.start()`
    opens it again for the recording itself. A threshold of zero would instead
    close and reopen it on every poll iteration, which is issue #6 at 50 Hz.
    """
    wait_for(lambda: harness["recorder"].opened, what="the stream to open")
    harness["settings"].keep_stream_warm = False
    wait_for(lambda: not harness["recorder"].opened, what="the stream to close")


def test_a_recording_still_works_with_the_warm_stream_off(harness):
    """The cost of turning it off is latency, not a broken hotkey."""
    harness["settings"].keep_stream_warm = False
    wait_for(lambda: not harness["recorder"].opened, what="the stream to close")

    harness["down"].add("rctrl")
    wait_for(lambda: harness["recorder"].recording, what="recording to start")
    assert harness["recorder"].opened, "start() did not open the stream"

    harness["down"].clear()
    wait_for(lambda: harness["texts"] == ["hello there"], what="the transcript")
    # And the loop closes it again rather than leaving it open for ever, which
    # is what happens if the loop's own flag never learns about start()'s open.
    wait_for(lambda: not harness["recorder"].opened,
             what="the stream to be released again")


def test_turning_the_minimum_hold_off_transcribes_a_short_tap(harness):
    """FR-3 is a default, not a law -- but switching it off is a saved setting."""
    harness["recorder"]._samples = np.zeros(
        int(16_000 * engine_mod.MIN_RECORD_SEC / 2), np.float32)
    harness["settings"].ignore_short_holds = False

    harness["down"].add("rctrl")
    wait_for(lambda: harness["recorder"].recording, what="recording to start")
    harness["down"].clear()

    wait_for(lambda: harness["texts"] == ["hello there"],
             what="the short tap to be transcribed")


def test_an_empty_recording_is_never_transcribed(harness):
    """
    Turning the minimum hold off means "do not discard short recordings", not
    "hand an empty array to inference" -- which is the one input the model has
    no answer for.
    """
    harness["recorder"]._samples = np.empty(0, np.float32)
    harness["settings"].ignore_short_holds = False

    harness["down"].add("rctrl")
    wait_for(lambda: harness["recorder"].recording, what="recording to start")
    harness["down"].clear()

    wait_for(lambda: harness["states"][-1][0] == "idle", what="a return to idle")
    assert harness["texts"] == []


def test_the_start_click_plays_only_when_it_is_switched_on(harness):
    harness["down"].add("rctrl")
    wait_for(lambda: harness["recorder"].recording, what="recording to start")
    harness["down"].clear()
    wait_for(lambda: harness["texts"], what="the transcript")
    assert harness["clicks"] == []

    harness["settings"].start_click = True
    harness["down"].add("rctrl")
    wait_for(lambda: harness["clicks"], what="the click")


# -- the vocabulary ----------------------------------------------------------

def test_the_vocabulary_is_read_from_settings_at_transcription_time(harness):
    """
    The rules reach `transcribe_audio`, which is where the substitution happens
    -- the only point that is both after `clean_text` and before `paste_text`.
    Read live, like the chord, so an edit applies to the next thing said.
    """
    rules = (vocabulary.Rule("w s l", "WSL"),)
    harness["settings"].vocabulary = rules

    harness["down"].add("rctrl")
    wait_for(lambda: harness["recorder"].recording, what="recording to start")
    harness["down"].clear()
    wait_for(lambda: harness["rules"], what="a transcription")

    assert harness["rules"][-1] == rules


# -- what the diagnostics panel reads ----------------------------------------

def test_the_engine_remembers_what_the_last_dictation_cost(harness):
    """
    Both figures are already logged. They are kept as well, because OBS-4
    guarantees debug_log.txt is plain text and not that any line in it has a
    stable format -- a panel that parsed them back out would break silently the
    first time a message was reworded.
    """
    assert harness["engine"].median_latency() is None

    harness["down"].add("rctrl")
    wait_for(lambda: harness["recorder"].recording, what="recording to start")
    harness["down"].clear()
    wait_for(lambda: harness.get("pasted"), what="a paste")

    assert harness["engine"].median_latency() is not None
    assert harness["engine"].last_paste_target == "TestClass"
    assert "words" in harness["engine"].last_summary


def test_the_latency_history_is_capped(harness):
    """A median over the last twenty, not over the whole session."""
    engine = harness["engine"]
    engine._record_transcription(1.0, "one two three")
    for _ in range(engine_mod.LATENCY_SAMPLES + 5):
        engine._record_transcription(2.0, "one two three")
    assert len(engine._latencies) == engine_mod.LATENCY_SAMPLES
    assert engine.median_latency() == 2.0


def test_the_level_and_device_readouts_tolerate_no_recorder(tmp_path):
    """
    The settings window can be built and shown before `run()` has made one, and
    a panel that raised there would take the window down over a meter.
    """
    settings = config.Settings(path=str(tmp_path / "config.json"))
    engine = engine_mod.Engine(settings, cuda_supported=False, on_state=lambda s, t: None)
    assert engine.input_level() == 0.0
    assert engine.input_device_name() == ""
    assert engine.stream_is_open() is False
    assert engine.median_latency() is None


# -- the frontend cannot kill the loop ---------------------------------------

def test_a_raising_state_callback_does_not_kill_the_poll_loop(monkeypatch, tmp_path):
    """
    `_emit`'s contract. A frontend bug must not strand `recording=True` with the
    microphone live -- which is why the callback is wrapped and only logged.
    """
    monkeypatch.setattr(
        transcribe, "load_model_with_fallback",
        lambda m, g, c, on_fallback=None: (object(), "cpu", "Ready (CPU)"))
    monkeypatch.setattr(audio_mod, "get_idle_duration", lambda: 9999.0)
    monkeypatch.setattr(audio_mod, "Recorder",
                        lambda samplerate, device=None: FakeRecorder())

    asked = []
    settings = config.Settings(path=str(tmp_path / "config.json"))

    def exploding_on_state(state, status):
        raise RuntimeError("the frontend is broken")

    engine = engine_mod.Engine(
        settings, cuda_supported=False,
        on_state=exploding_on_state,
        chord_held=lambda chord: asked.append(tuple(chord)) or False,
    )
    thread = threading.Thread(target=engine.run, daemon=True)
    thread.start()
    try:
        wait_for(lambda: len(asked) > 3, what="the loop to keep polling")
    finally:
        engine.stop()
        thread.join(timeout=TIMEOUT)


def test_cuda_unsupported_forces_cpu_at_construction(tmp_path):
    """
    Hardware has the last word over the saved preference, and the rule lives in
    the engine so every frontend inherits it.
    """
    settings = config.Settings(path=str(tmp_path / "config.json"))
    settings.use_gpu = True
    engine_mod.Engine(settings, cuda_supported=False, on_state=lambda s, t: None)
    assert settings.use_gpu is False
