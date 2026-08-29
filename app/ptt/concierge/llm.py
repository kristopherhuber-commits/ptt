"""
D-CG-2 / D-CG-3 -- the SSE client, and tool-call integrity.

Two request shapes, **one registry** (Q15). `grammar_schema()` builds design
4.1's two-level discriminated union for `response_format`; `tools_array()`
builds the OpenAI-style `tools` array for a model whose own chat template is
good. Both read the same `tools.Registry` and the same `config.FIELDS`, so the
schema, the dispatcher and the settings whitelist cannot drift -- and both are
pinned by L1, because otherwise the mode that ships is the one no test exercises
and the mode L1 pins is the one no user runs.

**Why the union has two levels** (Q12). An earlier draft of design 4.1 showed a
flat object -- `action`, `tool {name, arguments}`, `reply`, all required. That is
the shape spike C1 scored 10/10 on, and it is not a shape a registry can
generate: a flat object cannot say "arguments must match the schema selected by
`tool.name`", and to be coherent at all it needed a prompt convention ("when
action is reply, leave tool's name as get_state and arguments empty") -- a rule
held up by prose, inside the mechanism whose entire claim is that malformed
calls are structurally impossible. Level one discriminates `reply` from `tool`;
level two discriminates on `tool.name` and carries that tool's own arguments.

**Where the union deliberately stops.** `value` is a scalar union, not a third
level keyed to `key`. A third level would make a type error unrepresentable and
would produce a grammar whose size, conversion fidelity and decode cost nobody
has measured; the deepest union the spike tested was one level. So: shape is
guaranteed at the sampler, sense is guaranteed by `Settings.set()` at dispatch,
and section 4.3's repair loop connects them.

**Truncation is its own error class, not a parse failure.** Spike C1 found the
one real hole in "structurally impossible to malform": a generation cut off at
the token cap can leave an unterminated string -- 2 in 46, both flagged
`finish_reason: "length"`. `decide()` therefore checks the finish reason
*before* it parses, and a truncated generation can never be read as a valid
decision no matter what it happens to contain.
"""

import json
import time
from typing import NamedTuple

from ptt.logging_setup import log_debug

# -- design 4.3's timeout table, whole, in one place (Q18) --------------------
#
# Nothing bounded a turn before these existed: six repair iterations at the
# spike's measured 30.1 tok/s, each carrying a reply, is minutes, and a server
# that accepts a connection and then sends nothing leaves the panel generating
# forever. Every one of them is a *forced stop*, and T6's carried-over rule from
# issue #11 applies to all three: visible in the chat AND written to the log. A
# swallowed timeout is the same defect as a swallowed paste.

#: No SSE chunk received. A stall bound rather than a time-to-first-token one,
#: because it also catches a hang mid-stream. Baseline: 0.342 s warm TTFT,
#: 7.17 s cold-with-pack -- roughly 90x and 4x margin respectively.
STALL_TIMEOUT_SEC = 30.0

#: Send to final reply, across every repair iteration.
TURN_TIMEOUT_SEC = 180.0

#: Launch to healthy. Baseline 5.0-6.8 s, about 10x margin.
SERVER_READY_TIMEOUT_SEC = 60.0

#: How often a blocking read gives up and reports "nothing yet", so the stall
#: bound is checked by the harness rather than by the socket layer. It is also
#: what makes the bound testable at L1 with a fake clock.
POLL_INTERVAL_SEC = 1.0

#: The cap design 4.1 puts on `reply`, in characters.
#:
#: About 750 tokens at the 4-chars-per-token measure -- well inside section 5's
#: 4k generation headroom, and short enough that the degenerate-loop failure C1
#: reproduced (the model repeating `100% 0.00%` inside an unbounded string until
#: the token cap cut it mid-quote) cannot reach the cap in the first place.
#: Whether llama.cpp's JSON-Schema-to-GBNF converter honours it is measured, not
#: assumed: see `spike_results.md` section C7.
REPLY_MAX_CHARS = 3000

