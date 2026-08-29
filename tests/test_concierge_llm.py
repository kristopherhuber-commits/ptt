"""
Tool-call integrity: both request shapes from one registry, and the timeouts.

`V-CG-20` … `V-CG-29`. The HTTP layer is a fake throughout (L1 forbids sockets),
and the clock is injected, so the stall bound is arithmetic rather than a
thirty-second test.

Two things here are pinned harder than the rest, because both are places where a
plausible-looking implementation is wrong in a way nothing else would catch: the
streaming `tool_calls` delta accumulator, which spike C2 flagged, and the rule
that `finish_reason == "length"` is classified **before** anything is parsed.
"""

import json

import pytest

from ptt import config, transcribe
from ptt.concierge import llm
from ptt.concierge import tools as tools_mod


@pytest.fixture
def registry(tmp_path):
    settings = config.Settings(path=str(tmp_path / "config.json"))
    return tools_mod.Registry(settings)


# -- one registry, two request shapes (Q15) ----------------------------------

def test_the_schema_is_a_two_level_discriminated_union(registry):
    """
    Q12. Level one discriminates reply from tool; level two discriminates on
    `tool.name` and carries that tool's own arguments. A flat object cannot say
    "arguments must match the schema selected by the name" -- that is a
    dependency between siblings, and JSON Schema expresses it only this way.
    """
    schema = llm.grammar_schema(registry)
    top = schema["oneOf"]
    assert len(top) == 2
    assert top[0]["properties"]["action"]["const"] == "reply"
    assert top[1]["properties"]["action"]["const"] == "tool"

    branches = top[1]["properties"]["tool"]["oneOf"]
    assert [b["properties"]["name"]["const"] for b in branches] == list(registry.names())


def test_the_reply_branch_carries_a_maxlength(registry):
    """
    Design 4.1's truncation mitigation, and it is a measured one: spike C7b set
    it to 40 and the reply stopped at exactly 40 characters, mid-word, with a
    clean `finish_reason: "stop"`.
    """
    reply = llm.grammar_schema(registry)["oneOf"][0]["properties"]["reply"]
    assert reply["type"] == "string"
    assert reply["maxLength"] == llm.REPLY_MAX_CHARS


def test_the_key_enum_comes_from_the_fields_table(registry):
    """
    D-CG-13's third consumer. Nothing here lists a setting name, so a field
    added to `config.FIELDS` is settable with no edit in this module.
    """
    branches = llm.grammar_schema(registry)["oneOf"][1]["properties"]["tool"]["oneOf"]
    setter = next(b for b in branches
                  if b["properties"]["name"]["const"] == "set_config")
    assert setter["properties"]["arguments"]["properties"]["key"]["enum"] == list(
        config.WRITABLE_KEYS)


def test_the_value_is_a_scalar_union_and_stops_there(registry):
    """
    Design 4.1's deliberate stopping point. A third union level keyed to `key`
    would make a type error unrepresentable and would produce a grammar whose
    size, conversion fidelity and decode cost nobody has measured -- the deepest
    union the spike tested was one level.
    """
    branches = llm.grammar_schema(registry)["oneOf"][1]["properties"]["tool"]["oneOf"]
    setter = next(b for b in branches
                  if b["properties"]["name"]["const"] == "set_config")
    value = setter["properties"]["arguments"]["properties"]["value"]
    assert [m["type"] for m in value["oneOf"]] == [
        "boolean", "string", "integer", "null", "array"]


def test_a_tool_with_no_arguments_still_declares_an_arguments_object(registry):
    branches = llm.grammar_schema(registry)["oneOf"][1]["properties"]["tool"]["oneOf"]
    state = next(b for b in branches
                 if b["properties"]["name"]["const"] == "get_state")
    args = state["properties"]["arguments"]
    assert args["properties"] == {} and args["required"] == []
    assert args["additionalProperties"] is False


def test_optional_arguments_are_not_required(registry):
    branches = llm.grammar_schema(registry)["oneOf"][1]["properties"]["tool"]["oneOf"]
    log = next(b for b in branches
               if b["properties"]["name"]["const"] == "read_log")
    assert log["properties"]["arguments"]["required"] == []


