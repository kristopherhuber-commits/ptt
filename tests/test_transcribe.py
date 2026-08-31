"""
Text cleanup, the model catalogue, and the benchmark clip.

Nothing here loads a model. `transcribe.py` keeps `faster_whisper` and
`ctranslate2` out of module scope so the CUDA DLL directories are always
registered first (issue #1), and the side effect is that the module imports --
and so these tests run -- without either package present.
"""

import wave

import numpy as np
import pytest

from ptt import paths, transcribe, vocabulary


# -- clean_text: retrospective issue #4 -------------------------------------

def test_clean_text_strips_runs_of_full_stops():
    """
    large-v3 hallucinates runs of full stops on trailing silence; saying
    "testing one two three" could type "Testing .......".
    """
    assert transcribe.clean_text("Testing .......") == "Testing"


def test_clean_text_keeps_a_single_full_stop():
    assert transcribe.clean_text("That is all.") == "That is all."


def test_clean_text_strips_a_run_from_the_middle():
    assert transcribe.clean_text("one ... two") == "one  two"


def test_clean_text_trims_surrounding_whitespace():
    assert transcribe.clean_text("   hello   ") == "hello"


@pytest.mark.parametrize("value", ["", "   ", "...", ".."])
def test_clean_text_handles_input_with_nothing_in_it(value):
    assert transcribe.clean_text(value) == ""


# -- the model catalogue -----------------------------------------------------

def test_the_default_model_is_in_the_catalogue():
    assert transcribe.DEFAULT_MODEL in transcribe.MODEL_NAMES


def test_model_names_derive_from_the_catalogue():
    assert transcribe.MODEL_NAMES == tuple(m.name for m in transcribe.MODELS)


def test_model_names_are_unique():
    assert len(set(transcribe.MODEL_NAMES)) == len(transcribe.MODEL_NAMES)


def test_every_catalogue_row_is_fully_populated():
    """A blank cell would render as an empty column in the Model panel."""
    for info in transcribe.MODELS:
        assert info.name and info.params and info.disk and info.character


def test_disk_figures_are_marked_as_estimates():
    """
    The panel replaces these with the real byte count for anything on disk, so
    the two must never be confusable.
    """
    for info in transcribe.MODELS:
        assert info.disk.startswith("~"), info.name


# -- resolve_model_path ------------------------------------------------------

def test_resolve_model_path_returns_the_bare_name_when_nothing_is_bundled(
        monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "local_model_dir",
                        lambda name: str(tmp_path / "absent" / name))
    assert transcribe.resolve_model_path("small.en") == "small.en"


def test_resolve_model_path_prefers_a_bundled_directory(monkeypatch, tmp_path):
    bundled = tmp_path / "models" / "small.en"
    bundled.mkdir(parents=True)
    monkeypatch.setattr(paths, "local_model_dir", lambda name: str(tmp_path / "models" / name))
    assert transcribe.resolve_model_path("small.en") == str(bundled)


# -- the benchmark clip ------------------------------------------------------

def write_wav(path, channels=1, width=2, rate=16_000, frames=1600):
    with wave.open(str(path), "wb") as f:
        f.setnchannels(channels)
        f.setsampwidth(width)
        f.setframerate(rate)
        f.writeframes(b"\x00" * (frames * channels * width))
    return path


def test_the_bundled_clip_is_the_format_the_benchmark_expects():
    """It ships in the repo, so this asserts against the real file."""
    with wave.open(paths.asset_path("benchmark_sample.wav"), "rb") as f:
        assert (f.getnchannels(), f.getsampwidth(), f.getframerate()) == (1, 2, 16_000)


def test_load_benchmark_clip_returns_float32_in_range():
    """
    `Recorder.stop()` hands the engine float32 in [-1, 1) and a WAV is 16-bit,
    so something has to divide by 32768 -- this is it, and it is what makes the
    measured path identical to the dictation path from `transcribe_audio` in.
    """
    audio = transcribe.load_benchmark_clip()
    assert audio.dtype == np.float32
    assert audio.size > 0
    assert -1.0 <= float(audio.min()) and float(audio.max()) < 1.0


@pytest.mark.parametrize("kwargs", [
    {"rate": 8_000},        # wrong sample rate
    {"channels": 2},        # stereo
    {"width": 1},           # 8-bit
])
def test_load_benchmark_clip_refuses_the_wrong_format(monkeypatch, tmp_path, kwargs):
    """A benchmark that silently measured the wrong audio is worse than none."""
    wrong = write_wav(tmp_path / "wrong.wav", **kwargs)
    monkeypatch.setattr(paths, "asset_path", lambda *parts: str(wrong))
    with pytest.raises(ValueError):
        transcribe.load_benchmark_clip()


def test_benchmark_clip_id_is_stable():
    transcribe._clip_id = None
    first = transcribe.benchmark_clip_id()
    assert first
    assert transcribe.benchmark_clip_id() == first


def test_benchmark_clip_id_changes_with_the_clip(monkeypatch, tmp_path):
    """
    `record_sample.py` says re-recording the clip invalidates every cached
    measurement. This is what makes that enforceable instead of a comment.
    """
    transcribe._clip_id = None
    one = write_wav(tmp_path / "one.wav", frames=1600)
    monkeypatch.setattr(paths, "asset_path", lambda *parts: str(one))
    first = transcribe.benchmark_clip_id()

    transcribe._clip_id = None
    two = write_wav(tmp_path / "two.wav", frames=3200)
    monkeypatch.setattr(paths, "asset_path", lambda *parts: str(two))
    assert transcribe.benchmark_clip_id() != first