#: What `finish_reason` reads when the harness stopped the generation itself.
#: Deliberately not one of llama.cpp's own values, so it cannot be confused with
#: `"length"` -- which is a repair trigger, and a cancellation is not.
CANCELLED = "cancelled"

#: The scalar union `set_config.value` accepts. Design 4.1's stopping point.
SCALAR_UNION = [
    {"type": "boolean"},
    {"type": "string"},
    {"type": "integer"},
    {"type": "null"},
    {"type": "array", "items": {"type": "string"}},
]


# -- what a completed generation turned out to be -----------------------------

REPLY = "reply"
TOOL = "tool"
TRUNCATED = "truncated"
INVALID = "invalid"


class Decision(NamedTuple):
    """
    One generation, classified. The only thing `agent.py` acts on.

    `TRUNCATED` and `INVALID` are both repair triggers and are kept apart
    because they mean different things to the user: truncation is the harness's
    fault and says so, an invalid decision is the model's and is worth showing
    it verbatim.
    """
    kind: str
    reply: str = ""
    tool: str = ""
    arguments: dict = None
    reason: str = ""
    raw: str = ""

    @property
    def is_repairable(self):
        return self.kind in (TRUNCATED, INVALID)


class ForcedStop(Exception):
    """
    A bound was reached and the harness stopped the generation itself.

    Carries the sentence the chat shows, because design 4.3 requires every
    forced stop to be visible there as well as in the log, and a bare exception
    type leaves the panel to invent the wording.
    """

    def __init__(self, reason, message):
        super().__init__(reason)
        self.reason = reason
        self.message = message


# -- schema generation --------------------------------------------------------

def _argument_schema(spec):
    """One argument, as JSON Schema. The only place a type is turned into one."""
    if spec.json_type == "scalar":
        return {"oneOf": list(SCALAR_UNION), "description": spec.description}
    if spec.json_type == "array":
        return {"type": "array", "items": {"type": spec.item_type},
                "description": spec.description}
    node = {"type": spec.json_type, "description": spec.description}
    if spec.enum:
        node["enum"] = list(spec.enum)
    if spec.minimum is not None:
        node["minimum"] = spec.minimum
    if spec.maximum is not None:
        node["maximum"] = spec.maximum
    return node


def _arguments_object(tool):
    """A tool's whole argument object, shared by both request shapes."""
    return {
        "type": "object",
        "properties": {a.name: _argument_schema(a) for a in tool.args},
        "required": [a.name for a in tool.args if a.required],
        "additionalProperties": False,
    }


def grammar_schema(registry, reply_max_chars=REPLY_MAX_CHARS):
    """
    Design 4.1's two-level discriminated union, generated from the registry.

    Nothing here is hand-written per tool: adding a tool to `tools.Registry`
    adds a branch, and adding a setting to `config.FIELDS` widens `set_config`'s
    key enum. That is the property the section exists for -- the schema and the
    dispatcher read one declaration, so they cannot disagree about what a valid
    call is.
    """
    branches = []
    for tool in registry.tools():
        branches.append({
            "type": "object",
            "properties": {
                "name": {"const": tool.name, "description": tool.summary},
                "arguments": _arguments_object(tool),
            },
            "required": ["name", "arguments"],
            "additionalProperties": False,
        })

    return {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "action": {"const": REPLY},
                    "reply": {"type": "string", "maxLength": reply_max_chars},
                },
                "required": ["action", "reply"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "action": {"const": TOOL},
                    "tool": {"type": "object", "oneOf": branches},
                },
                "required": ["action", "tool"],
                "additionalProperties": False,
            },
        ],
    }


