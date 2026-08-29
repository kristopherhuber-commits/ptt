"""
NFR-CG-3 — dictation latency with the Concierge model resident, and generating.

The spike measured this once (`spike_results.md` C5) by reading real dictations
out of the installed app's own log during two deliberately held windows. That
gave the honest shape of the answer and a sample of **three** for the resident
state, which `concierge_verification.md` §4 records as the one measurement worth
repeating. This is the repeat, and it is an instrument rather than a window,
because the thing that made n=3 was that a person had to be talking.

**What it measures, and what it therefore does not.** The stopwatch runs across
`transcribe.transcribe_audio`, the same call `engine.py` makes on hotkey release
-- real `faster-whisper`, real CUDA, the real `large-v3-turbo` weights, the real
`BEAM_SIZE`, `VAD_FILTER` and `condition_on_previous_text` the application uses.
What it does not include is PortAudio's capture and `inject.paste_text`, neither
of which touches the GPU and neither of which the LLM can contend with. So this
is the GPU half of the user-visible latency, isolated on purpose: it is the half
NFR-CG-3 is about.

**Audio is one real recording, sliced.** `benchmark_sample.wav` is 30 s of
speech at 16 kHz mono, which is what the Model tab's Measure button already
uses. Slicing one clip to several lengths holds the speaker, the room and the
microphone constant across every reading, so a difference between two states is
the state and not the sentence. Whisper's decode cost tracks the number of
tokens it emits, so the clips are cut on the same boundaries in every state and
the same clip is compared with itself.

**Three states, interleaved.** Round-robin rather than block-by-block: a thermal
drift or a background Windows update that happened to land during one block
would otherwise read as that state's effect. Every round runs baseline,
resident-idle and generating in turn, so anything slow-moving hits all three.

    python tests/tools/contention.py --rounds 8
    python tests/tools/contention.py --rounds 3 --durations 5,10 --json out.json

It writes nothing of the developer's: `rig.open_workspace` rebinds
`paths.APP_DIR` to `tests/tools/.rig/` before anything is constructed, so the
config file, both logs, the state file and the API key all land there.
"""

import argparse
import json
import os
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "app"))

import rig                                          # noqa: E402
from ptt import paths, transcribe                   # noqa: E402
from ptt.concierge import server as server_mod      # noqa: E402
from ptt.concierge import state as state_mod        # noqa: E402

#: The three states NFR-CG-3 now names, plus the control it is measured against.
BASELINE, RESIDENT, GENERATING = "baseline", "resident-idle", "generating"
STATES = (BASELINE, RESIDENT, GENERATING)

#: Utterance lengths, in seconds. The spike's real dictations ran 1.2 s to
#: 26.8 s; these bracket the same range. 2 s is a short command, 20 s is a long
#: paragraph, and NFR-1's [2 s] bound is what both are checked against.
DEFAULT_DURATIONS = (2.0, 5.0, 10.0, 20.0)

SAMPLE_RATE = 16000


#: Resolved at import, before `open_workspace` can move `paths.APP_DIR`. The
#: rig captures the pack and the prompt the same way and for the same reason.
CLIP_PATH = paths.asset_path("benchmark_sample.wav")


def read_clip(path=None):
    """
    `benchmark_sample.wav` as a float32 mono buffer.

    A local reader rather than `transcribe.load_benchmark_clip`, and the
    duplication is deliberate: that function resolves its own path through
    `paths.asset_path` **at call time**, so after the workspace redirect it
    looks for the clip inside `tests/tools/.rig/assets/`, and it writes a line
    to `paths.debug_log_path()`, which before the redirect is the developer's
    real log. Reading the file here keeps the rig's promise that it touches
    nothing of yours. The format assertion is the same one, because a
    measurement that silently ran on the wrong sample rate would be worse than
    no measurement.
    """
    with wave.open(path or CLIP_PATH, "rb") as f:
        actual = (f.getnchannels(), f.getsampwidth(), f.getframerate())
        if actual != (1, 2, SAMPLE_RATE):
            raise ValueError(
                f"the benchmark clip must be mono 16-bit {SAMPLE_RATE} Hz, not "
                f"{f.getnchannels()}ch {f.getsampwidth() * 8}-bit "
                f"{f.getframerate()} Hz")
        frames = f.readframes(f.getnframes())
    samples = struct.unpack(f"<{len(frames) // 2}h", frames)
    return [s / 32768.0 for s in samples]