def test_the_tools_array_covers_the_same_registry(registry):
    """
    Q15: **both** paths generated from one declaration. Otherwise the mode that
    ships is the one L1 does not test, and the mode L1 pins is the one no user
    runs.
    """
    array = llm.tools_array(registry)
    assert [f["function"]["name"] for f in array] == list(registry.names())
    for entry in array:
        assert entry["type"] == "function"
        assert entry["function"]["parameters"]["type"] == "object"


def test_both_shapes_move_together_when_the_registry_does(registry):
    """
    The property, stated directly: add a tool and both request bodies change,
    because there is nowhere else for either of them to read it from.
    """
    extra = tools_mod.Tool("do_nothing", "A test tool.",
                           (tools_mod.Arg("n", "integer", "A number."),),
                           lambda n: {"n": n})
    registry._tools = registry._tools + (extra,)
    registry._by_name["do_nothing"] = extra

    branches = llm.grammar_schema(registry)["oneOf"][1]["properties"]["tool"]["oneOf"]
    assert branches[-1]["properties"]["name"]["const"] == "do_nothing"
    assert llm.tools_array(registry)[-1]["function"]["name"] == "do_nothing"


def test_the_request_body_carries_one_shape_or_the_other(registry):
    client = llm.Client("http://127.0.0.1:1")
    grammar = client.body([{"role": "user", "content": "hi"}], registry, "grammar")
    assert "response_format" in grammar and "tools" not in grammar

    native = client.body([{"role": "user", "content": "hi"}], registry, "native")
    assert "tools" in native and "response_format" not in native
    assert native["tool_choice"] == "auto"


def test_a_request_with_no_registry_offers_no_tools(registry):
    """
    What the forced reply sends: removing the ability to call a seventh tool is
    what "the harness forces a reply" means (design 4.3).
    """
    body = llm.Client("http://127.0.0.1:1").body([{"role": "user", "content": "x"}])
    assert "tools" not in body and "response_format" not in body


# -- classifying a completed generation --------------------------------------

def test_a_truncated_generation_is_never_a_valid_decision(registry):
    """
    **The order is the point.** Spike C1's failure mode was an unterminated
    string inside otherwise-valid JSON -- text a lenient parser would accept. So
    `finish_reason` is checked before anything is parsed, and a generation that
    hit the token cap is truncated whatever its text looks like.
    """
    perfect = json.dumps({"action": "reply", "reply": "all done"})
    decision = llm.decide(registry, "length", perfect)
    assert decision.kind == llm.TRUNCATED
    assert decision.is_repairable
    assert decision.reply == ""


def test_a_truncated_tool_call_is_also_never_valid(registry):
    decision = llm.decide(registry, "length", "",
                          [{"name": "get_state", "arguments": "{}"}])
    assert decision.kind == llm.TRUNCATED


def test_a_grammar_mode_reply_is_read_from_the_envelope(registry):
    decision = llm.decide(registry, "stop",
                          json.dumps({"action": "reply", "reply": "hello"}))
    assert decision.kind == llm.REPLY and decision.reply == "hello"


def test_a_grammar_mode_tool_call_is_read_from_the_envelope(registry):
    decision = llm.decide(registry, "stop", json.dumps(
        {"action": "tool",
         "tool": {"name": "set_config",
                  "arguments": {"key": "model", "value": "small.en"}}}))
    assert decision.kind == llm.TOOL
    assert decision.tool == "set_config"
    assert decision.arguments == {"key": "model", "value": "small.en"}


def test_a_native_mode_tool_call_parses_its_json_arguments(registry):
    decision = llm.decide(registry, "stop", "", [
        {"id": "call_1", "name": "get_config",
         "arguments": '{"key": "model"}'}])
    assert decision.kind == llm.TOOL
    assert decision.arguments == {"key": "model"}