def tools_array(registry):
    """
    The same registry as an OpenAI-style `tools` array, for native mode.

    One server process serves both modes: `--jinja` is enabled by default in the
    pinned build, so the choice is which body the client sends, not which flag
    the server was started with (spike setup finding 2). Switching modes
    restarts nothing.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.summary,
                "parameters": _arguments_object(tool),
            },
        }
        for tool in registry.tools()
    ]


def response_format(registry, reply_max_chars=REPLY_MAX_CHARS):
    """The `response_format` body grammar mode sends."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "concierge_decision",
            "strict": True,
            "schema": grammar_schema(registry, reply_max_chars),
        },
    }


# -- reading a completed generation -------------------------------------------

def decide(registry, finish_reason, content, tool_calls=None):
    """
    Classify one completed generation. Never raises.

    Order matters and is the point: **`finish_reason` is checked first**. A
    generation that hit the token cap is `TRUNCATED` whatever its text looks
    like, because C1 showed the failure mode is an unterminated string inside
    otherwise-valid JSON -- text that a lenient parser could well accept as a
    decision. Parsing first and checking the reason afterwards would be exactly
    the "truncated decision read as a valid one" the design forbids.
    """
    if finish_reason == CANCELLED:
        # A cancellation is not a repair trigger and not a reply. The caller
        # abandoned this turn; feeding a half-generated decision back to the
        # model would make the *next* turn answer a question nobody asked.
        return Decision(INVALID, raw=content or "", reason="the turn was cancelled")

    if finish_reason == "length":
        return Decision(TRUNCATED, raw=content or "",
                        reason="the generation hit the token cap")

    if tool_calls:
        call = tool_calls[0]
        name = call.get("name") or ""
        if not registry.get(name):
            return Decision(INVALID, raw=content or "",
                            reason=f"{name!r} is not a registered tool")
        raw_args = call.get("arguments")
        if isinstance(raw_args, dict):
            return Decision(TOOL, tool=name, arguments=raw_args)
        try:
            parsed = json.loads(raw_args or "{}")
        except Exception as e:
            return Decision(INVALID, raw=str(raw_args),
                            reason=f"the arguments were not JSON: {str(e)}")
        if not isinstance(parsed, dict):
            return Decision(INVALID, raw=str(raw_args),
                            reason="the arguments were not an object")
        return Decision(TOOL, tool=name, arguments=parsed)

    text = (content or "").strip()
    if not text:
        return Decision(INVALID, reason="the generation was empty")

    # Native mode with no tool call is an ordinary prose reply -- that is the
    # whole of C2's 0% false-trigger result. Grammar mode always returns the
    # envelope, so a body that does not parse as JSON there is a real defect.
    try:
        payload = json.loads(text)
    except Exception:
        return Decision(REPLY, reply=text)

    if not isinstance(payload, dict) or "action" not in payload:
        return Decision(REPLY, reply=text)

    action = payload.get("action")
    if action == REPLY:
        reply = payload.get("reply")
        if not isinstance(reply, str):
            return Decision(INVALID, raw=text,
                            reason="action was 'reply' with no reply string")
        return Decision(REPLY, reply=reply)

    if action == TOOL:
        call = payload.get("tool")
        if not isinstance(call, dict):
            return Decision(INVALID, raw=text,
                            reason="action was 'tool' with no tool object")
        name = call.get("name")
        if not registry.get(name):
            return Decision(INVALID, raw=text,
                            reason=f"{name!r} is not a registered tool")
        arguments = call.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return Decision(INVALID, raw=text,
                            reason="the tool's arguments were not an object")
        return Decision(TOOL, tool=name, arguments=arguments)

    return Decision(INVALID, raw=text, reason=f"unknown action {action!r}")


# -- the streaming tool_calls accumulator -------------------------------------

