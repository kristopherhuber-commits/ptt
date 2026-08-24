"""
Input-device selection: resolution, the fallback, and the level the meter reads.

No PortAudio. `audio.py` reaches the library through one module-level `sd`, so
the whole surface is replaceable with a fake -- which is the only way to test
the case that matters, because it needs a device that exists, advertises input
channels, and then refuses to open. That is not a hypothetical: several of this
machine's WDM-KS entries do exactly that, and before the fallback existed
choosing one from the picker left the application with no stream at all.
"""

import numpy as np
import pytest

from ptt import audio


class FakeStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        pass


class FakeSounddevice:
    """
    Enough of `sounddevice` for the Recorder, and a record of what it was asked.

    `refuse` names the device indexes that exist but cannot be opened.
    """

    def __init__(self, devices=None, refuse=(), hostapis=("MME",)):
        self.hostapis = list(hostapis)
        self.devices = devices if devices is not None else {
            0: {"name": "Array Microphone", "max_input_channels": 2, "hostapi": 0},
            1: {"name": "Headset", "max_input_channels": 1, "hostapi": 0},
            2: {"name": "Speakers", "max_input_channels": 0, "hostapi": 0},
        }
        self.refuse = set(refuse)
        self.opened = []          # every device an InputStream was attempted on
        self.initialised = 0
        self.streams = []

    def _initialize(self):
        self.initialised += 1

    def _terminate(self):
        self.initialised -= 1

    def query_devices(self, device=None, kind=None):
        if device is None:
            if kind == "input":
                return self.devices[0]
            return [self.devices[i] for i in sorted(self.devices)]
        if device not in self.devices:
            raise ValueError(f"Error querying device {device}")
        return self.devices[device]

    def query_hostapis(self):
        return [{"name": name} for name in self.hostapis]

    def InputStream(self, **kwargs):
        self.opened.append(kwargs.get("device"))
        if kwargs.get("device") in self.refuse:
            raise RuntimeError("Invalid device [PaErrorCode -9996]")
        stream = FakeStream(**kwargs)
        self.streams.append(stream)
        return stream


@pytest.fixture(autouse=True)
def fresh_enumeration_log(monkeypatch):
    """
    The full enumeration is logged once per process. Reset it, or the first test
    to enumerate swallows the line every later one would assert on.
    """
    monkeypatch.setattr(audio, "_enumeration_logged", False)


@pytest.fixture
def fake_sd(monkeypatch):
    fake = FakeSounddevice()
    monkeypatch.setattr(audio, "sd", fake)
    return fake


def device(index, name, api="MME"):
    return audio.InputDevice(index, name, api)


# -- enumeration -------------------------------------------------------------

def test_only_capture_devices_are_offered(fake_sd):
    """An output-only device in the microphone picker is a device that cannot work."""
    assert [d.index for d in audio.input_devices()] == [0, 1]


def test_a_device_is_labelled_with_its_name_alone(fake_sd):
    """
    Every offered device comes from one host API, so naming it on each row would
    put the same words on all of them.
    """
    assert audio.input_devices()[0].label() == "Array Microphone"


def test_a_device_from_another_api_can_be_labelled_with_it(fake_sd):
    """Used only for a device set by hand, so it is not mistaken for an offer."""
    assert audio.input_devices()[0].label_with_api() == "Array Microphone · MME"


def test_a_nameless_device_still_has_a_label(fake_sd):
    """PortAudio returns `Microphone Array 1 ()` and worse; an empty row is unusable."""
    assert device(7, "").label() == "Device 7"


def test_the_enumeration_is_logged_once_with_every_index(monkeypatch, log_lines):
    """
    The picker hides most of what PortAudio reports, so the log is the escape
    hatch: `audio_device` accepts any of these numbers. Hiding something without
    saying what was hidden is not a simplification.
    """
    monkeypatch.setattr(audio, "sd", FakeSounddevice(
        hostapis=("MME", "Windows DirectSound", "Windows WDM-KS"),
        devices={
            0: {"name": "Array", "max_input_channels": 2, "hostapi": 0},
            1: {"name": "Array", "max_input_channels": 2, "hostapi": 1},
            2: {"name": "Stereo Mix", "max_input_channels": 2, "hostapi": 2},
        }))

    audio.input_devices()
    logged = [line for line in log_lines() if "Input devices (" in line]
    assert len(logged) == 1
    assert "3 found, 1 offered" in logged[0]
    assert "0='Array' (MME) [hidden]" in logged[0]
    assert "1='Array' (Windows DirectSound);" in logged[0]
    assert "2='Stereo Mix' (Windows WDM-KS) [hidden]" in logged[0]

    audio.input_devices()
    assert len([l for l in log_lines() if "Input devices (" in l]) == 1


# -- what the picker offers --------------------------------------------------

