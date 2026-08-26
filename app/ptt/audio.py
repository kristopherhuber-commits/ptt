"""
Microphone capture, and the device list the Audio panel offers.

The stream is held open continuously while the user is active rather than being
started and stopped around each hotkey press: opening a PortAudio stream wakes
the audio hardware, which cost about a second of latency and an audible headset
chime on every recording (NFR-2, retrospective issue #6). Recording is gated in
the callback by a flag instead, and a short pre-roll buffer covers the gap
between the user starting to speak and the poll loop noticing the chord (NFR-3).

The stream is released once the user has been idle for a while so the machine
can reach low-power states (NFR-4); the engine owns that policy.

Choosing a device
-----------------

`Recorder.device` is a PortAudio input index, or `None` for "follow the Windows
default device", which is what the recorder did before the Audio panel existed
and is still the default. The index is re-read by the engine on every poll
iteration, the same way the chord is, so a change takes effect on the next
stream open with no restart.

A PortAudio index is not a stable identifier -- the numbering shifts when a
device is plugged in or removed -- so `_resolve_device` checks that a saved
index still names an input device before it is opened, and the resolved device's
name is logged every time the stream comes up. That log line is the only way to
tell after the fact which microphone a recording actually came from.
"""

import ctypes
import threading
from typing import NamedTuple

import numpy as np
import sounddevice as sd

# Must remain the statement immediately following the import. sounddevice
# initialises PortAudio at import time, and leaving it initialised blocks the
# machine from sleeping (issue #6).
sd._terminate()

from ptt.logging_setup import log_debug

#: Whisper's native sample rate.
SAMPLE_RATE = 16_000


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("dwTime", ctypes.c_uint)
    ]


def get_idle_duration():
    """Seconds since the last user input anywhere on the system."""
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        tick_count = ctypes.windll.kernel32.GetTickCount()
        millis = (tick_count - lii.dwTime) & 0xFFFFFFFF
        return millis / 1000.0
    return 0.0


#: Host APIs the picker offers, best first. PortAudio presents **the same
#: physical microphone once per host API** -- fourteen entries for one array
#: microphone on the development laptop -- so the picker shows the copies from
#: one API and hides the rest.
#:
#: The order is measured, not assumed. On this machine:
#:
#: - **WASAPI cannot be used at all.** PortAudio opens it in shared mode, where
#:   the stream must match the device's mix format, and the microphone's is
#:   48 kHz: `InputStream(samplerate=16000)` fails with `Invalid sample rate
#:   [PaErrorCode -9997]`. Whisper's rate is 16 kHz and is not negotiable
#:   (`SAMPLE_RATE`), so a WASAPI entry in the picker is an entry that cannot
#:   record.
#: - **DirectSound opens at 16 kHz and carries the full device name** -- 72
#:   characters here.
#: - **MME opens at 16 kHz but truncates every name to 31 characters**, which is
#:   where `Microphone Array (Intel® Smart ` comes from.
#: - **WDM-KS is never offered.** It is the raw kernel-streaming layer: it
#:   exposes output devices' input pins (two `PC Speaker` entries here), a
#:   `Stereo Mix` loopback, and at least one entry that advertises two input
#:   channels and then refuses to open -- see `open_stream`.
PICKER_HOST_APIS = ("Windows DirectSound", "MME")

#: PortAudio entries that are not devices. Both mean "whatever Windows is
#: currently using", which is what the picker's own first row already says.
ABSTRACT_DEVICE_NAMES = ("microsoft sound mapper", "primary sound capture driver")

#: Where MME cuts a device name off. Names exactly this long are assumed
#: truncated and are expanded from another host API's copy; see `_expand_name`.
MME_NAME_LIMIT = 31

#: The full enumeration is written to the log once per process, so a user who
#: wants a device the picker hides can find its index and set `audio_device` by
#: hand. Hiding something without saying what was hidden is not a simplification.
_enumeration_logged = False

#: Guards the initialise/enumerate/terminate sequence in `input_devices`, which
#: is a process-global refcount with two callers on two threads from v3.0. See
#: that function's docstring for what the interleaving costs.
_enumeration_lock = threading.Lock()


