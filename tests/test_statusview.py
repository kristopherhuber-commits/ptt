"""
The state display's derived logic. No widget is instantiated and no
QApplication exists; only the pure functions and the UiState dataclass are used.

These matter more than their size suggests. The engine has **no** error state --
both of its failure paths emit `idle` with a different status string -- so the
only thing standing between "the model failed to load" and a green Ready dot is
`is_error`, which discriminates on text.
"""

import pytest

from ptt.ui.qt_statusview import UNKNOWN, UiState, effective_state, is_error


# -- is_error ---------------------------------------------------------------

@pytest.mark.parametrize("status", [
    "Error loading model",
    "Error: something went wrong in the poll loop",
])
def test_the_two_failure_strings_the_engine_emits_are_errors(status):
    assert is_error(status) is True


@pytest.mark.parametrize("status", [
    "Ready (CUDA)", "Ready (CPU)", "Ready (CPU Fallback)",
    "Recording...", "Transcribing...", "Loading Model...", "", None,
])
def test_the_ordinary_status_strings_are_not_errors(status):
    assert is_error(status) is False


def test_error_is_matched_at_the_start_only():
    """"No Error" is not an error; the engine's strings always lead with it."""
    assert is_error("No Error occurred") is False


# -- effective_state --------------------------------------------------------

def test_error_text_overrides_the_state_for_the_dot():
    assert effective_state("idle", "Error loading model") == "error"


@pytest.mark.parametrize("state", ["idle", "loading", "recording", "transcribing"])
def test_a_healthy_state_passes_through(state):
    assert effective_state(state, "Ready (CUDA)") == state


# -- UiState.detail ---------------------------------------------------------

def test_an_error_points_at_the_evidence():
    detail = UiState(state="idle", status_text="Error loading model").detail()
    assert "debug_log.txt" in detail


def test_loading_names_the_model_being_loaded():
    assert "large-v3-turbo" in UiState(state="loading", model="large-v3-turbo").detail()


def test_recording_says_the_hotkey_is_held():
    assert UiState(state="recording", status_text="Recording...").detail() == "hotkey held"


def test_transcribing_says_what_happens_next():
    detail = UiState(state="transcribing", status_text="Transcribing...").detail()
    assert "pastes at the cursor" in detail


def test_measuring_does_not_claim_it_will_paste():
    """
    The benchmark reuses the `transcribing` state, so the generic detail line
    would be a plain lie about a measurement that pastes nothing.
    """
    detail = UiState(state="transcribing", status_text="Measuring large-v3...").detail()
    assert "paste" not in detail
    assert "clip" in detail


def test_the_cpu_fallback_says_the_setting_was_written():
    detail = UiState(state="idle", status_text="Ready (CPU Fallback)").detail()
    assert "use_gpu=false" in detail


def test_a_healthy_idle_names_the_device_and_the_model():
    detail = UiState(state="idle", status_text="Ready (CUDA)",
                     device="cuda", model="large-v3-turbo").detail()
    assert "CUDA" in detail and "large-v3-turbo" in detail


def test_an_idle_state_with_nothing_known_yet_says_nothing():
    assert UiState(state="idle", status_text="Ready", device="").detail() == ""


# -- the placeholder --------------------------------------------------------

def test_unsupplied_values_default_to_the_placeholder():
    """
    An em dash for a value this build cannot obtain. Inventing a plausible
    microphone name would be worse than admitting there is not one yet.
    """
    fresh = UiState()
    assert fresh.hotkey == fresh.model == fresh.microphone == fresh.last == UNKNOWN
