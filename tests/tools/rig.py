"""
The bench every Concierge instrument stands on (`concierge_design.md` section 7.2).

Two programs sit on top of this module and neither of them may know anything
about the app: `concierge_cli.py` is the REPL a human iterates the prompt in,
and `qualify.py` runs the section 6 qualification suite. They share this file
because they must share *exactly* one assembly of the harness -- a prompt judged
through one wiring and scored through another is a prompt nobody judged.

Three things it owns, and each of them is a decision rather than plumbing:

**A workspace, always.** `paths.APP_DIR` is redirected into a scratch directory
before anything is constructed, so `config.json`, `debug_log.txt`, the memory
note and the runtime state files all land there. The rig writes settings for a
living -- the write class of the suite is nothing but `set_config` calls -- and
an instrument that edits the developer's real configuration while measuring a
model is an instrument nobody can trust twice. The knowledge pack is resolved
*before* the redirect, because it is the one input that lives under the real
`APP_DIR`.

**An endpoint, either launched or attached.** `--model <gguf>` launches the
pinned llama-server through `server.Server`, which is the shipping launch path
including its four non-optional flags; `--base-url` attaches to anything
OpenAI-compatible. The second exists because section 6 says a candidate model
must be one flag, and not every candidate will be a GGUF this machine can host.

**A meter.** TTFT, decode rate and cold-load seconds are section 6 scorecard
columns, so they are measured by wrapping the transport and the client rather
than by timing `send()` from outside: the first SSE chunk is what NFR-CG-1 means
by first token, and it is the same measurement in both tool modes, where a
content-delta tap would miss native mode's `tool_calls` entirely.
"""

import hashlib
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if os.path.join(ROOT, "app") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "app"))

from ptt import config, paths, transcribe                          # noqa: E402
from ptt.concierge import agent as agent_mod                        # noqa: E402
from ptt.concierge import llm, server as server_mod, state, tools   # noqa: E402
from ptt.concierge import HARNESS_VERSION                           # noqa: E402

#: Resolved at import, before any workspace redirect can move `paths.APP_DIR`.
#: The pack is generated into the *real* `app/assets/`; the workspace holds only
#: what a run writes. `concierge_prompt_path()` derives from `PACKAGE_DIR` and is
#: unaffected, but it is captured here too so both inputs read from one place.
PACK_PATH = paths.knowledge_pack_path()
PROMPT_PATH = paths.concierge_prompt_path()

#: The bundled runtime and the spike's GGUF, as defaults. `spike/` is where the
#: 6.87 GB file already sits after the spike run, and gate 2.5 is told to leave
#: it there; `app/llama/` is where `build_llama_runtime.py` puts the binaries.
DEFAULT_EXE_CANDIDATES = (
    os.path.join(ROOT, "app", "llama", "llama-server.exe"),
    os.path.join(ROOT, "app", "llama", "bin", "llama-server.exe"),
    os.path.join(ROOT, "spike", "llama", "bin", "llama-server.exe"),
)
DEFAULT_MODEL_CANDIDATES = (
    os.path.join(ROOT, "app", "models", "concierge", "gemma-4-12B-it-Q4_K_M.gguf"),
    os.path.join(ROOT, "spike", "models", "gemma-4-12B-it-Q4_K_M.gguf"),
)
DEFAULT_WORKSPACE = os.path.join(HERE, ".rig")
SEEDS_DIR = os.path.join(HERE, "seeds")


def sha256_of_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_of_file(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ""


def _first_existing(candidates):
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return candidates[0]


def default_exe():
    return _first_existing(DEFAULT_EXE_CANDIDATES)


def default_model():
    return _first_existing(DEFAULT_MODEL_CANDIDATES)


# -- the workspace ------------------------------------------------------------

def open_workspace(path=DEFAULT_WORKSPACE, fresh=True):
    """
    Point `paths.APP_DIR` at a scratch directory and return it.

    A module-level rebind rather than a parameter, because `paths` is
    deliberately the single owner of every application-relative path and every
    caller reads it through a function at call time -- including
    `logging_setup.log_debug`, which appends to `paths.debug_log_path()` on every
    line. One assignment therefore redirects the settings file, both logs, the
    memory note, the state file and the API key together, which is what "the rig
    touches nothing of yours" has to mean in order to be true.
    """
    path = os.path.abspath(path)
    if fresh and os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)
    paths.APP_DIR = path
    return path