class InputDevice(NamedTuple):
    """One capture device, as the Audio panel's combo box lists it."""
    index: int
    name: str
    hostapi: str

    def label(self):
        """
        What the picker shows: the name alone.

        Every offered device comes from one host API (`PICKER_HOST_APIS`), so
        naming it would put the same words on every row. A device from any other
        API is one the user set by hand, and the panel labels that one with
        `label_with_api` so it is not mistaken for a row the picker offered.
        """
        return self.name or f"Device {self.index}"

    def label_with_api(self):
        return f"{self.label()} · {self.hostapi}" if self.hostapi else self.label()


def is_abstract(device):
    """Whether an entry is one of PortAudio's "the default device" placeholders."""
    lowered = device.name.lower()
    return any(lowered.startswith(prefix) for prefix in ABSTRACT_DEVICE_NAMES)


def pickable_devices(devices):
    """
    The subset of an enumeration a picker should offer.

    Pure, so the rule is testable without an audio device -- and the rule is the
    whole point: fourteen rows for one microphone is not a device list, it is
    PortAudio's internal structure leaking into a settings window.

    Falls back to every real device if no preferred host API has any, because an
    empty picker on an unusual machine is worse than a cluttered one.
    """
    real = tuple(d for d in devices if not is_abstract(d))
    for api in PICKER_HOST_APIS:
        offered = tuple(d for d in real if d.hostapi == api)
        if offered:
            return offered
    return real


def _expand_name(name, infos):
    """
    Undo MME's truncation by finding the same device's full name elsewhere.

    MME cuts every name at 31 characters, so the default device reports as
    `Microphone Array (Intel® Smart ` -- which reads as a rendering fault rather
    than as a name. DirectSound and WASAPI carry the whole thing, and the
    truncated form is a prefix of it.

    Only names at exactly the limit are expanded, so this cannot lengthen a name
    that was simply short. Comparison happens before stripping, because the
    truncation frequently lands on a space.
    """
    if len(name) != MME_NAME_LIMIT:
        return name
    longer = [
        str(info.get("name", "")) for info in infos
        if len(str(info.get("name", ""))) > MME_NAME_LIMIT
        and str(info.get("name", "")).startswith(name)
    ]
    return max(longer, key=len) if longer else name


def _describe(index, info, hostapis, infos):
    api = ""
    try:
        api = str(hostapis[info["hostapi"]]["name"])
    except Exception:
        pass
    return InputDevice(index, _expand_name(str(info.get("name", "")), infos).strip(), api)


def input_devices():
    """
    Every input device PortAudio can see, in index order. Never raises.

    The whole enumeration, not the picker's subset -- `pickable_devices` does
    the filtering, and a saved index the picker hides still has to be resolvable
    to a name.

    Safe to call while a stream is open, which is the non-obvious part. PortAudio
    reference-counts `Pa_Initialize`/`Pa_Terminate`, so the pair below nests
    inside the one `open_stream` holds and the running stream is untouched;
    without the pair the query fails outright, because this module terminates
    PortAudio at import (issue #6) and leaves it that way.

    Returns an empty tuple rather than raising if the query fails: the picker
    then offers only "follow the Windows default device", which is the
    behaviour of every build before this one.

    Serialised, from v3.0 (`concierge_handoff.md` section 4). The
    initialise/terminate pair above is a **process-global refcount**, and until
    the Concierge there was exactly one caller: the Audio tab, on the GUI
    thread, when the tab is shown. `list_audio_devices` is now a tool, called
    from the Concierge's worker thread, so two threads can be inside this pair
    at once -- and the interleaving that matters is one thread's `_terminate`
    dropping the count to zero while the other is between `query_hostapis` and
    `query_devices`. That is a native-level failure inside PortAudio, not a
    Python exception this function could report. The lock is the whole fix and
    it belongs here rather than in either caller, for the reason
    `Settings.set` owns validation: an invariant that holds only while every
    caller remembers it is not an invariant.
    """
    global _enumeration_logged
    devices = []
    try:
        with _enumeration_lock:
            sd._initialize()
            try:
                hostapis = sd.query_hostapis()
                infos = list(sd.query_devices())
                for index, info in enumerate(infos):
                    if info.get("max_input_channels", 0) > 0:
                        devices.append(_describe(index, info, hostapis, infos))
            finally:
                sd._terminate()
    except Exception as e:
        log_debug(f"Could not enumerate input devices: {str(e)}")

    devices = tuple(devices)
    if not _enumeration_logged:
        _enumeration_logged = True
        offered = {d.index for d in pickable_devices(devices)}
        log_debug(
            f"Input devices ({len(devices)} found, {len(offered)} offered in the "
            f"picker; the rest are the same hardware seen through another host "
            f"API). Any of these indexes may be set as audio_device by hand: "
            + "; ".join(
                f"{d.index}={d.name!r} ({d.hostapi})"
                f"{'' if d.index in offered else ' [hidden]'}"
                for d in devices
            )
        )
    return devices