def test_the_picker_shows_one_host_apis_copies(fake_sd):
    """
    The change this exists for. One array microphone arrives as fourteen entries
    on the development laptop, and a user cannot be asked to choose between
    fourteen rows describing one microphone.
    """
    devices = (device(0, "Sound Mapper"), device(1, "Array", "MME"),
               device(5, "Array", "Windows DirectSound"),
               device(9, "Array", "Windows WASAPI"),
               device(22, "Array 3 ()", "Windows WDM-KS"))
    assert audio.pickable_devices(devices) == (device(5, "Array", "Windows DirectSound"),)


def test_wasapi_is_never_offered():
    """
    Measured, not assumed: PortAudio opens WASAPI in shared mode, where the
    stream must match the device's 48 kHz mix format, so a 16 kHz InputStream
    fails with `Invalid sample rate`. Whisper's rate is not negotiable, so a
    WASAPI row is a row that cannot record.
    """
    assert "Windows WASAPI" not in audio.PICKER_HOST_APIS


def test_kernel_streaming_is_never_offered():
    """
    WDM-KS exposes output devices' input pins -- two `PC Speaker` entries on the
    test machine -- a `Stereo Mix` loopback, and an entry that advertises input
    channels and then refuses to open.
    """
    assert "Windows WDM-KS" not in audio.PICKER_HOST_APIS


def test_the_picker_falls_back_through_the_host_apis():
    """MME is offered when DirectSound has nothing, and only then."""
    mme_only = (device(1, "Array", "MME"),)
    assert audio.pickable_devices(mme_only) == mme_only


def test_placeholders_are_not_devices():
    """
    Both mean "whatever Windows is currently using", which is what the picker's
    own first row already says.
    """
    devices = (device(0, "Microsoft Sound Mapper - Input", "MME"),
               device(4, "Primary Sound Capture Driver", "Windows DirectSound"),
               device(5, "Array", "Windows DirectSound"))
    assert audio.pickable_devices(devices) == (device(5, "Array", "Windows DirectSound"),)


def test_an_unfamiliar_machine_is_offered_everything_real():
    """An empty picker is worse than a cluttered one."""
    odd = (device(3, "Some Capture Card", "ASIO"),)
    assert audio.pickable_devices(odd) == odd
    assert audio.pickable_devices(()) == ()


# -- MME's truncated names ---------------------------------------------------

FULL = "Microphone Array (Intel Smart Sound Technology for Digital Microphones)"
CUT = FULL[:audio.MME_NAME_LIMIT]


def test_a_truncated_name_is_expanded_from_another_apis_copy():
    """
    MME cuts every name at 31 characters, and the default device resolves
    through MME -- so without this the popover's always-on `Microphone` row
    reads `Microphone Array (Intel Smart `, cut off mid-word.
    """
    assert len(CUT) == 31
    infos = [{"name": CUT}, {"name": FULL}]
    assert audio._expand_name(CUT, infos) == FULL


def test_a_short_name_is_never_lengthened():
    """Only names at exactly the limit are candidates, so `Mic` stays `Mic`."""
    assert audio._expand_name("Mic", [{"name": "Mic 1 (Realtek)"}]) == "Mic"


def test_a_truncated_name_with_no_longer_copy_is_left_alone():
    assert audio._expand_name(CUT, [{"name": CUT}]) == CUT


def test_enumeration_leaves_portaudio_as_it_found_it(fake_sd):
    """
    It nests inside whatever the open stream holds. Leaving PortAudio
    initialised is what stops the machine sleeping (issue #6), and terminating
    one time too many would close the stream out from under a recording.
    """
    audio.input_devices()
    assert fake_sd.initialised == 0


def test_enumeration_reports_nothing_rather_than_raising(monkeypatch):
    """The picker then offers the Windows default, which is the old behaviour."""
    class Broken(FakeSounddevice):
        def query_devices(self, device=None, kind=None):
            raise OSError("no host API")

    monkeypatch.setattr(audio, "sd", Broken())
    assert audio.input_devices() == ()


# -- which device is opened --------------------------------------------------

def test_no_device_means_the_windows_default(fake_sd):
    """`None` is what every configuration written before this build carries."""
    rec = audio.Recorder(16_000)
    rec.open_stream()
    assert fake_sd.opened == [None]
    assert rec.is_open


def test_a_chosen_device_is_the_one_opened(fake_sd):
    rec = audio.Recorder(16_000, 1)
    rec.open_stream()
    assert fake_sd.opened == [1]
    assert rec.device_name == "Headset"


def test_device_zero_is_opened_and_not_read_as_no_device(fake_sd):
    """PortAudio numbers from zero, so a truthiness test here loses a device."""
    rec = audio.Recorder(16_000, 0)
    rec.open_stream()
    assert fake_sd.opened == [0]


def test_an_index_that_no_longer_exists_falls_back_and_logs(fake_sd, log_lines):
    """
    PortAudio renumbers when a device is plugged in or removed, so a saved index
    can come to mean nothing -- or, worse, something else.
    """
    rec = audio.Recorder(16_000, 99)
    rec.open_stream()
    assert fake_sd.opened == [None]
    assert any("is unavailable" in line for line in log_lines())