# -- measurement --------------------------------------------------------------

class Meter:
    """
    One record per generation: when it was asked for, when the first byte came
    back, when it finished, and what llama-server said it cost.

    Kept out of `llm.py` on purpose. The harness has no business timing itself
    -- section 6's numbers are the *instrument's* readings, and a client that
    measured its own throughput would be one more thing to keep correct in
    shipping code for the benefit of a test.
    """

    def __init__(self, clock=time.perf_counter):
        self._clock = clock
        self.records = []
        self._current = None

    def open(self):
        self._current = {"asked_at": self._clock(), "first_chunk_at": None,
                         "done_at": None, "usage": {}, "finish_reason": ""}
        return self._current

    def first_chunk(self):
        if self._current is not None and self._current["first_chunk_at"] is None:
            self._current["first_chunk_at"] = self._clock()

    def close(self, completion):
        if self._current is None:
            return
        self._current["done_at"] = self._clock()
        self._current["usage"] = dict(completion.usage or {})
        self._current["finish_reason"] = completion.finish_reason
        self.records.append(self._current)
        self._current = None

    def abandon(self):
        """A generation that raised. Recorded, so a stall is not an absent row."""
        if self._current is None:
            return
        self._current["done_at"] = self._clock()
        self._current["finish_reason"] = "forced-stop"
        self.records.append(self._current)
        self._current = None

    # -- readings ------------------------------------------------------------

    def since(self, index):
        """The records added after `index`, which is how one turn is isolated."""
        return self.records[index:]

    @staticmethod
    def ttft_seconds(records):
        """Time to the first SSE chunk of the turn's first generation."""
        for record in records:
            if record["first_chunk_at"] is not None:
                return record["first_chunk_at"] - record["asked_at"]
        return None

    @staticmethod
    def decode_rate(records):
        """
        Completion tokens per second of generation wall time, over a whole turn.

        Summed rather than averaged: a turn's cost is the tokens it produced over
        the time it took, and averaging per-generation rates would weight a
        three-token repair the same as a six-hundred-token answer.
        """
        tokens = 0
        seconds = 0.0
        for record in records:
            usage = record.get("usage") or {}
            count = usage.get("completion_tokens")
            if (not count or record["first_chunk_at"] is None
                    or record["done_at"] is None):
                continue
            tokens += int(count)
            seconds += max(record["done_at"] - record["first_chunk_at"], 1e-9)
        if not tokens or seconds <= 0:
            return None
        return tokens / seconds

    @staticmethod
    def completion_tokens(records):
        return sum(int((r.get("usage") or {}).get("completion_tokens") or 0)
                   for r in records)

    @staticmethod
    def prompt_tokens(records):
        return sum(int((r.get("usage") or {}).get("prompt_tokens") or 0)
                   for r in records)


class MeteredTransport:
    """`llm.HttpTransport`, with the arrival of the first chunk written down."""

    def __init__(self, meter, inner=None):
        self._meter = meter
        self._inner = inner or llm.HttpTransport()

    def post_stream(self, url, headers, payload, poll_interval):
        for line in self._inner.post_stream(url, headers, payload, poll_interval):
            if line is not None:
                self._meter.first_chunk()
            yield line