class ToolCallAccumulator:
    """
    Rebuilds `tool_calls` from streamed deltas.

    Spike C2 flagged this as the one thing a streaming client must get right:
    deltas arrive **by index**, each carrying a fragment -- an `id` and a `name`
    on the first, then `arguments` a few characters at a time. A client that
    reads only the last delta gets a name and no arguments; one that concatenates
    without keying on the index merges two calls into one.

    L1 pins it directly rather than only through the loop, because a partial
    accumulation produces a *plausible* call, and a plausible call with the
    wrong arguments is the failure this whole module exists to prevent.
    """

    def __init__(self):
        self._by_index = {}

    def add(self, deltas):
        for delta in deltas or []:
            index = delta.get("index", 0)
            slot = self._by_index.setdefault(
                index, {"id": "", "name": "", "arguments": ""})
            if delta.get("id"):
                slot["id"] = delta["id"]
            function = delta.get("function") or {}
            if function.get("name"):
                slot["name"] += function["name"]
            if function.get("arguments"):
                slot["arguments"] += function["arguments"]

    def calls(self):
        """The accumulated calls, in index order."""
        return [self._by_index[i] for i in sorted(self._by_index)]

    def __bool__(self):
        return bool(self._by_index)


# -- the client ---------------------------------------------------------------

class Completion(NamedTuple):
    """What one streamed generation produced."""
    content: str
    finish_reason: str
    tool_calls: list
    usage: dict


