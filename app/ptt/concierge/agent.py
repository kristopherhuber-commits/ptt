"""
D-CG-4 / D-CG-5 -- the loop, the context budget, and the undo journal.

The loop is hand-written and about two hundred lines (CON-CG-3): one system
prefix, one tool registry, a while loop that forwards tool results until the
model stops calling tools. No LangChain-class framework, and nothing here is
generic -- every branch exists because a named requirement asked for it.

Three things in this module are load-bearing and each closes a finding:

**The memory note goes LAST in the fixed prefix** (design 5, Q16 rider). The KV
cache is a *prefix* cache; spike C3 measured 8.10 s for a changed prefix and
1.53 s even on the return to a partly-evicted one. The note is the one mutable
thing in the fixed block, so everything immutable goes first. Placed anywhere
else, every `update_memory` call would invalidate about 10k tokens of cached
prefix and make the *next* message pay several seconds with no visible cause --
an NFR-CG-1 breach the agent inflicts on itself, appearing at random.

**Trimming is five numbered rules, and rule 5 is that every trim is logged**
(design 5.0, Q16b). A trim is not only a budget event: it invalidates the cache
from the trim point onward, so the answer gets worse *and* the next turn gets
slower. `OBS-1` forbids exactly that shape of silent double degradation.

**The undo journal covers `update_memory`, not just `set_config`** (Q22).
FR-CG-3 says "every Concierge-made change", which is not "every setting
change", and the note is the only durable state design 5.1 permits.
"""

import json
import os
import time
from typing import NamedTuple

from ptt.logging_setup import log_debug
from ptt.concierge import llm
from ptt.concierge.tools import Registry, encoded

#: Design 5's budget, in tokens of a 32k window:
#: pack ~8k + system rules ~1k + tools/schema ~1k + memory note <=1k
#: + history ~17k + generation headroom 4k.
CONTEXT_WINDOW_TOKENS = 32768
GENERATION_HEADROOM_TOKENS = 4096

#: The crude measure the whole design uses. Tokenising properly needs a
#: `/tokenize` round trip, and L1 forbids HTTP -- so the budget is arithmetic on
#: characters, deliberately, and errs by over-counting rather than under.
CHARS_PER_TOKEN = 4

#: Design 4.3. Six tool iterations per user message, then the harness forces a
#: reply from what it has. A repair counts as an iteration: an unparseable
#: decision that keeps being unparseable must not get six *extra* attempts.
MAX_TOOL_ITERATIONS = 6

#: Design 5's revisit trigger. Above this the pack is no longer a thing that
#: comfortably fits, and RAG becomes a design change proposed through that
#: document rather than something bolted on quietly.
PACK_REVISIT_TOKENS = 16000


def approx_tokens(text):
    """Characters as tokens, at the design's stated measure."""
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


# -- the undo journal ---------------------------------------------------------

class Change(NamedTuple):
    """
    One Concierge-made change, and how to put it back.

    `kind` is `config` or `memory`, which is the whole of FR-CG-3's scope: the
    two tools that write. `old` is the inverse -- everything Undo needs, held
    per change rather than as a whole-config snapshot, which is what makes Q24's
    session restore touch only keys the agent wrote.
    """
    seq: int
    kind: str
    key: str
    old: object
    new: object
    at: str


