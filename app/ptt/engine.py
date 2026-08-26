"""
The push-to-talk state machine: idle -> recording -> transcribing -> paste.

Owns the poll loop, the model lifecycle, and the audio stream's idle release.

**The engine must not import ptt.ui.** It reports state through a callback its
caller supplies, which is what lets one core drive both a tray icon and a
console (design.md section 4).

The state-callback contract
---------------------------

``on_state(state, status_text)``

1. `state` is one of "loading", "idle", "recording", "transcribing" and drives
   the tray's four icons (FR-7). `status_text` is the human string the tooltip
   shows -- "Ready (CUDA)", "Ready (CPU Fallback)", "Error loading model", ...

2. **Single producer.** The callback is invoked only from the thread that called
   `run()`, never concurrently, and in order. The engine makes **no promise that
   this is the UI thread** -- marshalling, if a frontend needs it, is that
   frontend's problem. In the tray it is the daemon thread the Qt frontend
   starts, and the marshalling is `ptt.ui.qt_app.EngineBridge`; in the console
   it is the main thread and there is none.

3. It must not block and must not raise. Every invocation is wrapped, so a
   frontend bug cannot kill the poll loop or strand `recording=True` with the
   microphone still live.

4. It may fire before `run()` returns and after `stop()` has been requested, so
   a frontend must tolerate being called before it is fully built.
"""

import statistics
import threading
import time
import traceback

from ptt import audio as audio_mod
from ptt import hotkey as hotkey_mod
from ptt import inject, transcribe
from ptt.logging_setup import log_debug

#: Hotkey polling interval. GetAsyncKeyState is cheap; 20 ms is imperceptible.
POLL_SEC = 0.02

#: How long the user must be idle before the audio device is released (NFR-4).
#: Applies only while `settings.keep_stream_warm` is on; see `run`.
IDLE_THRESHOLD_SEC = 240.0

#: Recordings shorter than this are treated as an accidental tap (FR-3).
#: Applies only while `settings.ignore_short_holds` is on; see `run`.
MIN_RECORD_SEC = 0.3

#: How many transcription times are kept for the Diagnostics panel's median.
#: Enough to be a median rather than a mood, few enough that a model change
#: half an hour ago is not still dragging the figure around.
LATENCY_SAMPLES = 20