def test_a_native_mode_answer_with_no_tool_call_is_a_reply(registry):
    """C2's 0% false-trigger result is exactly this path."""
    decision = llm.decide(registry, "stop", "The pre-roll buffer keeps 200 ms.")
    assert decision.kind == llm.REPLY
    assert decision.reply == "The pre-roll buffer keeps 200 ms."


def test_an_unregistered_tool_name_is_invalid_not_dispatched(registry):
    decision = llm.decide(registry, "stop", json.dumps(
        {"action": "tool", "tool": {"name": "rm_rf", "arguments": {}}}))
    assert decision.kind == llm.INVALID
    assert "not a registered tool" in decision.reason


def test_unparseable_tool_arguments_are_invalid(registry):
    decision = llm.decide(registry, "stop", "",
                          [{"name": "get_config", "arguments": "{key: model"}])
    assert decision.kind == llm.INVALID
    assert "not JSON" in decision.reason


def test_an_empty_generation_is_invalid(registry):
    assert llm.decide(registry, "stop", "   ").kind == llm.INVALID


def test_an_unknown_action_is_invalid(registry):
    decision = llm.decide(registry, "stop", json.dumps({"action": "explode"}))
    assert decision.kind == llm.INVALID and "explode" in decision.reason


def test_a_reply_action_with_no_string_is_invalid(registry):
    decision = llm.decide(registry, "stop",
                          json.dumps({"action": "reply", "reply": 42}))
    assert decision.kind == llm.INVALID


def test_a_tool_call_with_no_arguments_key_defaults_to_empty(registry):
    """
    A generated schema requires `arguments`, but native mode's template may not.
    Absent is not the same as malformed, and a no-argument tool is the common
    case.
    """
    decision = llm.decide(registry, "stop", json.dumps(
        {"action": "tool", "tool": {"name": "get_state"}}))
    assert decision.kind == llm.TOOL and decision.arguments == {}


# -- the streaming tool_calls accumulator (C2's flagged case) ----------------

def test_deltas_are_accumulated_by_index():
    """
    Deltas arrive **by index**, each carrying a fragment. A client that reads
    only the last delta gets a name and no arguments; one that concatenates
    without keying on the index merges two calls into one.
    """
    acc = llm.ToolCallAccumulator()
    acc.add([{"index": 0, "id": "call_a", "function": {"name": "set_"}}])
    acc.add([{"index": 0, "function": {"name": "config"}}])
    acc.add([{"index": 0, "function": {"arguments": '{"key":'}}])
    acc.add([{"index": 0, "function": {"arguments": '"model","value":"small.en"}'}}])
    assert acc.calls() == [{
        "id": "call_a", "name": "set_config",
        "arguments": '{"key":"model","value":"small.en"}'}]


def test_two_concurrent_calls_do_not_merge():
    acc = llm.ToolCallAccumulator()
    acc.add([{"index": 0, "id": "a", "function": {"name": "get_state",
                                                  "arguments": "{}"}},
             {"index": 1, "id": "b", "function": {"name": "get_config",
                                                  "arguments": '{"key":"model"}'}}])
    calls = acc.calls()
    assert [c["name"] for c in calls] == ["get_state", "get_config"]
    assert calls[1]["arguments"] == '{"key":"model"}'


def test_an_index_that_arrives_out_of_order_is_still_placed(registry):
    acc = llm.ToolCallAccumulator()
    acc.add([{"index": 1, "id": "b", "function": {"name": "get_state"}}])
    acc.add([{"index": 0, "id": "a", "function": {"name": "get_config"}}])
    assert [c["id"] for c in acc.calls()] == ["a", "b"]


def test_an_accumulator_with_nothing_in_it_is_falsy():
    assert not llm.ToolCallAccumulator()


# -- the SSE client ----------------------------------------------------------

class FakeTransport:
    """
    Yields the lines a fake server would send. `None` means "nothing yet",
    which is what turns the stall bound into arithmetic.
    """

    def __init__(self, lines):
        self.lines = list(lines)
        self.requests = []

    def post_stream(self, url, headers, payload, poll_interval):
        self.requests.append((url, headers, payload))
        for line in self.lines:
            yield line


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def sse(payload):
    return ("data: " + json.dumps(payload)).encode("utf-8")