class Journal:
    """
    The session's change list (FR-CG-3, handoff 5).

    Session-scoped by design (T5 item 4) -- which is precisely why the memory
    note *also* keeps a `.prev` file: a journal that dies with the session
    cannot repair a bad note discovered tomorrow.

    `restore()` replays inverses in **reverse order**, touching only keys this
    journal recorded (Q24). The earlier design snapshotted the whole config on
    panel open and wrote it back wholesale, which also reverted every change the
    *user* made by hand in the panels while the chat was open -- behind a
    confirm dialog that said nothing about it. Reverse order is well defined
    when several entries touch one key: the last write is undone first and the
    earliest entry's `old` is what survives.
    """

    def __init__(self, settings=None, memory=None, on_change=None, clock=None):
        self._settings = settings
        self._memory = memory
        self._on_change = on_change or (lambda _change: None)
        self._clock = clock or (lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))
        self._changes = []
        self._undone = set()
        self._seq = 0

    def record(self, kind, key, old, new):
        self._seq += 1
        change = Change(self._seq, kind, key, old, new, self._clock())
        self._changes.append(change)
        log_debug(f"Concierge journal #{change.seq}: {kind} {key} {old!r} -> {new!r}")
        try:
            self._on_change(change)
        except Exception as e:
            log_debug(f"ERROR in Concierge journal callback: {str(e)}")
        return change

    def changes(self):
        """Every recorded change, oldest first, undone ones included."""
        return tuple(self._changes)

    def pending(self):
        """The changes a session restore would still have to put back."""
        return tuple(c for c in self._changes if c.seq not in self._undone)

    def undo(self, seq):
        """One chip's Undo. `(ok, reason)`."""
        for change in self._changes:
            if change.seq == seq:
                if seq in self._undone:
                    return False, f"change #{seq} has already been undone"
                ok, reason = self._apply_inverse(change)
                if ok:
                    self._undone.add(seq)
                return ok, reason
        return False, f"there is no change #{seq} in this session"

    def restore(self):
        """
        The header's `↺ session`. Returns `(restored, failures)`.

        Reverse order, and only keys this journal touched. A change that has
        already been undone individually is skipped rather than replayed: the
        end state is the same either way, and skipping keeps the log honest
        about what this action actually did.
        """
        restored, failures = [], []
        for change in reversed(self._changes):
            if change.seq in self._undone:
                continue
            ok, reason = self._apply_inverse(change)
            if ok:
                self._undone.add(change.seq)
                restored.append(change)
            else:
                failures.append((change, reason))
        log_debug(
            f"Concierge session restore: {len(restored)} change(s) reverted, "
            f"{len(failures)} refused"
        )
        return restored, failures

    def _apply_inverse(self, change):
        if change.kind == "config":
            if self._settings is None:
                return False, "no settings object is attached"
            ok, reason = self._settings.set(change.key, change.old)
            if ok:
                log_debug(f"Concierge undo #{change.seq}: {change.key} -> {change.old!r}")
            return ok, reason
        if change.kind == "memory":
            if self._memory is None:
                return False, "no memory note is attached"
            ok, reason = self._memory.write(change.old)
            if ok:
                log_debug(f"Concierge undo #{change.seq}: memory note restored")
            return ok, reason
        return False, f"unknown change kind {change.kind!r}"


# -- the conversation ---------------------------------------------------------

class Entry(NamedTuple):
    """
    One item of conversation history.

    `turn` is what the trimming rules count in: it increments on every user
    message, so "older than 2 turns" is arithmetic rather than a heuristic about
    message counts.
    """
    role: str
    content: str
    turn: int
    tool: str = ""
    arguments: dict = None
    elided: bool = False

    def cost(self):
        return approx_tokens(self.content) + 4      # the role and framing

    def message(self):
        return {"role": self.role, "content": self.content}


class ContextOverflow(Exception):
    """
    Rule 4: the turn does not fit and the harness says so.

    Carries the sentence the chat shows, because a turn that fails silently is
    the one outcome design 5.0 rules out explicitly.
    """

    def __init__(self, reason, message):
        super().__init__(reason)
        self.reason = reason
        self.message = message