def play_start_click():
    """
    A short system sound at the moment recording starts. Never raises.

    `MessageBeep` is asynchronous -- it returns in well under a millisecond and
    the sound plays on a system thread -- so this is safe on the poll loop,
    which is the thread that notices the chord. A synchronous beep there would
    delay `rec.start()` and eat the first word, which is the failure the
    pre-roll buffer exists to prevent.

    Imported inside the function for the reason `hotkey._key_state` is:
    `winsound` is Windows-only, and design.md section 8 wants these modules
    importable in tests that have no Win32 at all.
    """
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_OK)
    except Exception as e:
        log_debug(f"Could not play the start-of-recording click: {str(e)}")


class Recorder:
    """Captures mono float32 audio from microphone into an in-memory buffer."""
    def __init__(self, samplerate=SAMPLE_RATE, device=None):
        self.samplerate = samplerate

        #: PortAudio input index, or None for the Windows default device. A
        #: plain attribute the engine rebinds between recordings; see the
        #: module docstring.
        self.device = device

        #: Name of the device the open stream is actually on, "" when closed.
        self.device_name = ""

        #: Peak amplitude of the most recent callback block, 0.0-1.0, for the
        #: Audio panel's level meter. See `_callback` for why this is a plain
        #: attribute and not a Qt signal.
        self.level = 0.0

        self._frames = []
        self._preroll = []
        self._stream = None
        self.recording = False

    @property
    def is_open(self):
        """Whether a stream is currently open. The meter says so when it is not."""
        return self._stream is not None

    def _callback(self, indata, frames, time_info, status):
        if status:
            log_debug(f"Audio stream status warning: {status}")

        # A plain float rebind, polled by the settings window's level meter on a
        # 30 Hz timer. Deliberately not a Qt signal: this is PortAudio's
        # realtime callback thread, where an emit would allocate a
        # QMetaCallEvent, take the receiver's post-event mutex and wake the GUI
        # thread several hundred times a second. Overrunning this callback's
        # deadline drops audio, which in a dictation app means dropped words.
        # Producer writes a value, consumer polls it -- the same shape the
        # chord's live re-read already uses.
        if frames:
            self.level = float(np.abs(indata).max())

        if self.recording:
            self._frames.append(indata.copy())
        else:
            self._preroll.append(indata.copy())
            while len(self._preroll) > 4:
                self._preroll.pop(0)

    def _resolve_device(self):
        """
        The index to open, or None for the Windows default.

        A saved index is a PortAudio index and PortAudio renumbers when a device
        is plugged in or removed, so an index that no longer names an input
        device falls back to the default **with a logged reason** (OBS-3).
        Opening whatever now sits at that number would record from the wrong
        microphone and say nothing about it.

        Must be called with PortAudio initialised; `open_stream` is the only
        caller and does that first.
        """
        if self.device is None:
            return None
        try:
            info = sd.query_devices(self.device)
        except Exception as e:
            log_debug(
                f"Configured input device {self.device} is unavailable "
                f"({str(e)}); using the system default."
            )
            return None
        if info.get("max_input_channels", 0) < 1:
            log_debug(
                f"Configured input device {self.device} "
                f"('{info.get('name', '?')}') has no input channels; "
                f"using the system default."
            )
            return None
        return self.device

    def _resolved_name(self, resolved):
        """
        Name of the device a stream was opened on, for the log and the UI.

        Expanded through `_expand_name`, because the default device resolves
        through MME -- so without this the popover's `Microphone` row reads
        `Microphone Array (Intel® Smart `, cut off mid-word, on the one surface
        that is always on screen.
        """
        try:
            info = sd.query_devices(kind="input") if resolved is None \
                else sd.query_devices(resolved)
            return _expand_name(str(info.get("name", "")), sd.query_devices()).strip()
        except Exception as e:
            log_debug(f"Could not name the open input device: {str(e)}")
            return ""

    @staticmethod
    def _device_note(device):
        return "the system default" if device is None else f"device {device}"

    def _try_open(self, device):
        """Open and start one stream. True on success; logs and returns False otherwise."""
        try:
            self._stream = sd.InputStream(
                samplerate=self.samplerate, channels=1,
                dtype="float32", device=device, callback=self._callback,
            )
            self._stream.start()
        except Exception as e:
            self._stream = None
            log_debug(
                f"ERROR: Failed to open audio input stream on "
                f"{self._device_note(device)}: {str(e)}"
            )
            return False

        log_debug("Audio input stream opened and started successfully.")
        self.device_name = self._resolved_name(device)
        log_debug(
            f"Recording from '{self.device_name}' ({self._device_note(device)})."
        )
        return True

    def open_stream(self):
        """
        Open the input stream, falling back to the Windows default device.

        The fallback is not belt and braces. PortAudio **lists devices it cannot
        open**: several of this machine's WDM-KS entries advertise input
        channels and then fail with `Invalid device [PaErrorCode -9996]`. A user
        who picks one of those from the combo box would otherwise get no stream
        at all -- no error on screen, the hotkey doing nothing, and only a line
        in debug_log.txt to say why. Same shape as the CUDA-to-CPU fallback in
        `transcribe.load_model_with_fallback`, and for the same reason: a bad
        choice must cost the choice, not the application.

        The saved setting is deliberately **not** rewritten. An unplugged
        headset comes back, and forgetting the user's device because it was
        missing once would be a worse failure than falling back each time and
        saying so.
        """
        if self._stream is not None:
            return

        log_debug("Initializing PortAudio and opening input stream...")
        try:
            sd._initialize()
        except Exception as e:
            log_debug(f"ERROR: Failed to initialise PortAudio: {str(e)}")
            return

        self._frames = []
        self._preroll = []
        resolved = self._resolve_device()
        if self._try_open(resolved):
            return
        if resolved is not None and self._try_open(None):
            log_debug(
                f"Fell back to the system default input device: "
                f"{self._device_note(resolved)} refused to open."
            )
            return

        # Nothing opened, so nothing is holding PortAudio. Release it rather
        # than leaving it initialised, which is what stops the machine reaching
        # low-power states (issue #6) -- the very thing close_stream exists for.
        try:
            sd._terminate()
        except Exception as e:
            log_debug(f"Exception while terminating PortAudio: {str(e)}")

    def close_stream(self):
        self.recording = False
        self.level = 0.0
        self.device_name = ""
        if self._stream is not None:
            try:
                log_debug("Closing audio stream and terminating PortAudio...")
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                log_debug(f"Exception while closing stream: {str(e)}")
            self._stream = None
            try:
                sd._terminate()
                log_debug("PortAudio terminated successfully.")
            except Exception as e:
                log_debug(f"Exception while terminating PortAudio: {str(e)}")
        self._frames = []
        self._preroll = []

    def start(self):
        if self._stream is None:
            self.open_stream()
        # Seed main frames with pre-roll data to capture early speech
        self._frames = list(self._preroll)
        self._preroll = []
        self.recording = True

    def stop(self):
        self.recording = False
        self._preroll = []
        if not self._frames:
            return np.empty(0, dtype=np.float32)
        audio_data = np.concatenate(self._frames, axis=0).flatten()
        self._frames = []
        return audio_data
