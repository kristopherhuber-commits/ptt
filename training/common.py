"""
What the training tools share: where things are, reading clips and labels, the
held-out set, and word error rate.

Pure standard library at module scope, so the labeller runs under any Python.
The tools that need numpy import it themselves.

Clips are laid out as `docs/background/training-data-plan.md` section 3 and
`docs/design.md` section 11.2 describe:

    <clips>\\<machine>\\labels.jsonl
    <clips>\\<machine>\\<yyyy-mm>\\<id>.wav
    <clips>\\<machine>\\<yyyy-mm>\\recordings.jsonl
    <clips>\\<machine>\\<yyyy-mm>\\flags.jsonl
"""

import datetime
import glob
import hashlib
import json
import os
import re

HOME = os.path.expanduser("~")

#: Everything v4's training keeps outside the repo: clips, the training
#: environment, models, runs and reports.
TRAINING_HOME = os.path.join(HOME, "tools", "ptt-v4-training")

#: The defaults the app uses too (`app/ptt/paths.py`, `config.FIELDS`).
CLIPS_ROOT = os.path.join(TRAINING_HOME, "ptt-clips")
#: `PTT_TRAIN_ROOT` moves models, runs and reports, for a dry run that must not
#: put a model in PTT's Model tab.
TRAIN_ROOT = os.environ.get("PTT_TRAIN_ROOT") or os.path.join(TRAINING_HOME, "ptt-train")
MODELS_DIR = os.path.join(TRAIN_ROOT, "models")
RUNS_DIR = os.path.join(TRAIN_ROOT, "runs")
REPORTS_DIR = os.path.join(TRAIN_ROOT, "reports")

#: The stock model, converted by the same converter as every trained one, so
#: the conversion cannot skew a comparison (training-data-plan.md section 8).
#: Outside MODELS_DIR, so it does not appear in PTT's Model tab.
STOCK_CT2 = os.path.join(TRAINING_HOME, "ptt-train", "baseline", "stock-large-v3-turbo-f16")

BASE_MODEL = "openai/whisper-large-v3-turbo"

#: Where the app's code is, for the report (which decodes exactly as dictation does).
REPO_APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")

LABELS_FILE = "labels.jsonl"
RECORDINGS_FILE = "recordings.jsonl"
FLAGS_FILE = "flags.jsonl"

#: Increment 1's held-out set: a fixed 1 in HELD_OUT_EVERY of the labelled
#: clips, chosen by a hash of the clip id, so a clip never changes side as more
#: are labelled. The whole-day split replaces this in increment 6.
HELD_OUT_EVERY = 5

#: A short reply, for the exact-match measure (NFR-8).
SHORT_REPLY_MAX_WORDS = 7


def machine_name():
    return (os.environ.get("COMPUTERNAME") or "unknown").strip().lower()


def utc_now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_jsonl(path):
    """Every JSON line in a file; [] if it is not there. A bad line is skipped, with a warning."""
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for number, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"warning: {path}:{number} is not JSON ({e}); skipped")
    return rows


def append_jsonl(path, row):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def machine_dirs(clips_root):
    """Each machine's folder under the clips root."""
    if not os.path.isdir(clips_root):
        return []
    return sorted(p for p in glob.glob(os.path.join(clips_root, "*")) if os.path.isdir(p))


def recordings(machine_dir):
    """Every record a machine saved, oldest first, each with its WAV path as `wav`."""
    rows = []
    for path in sorted(glob.glob(os.path.join(machine_dir, "*", RECORDINGS_FILE))):
        for row in read_jsonl(path):
            row["wav"] = os.path.join(os.path.dirname(path), row["id"] + ".wav")
            rows.append(row)
    return sorted(rows, key=lambda r: r["id"])


def labels(machine_dir):
    """The latest label per clip id: a later line overrides an earlier one."""
    return {row["id"]: row for row in read_jsonl(os.path.join(machine_dir, LABELS_FILE))}


def flagged(machine_dir):
    """The ids of the clips flagged as wrong with the double-tap (FR-13)."""
    return {row["id"] for path in glob.glob(os.path.join(machine_dir, "*", FLAGS_FILE))
            for row in read_jsonl(path)}


def labelled_clips(clips_root):
    """
    Every clip with a usable label, from every machine: the record, plus
    `reference` and `held_out`. Discarded clips and missing WAVs are left out.
    """
    clips = []
    for machine_dir in machine_dirs(clips_root):
        by_id = labels(machine_dir)
        for record in recordings(machine_dir):
            label = by_id.get(record["id"])
            if not label or label.get("reference") is None:
                continue
            if not os.path.exists(record["wav"]):
                print(f"warning: {record['wav']} is missing; skipped")
                continue
            clips.append({**record, "reference": label["reference"],
                          "held_out": is_held_out(record["id"])})
    return clips


def is_held_out(clip_id):
    digest = hashlib.sha256(clip_id.encode("utf-8")).hexdigest()
    return int(digest, 16) % HELD_OUT_EVERY == 0


def load_wav(path):
    """A 16 kHz mono 16-bit WAV as float32 in [-1, 1), as the app hands the model."""
    import wave
    import numpy as np
    with wave.open(path, "rb") as f:
        if (f.getnchannels(), f.getsampwidth(), f.getframerate()) != (1, 2, 16_000):
            raise ValueError(f"{path} is not 16 kHz mono 16-bit")
        raw = f.readframes(f.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


# -- scoring -------------------------------------------------------------------

def words(text):
    """Lower case, punctuation removed: the same normalisation for every model."""
    return re.sub(r"[^\w\s'-]", " ", text.lower()).replace("-", " ").split()


def word_errors(reference, hypothesis):
    """(S + D + I, N): the edit distance in words, and the words in the reference."""
    ref, hyp = words(reference), words(hypothesis)
    previous = list(range(len(hyp) + 1))
    for i in range(1, len(ref) + 1):
        current = [i] + [0] * len(hyp)
        for j in range(1, len(hyp) + 1):
            current[j] = min(previous[j] + 1, current[j - 1] + 1,
                             previous[j - 1] + (ref[i - 1] != hyp[j - 1]))
        previous = current
    return previous[-1], len(ref)


def is_short_reply(reference):
    return 1 <= len(words(reference)) <= SHORT_REPLY_MAX_WORDS