def test_an_index_that_is_now_an_output_device_falls_back_and_logs(fake_sd, log_lines):
    rec = audio.Recorder(16_000, 2)
    rec.open_stream()
    assert fake_sd.opened == [None]
    assert any("no input channels" in line for line in log_lines())


def test_a_device_that_refuses_to_open_falls_back_to_the_default(monkeypatch, log_lines):
    """
    The case that matters. PortAudio lists devices it cannot open, and without
    this the application has no stream at all: the hotkey does nothing, nothing
    is on screen to say why, and the user's microphone choice is the cause.
    """
    fake = FakeSounddevice(refuse={1})
    monkeypatch.setattr(audio, "sd", fake)

    rec = audio.Recorder(16_000, 1)
    rec.open_stream()

    assert fake.opened == [1, None]
    assert rec.is_open
    assert any("Fell back to the system default" in line for line in log_lines())


def test_a_refused_device_is_not_forgotten(monkeypatch):
    """
    The fallback happens per open, not by rewriting the setting. An unplugged
    headset comes back, and forgetting the choice because it was missing once
    is a worse failure than falling back again and saying so.
    """
    fake = FakeSounddevice(refuse={1})
    monkeypatch.setattr(audio, "sd", fake)
    rec = audio.Recorder(16_000, 1)
    rec.open_stream()
    assert rec.device == 1


def test_nothing_opening_at_all_releases_portaudio(monkeypatch, log_lines):
    """
    Leaving PortAudio initialised with no stream is issue #6 with none of the
    benefit: the machine cannot reach low-power states and nothing is recording.
    """
    fake = FakeSounddevice(refuse={None, 1})
    monkeypatch.setattr(audio, "sd", fake)

    rec = audio.Recorder(16_000, 1)
    rec.open_stream()

    assert not rec.is_open
    assert fake.initialised == 0


def test_opening_twice_opens_one_stream(fake_sd):
    rec = audio.Recorder(16_000)
    rec.open_stream()
    rec.open_stream()
    assert fake_sd.opened == [None]


def test_the_stream_is_opened_at_whispers_sample_rate_in_mono(fake_sd):
    rec = audio.Recorder(audio.SAMPLE_RATE)
    rec.open_stream()
    assert fake_sd.streams[0].kwargs["samplerate"] == 16_000
    assert fake_sd.streams[0].kwargs["channels"] == 1
    assert fake_sd.streams[0].kwargs["dtype"] == "float32"


# -- the level the meter reads -----------------------------------------------

def block(peak):
    return np.array([[0.0], [peak], [-peak / 2]], dtype=np.float32)


def test_the_callback_publishes_the_block_peak(fake_sd):
    rec = audio.Recorder(16_000)
    rec._callback(block(0.4), 3, None, None)
    assert rec.level == pytest.approx(0.4)


def test_the_peak_is_magnitude_not_sign(fake_sd):
    """A negative half-cycle is just as loud as a positive one."""
    rec = audio.Recorder(16_000)
    rec._callback(np.array([[-0.6]], dtype=np.float32), 1, None, None)
    assert rec.level == pytest.approx(0.6)


def test_a_closed_stream_reads_as_silent(fake_sd):
    """
    Otherwise the meter freezes at whatever the last block held, and a released
    microphone looks like a live one.
    """
    rec = audio.Recorder(16_000)
    rec.open_stream()
    rec._callback(block(0.4), 3, None, None)
    rec.close_stream()
    assert rec.level == 0.0
    assert rec.device_name == ""
    assert not rec.is_open


def test_the_callback_still_fills_the_preroll(fake_sd):
    """
    Metering must not have cost the pre-roll buffer, which is what stops the
    first word being clipped (NFR-3).
    """
    rec = audio.Recorder(16_000)
    rec.open_stream()
    for _ in range(6):
        rec._callback(block(0.1), 3, None, None)
    assert len(rec._preroll) == 4     # capped, oldest dropped

    rec.start()
    rec._callback(block(0.1), 3, None, None)
    assert rec.stop().size == 15      # four pre-roll blocks plus one recorded


def test_a_cold_start_has_no_preroll_to_seed_from(fake_sd):
    """
    The cost of turning the warm stream off, stated as a test: opening the
    device is what clears the buffer, so the first fraction of a second of
    speech is not there to recover (NFR-3, issue #6).
    """
    rec = audio.Recorder(16_000)
    rec.start()
    rec._callback(block(0.1), 3, None, None)
    assert rec.stop().size == 3


def test_recording_opens_the_stream_when_it_is_not_warm(fake_sd):
    """What the Audio panel's warm-stream checkbox relies on when it is off."""
    rec = audio.Recorder(16_000)
    rec.start()
    assert rec.is_open
    assert fake_sd.opened == [None]