def load_clips(durations):
    """The benchmark clip, cut to each requested length."""
    import numpy

    audio = numpy.asarray(read_clip(), dtype=numpy.float32)
    clips = {}
    for seconds in durations:
        frames = int(seconds * SAMPLE_RATE)
        if frames > len(audio):
            raise ValueError(
                f"{seconds}s is longer than the {len(audio) / SAMPLE_RATE:.1f}s "
                f"benchmark clip")
        clips[seconds] = audio[:frames]
    return clips


#: The load generator, as source for a *separate interpreter*. It ran as a
#: thread in the first draft, and that was a confound rather than a shortcut:
#: `transcribe_audio` is timed in this process, and a thread here parsing HTTP
#: responses competes for the GIL with the code under the stopwatch. The
#: measurement is meant to be about two things contending for a GPU, so the
#: contention has to be only that. A subprocess also happens to be what a real
#: installation looks like -- llama-server's client is the app, not the model.
LOAD_SOURCE = """
import json, sys, time, urllib.request
base_url, api_key, alias, max_tokens = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
prompt = ("Explain, at length and in detail, how push-to-talk dictation "
          "software captures audio, transcribes it and inserts the result "
          "into another application.")
completions = tokens = 0
while True:
    body = json.dumps({"model": alias,
                       "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": max_tokens, "stream": False}).encode()
    request = urllib.request.Request(
        base_url + "/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + api_key})
    with urllib.request.urlopen(request, timeout=300) as response:
        payload = json.loads(response.read())
    completions += 1
    tokens += payload.get("usage", {}).get("completion_tokens", 0)
    print(json.dumps({"completions": completions, "tokens": tokens}), flush=True)
"""