class Client:
    """
    An OpenAI-compatible streaming client, over an injected transport.

    The transport is a seam, not a detail: L1 forbids HTTP, and every timeout,
    every SSE-framing rule and the whole delta accumulator have to be pinned
    without a server. It yields `bytes` for a line and `None` for "nothing yet",
    which is what turns the stall bound into arithmetic the unit suite can check
    with a fake clock instead of a thirty-second test.

    `clock` and the transport are the only two things this class cannot get for
    itself, and both are injected for the same reason.
    """

    def __init__(self, base_url, api_key="", transport=None, clock=time.monotonic,
                 stall_timeout=STALL_TIMEOUT_SEC, on_forced_stop=None,
                 cancelled=None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._transport = transport
        self._clock = clock
        self._stall_timeout = stall_timeout
        self._on_forced_stop = on_forced_stop or (lambda _message: None)
        #: `cancelled()` is checked once per chunk. Design 2: the harness
        #: serialises sends and **a new send cancels the current generation**,
        #: because with `-np 1` a concurrent request would either queue behind
        #: this one or land somewhere that re-pays the knowledge pack in full.
        #: A callable rather than a flag so the Qt adapter can hand over a
        #: `threading.Event.is_set` without this module knowing what a thread is.
        self._cancelled = cancelled or (lambda: False)

    def headers(self):
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def body(self, messages, registry=None, tool_mode="grammar",
             max_tokens=1024, temperature=0.2, stream=True,
             reply_max_chars=REPLY_MAX_CHARS):
        """
        One request body. **Both modes are built here, from one registry.**

        `grammar` sends `response_format` and no `tools`; `native` sends `tools`
        and no `response_format`. Nothing else differs, which is what makes
        `tool_mode` a per-model qualification column rather than a fork in the
        harness (CON-CG-5).
        """
        payload = {
            "messages": list(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
        if stream:
            # llama-server only reports token counts on the final chunk when
            # asked; without them the qualification suite has no decode rate.
            payload["stream_options"] = {"include_usage": True}
        if registry is not None:
            if tool_mode == "native":
                payload["tools"] = tools_array(registry)
                payload["tool_choice"] = "auto"
            else:
                payload["response_format"] = response_format(
                    registry, reply_max_chars)
        return payload

    def stream(self, messages, registry=None, tool_mode="grammar",
               max_tokens=1024, temperature=0.2, on_token=None,
               deadline=None, reply_max_chars=REPLY_MAX_CHARS):
        """
        Run one generation. Returns a `Completion`; raises `ForcedStop`.

        `on_token(text)` is called for each content delta, which is what the
        panel streams. It is plain Python; the Qt adapter is what makes it a
        queued signal.

        `deadline` is the *turn's* bound, passed in from `agent.py` so that six
        repair iterations share one 180 s budget rather than getting 180 s each.
        """
        on_token = on_token or (lambda _text: None)
        payload = self.body(messages, registry, tool_mode, max_tokens,
                            temperature, True, reply_max_chars)
        url = f"{self.base_url}/v1/chat/completions"

        content = []
        finish_reason = ""
        usage = {}
        calls = ToolCallAccumulator()
        last_data = self._clock()

        for line in self._transport.post_stream(url, self.headers(), payload,
                                                POLL_INTERVAL_SEC):
            now = self._clock()
            if self._cancelled():
                # Not a forced stop: nobody needs telling that the thing they
                # just cancelled has stopped. The connection is closed by the
                # generator's `finally`, which is what actually frees the slot.
                log_debug("Concierge: generation cancelled by a new send.")
                return Completion("".join(content), "cancelled", calls.calls(), usage)
            if deadline is not None and now > deadline:
                raise self._stop("turn timeout",
                                 "The Concierge took too long and was stopped.")
            if line is None:
                if now - last_data > self._stall_timeout:
                    raise self._stop(
                        f"stall timeout after {self._stall_timeout:.0f}s",
                        "The Concierge stopped responding.")
                continue

            last_data = now
            event = _sse_payload(line)
            if event is None:
                continue
            if event == "[DONE]":
                break

            try:
                chunk = json.loads(event)
            except Exception:
                log_debug(f"Concierge: unparseable SSE chunk, ignored: {event[:120]!r}")
                continue

            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                piece = delta.get("content")
                if piece:
                    content.append(piece)
                    on_token(piece)
                if delta.get("tool_calls"):
                    calls.add(delta["tool_calls"])
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]

        return Completion("".join(content), finish_reason, calls.calls(), usage)

    def _stop(self, reason, message):
        """Log the forced stop and hand the chat its sentence. Design 4.3."""
        log_debug(f"Concierge forced stop: {reason}")
        try:
            self._on_forced_stop(message)
        except Exception as e:
            log_debug(f"ERROR in Concierge on_forced_stop callback: {str(e)}")
        return ForcedStop(reason, message)


class _Reader:
    """
    Byte-level plumbing for one streamed response: select, buffer, de-chunk.

    Separate from `HttpTransport` because it is the part with state, and the
    part that had to be written by hand. `http.client` cannot do this job: its
    response body is a `BufferedReader` over a `socket.SocketIO`, and
    `SocketIO.readinto` **latches** on a timeout --

        if self._timeout_occurred:
            raise OSError("cannot read from timed out object")

    -- so the first quiet second permanently poisons the response. That is not a
    subtle failure mode: the CLI rig's first run against a real llama-server got
    exactly one chunk and then `OSError: cannot read from timed out object`, and
    the prewarm never completed. The poll contract `Client.stream` is built on --
    "a line, or `None` for nothing yet" -- is therefore unimplementable through
    that reader, and the fix is to stop using it.

    `select` rather than a socket timeout is the whole repair: the socket never
    times out, so nothing can latch, and "nothing yet" is a select that expired
    rather than an exception. Chunked transfer-encoding is decoded here for the
    same reason the framing is -- llama-server sends SSE chunked, and a chunk
    boundary is free to fall in the middle of a `data:` line. Line-splitting the
    raw stream works right up until it does, which is the worst kind of works.
    """

    def __init__(self, sock, poll_interval):
        self._sock = sock
        self._poll = poll_interval
        self._raw = bytearray()        # as received, still chunk-framed
        self._body = bytearray()       # decoded body bytes
        self._chunked = False
        self._limit = None             # Content-Length, when there is one
        self._taken = 0
        self._state = "size"
        self._remaining = 0
        self.eof = False

    # -- the socket ---------------------------------------------------------

    def poll(self):
        """One select-then-recv. True if bytes arrived, False if it was quiet."""
        import select
        if self.eof:
            return False
        try:
            ready, _, _ = select.select([self._sock], [], [], self._poll)
        except (OSError, ValueError):
            self.eof = True
            return False
        if not ready:
            return False
        try:
            data = self._sock.recv(65536)
        except OSError:
            self.eof = True
            return False
        if not data:
            self.eof = True
            return False
        self._raw += data
        return True

    # -- the header phase ---------------------------------------------------

    def head_line(self):
        """One line of the status/header block, or None if it has not arrived."""
        index = self._raw.find(b"\n")
        if index < 0:
            return None
        line = bytes(self._raw[:index + 1])
        del self._raw[:index + 1]
        return line

    def begin_body(self, chunked, content_length=None):
        self._chunked = chunked
        self._limit = content_length

    # -- the body phase -----------------------------------------------------

    def body_line(self):
        """One line of the decoded body, or None. The SSE framing sits above this."""
        self._decode()
        index = self._body.find(b"\n")
        if index < 0:
            if self.eof and self._body:
                # A body that ends without its final newline. SSE always
                # terminates an event with a blank line so this should not
                # happen, and dropping the bytes silently if it does would be
                # the swallowed-failure shape OBS-1 rules out.
                line = bytes(self._body)
                self._body.clear()
                return line
            return None
        line = bytes(self._body[:index + 1])
        del self._body[:index + 1]
        return line

    def drain(self, limit=2048):
        """Whatever body has arrived, for an error response. Best effort."""
        for _ in range(4):
            if self.eof or len(self._body) >= limit:
                break
            self.poll()
            self._decode()
        return bytes(self._body[:limit]).decode("utf-8", errors="replace")

    def _decode(self):
        """Move `_raw` into `_body`, honouring chunked framing and Content-Length."""
        if not self._chunked:
            if self._raw:
                take = len(self._raw)
                if self._limit is not None:
                    take = min(take, self._limit - self._taken)
                self._body += self._raw[:take]
                del self._raw[:take]
                self._taken += take
                if self._limit is not None and self._taken >= self._limit:
                    self.eof = True
            return

        while True:
            if self._state == "size":
                index = self._raw.find(b"\r\n")
                if index < 0:
                    return
                text = bytes(self._raw[:index]).split(b";")[0].strip()
                del self._raw[:index + 2]
                try:
                    self._remaining = int(text, 16)
                except ValueError:
                    # Not a chunk header we understand. Stop rather than guess:
                    # a mis-decoded stream would reach `decide()` as garbage and
                    # be repaired six times before anyone learned why.
                    log_debug(f"Concierge: unparseable chunk header {text!r}; "
                              f"ending the stream.")
                    self._state = "done"
                    self.eof = True
                    return
                if self._remaining == 0:
                    self._state = "done"
                    self.eof = True
                    return
                self._state = "data"
            elif self._state == "data":
                take = min(self._remaining, len(self._raw))
                if take:
                    self._body += self._raw[:take]
                    del self._raw[:take]
                    self._remaining -= take
                if self._remaining:
                    return
                self._state = "crlf"
            elif self._state == "crlf":
                if len(self._raw) < 2:
                    return
                del self._raw[:2]
                self._state = "size"
            else:
                return


class HttpTransport:
    """
    The real transport: a plain socket against the loopback server.

    Stdlib rather than `requests` or `httpx` (CON-3 / CON-CG-3: the only
    additions v3.0 makes are a binary and a GGUF, not a dependency tree) -- and
    a *socket* rather than `http.client`, for the latching reason `_Reader`
    documents. The request is four lines of headers and a JSON body; what
    `http.client` was buying us was the response reader, and the response reader
    is the thing that does not work.

    A read that comes back with nothing yields `None` so the *harness* decides
    what a stall is -- which is what lets one rule cover both "the server never
    answered" and "the server stopped mid-stream", and what lets L1 pin it with
    a fake clock rather than with a thirty-second test. The header phase yields
    `None` too, so a server that accepts the connection and then says nothing at
    all is a stall rather than a hang.
    """

    def __init__(self, connect_timeout=10.0):
        self._connect_timeout = connect_timeout

    def post_stream(self, url, headers, payload, poll_interval):
        import socket
        import urllib.parse

        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or "127.0.0.1"
        port = parts.port or 80
        path = parts.path or "/"
        body = json.dumps(payload).encode("utf-8")

        sock = socket.create_connection((host, port),
                                        timeout=self._connect_timeout)
        try:
            sock.sendall(_request_bytes(host, port, path, headers, body))
            # From here `select` owns every wait. The socket must stay blocking:
            # a zero timeout would make `recv` raise `BlockingIOError` in the
            # window between a readable select and the read itself.
            sock.settimeout(None)
            reader = _Reader(sock, poll_interval)

            status, response_headers = None, []
            while True:
                line = reader.head_line()
                if line is None:
                    if reader.eof:
                        raise ForcedStop(
                            "the runtime closed the connection before answering",
                            "The Concierge's runtime did not answer.")
                    if not reader.poll():
                        yield None
                    continue
                text = line.decode("latin-1").rstrip("\r\n")
                if status is None:
                    status = _parse_status(text)
                    continue
                if text == "":
                    break
                response_headers.append(text)

            chunked, length = _framing(response_headers)
            reader.begin_body(chunked, length)

            if status != 200:
                detail = reader.drain()
                raise ForcedStop(
                    f"llama-server answered {status}: {detail[:200]}",
                    "The Concierge's runtime refused the request.")

            while True:
                line = reader.body_line()
                if line is not None:
                    yield line
                    continue
                if reader.eof:
                    return
                if not reader.poll():
                    yield None
        finally:
            # This is what actually frees llama-server's single slot when a
            # generation is cancelled, so it closes on every path including the
            # generator being abandoned mid-stream.
            try:
                sock.close()
            except Exception:
                pass


def _request_bytes(host, port, path, headers, body):
    """One HTTP/1.1 POST, as bytes. Four headers and the caller's."""
    lines = [f"POST {path} HTTP/1.1",
             f"Host: {host}:{port}",
             f"Content-Length: {len(body)}",
             # Close framing rather than keep-alive: the transport opens a fresh
             # connection per generation anyway, and an EOF that means "the body
             # is finished" is one less thing to get right.
             "Connection: close",
             "Accept: text/event-stream"]
    for key, value in (headers or {}).items():
        lines.append(f"{key}: {value}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body


def _parse_status(line):
    """The status code out of `HTTP/1.1 200 OK`. Never raises."""
    parts = line.split(" ", 2)
    if len(parts) < 2 or not parts[1].isdigit():
        raise ForcedStop(f"the runtime answered {line[:120]!r}, not HTTP",
                         "The Concierge's runtime answered something unexpected.")
    return int(parts[1])


def _framing(response_headers):
    """`(chunked, content_length)` from the response headers."""
    chunked, length = False, None
    for header in response_headers:
        name, _, value = header.partition(":")
        name = name.strip().lower()
        value = value.strip()
        if name == "transfer-encoding" and "chunked" in value.lower():
            chunked = True
        elif name == "content-length" and value.isdigit():
            length = int(value)
    return chunked, (None if chunked else length)


def _sse_payload(line):
    """
    The payload of one SSE line, or None if the line carries none.

    Server-sent events are line-framed with a `data: ` prefix and blank lines
    between events; comment lines start with `:` and exist to keep a connection
    open, which is precisely the case that must *not* look like a stall.
    """
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="replace")
    line = line.strip()
    if not line or line.startswith(":"):
        return None
    if not line.startswith("data:"):
        return None
    return line[len("data:"):].strip()
