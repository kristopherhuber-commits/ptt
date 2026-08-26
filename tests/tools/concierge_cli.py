"""
The Concierge's CLI rig (`concierge_design.md` section 7.2).

A terminal REPL over the **real** agent loop, against a **real** llama-server,
with real seams or `--fake-tools`. Zero app involvement: no Qt is imported, no
`QApplication` exists, and `paths.APP_DIR` points at a scratch workspace, so a
session here cannot touch the settings or the log of the installed application.

This is the Concierge's equivalent of the pinned-window probe -- an instrument,
shipped in `tests/`, never in the distribution. It exists because three things
cannot be judged anywhere else:

- **The system prompt.** L1 runs it against fakes, which proves the loop and
  proves nothing about the prompt. Session 2 measured three revisions here and
  found that the thing everyone blamed on the prompt -- spike C7a's rider, eight
  of ten prompts calling a tool where several wanted a reply -- is a property of
  the *request shape* and moves not at all with the wording. That is a sentence
  no unit test can write.
- **Grammar conformance under the real prefix.** The schema is generated; the
  question is whether *this* model, behind 7k tokens of pack, stays inside it.
- **Whether a refusal can be acted on.** Every L1 test asserts the refusal's
  *reason*, which was correct; no unit test can ask whether the `hint` beside it
  tells the model what to do next. It did not (`development_history.md` #19).
- **The numbers.** TTFT, decode rate and cold load are properties of a machine
  with a GPU in it.

    python tests/tools/concierge_cli.py                       # bundled runtime + pinned GGUF
    python tests/tools/concierge_cli.py --fake-tools
    python tests/tools/concierge_cli.py --tool-mode native
    python tests/tools/concierge_cli.py --base-url http://127.0.0.1:8080
    python tests/tools/concierge_cli.py --ask "what does the pre-roll buffer do?"

Every session writes `transcript.jsonl` and `transcript.md` under
`tests/tools/runs/<stamp>/`, because a prompt iteration whose evidence was in a
terminal scrollback is a prompt iteration nobody can check.
"""

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import rig                                                    # noqa: E402
from ptt.concierge import llm, state                          # noqa: E402

RUNS_DIR = os.path.join(HERE, "runs")

BANNER = """\
Concierge rig -- the real agent loop, no app, no Qt.
Type a message, or /help for the commands. /quit leaves and stops the runtime.
"""

HELP = """\
  /help                 this
  /quit  /exit          stop the runtime and leave
  /reset                fresh session (FR-CG-13: pack + note, no transcript)
  /state                the state machine, the endpoint and the seams
  /config [key]         what the settings currently say
  /changes              this session's undo journal
  /undo <n>             undo change #n
  /restore              replay every pending inverse, newest first
  /memory [text]        show the note, or replace it by hand
  /tools                the registry, as the model is shown it
  /schema               the generated grammar schema (design section 4.1)
  /prefix               the fixed prefix: size, budget and digest
  /mode <grammar|native>  switch request shape; restarts nothing
  /reload               re-read system_prompt.md and rebuild the session
  /last                 the last turn's timings and tool calls
  /seed <name>          point read_log at tests/tools/seeds/<name>.log
"""


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="concierge_cli",
        description="REPL over the real Concierge agent loop.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    rig.add_common_arguments(parser)
    session_group = parser.add_argument_group("session")
    session_group.add_argument(
        "--ask", action="append", default=[],
        help="Send this message and exit. Repeatable, and the messages share "
             "one session, which is how a multi-turn probe is scripted.")
    session_group.add_argument(
        "--fresh-per-ask", action="store_true",
        help="Give each --ask its own session. What prompt iteration needs: "
             "one message's tool selection is not evidence about the prompt if "
             "the message before it left four tool results in the history.")
    session_group.add_argument(
        "--seed-log", default="",
        help="Seed read_log from tests/tools/seeds/<name>.log.")
    session_group.add_argument(
        "--memory", default="",
        help="Start with this text in the memory note.")
    session_group.add_argument(
        "--runs-dir", default=RUNS_DIR,
        help="Where the transcript goes.")
    args = parser.parse_args(argv)

    bench = rig.Bench(args)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    transcript = rig.Transcript(os.path.join(args.runs_dir, f"cli-{stamp}"),
                                f"Concierge rig session {stamp}")

    print(BANNER)
    print(f"  workspace   {bench.workspace}")
    print(f"  prompt      {rig.relative(bench.prompt_path)} "
          f"({len(bench.prompt)} chars, sha {bench.prompt_sha[:12]})")
    print(f"  pack        {rig.relative(bench.pack_path)} "
          f"({len(bench.pack)} chars, sha {bench.pack_sha[:12]})")
    print(f"  transcript  {transcript.directory}")
    print(f"\n  starting {os.path.basename(args.model) if not args.base_url else args.base_url} ...")

    started = time.perf_counter()
    ok, reason = bench.start()
    if not ok:
        print(f"  FAILED: {reason}")
        return 2
    print(f"  {bench.machine.state} in {time.perf_counter() - started:.1f}s "
          f"({rig.describe(bench)})\n")
    transcript.provenance(bench.provenance())

    console = Console(bench, transcript, args)
    try:
        if args.ask:
            for index, message in enumerate(args.ask):
                if args.fresh_per_ask and index:
                    console.session = console.new_session()
                print(f"you> {message}")
                console.turn(message)
        else:
            console.loop()
    finally:
        bench.stop()
        print(f"\n  transcript: {transcript.markdown}")
    return 0