class Context:
    """
    Assembles one request. Owns the prefix order and the five trimming rules.

    The prefix is a **single** system message, not several, so that its byte
    order is exactly its token order: llama-server caches a prefix, and a
    reordering that is invisible in a list of messages is a total cache miss.
    """

    def __init__(self, pack, system_prompt, registry, memory=None,
                 window_tokens=CONTEXT_WINDOW_TOKENS,
                 headroom_tokens=GENERATION_HEADROOM_TOKENS):
        self.pack = pack or ""
        self.system_prompt = system_prompt or ""
        self.registry = registry
        self.memory = memory
        self.window_tokens = window_tokens
        self.headroom_tokens = headroom_tokens

    # -- the fixed prefix ---------------------------------------------------

    #: The line that goes above the tool list.
    #:
    #: The digest is the last thing in the prefix before the conversation, and it
    #: was a bare list of capabilities -- so the most recent and most concrete
    #: instruction the model held was "here are eight things you can do", with
    #: nothing anywhere near it about when not to. Saying so belongs where the
    #: list is rather than three thousand characters earlier.
    #:
    #: **Not measured to help, and it is not what fixes tool selection.** Session
    #: 2 ran the reply/tool probe set as a 2x2 -- prompt v1 and v4 crossed with
    #: grammar and native mode -- and selection moved with the request shape and
    #: with nothing else (7/14 either prompt in grammar, 13/14 either prompt in
    #: native). This sentence is here because a tool list with no restraint in it
    #: is a gap, not because it closes one. `system_prompt.md`'s header carries
    #: the numbers.
    DIGEST_HEADER = (
        "Each of these reads or changes **this machine**. None of them returns "
        "documentation: what a setting means, why it exists and what goes wrong "
        "are already above, and no tool will tell you again. Call one only when "
        "the answer depends on how this computer is configured right now."
    )

    def tool_digest(self):
        """
        The tool list, as the model reads it.

        Present in **both** modes. Grammar mode constrains the sampler, which
        makes an unregistered call impossible but tells the model nothing about
        *which* call to make; native mode passes the same registry through the
        chat template. Selection quality is a section 6 threshold, so the model
        gets the names and the summaries either way.
        """
        lines = [self.DIGEST_HEADER, ""]
        for tool in self.registry.tools():
            args = ", ".join(
                f"{a.name}{'' if a.required else '?'}: {a.json_type}"
                for a in tool.args
            ) or "no arguments"
            lines.append(f"- {tool.name}({args}) -- {tool.summary}")
        return "\n".join(lines)

    def prefix(self):
        """
        The fixed block, in design 5's order: **note last**.

        pack -> system rules -> tools -> memory note. Everything immutable
        first. The note is the one thing in here that a tool can change
        mid-session, and the KV cache is a prefix cache.
        """
        note = self.memory.read() if self.memory is not None else ""
        parts = [
            "# What you know about this application\n\n" + self.pack,
            "# How you behave\n\n" + self.system_prompt,
            "# Tools you can call\n\n" + self.tool_digest(),
        ]
        # Last, always. See this class's docstring and design section 5.
        parts.append(
            "# What you remember about this user\n\n"
            + (note.strip() or "(nothing recorded yet)")
        )
        return "\n\n".join(parts)

    def prefix_tokens(self):
        return approx_tokens(self.prefix())

    def history_budget_tokens(self):
        """What is left for the conversation after the prefix and the headroom."""
        return (self.window_tokens - self.headroom_tokens - self.prefix_tokens())

    # -- the five rules ------------------------------------------------------

    def assemble(self, entries, current_turn):
        """
        Build the messages for one request, trimming to fit. `(messages, trims)`.

        Design 5.0, in order:

        1. Never dropped: the pack, the system rules, the tool schema, the
           memory note, the current user message and everything after it.
        2. First, replace the *body* of any tool result older than 2 turns with
           a one-line summary.
        3. Then drop the oldest complete exchange, repeating until it fits.
        4. If it still does not fit, the turn fails visibly.
        5. Every trim writes one line to `debug_log.txt` naming what was dropped
           **and that the KV cache was invalidated from that point.**
        """
        budget = self.history_budget_tokens()
        working = list(entries)
        trims = []

        if budget <= 0:
            raise ContextOverflow(
                f"the fixed prefix alone needs {self.prefix_tokens()} tokens of "
                f"a {self.window_tokens}-token window",
                "The Concierge's knowledge pack no longer fits its context "
                "window. This is a build problem, not something you did.",
            )

        # Rule 2 -- tool-result bodies older than two turns.
        #
        # `entry.tool`, not `entry.role`: a tool result is fed back as a *user*
        # message, because that is the role an OpenAI-compatible endpoint
        # accepts for one, and `tool` is what marks it as bulk rather than
        # conversation. Keying this on the role instead made the rule dead code
        # that never fired, which is why design 5.0 gets one test per numbered
        # rule rather than one test for "trimming".
        for index, entry in enumerate(working):
            if _cost(working) <= budget:
                break
            if not entry.tool or entry.elided:
                continue
            if entry.turn > current_turn - 2:
                continue
            working[index] = _elide(entry)
            trims.append(self._log_trim(
                f"elided the body of {entry.tool}'s result from turn {entry.turn}",
                len(entry.content)))

        # Rule 3 -- the oldest complete exchange, repeatedly.
        while _cost(working) > budget:
            oldest = min((e.turn for e in working if e.turn < current_turn),
                         default=None)
            if oldest is None:
                break
            dropped = [e for e in working if e.turn == oldest]
            working = [e for e in working if e.turn != oldest]
            trims.append(self._log_trim(
                f"dropped the whole of turn {oldest} "
                f"({len(dropped)} message(s))",
                sum(len(e.content) for e in dropped)))

        # Rule 4 -- visible failure, never a silent one.
        if _cost(working) > budget:
            raise ContextOverflow(
                f"the current turn needs {_cost(working)} tokens and only "
                f"{budget} are available",
                "That was too long for the Concierge to hold in one turn. "
                "Ask for less at a time, or start a new session.",
            )

        messages = [{"role": "system", "content": self.prefix()}]
        messages.extend(e.message() for e in working)
        return messages, trims

    def _log_trim(self, what, chars):
        """
        Rule 5. One line per trim, and it names the cache cost.

        The second clause is the one that took a review to notice: trimming
        invalidates the KV cache from the trim point onward, so this is both a
        worse answer and a slower next turn. Logging only the budget half would
        leave the latency half looking like the model having a bad day.
        """
        line = (f"Concierge context trim: {what} (~{chars} chars); "
                f"the KV cache is invalidated from that point on")
        log_debug(line)
        return line


