"""
Saving each dictation for training (v4, FR-11).

When `save_clips` is on, every transcribed dictation is written to the clips
folder as a WAV and one JSON line, laid out as `training-data-plan.md` section 3
describes:

    <clips folder>\\<machine>\\<yyyy-mm>\\<id>.wav
    <clips folder>\\<machine>\\<yyyy-mm>\\recordings.jsonl     (append-only)

The audio is the exact buffer the model was given -- pre-roll included, before
the model's own silence trimming -- as 16 kHz mono 16-bit PCM.

**Saving never delays dictation (NFR-14) and never breaks it.** The engine
hands each clip to `ClipSaver.submit`, which only queues it; one background
thread writes. Every failure is logged with its reason (OBS-1) and the clip is
dropped. Showing a failure to the user (OBS-5) is increment 2.

No Qt and no model here, so it is testable with a temporary folder.
"""

import datetime
import json
import os
import queue
import threading
import wave

import numpy as np

from ptt.logging_setup import log_debug

#: The rate every clip is saved at: the rate `audio.Recorder` records at.
SAMPLE_RATE = 16_000

RECORDINGS_FILE = "recordings.jsonl"
LABELS_FILE = "labels.jsonl"


def utc_now():
    """The moment a recording starts, as the clip id records it."""
    return datetime.datetime.now(datetime.timezone.utc)


def clip_id(machine, started):
    """
    `darklord-20261104T153012.345Z`: machine, then UTC time to the millisecond.

    Unique across both machines with no coordination, and it sorts by time.
    """
    return f"{machine}-{started:%Y%m%dT%H%M%S}.{started.microsecond // 1000:03d}Z"


def month_dir(root, machine, started):
    """The folder one clip goes in: `<root>\\<machine>\\<yyyy-mm>`, by UTC month."""
    return os.path.join(root, machine, f"{started:%Y-%m}")


def build_record(*, machine, started, samples, transcript, latency_s, mic,
                 app_version, model, model_revision, compute_type, beam_size, vad):
    """
    The JSON line for one clip. Every field is saved from day one, even those
    not needed yet: none can be recovered later (training-data-plan.md
    section 3).
    """
    return {
        "id": clip_id(machine, started),
        "machine": machine,
        "mic": mic or "",
        "app_version": app_version,
        "model": model,
        "model_revision": model_revision,
        "compute_type": compute_type,
        "beam_size": beam_size,
        "vad": vad,
        "hint": "",
        "duration_s": round(samples.size / SAMPLE_RATE, 2),
        "latency_s": round(latency_s, 2),
        "raw_text": transcript.raw_text,
        "pasted_text": transcript.text,
        "rules_fired": list(transcript.rules_fired),
        "segments": list(transcript.segments),
    }


def to_pcm16(samples):
    """float32 in [-1, 1) to 16-bit PCM: the inverse of `load_benchmark_clip`."""
    scaled = np.round(np.asarray(samples, dtype=np.float32).ravel() * 32768.0)
    return np.clip(scaled, -32768, 32767).astype("<i2")


def write_wav(path, samples):
    """Write 16 kHz mono 16-bit, atomically: never a half-written WAV."""
    tmp = path + ".tmp"
    try:
        with wave.open(tmp, "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(SAMPLE_RATE)
            f.writeframes(to_pcm16(samples).tobytes())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def save(root, record, samples):
    """
    Write one clip: the WAV first, then its line in `recordings.jsonl`.

    In that order, so a record never points at audio that is not there. Raises
    on failure; `ClipSaver` is what catches and logs.
    """
    started = datetime.datetime.strptime(record["id"].rsplit("-", 1)[1],
                                         "%Y%m%dT%H%M%S.%fZ")
    folder = month_dir(root, record["machine"], started)
    os.makedirs(folder, exist_ok=True)
    write_wav(os.path.join(folder, record["id"] + ".wav"), samples)
    with open(os.path.join(folder, RECORDINGS_FILE), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return folder


class ClipSaver:
    """
    One background thread that writes queued clips, in order.

    `submit` never blocks and never raises. The thread is a daemon, started on
    the first submit, so a frontend that never saves never starts it.
    """

    def __init__(self):
        self._queue = queue.Queue()
        self._thread = None
        self._lock = threading.Lock()
        #: Clips written and clips lost since start, for the tests and the log.
        self.saved = 0
        self.failed = 0

    def submit(self, root, record, samples):
        try:
            with self._lock:
                if self._thread is None:
                    self._thread = threading.Thread(
                        target=self._run, name="clip-saver", daemon=True)
                    self._thread.start()
            self._queue.put((root, record, samples))
        except Exception as e:
            self.failed += 1
            log_debug(f"ERROR: clip {record.get('id', '?')} not saved: {e}")

    def flush(self, timeout=5.0):
        """Wait until every queued clip is written or `timeout` passes."""
        done = threading.Event()
        self._queue.put(done)
        if self._thread is not None:
            done.wait(timeout)

    def _run(self):
        while True:
            item = self._queue.get()
            if isinstance(item, threading.Event):
                item.set()
                continue
            root, record, samples = item
            try:
                folder = save(root, record, samples)
                self.saved += 1
                log_debug(f"Saved clip {record['id']} to {folder}.")
            except Exception as e:
                self.failed += 1
                log_debug(f"ERROR: clip {record.get('id', '?')} not saved "
                          f"to {root}: {e}")