def content_chunk(text, finish=None):
    return sse({"choices": [{"delta": {"content": text},
                             "finish_reason": finish}]})


def test_content_deltas_are_streamed_and_joined():
    transport = FakeTransport([
        content_chunk("Hel"), content_chunk("lo"),
        content_chunk("", "stop"), b"data: [DONE]"])
    seen = []
    client = llm.Client("http://127.0.0.1:1", transport=transport)
    completion = client.stream([{"role": "user", "content": "hi"}],
                               on_token=seen.append)
    assert completion.content == "Hello"
    assert completion.finish_reason == "stop"
    assert seen == ["Hel", "lo"]


def test_the_finish_reason_and_usage_survive_the_stream():
    transport = FakeTransport([
        content_chunk("x", "length"),
        sse({"usage": {"completion_tokens": 900}, "choices": []}),
        b"data: [DONE]"])
    completion = llm.Client("http://x", transport=transport).stream([])
    assert completion.finish_reason == "length"
    assert completion.usage["completion_tokens"] == 900


def test_comments_and_blank_lines_are_not_data():
    """
    A `:` line is an SSE keep-alive. It carries no payload and it is precisely
    the case that must not look like a stall.
    """
    transport = FakeTransport([b": keep-alive", b"", content_chunk("ok", "stop"),
                               b"data: [DONE]"])
    assert llm.Client("http://x", transport=transport).stream([]).content == "ok"


def test_an_unparseable_chunk_is_skipped_rather_than_fatal():
    transport = FakeTransport([b"data: {not json",
                               content_chunk("fine", "stop"), b"data: [DONE]"])
    assert llm.Client("http://x", transport=transport).stream([]).content == "fine"


def test_streamed_tool_calls_reach_the_completion():
    transport = FakeTransport([
        sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c1", "function": {"name": "get_state"}}]}}]}),
        sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "{}"}}]},
            "finish_reason": "tool_calls"}]}),
        b"data: [DONE]"])
    completion = llm.Client("http://x", transport=transport).stream([])
    assert completion.tool_calls == [
        {"id": "c1", "name": "get_state", "arguments": "{}"}]


# -- the three timeouts (Q18) ------------------------------------------------

def test_the_three_bounds_are_the_documented_numbers():
    """Design 4.3's table, in one place, so nothing restates it."""
    assert llm.STALL_TIMEOUT_SEC == 30.0
    assert llm.TURN_TIMEOUT_SEC == 180.0
    assert llm.SERVER_READY_TIMEOUT_SEC == 60.0


def test_a_stall_stops_the_generation_visibly_and_is_logged(log_lines):
    """
    T6's carried-over rule from issue #11: every forced stop is visible in the
    chat AND written to the log. A swallowed timeout is the same defect as a
    swallowed paste.
    """
    clock = FakeClock()

    def ticking(lines):
        for line in lines:
            clock.now += 11.0
            yield line

    transport = FakeTransport([None, None, None, None])
    transport.post_stream = lambda *a, **k: ticking([None, None, None, None])

    shown = []
    client = llm.Client("http://x", transport=transport, clock=clock,
                        on_forced_stop=shown.append)
    with pytest.raises(llm.ForcedStop) as caught:
        client.stream([])
    assert "stall timeout" in caught.value.reason
    assert shown == ["The Concierge stopped responding."]
    assert any("forced stop" in line for line in log_lines())


def test_a_keepalive_resets_the_stall_clock():
    """
    Which is the whole reason the transport distinguishes a comment line from
    nothing at all: a server sending keep-alives is not a server that has hung.
    """
    clock = FakeClock()

    def ticking(*_a, **_k):
        for line in [None, b": keep-alive", None, content_chunk("ok", "stop"),
                     b"data: [DONE]"]:
            clock.now += 20.0
            yield line

    transport = FakeTransport([])
    transport.post_stream = ticking
    client = llm.Client("http://x", transport=transport, clock=clock)
    assert client.stream([]).content == "ok"


