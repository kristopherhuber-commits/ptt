"""
Microphone capture.

The stream is held open continuously while the user is active rather than being
started and stopped around each hotkey press: opening a PortAudio stream wakes
the audio hardware, which cost about a second of latency and an audible headset
chime on every recording (NFR-2, retrospective issue #6). Recording is gated in
the callback by a flag instead, and a short pre-roll buffer covers the gap
between the user starting to speak and the poll loop noticing the chord (NFR-3).

The stream is released once the user has been idle for a while so the machine
can reach low-power states (NFR-4); the engine owns that policy.
"""

import ctypes

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


class Recorder:
    """Captures mono float32 audio from microphone into an in-memory buffer."""
    def __init__(self, samplerate=SAMPLE_RATE):
        self.samplerate = samplerate
        self._frames = []
        self._preroll = []
        self._stream = None
        self.recording = False

    def _callback(self, indata, frames, time_info, status):
        if status:
            log_debug(f"Audio stream status warning: {status}")
        if self.recording:
            self._frames.append(indata.copy())
        else:
            self._preroll.append(indata.copy())
            while len(self._preroll) > 4:
                self._preroll.pop(0)

    def open_stream(self):
        if self._stream is not None:
            return
        try:
            log_debug("Initializing PortAudio and opening input stream...")
            sd._initialize()
            self._frames = []
            self._preroll = []
            self._stream = sd.InputStream(
                samplerate=self.samplerate, channels=1,
                dtype="float32", callback=self._callback,
            )
            self._stream.start()
            log_debug("Audio input stream opened and started successfully.")
        except Exception as e:
            log_debug(f"ERROR: Failed to open audio input stream: {str(e)}")

    def close_stream(self):
        self.recording = False
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