def test_benchmark_clip_id_is_empty_when_the_clip_is_missing(monkeypatch, tmp_path):
    transcribe._clip_id = None
    monkeypatch.setattr(paths, "asset_path", lambda *parts: str(tmp_path / "gone.wav"))
    assert transcribe.benchmark_clip_id() == ""


@pytest.fixture(autouse=True)
def reset_clip_id():
    """The digest is cached in a module global; leave it as we found it."""
    yield
    transcribe._clip_id = None


# -- installed_sizes ---------------------------------------------------------

def test_installed_sizes_reports_a_bundled_model_directory(monkeypatch, tmp_path):
    name = transcribe.MODEL_NAMES[0]
    bundled = tmp_path / name
    bundled.mkdir()
    (bundled / "model.bin").write_bytes(b"x" * 2048)
    monkeypatch.setattr(paths, "local_model_dir", lambda n: str(tmp_path / n))

    assert transcribe.installed_sizes()[name] == 2048


def test_installed_sizes_reports_nothing_when_no_model_is_present(monkeypatch, tmp_path):
    """
    The Hugging Face cache is consulted too, so this only asserts the bundled
    half: whatever the machine running the tests happens to have downloaded is
    not something a test may depend on.
    """
    monkeypatch.setattr(paths, "local_model_dir", lambda n: str(tmp_path / "empty" / n))
    sizes = transcribe.installed_sizes()
    assert all(v > 0 for v in sizes.values())
    assert set(sizes).issubset(set(transcribe.MODEL_NAMES))


# -- transcribe_audio: where the vocabulary is applied ------------------------

class FakeSegment:
    """One segment of a model's output. `transcribe_audio` reads only `.text`."""

    def __init__(self, text):
        self.text = text


class FakeModel:
    """
    A model that returns fixed segments and records how it was called.

    Enough of `WhisperModel` to exercise the substitution point without either
    heavy package present -- which is the whole reason `transcribe.py` keeps
    those imports inside functions.
    """

    def __init__(self, *texts):
        self._texts = texts
        self.kwargs = {}

    def transcribe(self, _audio, **kwargs):
        self.kwargs = kwargs
        return [FakeSegment(t) for t in self._texts], None


def test_transcribe_audio_joins_the_segments_and_cleans_them():
    model = FakeModel("Testing one two", " three .......")
    assert transcribe.transcribe_audio(model, None) == "Testing one two three"


def test_transcribe_audio_passes_the_flags_the_advanced_panel_reports():
    """
    The panel reads `BEAM_SIZE`, `VAD_FILTER` and `LANGUAGE` from this module
    and calls them the values in force; this is what makes that true.
    """
    model = FakeModel("x")
    transcribe.transcribe_audio(model, None)
    assert model.kwargs["beam_size"] == transcribe.BEAM_SIZE
    assert model.kwargs["vad_filter"] == transcribe.VAD_FILTER
    assert model.kwargs["language"] == transcribe.LANGUAGE
    assert model.kwargs["condition_on_previous_text"] is False


def test_the_vocabulary_is_applied_inside_transcribe_audio():
    """
    gui_handoff 6.4 puts substitution after `clean_text` and before
    `paste_text`. `clean_text` is called from in here, so this is the only
    point that is genuinely both.
    """
    model = FakeModel("run it in w s l")
    rules = (vocabulary.Rule("w s l", "WSL"),)
    assert transcribe.transcribe_audio(model, None, rules) == "run it in WSL"


def test_substitution_happens_after_the_cleanup_not_before():
    """
    Order matters and is testable: the rule below can only match once the run
    of full stops large-v3 leaves on trailing silence has been stripped.
    """
    model = FakeModel("say w s l...... now")
    rules = (vocabulary.Rule("w s l now", "WSL now"),)
    assert transcribe.transcribe_audio(model, None, rules) == "say WSL now"


def test_no_vocabulary_leaves_the_cleaned_text_alone():
    """The benchmark path passes no rules, so it measures the dictation path."""
    model = FakeModel("nothing to replace")
    assert transcribe.transcribe_audio(model, None) == "nothing to replace"


# -- a load that cannot even import ------------------------------------------

def test_an_unimportable_faster_whisper_is_reported_not_raised(monkeypatch):
    r"""
    The import lives inside the guard, not above it.

    `av\error.pyd` is unsigned, and Smart App Control refused to load it on one
    machine's first run of v3.0. Raised from above the `try`, that left this
    function entirely and took the caller's poll loop with it. It is a model
    load that failed and is reported as one, so the CPU/GPU contract -- a
    triple, never an exception -- holds for every failure and not just the ones
    CTranslate2 raises.
    """
    def blocked():
        raise ImportError("DLL load failed while importing error: "
                          "a code integrity policy blocked the file")

    monkeypatch.setattr(transcribe, "_whisper_model_cls", blocked)
    model, device, status = transcribe.load_model_with_fallback(
        "large-v3", use_gpu=True, cuda_supported=True)
    assert model is None
    assert device == "cuda"
    assert status == "Error loading model"


def test_an_unimportable_faster_whisper_reports_cpu_when_cpu_was_asked_for(
        monkeypatch):
    """The reported device is the one that was attempted, not a guess."""
    def blocked():
        raise ImportError("nope")

    monkeypatch.setattr(transcribe, "_whisper_model_cls", blocked)
    _model, device, _status = transcribe.load_model_with_fallback(
        "large-v3", use_gpu=False, cuda_supported=True)
    assert device == "cpu"