def _cost(entries):
    return sum(e.cost() for e in entries)


def _elide(entry):
    """Rule 2's one-line summary: `{tool, args, elided, bytes}`."""
    summary = json.dumps({
        "tool": entry.tool,
        "args": entry.arguments or {},
        "elided": True,
        "bytes": len(entry.content.encode("utf-8")),
    }, ensure_ascii=False, separators=(",", ":"))
    return entry._replace(content=summary, elided=True)


# -- the loop -----------------------------------------------------------------

class Turn(NamedTuple):
    """What one user message produced."""
    reply: str
    iterations: int
    tool_calls: tuple
    forced: str = ""
    trims: tuple = ()


class Agent:
    """
    The Concierge's agent loop.

    Constructed with a client, a registry, a context and a journal; owns no
    seams of its own. `send()` is synchronous and blocking -- the Qt adapter is
    what puts it on a worker thread, and the CLI rig calls it straight.
    """

    def __init__(self, client, registry, context, journal=None,
                 tool_mode="grammar", clock=time.monotonic,
                 turn_timeout=llm.TURN_TIMEOUT_SEC,
                 on_token=None, on_tool=None, on_notice=None,
                 max_iterations=MAX_TOOL_ITERATIONS):
        self.client = client
        self.registry = registry
        self.context = context
        self.journal = journal
        self.tool_mode = tool_mode
        self._clock = clock
        self._turn_timeout = turn_timeout
        self._on_token = on_token or (lambda _text: None)
        self._on_tool = on_tool or (lambda _name, _args, _result: None)
        self._on_notice = on_notice or (lambda _text: None)
        self._max_iterations = max_iterations
        self._entries = []
        self._turn = 0

    # -- history ------------------------------------------------------------

    def entries(self):
        return tuple(self._entries)

    def reset(self):
        """
        Start a fresh session (FR-CG-13).

        The pack and the note are the context; prior transcripts never are.
        Loading an old session as extra context is deferred to v3.1 and only if
        the note proves insufficient -- it reintroduces exactly the big-context
        failure mode design 5.1 exists to avoid.
        """
        self._entries = []
        self._turn = 0

    # -- one user message ---------------------------------------------------

    def send(self, text):
        """
        Run one turn to completion. Returns a `Turn`; raises `ContextOverflow`.

        A `ForcedStop` from the client -- a stall or the turn bound -- is caught
        here rather than propagated, because design 4.3 requires the stop to be
        *visible in the chat*, and an exception escaping to the panel is a
        traceback in the log and nothing on screen.
        """
        self._turn += 1
        self._entries.append(Entry("user", text, self._turn))
        deadline = self._clock() + self._turn_timeout
        calls, trims = [], []

        for iteration in range(1, self._max_iterations + 1):
            messages, trimmed = self.context.assemble(self._entries, self._turn)
            trims.extend(trimmed)

            try:
                completion = self.client.stream(
                    messages, self.registry, self.tool_mode,
                    on_token=self._on_token, deadline=deadline)
            except llm.ForcedStop as stop:
                return self._forced(stop.message, iteration, calls, trims,
                                    stop.reason)

            decision = llm.decide(self.registry, completion.finish_reason,
                                  completion.content, completion.tool_calls)

            if completion.finish_reason == llm.CANCELLED:
                # Design 2: a new send cancels the current generation. The
                # abandoned turn leaves no assistant entry, so the history the
                # next request carries is the one the user can see -- a
                # half-generated answer in the transcript would be the model
                # apparently talking to itself.
                log_debug(f"Concierge: turn {self._turn} cancelled after "
                          f"{iteration} iteration(s)")
                return Turn("", iteration, tuple(calls), forced="cancelled",
                            trims=tuple(trims))

            if decision.kind == llm.REPLY:
                self._entries.append(
                    Entry("assistant", decision.reply, self._turn))
                return Turn(decision.reply, iteration, tuple(calls),
                            trims=tuple(trims))

            if decision.kind == llm.TOOL:
                result = self.registry.call(decision.tool, decision.arguments)
                calls.append((decision.tool, decision.arguments, result))
                self._emit_tool(decision.tool, decision.arguments, result)
                self._entries.append(Entry(
                    "user",
                    _tool_result_message(decision.tool, decision.arguments, result),
                    self._turn, tool=decision.tool,
                    arguments=decision.arguments))
                continue

            # TRUNCATED or INVALID -- design 4.3's repair path. The reason goes
            # back to the model verbatim; a truncated generation is never
            # parsed as a decision no matter what it contains.
            log_debug(
                f"Concierge repair (iteration {iteration}): {decision.kind} -- "
                f"{decision.reason}")
            self._entries.append(Entry(
                "user", _repair_message(decision), self._turn))

        return self._exhausted(deadline, calls, trims)

    # -- the two forced endings ---------------------------------------------

    def _exhausted(self, deadline, calls, trims):
        """
        The iteration cap. One last request with no tools, then whatever it says.

        Design 4.3: "then the harness forces a reply". Forcing means removing
        the ability to call another tool, not truncating mid-thought -- a
        seventh tool call is what the cap forbids, and an answer is what the
        user asked for.
        """
        log_debug(
            f"Concierge: {self._max_iterations} tool iterations reached; "
            f"forcing a reply")
        self._entries.append(Entry(
            "user",
            "SYSTEM: You have used every tool call available for this message. "
            "Answer now, from what you already have. Do not request another "
            "tool. If you could not finish, say plainly what is missing.",
            self._turn))
        messages, trimmed = self.context.assemble(self._entries, self._turn)
        trims.extend(trimmed)
        try:
            completion = self.client.stream(
                messages, None, self.tool_mode,
                on_token=self._on_token, deadline=deadline)
        except llm.ForcedStop as stop:
            return self._forced(stop.message, self._max_iterations, calls,
                                trims, stop.reason)

        reply = (completion.content or "").strip() or (
            "I ran out of steps on that one and could not finish.")
        self._entries.append(Entry("assistant", reply, self._turn))
        notice = (f"Stopped after {self._max_iterations} tool calls.")
        self._notice(notice)
        return Turn(reply, self._max_iterations, tuple(calls), forced=notice,
                    trims=tuple(trims))

    def _forced(self, message, iteration, calls, trims, reason):
        """
        A timeout. Visible in the chat AND written to the log, per design 4.3.

        `llm.Client` has already logged the reason and called its own forced-stop
        hook; this is the half that puts a sentence in the transcript, so the
        panel never has to invent one and the user never sees generating stop
        for no stated cause.
        """
        self._entries.append(Entry("assistant", message, self._turn))
        self._notice(message)
        return Turn(message, iteration, tuple(calls), forced=reason,
                    trims=tuple(trims))

    def _notice(self, text):
        try:
            self._on_notice(text)
        except Exception as e:
            log_debug(f"ERROR in Concierge on_notice callback: {str(e)}")

    def _emit_tool(self, name, arguments, result):
        try:
            self._on_tool(name, arguments, result)
        except Exception as e:
            log_debug(f"ERROR in Concierge on_tool callback: {str(e)}")