class Load:
    """
    A separate process that keeps llama-server decoding while it is open.

    Not one long generation: a single 4096-token completion would spend its
    first seconds on prompt processing, which is a different kind of GPU work
    from decode, and would then stop. This issues back-to-back completions and
    keeps issuing them, so the GPU is busy for the whole window a transcription
    is timed in -- which is the state `FR-CG-4`'s guided setup actually puts the
    user in, dictating while the Concierge answers.

    **It is a harsher state than the spike measured.** C5's window was twelve
    bursts over four minutes of a real conversation; this one never stops. Both
    are real -- a single long answer decodes continuously for as long as it
    takes -- but they are different states and their numbers are not
    interchangeable.
    """

    def __init__(self, base_url, api_key, max_tokens=512):
        self.base_url = base_url
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.completions = 0
        self.tokens = 0
        self._process = None
        self._reader = None

    def __enter__(self):
        import subprocess
        self._process = subprocess.Popen(
            [sys.executable, "-c", LOAD_SOURCE, self.base_url, self.api_key,
             server_mod.SERVER_ALIAS, str(self.max_tokens)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        # Do not start timing into a cold GPU: wait for the first completion so
        # the measured window is decode and not prompt processing.
        deadline = time.monotonic() + 180
        while self.completions == 0:
            if self._process.poll() is not None:
                raise RuntimeError(
                    f"the load generator exited: "
                    f"{self._process.stderr.read()[-800:]}")
            if time.monotonic() > deadline:
                raise RuntimeError("the load generator produced nothing in 180 s")
            time.sleep(0.1)
        return self

    def __exit__(self, *_exc):
        if self._process.poll() is None:
            self._process.kill()
        self._process.wait(timeout=30)
        return False

    def _read(self):
        for line in self._process.stdout:
            try:
                seen = json.loads(line)
            except ValueError:
                continue
            self.completions = seen["completions"]
            self.tokens = seen["tokens"]


def nvidia_smi(query):
    """One `nvidia-smi` field as an int, or None."""
    import subprocess
    try:
        out = subprocess.run(
            ["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=20, check=True)
        return int(out.stdout.strip().splitlines()[0])
    except Exception:                                    # noqa: BLE001
        return None


def gpu_used_mib():
    """`nvidia-smi`'s used-memory figure, or None. Recorded, never asserted."""
    return nvidia_smi("memory.used")


def wait_until_idle(limit=2, samples=3, timeout=60.0):
    """
    Block until the GPU reports near-zero utilisation `samples` times running.

    **This exists because the first run of this instrument produced a wrong
    number and it was visible.** The resident-idle block follows a block that
    restarts llama-server, and the 2 s clip -- the first reading after that
    restart -- came in at 2.10x the baseline while 5 s, 10 s and 20 s all sat at
    1.00-1.02x. A state called "idle" that is measured while 7 GB of weights are
    still settling into VRAM is not the state it is named after.

    Asserting the condition rather than sleeping a guessed interval, because the
    sleep that is long enough on this machine is a number nobody can check.
    """
    quiet, deadline = 0, time.monotonic() + timeout
    while time.monotonic() < deadline:
        busy = nvidia_smi("utilization.gpu")
        if busy is None:
            return None                        # no nvidia-smi: nothing to assert
        quiet = quiet + 1 if busy <= limit else 0
        if quiet >= samples:
            return True
        time.sleep(0.5)
    return False


def fit(rows):
    """
    Least squares `latency = a + b * audio_seconds`, the way C5 fitted its
    baseline. Returns `(a, b)`.

    A fit rather than a median because latency scales with utterance length and
    the three states are compared across the same four lengths: a median would
    be a statement about the mix of clip lengths, not about the state.
    """
    n = len(rows)
    sx = sum(r["audio_seconds"] for r in rows)
    sy = sum(r["seconds"] for r in rows)
    sxx = sum(r["audio_seconds"] ** 2 for r in rows)
    sxy = sum(r["audio_seconds"] * r["seconds"] for r in rows)
    denominator = n * sxx - sx * sx
    if not denominator:
        return sy / n, 0.0
    b = (n * sxy - sx * sy) / denominator
    return (sy - b * sx) / n, b


def median(values):
    values = sorted(values)
    if not values:
        return float("nan")
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=8,
                        help="passes over every clip in every state (default 8)")
    parser.add_argument("--durations", default=None,
                        help="comma-separated utterance lengths in seconds")
    parser.add_argument("--model-size", default="large-v3-turbo",
                        help="the Whisper tier to load (default large-v3-turbo)")
    parser.add_argument("--exe", default=rig.default_exe())
    parser.add_argument("--model", default=rig.default_model(),
                        help="the Concierge GGUF")
    parser.add_argument("--workspace", default=rig.DEFAULT_WORKSPACE)
    parser.add_argument("--json", default=None, help="write every reading here")
    parser.add_argument("--states", default=",".join(STATES))
    args = parser.parse_args(argv)

    durations = tuple(float(d) for d in args.durations.split(",")) \
        if args.durations else DEFAULT_DURATIONS
    states = tuple(s for s in args.states.split(",") if s)

    rig.open_workspace(args.workspace, fresh=True)
    print(f"workspace: {paths.APP_DIR}")

    print(f"loading Whisper {args.model_size} on CUDA ...")
    started = time.perf_counter()
    model, device, status = transcribe.load_model_with_fallback(
        args.model_size, use_gpu=True, cuda_supported=transcribe.cuda_available())
    if model is None or device != "cuda":
        print(f"ERROR: {status} -- this measurement is about the GPU.")
        return 1
    print(f"  {status} in {time.perf_counter() - started:.1f} s")

    clips = load_clips(durations)
    print(f"clips: {', '.join(f'{d:g}s' for d in durations)}")

    # One untimed pass. The first inference after a load pays for CUDA graph
    # capture and cuDNN autotuning, and charging that to whichever state
    # happened to run first is how a measurement invents an effect.
    print("warming the model (untimed) ...")
    for clip in clips.values():
        transcribe.transcribe_audio(model, clip)

    vram = {BASELINE: gpu_used_mib()}
    print(f"  VRAM with Whisper resident: {vram[BASELINE]} MiB")

    server = load = None
    rows, busy = [], []
    try:
        if RESIDENT in states or GENERATING in states:
            print("starting llama-server ...")
            machine = state_mod.Machine(state_mod.STOPPED)
            server = server_mod.Server(args.exe, args.model, machine)
            started = time.perf_counter()
            ok, reason = server.start()
            if not ok:
                print(f"ERROR: {reason}")
                return 1
            print(f"  ready in {time.perf_counter() - started:.1f} s at "
                  f"{server.base_url()}")
            vram[RESIDENT] = gpu_used_mib()
            print(f"  VRAM with both resident: {vram[RESIDENT]} MiB")

        for round_index in range(args.rounds):
            for state in states:
                if state == GENERATING:
                    load = Load(server.base_url(), server.api_key)
                    with load:
                        vram.setdefault(GENERATING, gpu_used_mib())
                        busy.append(nvidia_smi("utilization.gpu"))
                        rows += measure(model, clips, state, round_index)
                    continue
                if state == RESIDENT:
                    # An idle llama-server holds VRAM and no SM time. Wait for
                    # the card to say so rather than assuming it: this block
                    # follows the one that restarts llama-server.
                    if wait_until_idle() is False:
                        print("  WARNING: the GPU never went quiet; the "
                              "resident-idle readings in this round are not "
                              "what they claim to be")
                if state == BASELINE and server is not None:
                    server.stop("baseline round")
                rows += measure(model, clips, state, round_index)
                if state == BASELINE and server is not None:
                    ok, reason = server.start()
                    if not ok:
                        print(f"ERROR: could not restart llama-server: {reason}")
                        return 1
            print(f"  round {round_index + 1}/{args.rounds} done")
    finally:
        if server is not None:
            server.stop("measurement finished")

    if busy:
        print(f"\nGPU utilisation sampled inside the generating block: "
              f"{sorted(b for b in busy if b is not None)}")
    report(rows, vram, durations, states)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"rows": rows, "vram_mib": vram,
                       "durations": list(durations)}, f, indent=2)
        print(f"\nwrote {args.json}")
    return 0


def measure(model, clips, state, round_index):
    rows = []
    for seconds, clip in clips.items():
        started = time.perf_counter()
        text = transcribe.transcribe_audio(model, clip)
        elapsed = time.perf_counter() - started
        rows.append({"state": state, "round": round_index,
                     "audio_seconds": seconds, "seconds": elapsed,
                     "characters": len(text)})
    return rows


def report(rows, vram, durations, states):
    print("\n=== NFR-CG-3 ===")
    print(f"{'state':<16} {'n':>4} {'fit (a + b*audio)':>26} "
          f"{'10 s predicted':>15} {'max':>8}  VRAM")
    fits = {}
    for state in states:
        subset = [r for r in rows if r["state"] == state]
        if not subset:
            continue
        a, b = fit(subset)
        fits[state] = (a, b)
        worst = max(r["seconds"] for r in subset)
        print(f"{state:<16} {len(subset):>4} "
              f"{f'{a:.3f} + {b:.4f} x':>26} {a + 10 * b:>14.3f}s "
              f"{worst:>7.3f}s  {vram.get(state, '-')} MiB")

    if BASELINE in fits:
        a0, b0 = fits[BASELINE]
        print("\nagainst the baseline, per clip length:")
        header = "  " + "".join(f"{d:>10.0f}s" for d in durations)
        print(f"{'':<16}{header}")
        for state in states:
            if state == BASELINE:
                continue
            cells = []
            for seconds in durations:
                ours = median([r["seconds"] for r in rows
                               if r["state"] == state
                               and r["audio_seconds"] == seconds])
                theirs = median([r["seconds"] for r in rows
                                 if r["state"] == BASELINE
                                 and r["audio_seconds"] == seconds])
                cells.append(f"{ours / theirs:>10.2f}x" if theirs else " " * 11)
            print(f"{state:<16}  {''.join(cells)}")
            a, b = fits[state]
            print(f"{'':<16}   ratio of the fitted 10 s figure: "
                  f"{(a + 10 * b) / (a0 + 10 * b0):.2f}x")

    breaches = [r for r in rows if r["seconds"] >= 2.0]
    print(f"\nNFR-1 [2 s]: {len(rows) - len(breaches)}/{len(rows)} readings "
          f"inside the bound")
    for row in breaches:
        print(f"  {row['state']} {row['audio_seconds']:g}s -> "
              f"{row['seconds']:.3f}s")


if __name__ == "__main__":
    raise SystemExit(main())