class Console:
    """The REPL itself: one session at a time, and the commands that rebuild it."""

    def __init__(self, bench, transcript, args):
        self.bench = bench
        self.transcript = transcript
        self.args = args
        self.seed = args.seed_log
        self.counter = 0
        self.last = None
        self.session = self.new_session()

    # -- session ------------------------------------------------------------

    def new_session(self):
        self.counter += 1
        seed_log = None
        if self.seed:
            seed_log = os.path.join(rig.SEEDS_DIR, f"{self.seed}.log")
            if not os.path.exists(seed_log):
                print(f"  no seed log at {seed_log}; read_log will find nothing")
        session = self.bench.session(name=f"cli-{self.counter}",
                                     seed_log=seed_log,
                                     memory_text=self.args.memory)
        session.on_token = _echo
        return session

    # -- the loop -----------------------------------------------------------

    def loop(self):
        while True:
            try:
                line = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if not line:
                continue
            if line.startswith("/"):
                if self.command(line) is False:
                    return
                continue
            self.turn(line)

    def turn(self, text):
        print("\nconcierge> ", end="", flush=True)
        self.bench.machine.to(state.GENERATING, "sending")
        turn, records = self.session.send(text)
        self.bench.machine.to(state.READY, "idle")
        if not self.session.tokens and turn is not None:
            # Grammar mode streams the JSON envelope, so `on_token` has already
            # printed it; native mode with a tool call streams nothing at all,
            # and an empty line under the prompt would read as no answer.
            print(turn.reply, end="")
        print()

        for name, arguments, result in (turn.tool_calls if turn else ()):
            print(f"  [{name}({_compact(arguments)}) -> {_compact(result, 160)}]")
        for notice in self.session.notices:
            print(f"  [{notice}]")
        for change in self.session.journal.pending():
            print(f"  [#{change.seq} {change.key}: {change.old!r} -> "
                  f"{change.new!r} - /undo {change.seq}]")

        ttft = rig.Meter.ttft_seconds(records)
        rate = rig.Meter.decode_rate(records)
        print(f"  [{len(records)} generation(s), "
              f"{self.session.elapsed:.2f}s, "
              f"ttft {_fmt(ttft)}s, {_fmt(rate, 1)} tok/s]\n")

        self.transcript.turn(text, turn, records, self.session)
        self.last = (turn, records)

    # -- commands -----------------------------------------------------------

    def command(self, line):
        name, _, rest = line[1:].partition(" ")
        rest = rest.strip()
        handler = getattr(self, f"_cmd_{name}", None)
        if handler is None:
            print(f"  no such command: /{name}. /help lists them.")
            return True
        return handler(rest)

    def _cmd_help(self, _rest):
        print(HELP)

    def _cmd_quit(self, _rest):
        return False

    _cmd_exit = _cmd_quit

    def _cmd_reset(self, _rest):
        self.session = self.new_session()
        print(f"  fresh session ({self.session.name}): the pack and the note, "
              f"nothing else")

    def _cmd_reload(self, _rest):
        """Re-read the prompt from disk. The iteration loop, made short."""
        from ptt.concierge import agent as agent_mod
        self.bench.prompt = agent_mod.load_system_prompt(self.bench.prompt_path)
        self.bench.prompt_sha = rig.sha256_of_text(self.bench.prompt)
        self.session = self.new_session()
        print(f"  reloaded {rig.relative(self.bench.prompt_path)}: "
              f"{len(self.bench.prompt)} chars, sha "
              f"{self.bench.prompt_sha[:12]}")
        print("  the KV prefix has changed, so the next message pays the pack "
              "again")

    def _cmd_state(self, _rest):
        machine = self.bench.machine
        print(f"  state       {machine.state}"
              + (f" ({machine.detail})" if machine.detail else ""))
        print(f"  endpoint    {rig.describe(self.bench)}")
        print(f"  session     {self.session.name}, "
              f"{len(self.session.agent.entries())} history entrie(s)")
        print(f"  seed log    {self.seed or '(none)'}")
        print(f"  workspace   {self.session.dir}")

    def _cmd_config(self, rest):
        result = self.session.registry.call("get_config",
                                            {"key": rest} if rest else {})
        print(json.dumps(result, indent=2, ensure_ascii=False)[:4000])

    def _cmd_changes(self, _rest):
        changes = self.session.journal.changes()
        if not changes:
            print("  nothing changed this session")
            return
        pending = {c.seq for c in self.session.journal.pending()}
        for change in changes:
            mark = " " if change.seq in pending else "x"
            print(f"  [{mark}] #{change.seq} {change.kind} {change.key}: "
                  f"{change.old!r} -> {change.new!r}")

    def _cmd_undo(self, rest):
        try:
            seq = int(rest)
        except ValueError:
            print("  /undo <n>, where n is a change number from /changes")
            return
        ok, reason = self.session.journal.undo(seq)
        print(f"  {'undone' if ok else 'refused: ' + str(reason)}")

    def _cmd_restore(self, _rest):
        restored, failures = self.session.journal.restore()
        print(f"  {len(restored)} reverted, {len(failures)} refused")
        for change, reason in failures:
            print(f"    #{change.seq} {change.key}: {reason}")

    def _cmd_memory(self, rest):
        if not rest:
            note = self.session.memory.read()
            print(f"  {note!r}" if note else "  (nothing recorded yet)")
            return
        ok, reason = self.session.memory.write(rest)
        print(f"  {'written' if ok else 'refused: ' + str(reason)}")

    def _cmd_tools(self, _rest):
        print(self.session.context.tool_digest())

    def _cmd_schema(self, _rest):
        schema = llm.grammar_schema(self.session.registry)
        text = json.dumps(schema, indent=2)
        print(f"  {len(json.dumps(schema))} bytes, "
              f"{len(schema['oneOf'][1]['properties']['tool']['oneOf'])} "
              f"tool branches")
        print(text)

    def _cmd_prefix(self, _rest):
        context = self.session.context
        print(f"  prefix      {len(context.prefix())} chars, "
              f"~{context.prefix_tokens()} tokens")
        print(f"  history     ~{context.history_budget_tokens()} tokens left "
              f"of {context.window_tokens} minus {context.headroom_tokens} "
              f"headroom")
        print(f"  digest      {self.session.prefix_sha()}")

    def _cmd_mode(self, rest):
        if rest not in ("grammar", "native"):
            print("  /mode grammar | native")
            return
        self.bench.tool_mode = rest
        self.session.agent.tool_mode = rest
        print(f"  tool_mode = {rest} (one server serves both; nothing restarted)")

    def _cmd_seed(self, rest):
        self.seed = rest
        self.session = self.new_session()
        print(f"  read_log now reads "
              f"{os.path.join(rig.SEEDS_DIR, rest + '.log') if rest else 'this session'}")

    def _cmd_last(self, _rest):
        if self.last is None:
            print("  nothing sent yet")
            return
        turn, records = self.last
        for index, record in enumerate(records, 1):
            usage = record.get("usage") or {}
            first = record["first_chunk_at"]
            print(f"  generation {index}: "
                  f"ttft {_fmt(None if first is None else first - record['asked_at'])}s, "
                  f"{usage.get('prompt_tokens', '?')} prompt + "
                  f"{usage.get('completion_tokens', '?')} completion tokens, "
                  f"finish={record['finish_reason']}")
        if turn is not None:
            print(f"  iterations {turn.iterations}, forced={turn.forced or 'no'}")
            for trim in turn.trims:
                print(f"  trim: {trim}")


def _echo(text):
    sys.stdout.write(text)
    sys.stdout.flush()


def _compact(value, limit=120):
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "..."


def _fmt(value, places=3):
    return "-" if value is None else f"{value:.{places}f}"


if __name__ == "__main__":
    raise SystemExit(main())