class MeteredClient(llm.Client):
    """
    `llm.Client`, bracketing each generation with a meter record.

    Subclassed rather than reimplemented so that every timeout, every SSE rule
    and the whole delta accumulator under measurement are the shipping ones. An
    instrument that reimplements the thing it measures measures itself.
    """

    def __init__(self, *args, meter=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.meter = meter or Meter()

    def stream(self, *args, **kwargs):
        self.meter.open()
        try:
            completion = super().stream(*args, **kwargs)
        except BaseException:
            self.meter.abandon()
            raise
        self.meter.close(completion)
        return completion


# -- seams --------------------------------------------------------------------

class FakeDevice:
    """What `audio.input_devices()` yields, minus PortAudio."""

    def __init__(self, index, name, hostapi):
        self.index = index
        self.name = name
        self.hostapi = hostapi


#: The fake device list. Fourteen rows, because that is what the reference
#: machine enumerated (`concierge_handoff.md` section 4) and the 16 KiB cap's
#: behaviour on this tool was argued against that number. The duplicates across
#: host APIs are not padding: they are what makes "which microphone?" a real
#: question rather than a lookup.
FAKE_DEVICES = (
    FakeDevice(0, "Microsoft Sound Mapper - Input", "MME"),
    FakeDevice(1, "Microphone (Yeti Stereo Microph", "MME"),
    FakeDevice(2, "Headset (WH-1000XM4 Hands-Free", "MME"),
    FakeDevice(3, "Line In (Realtek(R) Audio)", "MME"),
    FakeDevice(4, "Primary Sound Capture Driver", "Windows DirectSound"),
    FakeDevice(5, "Microphone (Yeti Stereo Microphone)", "Windows DirectSound"),
    FakeDevice(6, "Headset (WH-1000XM4 Hands-Free AG Audio)", "Windows DirectSound"),
    FakeDevice(7, "Line In (Realtek(R) Audio)", "Windows DirectSound"),
    FakeDevice(8, "Microphone (Yeti Stereo Microphone)", "Windows WASAPI"),
    FakeDevice(9, "Headset (WH-1000XM4 Hands-Free AG Audio)", "Windows WASAPI"),
    FakeDevice(10, "Line In (Realtek(R) Audio)", "Windows WASAPI"),
    FakeDevice(11, "Microphone (Yeti Stereo Microphone)", "Windows WDM-KS"),
    FakeDevice(12, "Headset (WH-1000XM4 Hands-Free AG Audio)", "Windows WDM-KS"),
    FakeDevice(13, "Line In (Realtek(R) Audio)", "Windows WDM-KS"),
)

#: The parts of `get_state()` that are genuinely about the moment rather than
#: about the configuration. Everything else is **derived from this session's
#: settings** by `fake_state()`, and that is not tidiness.
#:
#: The first draft was a flat constant saying `hotkey: "Right Ctrl"`. A scenario
#: that seeds `hotkey: [ralt]` and asks what the hotkey is then gets `ralt` from
#: `get_config` and "Right Ctrl" from `get_state` -- two seams disagreeing about
#: one fact -- and whichever the model reads, the scenario scores it against the
#: other. The shakedown run scored exactly that as a model failure (`sel-02`). An
#: instrument that contradicts itself measures nothing, and in the app these two
#: cannot disagree, because `UiState` is filled from the same `Settings` object.
FAKE_MOMENT = {
    "state": "idle",
    "last": "2.1 s for 47 words",
}


def fake_state(settings):
    """`get_state()`'s keys, agreeing with the settings the session actually has."""
    device = "cuda" if settings.get("use_gpu") else "cpu"
    model = settings.get("model")
    index = settings.get("audio_device")
    microphone = next(
        (d.name for d in FAKE_DEVICES if d.index == index),
        "Microphone (Yeti Stereo Microphone)" if index is None else f"device {index}")
    return {
        "state": FAKE_MOMENT["state"],
        "status_text": f"Ready ({device.upper()})",
        "detail": f"{model} on {device.upper()}",
        "hotkey": tools.hotkey_label(settings.get("hotkey")),
        "model": model,
        "device": device,
        "microphone": microphone,
        "last": FAKE_MOMENT["last"],
    }

#: A deterministic stand-in for the Measure button: seconds per tier against the
#: bundled 30-second clip, in the shape `Registry._run_benchmark` expects. These
#: are the reference machine's order of magnitude, not measurements, and
#: `--real-benchmark` is the flag that makes them real.
#:
#: **Keyed off `transcribe.MODEL_NAMES`, not written out.** The first draft
#: listed `"tiny"`, `"medium"` and so on; the catalogue's names are `tiny.en`
#: and `medium.en`, so every lookup missed and every tier fell back to the same
#: 2.0 s -- a fake that answers plausibly for a name that does not exist is the
#: exact defect `config.FIELDS` exists to stop, one directory over.
FAKE_BENCHMARK_SECONDS = dict(zip(
    transcribe.MODEL_NAMES, (0.61, 0.83, 1.42, 2.67, 6.02, 2.34)))


def fake_benchmark(model):
    if model not in FAKE_BENCHMARK_SECONDS:
        return {"error": True, "reason": f"{model!r} is not a tier"}
    time.sleep(0.2)          # long enough that the progress line means something
    return {"seconds": FAKE_BENCHMARK_SECONDS[model], "device": "cuda"}


def real_benchmark(settings):
    """
    Time one real transcription of the bundled clip, with no engine and no Qt.

    The same three calls `Engine._benchmark` makes -- load, clip, transcribe --
    reached directly, because constructing an `Engine` would start a hotkey poll
    and open an audio stream in order to measure a model. Off by default: this
    puts a Whisper model in VRAM beside a 9.4 GB LLM, which spike C5 measured the
    cost of, and a rig run is not the place to rediscover it.
    """
    def run(model):
        loaded, device = transcribe.load_model_with_fallback(
            model, settings.use_gpu, transcribe.cuda_available())
        audio = transcribe.load_benchmark_clip()
        started = time.perf_counter()
        transcribe.transcribe_audio(loaded, audio)
        return {"seconds": time.perf_counter() - started, "device": device}
    return run


def _real_devices():
    """PortAudio's real enumeration, imported late so `--fake-tools` needs none."""
    from ptt import audio
    return audio.input_devices()


# -- one session --------------------------------------------------------------

class Session:
    """
    One conversation: its own settings file, journal, memory note and agent.

    Per-scenario isolation is why this is a class rather than four locals. The
    suite runs forty-one scenarios against one loaded model, and a `set_config` from
    scenario 12 that survived into scenario 13 would make the second one score
    something nobody asked for. Each session gets a fresh `config.json`; the
    *prefix* is deliberately unchanged between them, because it is what
    llama-server's KV cache is keyed on and re-paying 7k tokens forty times is
    an hour of nothing.
    """

    def __init__(self, bench, name="session", seed_config=None,
                 seed_log=None, seed_previous_log=None, memory_text=""):
        self.bench = bench
        self.name = name
        self.dir = os.path.join(bench.workspace, "sessions", name)
        os.makedirs(self.dir, exist_ok=True)

        self.config_path = os.path.join(self.dir, "config.json")
        if seed_config:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(seed_config, f, indent=2)
        self.settings = config.load(self.config_path)

        self.log_path = seed_log or os.path.join(self.dir, "debug_log.txt")
        self.previous_log_path = (
            seed_previous_log or os.path.join(self.dir, "debug_log.prev.txt"))

        self.memory = tools.MemoryNote(
            os.path.join(self.dir, "concierge_memory.txt"),
            os.path.join(self.dir, "concierge_memory.prev.txt"))
        if memory_text:
            self.memory.write(memory_text)

        self.journal = agent_mod.Journal(settings=self.settings,
                                         memory=self.memory)
        self.applied = []
        self.progress = []
        self.registry = tools.Registry(
            self.settings,
            state_provider=lambda: fake_state(self.settings),
            devices=(lambda: FAKE_DEVICES) if bench.fake_tools else _real_devices,
            benchmark=(real_benchmark(self.settings) if bench.real_benchmark
                       else fake_benchmark),
            memory=self.memory,
            journal=self.journal,
            on_applied=lambda k, o, n: self.applied.append((k, o, n)),
            progress=self.progress.append,
            log_path=self.log_path,
            previous_log_path=self.previous_log_path,
            llm_resident=bench.resident,
            installed_sizes=((lambda: {}) if bench.fake_tools
                             else transcribe.installed_sizes),
        )
        self.context = agent_mod.Context(bench.pack, bench.prompt, self.registry)
        self.agent = agent_mod.Agent(
            bench.client, self.registry, self.context, self.journal,
            tool_mode=bench.tool_mode,
            on_token=self._on_token, on_tool=self._on_tool,
            on_notice=self._on_notice)

        self.tokens = []
        self.notices = []
        self.tool_events = []
        self.elapsed = 0.0
        #: Set by the REPL to echo tokens to the screen as they arrive. Left
        #: `None` by the suite, which wants the transcript and not the theatre.
        self.on_token = None

    # -- callbacks ------------------------------------------------------------

    def _on_token(self, text):
        self.tokens.append(text)
        if self.on_token is not None:
            self.on_token(text)

    def _on_tool(self, name, arguments, result):
        self.tool_events.append((name, arguments, result))

    def _on_notice(self, text):
        self.notices.append(text)

    # -- one turn -------------------------------------------------------------

    def send(self, text):
        """
        One user message, with the meter records this turn produced.

        Returns `(turn, records)`, or `(None, records)` when the context refused
        the turn -- `ContextOverflow` is caught here because section 5.0 rule 4
        says that failure is visible, and a traceback out of an instrument is not
        that.
        """
        self.tokens = []
        self.notices = []
        self.tool_events = []
        mark = len(self.bench.meter.records)
        started = time.perf_counter()
        try:
            turn = self.agent.send(text)
        except agent_mod.ContextOverflow as overflow:
            self.notices.append(overflow.message)
            turn = None
        self.elapsed = time.perf_counter() - started
        return turn, self.bench.meter.since(mark)

    def prefix_sha(self):
        return sha256_of_text(self.context.prefix())


# -- the bench ----------------------------------------------------------------

class Bench:
    """
    The endpoint, the two frozen inputs, and the meter. Sessions hang off it.

    Constructed from parsed arguments so that both instruments take the same
    flags and every scorecard can say which ones were used.
    """

    def __init__(self, args):
        self.args = args
        self.workspace = open_workspace(args.workspace, fresh=not args.keep)
        self.tool_mode = args.tool_mode
        self.fake_tools = args.fake_tools
        self.real_benchmark = getattr(args, "real_benchmark", False)

        self.pack = agent_mod.load_pack(args.pack)
        self.prompt = agent_mod.load_system_prompt(args.prompt)
        #: What gate 2.5 freezes. The prompt's digest is taken over what the
        #: model actually sees -- header stripped -- because that is the thing a
        #: scorecard claims to hold constant (Q17). The pack's is taken over the
        #: file, which is the artifact the build produces (Q20).
        self.prompt_sha = sha256_of_text(self.prompt)
        self.pack_sha = sha256_of_file(args.pack)
        self.pack_path = args.pack
        self.prompt_path = args.prompt

        self.meter = Meter()
        self.machine = state.Machine(state.STOPPED)
        self.server = None
        self.base_url = args.base_url or ""
        self.api_key = args.api_key or ""
        self.cold_load_seconds = None
        self.prewarm_seconds = None
        self.client = None

    # -- endpoint -------------------------------------------------------------

    def resident(self):
        """Whether a model is in VRAM on our account -- `run_benchmark` records it."""
        return self.server is not None and self.server.process is not None

    def start(self):
        """`(ok, reason)`. Launch llama-server, or attach to a running endpoint."""
        ok, reason = self._attach() if self.base_url else self._launch()
        if not ok:
            return False, reason
        self.client = MeteredClient(
            self.base_url, self.api_key,
            transport=MeteredTransport(self.meter),
            meter=self.meter,
            stall_timeout=self.args.stall_timeout,
            on_forced_stop=lambda message: print(f"  [{message}]"))
        return True, None

    def _attach(self):
        import urllib.parse
        parts = urllib.parse.urlsplit(self.base_url)
        port, host = parts.port or 80, parts.hostname or "127.0.0.1"
        if not server_mod.Probe(timeout=5.0).healthy(port, host, self.api_key):
            return False, f"nothing healthy answered at {self.base_url}"
        # An attached endpoint's cold-load figure is not ours to claim: somebody
        # else paid it, possibly hours ago. Recorded as absent rather than as a
        # number that happens to be small, because section 6 compares this column
        # between candidates.
        self.cold_load_seconds = None
        self.machine.to(state.LOADING, "attaching")
        if self.args.prewarm:
            self.prewarm_seconds = self._time_prewarm(port, host, self.api_key)
        self.machine.to(state.READY, "attached to a running endpoint")
        return True, None

    def _launch(self):
        if not os.path.exists(self.args.exe):
            return False, (f"no llama-server at {self.args.exe} -- pass --exe, "
                           f"or run build_llama_runtime.py")
        if not self.args.model or not os.path.exists(self.args.model):
            return False, (f"no GGUF at {self.args.model!r} -- pass --model, or "
                           f"--base-url to attach to a running endpoint")
        started = time.perf_counter()
        self.server = server_mod.Server(
            self.args.exe, self.args.model, self.machine,
            context_size=self.args.context_size,
            ready_timeout=self.args.ready_timeout,
            prewarm=self._prewarm if self.args.prewarm else None)
        ok, reason = self.server.start()
        if not ok:
            return False, reason
        #: NFR-CG-2's number: launch to *genuinely ready*, prewarm included,
        #: because `ready` is defined as "the first message will be fast".
        self.cold_load_seconds = time.perf_counter() - started
        self.base_url = self.server.base_url()
        self.api_key = self.server.api_key
        return True, None

    def _prewarm(self, port, api_key):
        """
        `server.Server`'s prewarm hook: pay the pack's cost inside `loading`.

        Deliberately the rig's own prefix and not a token of filler. Spike C3
        measured 7.17 s to first token on the first request that carries the
        pack and 0.345 s afterwards, and a prewarm that sends different bytes
        warms a prefix nothing will ever hit again.
        """
        self.prewarm_seconds = self._time_prewarm(port, "127.0.0.1", api_key)

    def _time_prewarm(self, port, host, api_key):
        probe = Session(self, name="_prewarm")
        client = llm.Client(f"http://{host}:{port}", api_key,
                            transport=llm.HttpTransport())
        messages = [{"role": "system", "content": probe.context.prefix()},
                    {"role": "user", "content": "ready?"}]
        started = time.perf_counter()
        client.stream(messages, None, self.tool_mode, max_tokens=1)
        return time.perf_counter() - started

    def session(self, **kwargs):
        return Session(self, **kwargs)

    def stop(self):
        if self.server is not None:
            self.server.stop("the rig is finished")
            self.server = None

    # -- provenance -----------------------------------------------------------

    def provenance(self):
        """
        Everything a scorecard row needs about *what was measured*, not how well.

        The two digests are the load-bearing entries: without them section 6's
        suite measures the prompt and the pack rather than the model (Q17, Q20),
        and two candidates scored either side of a prompt edit are not
        comparable.
        """
        return {
            "harness_version": HARNESS_VERSION,
            "model": (os.path.basename(self.args.model) if self.args.model
                      else f"(attached) {self.base_url}"),
            "tool_mode": self.tool_mode,
            "reasoning": self.args.reasoning,
            "context_size": self.args.context_size,
            "seams": "fakes" if self.fake_tools else "real",
            "system_prompt_sha256": self.prompt_sha,
            "system_prompt_path": relative(self.prompt_path),
            "system_prompt_chars": len(self.prompt),
            "knowledge_pack_sha256": self.pack_sha,
            "knowledge_pack_path": relative(self.pack_path),
            "knowledge_pack_chars": len(self.pack),
            "cold_load_seconds": _round(self.cold_load_seconds),
            "prewarm_seconds": _round(self.prewarm_seconds),
        }


def relative(path):
    try:
        return os.path.relpath(path, ROOT).replace("\\", "/")
    except ValueError:
        return path


def _round(value, places=3):
    return None if value is None else round(value, places)


# -- transcripts --------------------------------------------------------------

class Transcript:
    """
    Two files per run: `transcript.jsonl` to grade, `transcript.md` to read.

    Both, because they answer different questions. The JSONL is what a scorer and
    a diff work on; the Markdown is what a person reads at one in the morning
    deciding whether the prompt got better, and a person will not read JSONL.
    """

    def __init__(self, directory, title):
        os.makedirs(directory, exist_ok=True)
        self.directory = directory
        self.jsonl = os.path.join(directory, "transcript.jsonl")
        self.markdown = os.path.join(directory, "transcript.md")
        self.append_markdown(f"# {title}\n")

    def event(self, kind, **fields):
        record = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind}
        record.update(fields)
        with open(self.jsonl, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return record

    def provenance(self, data):
        self.event("provenance", **data)
        rows = "\n".join(f"| `{k}` | {v} |" for k, v in data.items())
        self.append_markdown(f"\n| field | value |\n|---|---|\n{rows}\n")

    def turn(self, user, turn, records, session, **extra):
        self.event(
            "turn", user=user,
            reply=(turn.reply if turn else ""),
            iterations=(turn.iterations if turn else 0),
            forced=(turn.forced if turn else "context-overflow"),
            tool_calls=[{"tool": n, "arguments": a, "result": r}
                        for n, a, r in (turn.tool_calls if turn else ())],
            notices=list(session.notices),
            trims=list(turn.trims if turn else ()),
            generations=len(records),
            ttft_seconds=_round(Meter.ttft_seconds(records)),
            decode_tokens_per_second=_round(Meter.decode_rate(records), 2),
            prompt_tokens=Meter.prompt_tokens(records),
            completion_tokens=Meter.completion_tokens(records),
            elapsed_seconds=_round(session.elapsed),
            **extra)
        lines = [f"\n## You\n\n{user}\n"]
        for name, arguments, result in (turn.tool_calls if turn else ()):
            lines.append(f"\n> `{name}({json.dumps(arguments)})` -> "
                         f"`{_short(json.dumps(result, ensure_ascii=False))}`\n")
        for notice in session.notices:
            lines.append(f"\n> _{notice}_\n")
        lines.append(f"\n## Concierge\n\n{turn.reply if turn else '(no reply)'}\n")
        self.append_markdown("".join(lines))

    def append_markdown(self, text):
        with open(self.markdown, "a", encoding="utf-8") as f:
            f.write(text)


def _short(text, limit=400):
    return text if len(text) <= limit else text[:limit] + "... (transcript cut)"


# -- arguments ----------------------------------------------------------------

def add_common_arguments(parser):
    """The flags both instruments take, so one line describes a run."""
    endpoint = parser.add_argument_group("endpoint")
    endpoint.add_argument(
        "--model", default=default_model(),
        help="GGUF to launch llama-server with. Section 6's 'a candidate model "
             "is one flag'.")
    endpoint.add_argument(
        "--exe", default=default_exe(),
        help="llama-server.exe (default: the bundled runtime, then the spike's).")
    endpoint.add_argument(
        "--base-url", default="",
        help="Attach to a running OpenAI-compatible endpoint instead of "
             "launching one. Any endpoint, which is the other half of 'one "
             "flag'.")
    endpoint.add_argument("--api-key", default="",
                          help="Bearer token for --base-url.")
    endpoint.add_argument("--context-size", type=int,
                          default=server_mod.CONTEXT_SIZE)
    endpoint.add_argument("--ready-timeout", type=float,
                          default=server_mod.SERVER_READY_TIMEOUT_SEC)
    endpoint.add_argument("--stall-timeout", type=float,
                          default=llm.STALL_TIMEOUT_SEC)
    endpoint.add_argument(
        "--no-prewarm", dest="prewarm", action="store_false",
        help="Skip the knowledge-pack prewarm and pay it on the first message.")
    endpoint.set_defaults(prewarm=True)
    endpoint.add_argument(
        "--reasoning", default="off",
        help="Recorded in the scorecard as this model's reasoning budget. The "
             "launch path passes -rea off; this flag is what a future "
             "reasoning-qualified model changes (design section 6).")

    harness = parser.add_argument_group("harness")
    harness.add_argument(
        "--tool-mode", choices=("grammar", "native"), default="grammar",
        help="Default grammar until gate 2.5 (Q15): it is the conformance "
             "reference and CON-CG-5's model-agnostic floor.")
    harness.add_argument(
        "--fake-tools", action="store_true",
        help="Deterministic seams -- no PortAudio, no Whisper, no installed "
             "model sizes.")
    harness.add_argument(
        "--real-benchmark", action="store_true",
        help="Let run_benchmark load Whisper and time the bundled clip for "
             "real. Costs VRAM beside the LLM.")
    harness.add_argument("--pack", default=PACK_PATH,
                         help="The knowledge pack. Hashed into every scorecard.")
    harness.add_argument("--prompt", default=PROMPT_PATH,
                         help="system_prompt.md. Hashed into every scorecard.")
    harness.add_argument("--workspace", default=DEFAULT_WORKSPACE,
                         help="Scratch APP_DIR. Wiped at start unless --keep.")
    harness.add_argument("--keep", action="store_true",
                         help="Keep the previous workspace instead of wiping it.")
    return parser


def describe(bench):
    what = (os.path.basename(bench.args.model) if bench.args.model
            else "whatever is listening")
    return (f"{what} at {bench.base_url or '(not started)'} - "
            f"tool_mode={bench.tool_mode}, "
            f"seams={'fakes' if bench.fake_tools else 'real'}")