def test_the_turn_deadline_stops_a_generation_that_is_still_producing():
    """
    The turn bound is separate from the stall bound because a model that keeps
    streaming forever never stalls. Six repair iterations share one 180 s
    budget, which is why the deadline is passed in rather than computed here.
    """
    clock = FakeClock()

    def ticking(*_a, **_k):
        while True:
            clock.now += 10.0
            yield content_chunk("still going")

    transport = FakeTransport([])
    transport.post_stream = ticking
    shown = []
    client = llm.Client("http://x", transport=transport, clock=clock,
                        on_forced_stop=shown.append)
    with pytest.raises(llm.ForcedStop) as caught:
        client.stream([], deadline=25.0)
    assert "turn timeout" in caught.value.reason
    assert shown == ["The Concierge took too long and was stopped."]


def test_a_forced_stop_carries_the_sentence_the_chat_shows():
    """
    A bare exception type leaves the panel to invent the wording, and two
    surfaces inventing their own wording for the same event is what the status
    line exists to prevent.
    """
    stop = llm.ForcedStop("stall timeout after 30s", "The Concierge stopped responding.")
    assert stop.message.endswith(".")
    assert stop.reason != stop.message


def test_a_frontend_that_raises_does_not_replace_the_forced_stop(log_lines):
    clock = FakeClock()

    def ticking(*_a, **_k):
        for _ in range(4):
            clock.now += 11.0
            yield None

    transport = FakeTransport([])
    transport.post_stream = ticking

    def bad(_message):
        raise RuntimeError("panel is gone")

    client = llm.Client("http://x", transport=transport, clock=clock,
                        on_forced_stop=bad)
    with pytest.raises(llm.ForcedStop):
        client.stream([])
    assert any("ERROR in Concierge on_forced_stop" in line for line in log_lines())


def test_the_api_key_is_sent_as_a_bearer_token():
    """Q19's other half: the key is useless if the client does not present it."""
    client = llm.Client("http://x", api_key="s3cret")
    assert client.headers()["Authorization"] == "Bearer s3cret"
    assert "Authorization" not in llm.Client("http://x").headers()


# -- cancellation (design 2: a new send cancels the current generation) -------

def test_a_cancelled_generation_stops_and_is_not_a_forced_stop(log_lines):
    """
    Design 2 serialises sends: with `-np 1` a concurrent request would either
    queue behind this one or land somewhere that re-pays the knowledge pack in
    full, so a new send cancels the one in flight.

    It is deliberately **not** a `ForcedStop`. Nobody needs telling that the
    thing they just cancelled has stopped, and the chat message a forced stop
    carries would be noise.
    """
    stopping = {"now": False}
    transport = FakeTransport([content_chunk("one"), content_chunk("two"),
                               content_chunk("three", "stop"), b"data: [DONE]"])
    shown = []

    def cancelled():
        return stopping["now"]

    def on_token(_text):
        stopping["now"] = True          # something else sends, mid-stream

    client = llm.Client("http://x", transport=transport, cancelled=cancelled,
                        on_forced_stop=shown.append)
    completion = client.stream([], on_token=on_token)
    assert completion.finish_reason == llm.CANCELLED
    assert completion.content == "one"
    assert shown == []
    assert any("cancelled by a new send" in line for line in log_lines())


def test_a_cancellation_is_not_a_repair_trigger(registry):
    """
    A cancelled turn is not truncated and not a reply: the caller abandoned it.
    Feeding a half-generated decision back would make the *next* turn answer a
    question nobody asked.
    """
    decision = llm.decide(registry, llm.CANCELLED,
                          '{"action": "reply", "reply": "half a th')
    assert decision.kind == llm.INVALID
    assert "cancelled" in decision.reason
    assert llm.CANCELLED not in ("stop", "length", "tool_calls")


def test_nothing_is_cancelled_by_default():
    transport = FakeTransport([content_chunk("all of it", "stop"), b"data: [DONE]"])
    completion = llm.Client("http://x", transport=transport).stream([])
    assert completion.finish_reason == "stop" and completion.content == "all of it"