class Engine:
    def __init__(self, settings, cuda_supported, on_state,
                 on_text=None, on_benchmark=None, chord_held=None):
        """
        `settings` is held by reference and re-read as the loop runs; see `run`.

        `chord_held` is a seam so the loop can be driven without a keyboard in
        step 2's tests. It defaults to the real detector.

        `on_benchmark(model, device, seconds)` reports a latency measurement.
        Like `on_state` it is called from the engine thread, and like
        `on_fallback` it is a callback rather than a write so this module never
        reaches config.json itself.
        """
        self._settings = settings
        self.cuda_supported = cuda_supported
        self.current_device = "cpu"

        # Hardware has the last word over the saved preference. Done here rather
        # than in each frontend so the rule lives once: the tray needs it so the
        # menu checkmark tells the truth, and load_model_with_fallback needs it
        # so it does not attempt CUDA on a machine without it.
        if not cuda_supported:
            log_debug("CUDA not supported on this hardware. Overriding config to use CPU.")
            # `override`, not `set`: this run only. A machine whose driver is
            # broken this morning must not lose the preference the user chose
            # (config.Settings.override).
            settings.override("use_gpu", False)

        self._on_state = on_state
        self._on_text = on_text or (lambda _text: None)
        self._on_benchmark = on_benchmark or (lambda _m, _d, _s: None)
        self._chord_held = chord_held or hotkey_mod.chord_held

        self._model = None
        self._running = True
        self._reload_model = threading.Event()
        self._benchmark_model = threading.Event()

        # The recorder is built in `run`; this is the handle the Diagnostics and
        # Audio panels reach it through. None until the loop starts, which is
        # why every accessor below tolerates that.
        self._recorder = None

        # Written by the poll loop, read by the settings window on its own
        # timer. Whole-value rebinds, never in-place edits, for the reason
        # `config.Settings`' docstring gives: a reader on the GUI thread sees
        # either the old value or the new one and never a half-built one.
        self._latencies = ()
        self.last_paste_target = ""
        self.last_summary = ""

    # -- public API ---------------------------------------------------------

    def stop(self):
        """Ask the loop to finish. Thread-safe: a plain bool rebind."""
        self._running = False

    def request_model_reload(self):
        """
        Ask the loop to rebuild the model, e.g. after a CPU/GPU toggle.

        Thread-safe. Serviced at the top of the next poll iteration, so a toggle
        during an in-flight transcription is honoured only once that finishes.
        That is the pre-existing behaviour and should not be "fixed" into an
        interrupt.
        """
        self._reload_model.set()

    def request_benchmark(self):
        """
        Ask the loop to time one transcription of the bundled sample clip.

        Thread-safe, and serviced the same way a reload is. It measures the
        model **already resident**, which is why there is no model argument:
        the Model panel's selection is the loaded model, so measuring never
        needs a second `WhisperModel` alongside the working one. Two models on
        one card -- 3.1 GB plus 1.6 GB of float16 weights before activations --
        is a plausible CUDA OOM, and an allocation failure while *measuring*
        must not be able to take down the model that dictation depends on.

        It also makes the number mean something: a latency measured while
        another model holds VRAM is not comparable to one measured beside it.
        """
        self._benchmark_model.set()

    def input_level(self):
        """
        Peak microphone amplitude from the most recent audio block, 0.0-1.0.

        A plain attribute read, polled by the Audio panel's level meter on the
        GUI thread. The value is produced in PortAudio's realtime callback; see
        `audio.Recorder._callback` for why it is polled rather than signalled.
        """
        rec = self._recorder
        return rec.level if rec is not None else 0.0

    def input_device_name(self):
        """Name of the device the open stream is on, or "" when it is closed."""
        rec = self._recorder
        return rec.device_name if rec is not None else ""

    def stream_is_open(self):
        """Whether the microphone is currently held open (NFR-2, NFR-4)."""
        rec = self._recorder
        return bool(rec is not None and rec.is_open)

    def median_latency(self):
        """
        Median transcription time over the last `LATENCY_SAMPLES` dictations,
        or None before there has been one.

        Median rather than mean: the first transcription after a model load
        carries warm-up cost that is not representative of the rest, and one
        such outlier moves a mean of twenty by enough to misreport the machine.

        Benchmarks are deliberately excluded -- a 30-second clip is not a
        dictation, and mixing the two would produce a number that describes
        neither.
        """
        samples = self._latencies
        return statistics.median(samples) if samples else None

    # -- internals ----------------------------------------------------------

    def _emit(self, state, status_text):
        """Report a state change. A misbehaving frontend must not kill the loop."""
        try:
            self._on_state(state, status_text)
        except Exception as e:
            log_debug(f"ERROR in on_state callback: {str(e)}")
            log_debug(traceback.format_exc())

    def _emit_text(self, text):
        """Hand the transcript to the frontend, for a console that prints it."""
        try:
            self._on_text(text)
        except Exception as e:
            log_debug(f"ERROR in on_text callback: {str(e)}")

    def _emit_benchmark(self, model_name, device, seconds):
        """Report a latency measurement. Wrapped for the same reason `_emit` is."""
        try:
            self._on_benchmark(model_name, device, seconds)
        except Exception as e:
            log_debug(f"ERROR in on_benchmark callback: {str(e)}")

    def _persist_cpu_fallback(self):
        """Remember that CUDA failed, so the next start does not retry it (FR-6)."""
        self._settings.set("use_gpu", False)

    def _reload(self):
        self._emit("loading", "Loading Model...")

        # Deallocate the old model before loading its replacement
        self._model = None

        # LOAD-BEARING for the same reason the chord is: the model name is read
        # from the settings object here, not cached, so the Model panel's
        # selection takes effect on the next reload with no restart.
        self._model, self.current_device, status_text = transcribe.load_model_with_fallback(
            self._settings.model, self._settings.use_gpu, self.cuda_supported,
            on_fallback=self._persist_cpu_fallback,
        )
        self._emit("idle", status_text)

    def _benchmark(self):
        """
        Time one transcription of the bundled clip with the resident model.

        Runs on the poll thread and blocks it, so dictation pauses for the
        duration. That is deliberate rather than an oversight: the alternative
        is inference on one `WhisperModel` from two threads at once, and
        faster-whisper does not promise that is safe. The user asked for this by
        pressing a button and the banner says what is happening throughout.

        The state stays `transcribing` -- it *is* transcribing -- so the
        state->UI contract in gui_handoff section 7 needs no new row. Only the
        status text is new, and it comes from here, which is the rule: the
        headline is whatever the engine reported.
        """
        model_name = self._settings.model
        if self._model is None:
            log_debug("Benchmark requested with no model loaded; ignoring.")
            self._emit("idle", self._ready_text())
            return

        self._emit("transcribing", f"Measuring {model_name}...")
        try:
            audio = transcribe.load_benchmark_clip()
            t0 = time.time()
            text = transcribe.transcribe_audio(self._model, audio)
            seconds = time.time() - t0
            log_debug(
                f"Benchmark: {model_name} on {self.current_device.upper()} took "
                f"{seconds:.2f}s for the sample clip. Result: '{text}'"
            )
            self._emit_benchmark(model_name, self.current_device, seconds)
        except Exception as e:
            log_debug(f"ERROR while measuring {model_name}: {str(e)}")
            log_debug(traceback.format_exc())
        self._emit("idle", self._ready_text())

    def _ready_text(self):
        return f"Ready ({self.current_device.upper()})"

    def _record_transcription(self, seconds, text):
        """
        Remember what the last dictation cost, for the two UI surfaces that
        report it: the popover's `Last` row and the Diagnostics panel's median.

        Both values were already computed and logged; this keeps them instead of
        leaving the panel to parse them back out of debug_log.txt. `OBS-4`
        guarantees that file is plain text, not that its lines are a stable
        format, so a panel built on parsing them would break silently the first
        time a message was reworded.

        The tuple is rebuilt and rebound rather than appended to, because the
        GUI thread reads it while this one writes it.
        """
        self._latencies = (self._latencies + (seconds,))[-LATENCY_SAMPLES:]
        words = len(text.split())
        self.last_summary = (
            f"{seconds:.1f} s · {words} word{'' if words == 1 else 's'}"
            if text else f"{seconds:.1f} s · nothing said"
        )

    # -- the loop -----------------------------------------------------------

    def run(self):
        """
        Run the poll loop until `stop()`. Blocks the calling thread.

        Cleanup lives in a `finally` so a KeyboardInterrupt in the console
        frontend releases the microphone the same way a tray Exit does.
        """
        # Build the model on the first iteration, so "loading" is emitted from
        # inside the loop exactly as it is for a later toggle.
        self._reload_model.set()

        rec = audio_mod.Recorder(audio_mod.SAMPLE_RATE, self._settings.audio_device)
        self._recorder = rec
        recording = False
        stream_open = False

        try:
            while self._running:
                if self._reload_model.is_set():
                    self._reload_model.clear()
                    self._reload()

                # After the reload check, so selecting a model and measuring it
                # in one go measures the model that was just loaded.
                if self._benchmark_model.is_set():
                    self._benchmark_model.clear()
                    self._benchmark()

                # LOAD-BEARING for the same reason the chord is: the input
                # device is re-read from the settings object here, never cached,
                # so the Audio panel's choice takes effect with no restart. The
                # stream is only closed -- reopening is left to the one branch
                # below that owns the decision.
                #
                # Skipped entirely while recording, rather than rebinding and
                # closing later. PortAudio binds the device when the stream is
                # created, so a change mid-recording cannot take effect on the
                # recording in progress; noting it here and acting on it after
                # the release is the difference between the next recording using
                # the new microphone and the old stream being kept because the
                # two indexes now match.
                if not recording and rec.device != self._settings.audio_device:
                    log_debug(
                        f"Input device changed from {rec.device} to "
                        f"{self._settings.audio_device}; reopening the stream."
                    )
                    rec.device = self._settings.audio_device
                    if stream_open:
                        rec.close_stream()
                        stream_open = False

                # Release the audio device while the user is away (NFR-4).
                #
                # With the warm stream switched off in the Audio panel the
                # threshold does not apply at all: nothing opens the stream
                # ahead of time, `rec.start()` opens it when the chord goes
                # down, and the branch below closes it as soon as the recording
                # ends. That is a boolean, not a threshold of zero -- a zero
                # threshold would close and reopen the device on every poll
                # iteration, which is issue #6 fifty times a second.
                idle = audio_mod.get_idle_duration()
                if self._settings.keep_stream_warm and idle < IDLE_THRESHOLD_SEC:
                    if not stream_open:
                        rec.open_stream()
                        stream_open = True
                elif stream_open and not recording:
                    rec.close_stream()
                    stream_open = False

                if self._model is not None:
                    try:
                        # LOAD-BEARING: the chord is re-read from the settings
                        # object on every iteration, never cached in a local or
                        # on self. That is what lets it change while the engine
                        # runs, with no restart. Do not hoist this.
                        held = self._chord_held(self._settings.hotkey)

                        if held and not recording:
                            recording = True
                            # Break up the Alt press now, while it is still held: once the
                            # user releases it the menu has already taken focus.
                            inject.suppress_alt_menu()
                            rec.start()
                            # `start` opens the stream itself when the warm
                            # stream is off, so the loop's own flag has to learn
                            # about it or nothing ever closes it again.
                            stream_open = True
                            if self._settings.start_click:
                                audio_mod.play_start_click()
                            self._emit("recording", "Recording...")
                            log_debug("Recording started...")

                        elif not held and recording:
                            recording = False
                            samples = rec.stop()
                            self._emit("transcribing", "Transcribing...")
                            log_debug(f"Recording stopped. Audio samples: {samples.size}")

                            # An empty buffer is skipped whatever the setting
                            # says: turning the minimum hold off means "do not
                            # discard short recordings", not "hand nothing at
                            # all to inference", and an empty array is the one
                            # input transcribe_audio has no answer for.
                            minimum = (MIN_RECORD_SEC
                                       if self._settings.ignore_short_holds else 0.0)
                            if (samples.size == 0
                                    or samples.size < audio_mod.SAMPLE_RATE * minimum):
                                log_debug("Recording too short, skipping transcription.")
                                self._emit("idle", self._ready_text())
                                continue

                            log_debug("Starting transcription...")
                            t0 = time.time()
                            # The vocabulary is re-read here, on the same
                            # principle as the chord: the Vocabulary panel's
                            # edits apply to the next thing said, with no
                            # reload and no restart.
                            text = transcribe.transcribe_audio(
                                self._model, samples, self._settings.vocabulary)
                            t1 = time.time()
                            log_debug(f"Transcription finished in {t1-t0:.2f}s. Result: '{text}'")
                            self._record_transcription(t1 - t0, text)

                            if text:
                                self._emit_text(text)
                                if not inject.target_accepts_keys():
                                    log_debug("WARNING: focused window has no caret; paste may be discarded.")
                                inject.paste_text(text)
                                self.last_paste_target = inject.foreground_window_class()
                                log_debug(f"Pasted {len(text)} chars into '{self.last_paste_target}'.")
                            self._emit("idle", self._ready_text())

                    except Exception as e:
                        log_debug(f"ERROR inside main processing loop: {str(e)}")
                        log_debug(traceback.format_exc())
                        self._emit("idle", f"Error: {str(e)}")

                time.sleep(POLL_SEC)
        finally:
            if recording:
                rec.stop()
            rec.close_stream()
            log_debug("Transcription background loop finished.")