def _tool_result_message(name, arguments, result):
    """
    A tool result, as the model reads it back.

    Compact JSON with stable key order and explicit units, never prose (design
    4.4) -- and prefixed so the model can tell a *result* from a user's words.
    Design 4.5 part 5 is the other half of this: results are data, never
    instructions, because `read_log` returns text an outside application
    influenced, and the log carries the full text of every transcription.
    """
    return "TOOL RESULT " + json.dumps(
        {"tool": name, "arguments": arguments or {}, "result": result},
        ensure_ascii=False, separators=(",", ":"))


def _repair_message(decision):
    """The structured error the repair loop hands back (design 4.3)."""
    body = {"error": True, "kind": decision.kind, "reason": decision.reason}
    if decision.kind == llm.TRUNCATED:
        body["hint"] = ("your last answer was cut off before it finished; "
                        "answer again, more briefly")
    else:
        body["hint"] = "reply with one valid decision"
    return "TOOL RESULT " + json.dumps(body, ensure_ascii=False,
                                       separators=(",", ":"))


# -- assembling one from the parts --------------------------------------------

def load_system_prompt(path):
    """
    Read the versioned prompt artifact (D-CG-12), minus its editorial header.

    Loaded, never assembled: an inline prompt cannot be hashed into a
    qualification scorecard, and NFR-CG-6's "qualified by evidence" claim rests
    on the scorecards being comparable between candidates.

    The leading HTML comment carries the versioning note for whoever edits the
    file and is stripped here, so that editing it costs no tokens and -- more to
    the point -- invalidates no KV prefix. What gets hashed at gate 2.5 is what
    the model actually sees, which is what this returns.
    """
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    return strip_prompt_header(text)


def strip_prompt_header(text):
    """Remove one leading `<!-- ... -->` block. Pure, so L1 can pin it."""
    stripped = text.lstrip()
    if not stripped.startswith("<!--"):
        return text.strip()
    end = stripped.find("-->")
    if end < 0:
        return text.strip()
    return stripped[end + 3:].strip()


def load_pack(path):
    """
    Read the generated knowledge pack, or "" if it has not been built.

    Empty rather than an exception: a development tree without a built pack must
    still start, and the L1 budget test is what makes the shipped one correct.
    The absence is logged, because a Concierge answering from no knowledge at
    all would otherwise look like a bad model rather than a missing build step
    (`OBS-1`).
    """
    if not os.path.exists(path):
        log_debug(f"Concierge: no knowledge pack at {path}; answers will be ungrounded.")
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


__all__ = [
    "Agent", "Context", "ContextOverflow", "Change", "Journal", "Entry",
    "Turn", "Registry", "approx_tokens", "encoded", "load_pack",
    "load_system_prompt", "CONTEXT_WINDOW_TOKENS", "GENERATION_HEADROOM_TOKENS",
    "MAX_TOOL_ITERATIONS", "PACK_REVISIT_TOKENS", "CHARS_PER_TOKEN",
]
