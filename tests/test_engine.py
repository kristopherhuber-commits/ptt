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
from ptt import config, engine as engine_mod, inject, transcribe


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

    def __init__(self, samplerate=16_000, samples=None):
        self.samplerate = samplerate
        self.recording = False
        self.opened = False
        self._samples = samples if samples is not None else np.zeros(16_000, np.float32)

    def open_stream(self):
        self.opened = True

    def close_stream(self):
        self.opened = False

    def start(self):
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
    }
    recorder = FakeRecorder()

    def fake_load(model_size, use_gpu, cuda_supported, on_fallback=None):
        state["loads"].append((model_size, use_gpu))
        return object(), "cuda" if (use_gpu and cuda_supported) else "cpu", "Ready (CPU)"

    monkeypatch.setattr(transcribe, "load_model_with_fallback", fake_load)
    monkeypatch.setattr(transcribe, "transcribe_audio", lambda model, audio: "hello there")
    monkeypatch.setattr(transcribe, "load_benchmark_clip",
                        lambda: np.zeros(16_000, np.float32))
    monkeypatch.setattr(audio_mod, "get_idle_duration", lambda: 0.0)
    monkeypatch.setattr(audio_mod, "Recorder", lambda samplerate: recorder)
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
    monkeypatch.setattr(audio_mod, "Recorder", lambda samplerate: FakeRecorder())

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
