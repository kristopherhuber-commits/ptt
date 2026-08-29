"""
The thread adapter: the seams it fills, and the checks that prove the hops.

`V-CG-115` … `V-CG-124`. No `QApplication`, no thread is started and no widget
is built. A `QObject` can be constructed without an application instance, so the
worker is exercised directly and its signals are read through ordinary direct
connections -- which is exactly what a queued connection would deliver, minus
the event loop.

What is worth pinning here is not the plumbing but the four seams that would
each fail silently:

- **`state_snapshot` fills what `tools.STATE_KEYS` declares** (Q26). The harness
  cannot import `UiState`, so a key added on one side and forgotten on the other
  is a key `get_state` returns as `"unknown"` -- and a model handed `"unknown"`
  invents a value for it. `test_concierge_layering.py` owns the harness half and
  says the Qt half lands in session 3; this is it.
- **`RELOAD_KEYS` is the panels' rule, not a second copy of it.**
- **`settings_applied` fires on a successful write and not on a refused one.**
  That signal is the whole of FR-CG-2's queued hop, and a write that changed
  `config.json` without emitting it is a change the user cannot see.
- **`SignalAudit` logs once, and once more from a second thread.** The token
  signal fires about thirty times a second into the log `read_log` reads.
"""

import ast
import hashlib
import os
import threading

import pytest

from ptt import config, paths
from ptt.concierge import fetch, llm
from ptt.concierge import sessions as sessions_mod
from ptt.concierge import state as state_mod
from ptt.concierge import tools as tools_mod
from ptt.ui import qt_concierge as panel_mod
from ptt.ui import qt_concierge_worker as worker_mod
from ptt.ui import qt_window as window_mod
from ptt.ui.qt_concierge_worker import BenchmarkBridge, ConciergeWorker
from ptt.ui.qt_statusview import UiState
from ptt.ui.qt_threadcheck import SignalAudit

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# -- V-CG-115: the module may not reach for a widget --------------------------

def test_the_adapter_imports_nothing_from_qtwidgets_or_qtgui():
    """
    Structural, and it is the rule stated as code: this module runs half its
    lines on a worker thread, and the way a violation of criterion v2-9 gets
    written is by importing a widget class here because it was convenient.
    Nothing in `PySide6.QtCore` can be painted.
    """
    tree = ast.parse(open(os.path.join(REPO, "app", "ptt", "ui",
                                       "qt_concierge_worker.py"),
                          encoding="utf-8").read())
    modules = {node.module for node in ast.walk(tree)
               if isinstance(node, ast.ImportFrom) and node.module
               and node.module.startswith("PySide6")}
    assert modules == {"PySide6.QtCore"}


def test_the_adapter_needs_no_pillow():
    """
    Why `log_thread` moved out of `qt_tray`. The tray draws its icon with PIL,
    which the test environment deliberately does not install, so importing the
    thread check from there made this whole file unimportable.
    """
    tree = ast.parse(open(os.path.join(REPO, "app", "ptt", "ui",
                                       "qt_threadcheck.py"),
                          encoding="utf-8").read())
    names = {node.module for node in ast.walk(tree)
             if isinstance(node, ast.ImportFrom) and node.module}
    assert not any(name.startswith("PIL") for name in names)


# -- V-CG-116: the state seam (Q26's Qt half) ---------------------------------

def test_the_adapter_supplies_exactly_the_keys_the_harness_declares():
    assert set(worker_mod.state_snapshot(UiState())) == set(tools_mod.STATE_KEYS)


def test_no_declared_key_comes_back_as_none():
    """
    `get_state` turns a missing key into `"unknown"`, which is a value the model
    will explain to the user. An empty string is honest; `None` is a crash in
    the JSON encoder or an invention downstream.
    """
    snapshot = worker_mod.state_snapshot(UiState())
    assert all(value is not None for value in snapshot.values())


def test_the_derived_detail_line_is_called_not_read():
    snapshot = worker_mod.state_snapshot(
        UiState(state="idle", status_text="Ready (CUDA)", device="cuda",
                model="large-v3-turbo"))
    assert "large-v3-turbo" in snapshot["detail"]
    assert snapshot["status_text"] == "Ready (CUDA)"


def test_a_key_added_to_the_declaration_arrives_rather_than_vanishing(monkeypatch):
    """
    The mutation this seam needs (`verification.md` section 4.1's discipline):
    add a key to the harness's declaration and the adapter must still produce
    it, because the alternative -- a snapshot written out by hand -- drops it
    silently and `get_state` reports `"unknown"`.
    """
    monkeypatch.setattr(tools_mod, "STATE_KEYS",
                        tools_mod.STATE_KEYS + ("pre_roll_seconds",))
    snapshot = worker_mod.state_snapshot(UiState())
    assert snapshot["pre_roll_seconds"] == ""
    assert set(snapshot) == set(tools_mod.STATE_KEYS)


# -- V-CG-117: the reload rule is the panels' -------------------------------

def panel_reload_fields():
    """Every field a panel passes to `apply_now(..., reload_model=True)`."""
    directory = os.path.join(REPO, "app", "ptt", "ui", "panels")
    found = set()
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(directory, name),
                              encoding="utf-8").read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            attr = getattr(node.func, "attr", "")
            if attr != "apply_now" or not node.args:
                continue
            reload_model = any(
                kw.arg == "reload_model" and getattr(kw.value, "value", False)
                for kw in node.keywords)
            if reload_model and isinstance(node.args[0], ast.Constant):
                found.add(node.args[0].value)
    return found


def test_the_reload_keys_are_the_ones_the_panels_reload_for():
    """
    Not a list someone kept in step. The panels wrote this rule down first, at
    their call sites, and a third setting joining them there must not leave the
    Concierge writing it without telling the engine.
    """
    assert set(worker_mod.RELOAD_KEYS) == panel_reload_fields()


def test_the_reload_keys_are_real_settings():
    for key in worker_mod.RELOAD_KEYS:
        assert key in config.FIELDS


# -- V-CG-118: the thread check ----------------------------------------------

def test_a_signal_logs_once_and_then_stays_quiet():
    lines = []
    audit = SignalAudit(log=lambda where, expect_gui: lines.append(where))
    assert audit.check("token", expect_gui=False) is True
    for _ in range(30):
        audit.check("token", expect_gui=False)
    assert lines == ["Concierge token"]


def test_the_same_signal_from_a_second_thread_logs_again():
    """
    The refinement Q26 needs to produce v3-10's evidence: the idle timer emits
    `state_changed`, the same signal the worker thread already emitted, and
    keying on the signal alone would suppress the one line that proves the
    second hop exists.
    """
    lines = []
    audit = SignalAudit(log=lambda where, expect_gui: lines.append(where))
    audit.check("state_changed", expect_gui=False)
    thread = threading.Thread(target=audit.check, name="concierge-idle",
                              args=("state_changed", False))
    thread.start()
    thread.join()
    assert len(lines) == 2
    assert {name for _what, name in audit.seen()} == \
        {threading.current_thread().name, "concierge-idle"}


def test_a_renamed_thread_is_still_the_same_thread():
    """
    **`V-CG-138`, and it is `development_history.md` #48.** The bound was keyed
    on `threading.current_thread().name`, which is stable for a Python thread
    and is *not* stable for a `QThread`: PySide6 enters the interpreter afresh
    for each queued slot invocation, so one worker thread reports `Dummy-1`,
    `Dummy-2`, `Dummy-3` in turn and no key is ever a repeat.

    L1 has no Qt in it (CON-CG-6), so the condition is reproduced the way it
    actually presents -- one thread whose name changes underneath the audit --
    rather than by importing the thing that causes it. Six emissions, one
    thread, one line.
    """
    lines = []
    audit = SignalAudit(log=lambda where, expect_gui: lines.append(where))
    current = threading.current_thread()
    original = current.name
    try:
        for index in range(6):
            current.name = f"Dummy-{index + 1}"
            audit.check("token", expect_gui=False)
    finally:
        current.name = original
    assert lines == ["Concierge token"]


def test_a_signal_that_never_fires_never_logs():
    lines = []
    SignalAudit(log=lambda where, expect_gui: lines.append(where))
    assert lines == []


def test_a_failing_logger_does_not_take_the_emit_down(log_lines):
    def explode(_where, _expect_gui):
        raise RuntimeError("no log today")

    audit = SignalAudit(log=explode)
    assert audit.check("token", expect_gui=False) is True
    assert any("thread check itself failed" in line for line in log_lines())


# -- the worker ---------------------------------------------------------------

@pytest.fixture
def workspace(tmp_path):
    pack = tmp_path / "kb.md"
    pack.write_text("# knowledge\nthe pre-roll buffer covers the gap.\n",
                    encoding="utf-8")
    prompt = tmp_path / "system_prompt.md"
    prompt.write_text("<!-- editorial -->\nYou are the Concierge.\n",
                      encoding="utf-8")
    return {"pack": str(pack), "prompt": str(prompt)}


@pytest.fixture
def worker(tmp_path, workspace):
    settings = config.Settings(path=str(tmp_path / "config.json"))
    # Opted in, because that is the state every test below except the gate's
    # own is about. A fresh `Settings` arrives `unset` -- which is the whole
    # point of Q26's tri-state, and which now stops the runtime starting.
    settings.set("concierge.opt_in", config.OPT_IN_ACCEPTED)
    memory = tools_mod.MemoryNote(str(tmp_path / "note.txt"),
                                  str(tmp_path / "note.prev.txt"))
    made = ConciergeWorker(
        settings,
        state_provider=lambda: worker_mod.state_snapshot(UiState()),
        devices=lambda: (),
        benchmark=lambda _model: {"seconds": 1.0, "device": "cuda"},
        cuda_supported=True,
        exe_path=str(tmp_path / "llama-server.exe"),
        model_dir=str(tmp_path / "models"),
        pack_path=workspace["pack"],
        prompt_path=workspace["prompt"],
        memory=memory,
    )
    ok, reason = made._ensure_context()
    assert ok, reason
    return made


def collect(signal):
    """Record every emission of one signal, through a direct connection."""
    seen = []
    signal.connect(lambda *args: seen.append(args))
    return seen


# -- V-CG-119: where the machine starts ---------------------------------------

def test_no_cuda_starts_disabled_and_stays_there(tmp_path, workspace):
    made = ConciergeWorker(
        config.Settings(path=str(tmp_path / "config.json")),
        state_provider=dict, cuda_supported=False,
        model_dir=str(tmp_path / "models"),
        pack_path=workspace["pack"], prompt_path=workspace["prompt"],
        memory=tools_mod.MemoryNote(str(tmp_path / "n.txt"),
                                    str(tmp_path / "n.prev.txt")))
    assert made.machine.state == state_mod.DISABLED
    made.on_start()
    assert made.machine.state == state_mod.DISABLED
    assert made.server is None


def test_a_missing_gguf_starts_not_downloaded(worker):
    assert worker.machine.state == state_mod.NOT_DOWNLOADED


def test_a_present_gguf_starts_stopped(tmp_path, workspace):
    settings = config.Settings(path=str(tmp_path / "config.json"))
    from ptt.concierge import fetch
    spec = fetch.spec_for(settings.get("concierge.model"))
    models = tmp_path / "models"
    models.mkdir()
    (models / spec.filename).write_bytes(b"not really a gguf")
    made = ConciergeWorker(
        settings, state_provider=dict, model_dir=str(models),
        pack_path=workspace["pack"], prompt_path=workspace["prompt"],
        memory=tools_mod.MemoryNote(str(tmp_path / "n.txt"),
                                    str(tmp_path / "n.prev.txt")))
    assert made.machine.state == state_mod.STOPPED


def test_starting_without_a_model_says_so_rather_than_launching(worker):
    states = collect(worker.state_changed)
    worker.on_start()
    assert worker.server is None
    assert states[-1][0] == state_mod.NOT_DOWNLOADED
    assert "downloaded" in states[-1][1]


# -- V-CG-120: FR-CG-2's hop --------------------------------------------------

def test_a_successful_write_emits_the_settings_hop(worker):
    """
    The signal the GUI thread turns into `refresh_panels()` + `refresh_menu()`.
    Without it the value is in `config.json` and in the settings object and
    nowhere on screen until something else happens to repaint.
    """
    applied = collect(worker.settings_applied)
    result = worker.registry.call("set_config", {"key": "use_gpu", "value": False})
    assert result.get("ok") is True
    assert applied == [("use_gpu", True, False)]


def test_a_refused_write_emits_nothing_and_changes_nothing(worker):
    applied = collect(worker.settings_applied)
    changes = collect(worker.change_recorded)
    result = worker.registry.call("set_config", {"key": "use_gpu",
                                                 "value": "false"})
    assert result.get("error") is True
    assert applied == [] and changes == []
    assert worker._settings.get("use_gpu") is True


def test_a_write_the_registry_does_not_allow_never_reaches_settings(worker):
    applied = collect(worker.settings_applied)
    result = worker.registry.call("set_config", {"key": "vocabulary",
                                                 "value": "anything"})
    assert result.get("error") is True and applied == []


def test_both_writing_tools_record_a_chip(worker):
    changes = collect(worker.change_recorded)
    worker.registry.call("set_config", {"key": "use_gpu", "value": False})
    worker.registry.call("update_memory", {"text": "prefers the medium model"})
    assert [c[1] for c in changes] == ["config", "memory"]
    assert changes[0][2] == "use_gpu"


def test_the_worker_never_calls_apply_now():
    """
    Stated as a check because it is the one mistake the whole adapter exists to
    prevent: `InstantApplyPanel.apply_now` is a QWidget method, and calling it
    from this thread is a criterion v2-9 violation that raises nothing and
    corrupts the UI silently.
    """
    source = open(os.path.join(REPO, "app", "ptt", "ui",
                               "qt_concierge_worker.py"), encoding="utf-8").read()
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and getattr(node.func, "attr", "") == "apply_now"]
    assert calls == []


# -- V-CG-120b: the client is built with a transport --------------------------

def test_the_client_the_adapter_builds_carries_a_transport(worker):
    """
    Found in the session's own hand test, twice, as "the knowledge pack
    prewarm failed: 'NoneType' object has no attribute 'post_stream'".

    `llm.Client`'s transport seam defaults to `None` on purpose -- an L1 test
    that forgets to inject a fake must not be able to open a socket -- so
    filling it belongs to the caller, and this adapter is the only caller in
    the shipped app. Both of its call sites forgot, and every test in this file
    injects a fake client, so nothing here could see it.
    """
    client = worker._client("http://127.0.0.1:1", "key")
    assert client._transport is not None
    assert isinstance(client._transport, llm.HttpTransport)


def test_an_injected_transport_is_not_overwritten(worker):
    """The seam stays a seam: the rig and the L1 fakes still supply their own."""
    sentinel = object()
    client = worker._client("http://127.0.0.1:1", "key", transport=sentinel)
    assert client._transport is sentinel


def test_every_client_this_adapter_builds_goes_through_that_helper():
    """
    Structural, because the defect was a *missing* call and not a wrong one:
    the two call sites each constructed a client directly, so fixing one would
    have left the other.
    """
    source = open(os.path.join(REPO, "app", "ptt", "ui",
                               "qt_concierge_worker.py"), encoding="utf-8").read()
    tree = ast.parse(source)
    direct = [node for node in ast.walk(tree)
              if isinstance(node, ast.Call)
              and getattr(node.func, "attr", "") == "_make_client"]
    assert len(direct) == 1, "only `_client` may construct a client"


# -- V-CG-121: undo and session restore --------------------------------------

def test_one_chip_is_undone_and_the_broadcast_names_the_key(worker):
    """
    The key matters, not just the fact of a change: `RELOAD_KEYS` is checked
    against it, so an undo of "switch me to the medium model" that broadcast an
    empty key would put `model` back in `config.json` and leave the medium model
    loaded in VRAM.
    """
    worker.registry.call("set_config", {"key": "model", "value": "medium.en"})
    finished = collect(worker.undo_finished)
    applied = collect(worker.settings_applied)
    worker.on_undo(1)
    assert finished == [(1, True, "")]
    assert worker._settings.get("model") == "large-v3-turbo"
    assert applied and applied[0][0] == "model"
    assert applied[0][0] in worker_mod.RELOAD_KEYS


def test_a_restore_names_every_key_it_put_back(worker):
    worker.registry.call("set_config", {"key": "model", "value": "medium.en"})
    worker.registry.call("set_config", {"key": "use_gpu", "value": False})
    applied = collect(worker.settings_applied)
    worker.on_restore()
    assert sorted(key for key, _new, _old in applied) == ["model", "use_gpu"]


def test_a_refused_undo_reports_the_reason(worker):
    finished = collect(worker.undo_finished)
    worker.on_undo(42)
    assert finished[0][0] == 42 and finished[0][1] is False
    assert "no change #42" in finished[0][2]


def test_a_session_restore_reports_every_change_it_put_back(worker):
    worker.registry.call("set_config", {"key": "use_gpu", "value": False})
    worker.registry.call("set_config", {"key": "keep_stream_warm", "value": False})
    finished = collect(worker.undo_finished)
    notices = collect(worker.notice)
    worker.on_restore()
    assert sorted(seq for seq, _ok, _reason in finished) == [1, 2]
    assert all(ok for _seq, ok, _reason in finished)
    assert worker._settings.get("use_gpu") is True
    assert worker._settings.get("keep_stream_warm") is True
    assert "Restored 2 change(s)" in notices[-1][0]


def test_a_restore_touches_only_what_the_journal_recorded(worker):
    """
    Q24. The user edited `model` by hand in the Model tab while the chat was
    open; the Concierge never touched it, so a restore must leave it alone.
    """
    worker.registry.call("set_config", {"key": "use_gpu", "value": False})
    worker._settings.set("model", "medium.en")
    worker.on_restore()
    assert worker._settings.get("model") == "medium.en"
    assert worker._settings.get("use_gpu") is True


# -- V-CG-122: the memory note -----------------------------------------------

def test_the_note_is_published_whenever_it_changes(worker):
    changed = collect(worker.memory_changed)
    worker.on_memory_save("prefers the medium model")
    assert changed[-1] == ("prefers the medium model", False)
    worker.on_memory_save("and speaks into a Yeti")
    assert changed[-1] == ("and speaks into a Yeti", True)


def test_restoring_the_previous_note_swaps_the_two(worker):
    worker.on_memory_save("first")
    worker.on_memory_save("second")
    changed = collect(worker.memory_changed)
    worker.on_memory_restore()
    assert changed[-1][0] == "first"
    worker.on_memory_restore()
    assert worker.memory.read() == "second"


def test_restoring_with_no_previous_version_says_so(worker):
    notices = collect(worker.notice)
    worker.on_memory_restore()
    assert "no previous memory note" in notices[-1][0]


def test_an_oversized_note_is_refused_and_reported(worker):
    notices = collect(worker.notice)
    worker.on_memory_save("x" * (tools_mod.MEMORY_NOTE_MAX_CHARS + 1))
    assert "not saved" in notices[-1][0]
    assert worker.memory.read() == ""


# -- V-CG-123: deleting the model (Q25) ---------------------------------------

def test_deleting_the_model_removes_it_and_returns_to_not_downloaded(
        tmp_path, workspace):
    settings = config.Settings(path=str(tmp_path / "config.json"))
    from ptt.concierge import fetch
    spec = fetch.spec_for(settings.get("concierge.model"))
    models = tmp_path / "models"
    models.mkdir()
    gguf = models / spec.filename
    gguf.write_bytes(b"weights")
    (models / (spec.filename + ".part")).write_bytes(b"half a download")

    made = ConciergeWorker(
        settings, state_provider=dict, model_dir=str(models),
        pack_path=workspace["pack"], prompt_path=workspace["prompt"],
        memory=tools_mod.MemoryNote(str(tmp_path / "n.txt"),
                                    str(tmp_path / "n.prev.txt")))
    assert made.machine.state == state_mod.STOPPED
    made.on_delete_model()
    assert not gguf.exists()
    assert not (models / (spec.filename + ".part")).exists()
    assert made.machine.state == state_mod.NOT_DOWNLOADED


def test_deleting_when_there_is_nothing_to_delete_says_so(worker):
    notices = collect(worker.notice)
    worker.on_delete_model()
    assert "no downloaded model" in notices[-1][0]


# -- V-CG-124: the benchmark handshake ---------------------------------------

class FakeEngine:
    """
    The engine, as the benchmark bridge sees it.

    `current_model` is the tier **actually resident**, which stopped being the
    same thing as `settings.model` in v3.0 -- a Concierge write lands
    immediately and the reload behind it can be seconds late. The bridge waits
    for this to catch up before it measures anything.
    """

    def __init__(self, current_model="large-v3-turbo"):
        self.asked = 0
        self.reloads = 0
        self.current_model = current_model

    def request_benchmark(self):
        self.asked += 1

    def request_model_reload(self):
        self.reloads += 1


def test_a_tier_that_is_not_loaded_is_refused_with_both_ways_out(tmp_path):
    """
    Both, and the cheap one first. "Measure the model I'm using" with a stale
    tier in the argument is the commonest way here, and the right correction is
    "call it again with the loaded one" -- not "change the user's settings",
    which is what the hint used to say and what the model then relayed as "I
    cannot measure the model you are currently using".
    """
    settings = config.Settings(path=str(tmp_path / "config.json"))
    engine = FakeEngine()
    bridge = BenchmarkBridge(settings, lambda: engine)
    result = bridge.run("tiny.en")
    assert result["error"] is True
    assert "only the loaded model" in result["reason"]
    assert f"run_benchmark({settings.get('model')!r})" in result["hint"]
    assert "set_config('model', 'tiny.en')" in result["hint"]
    assert result["hint"].index("run_benchmark") < result["hint"].index("set_config")
    assert engine.asked == 0


def test_the_measurement_the_engine_reports_is_what_comes_back(tmp_path):
    settings = config.Settings(path=str(tmp_path / "config.json"))
    engine = FakeEngine(current_model=settings.get("model"))
    bridge = BenchmarkBridge(settings, lambda: engine)
    threading.Timer(0.05, lambda: bridge.deliver(settings.get("model"),
                                                 "cuda", 2.34)).start()
    result = bridge.run(settings.get("model"))
    assert result["seconds"] == 2.34 and result["device"] == "cuda"
    assert engine.asked == 1


def test_a_measurement_of_some_other_tier_is_not_taken_as_this_one(tmp_path):
    """
    The Model tab's own Measure button reaches the same hop. Handing its number
    to a tool call that asked about a different tier is how a benchmark comes
    back confidently wrong -- which is the defect this whole path was rebuilt
    around.
    """
    settings = config.Settings(path=str(tmp_path / "config.json"))
    engine = FakeEngine(current_model=settings.get("model"))
    bridge = BenchmarkBridge(settings, lambda: engine, timeout=0.4)
    threading.Timer(0.05, lambda: bridge.deliver("tiny.en", "cuda", 0.4)).start()
    result = bridge.run(settings.get("model"))
    assert result["error"] is True and "did not finish" in result["reason"]


def test_a_held_reload_is_flushed_before_the_measurement(tmp_path):
    """
    The other half of `_request_reload`. A reload is held while the model is
    *generating*, because the allocation trips the stall bound; a tool call is
    the opposite case -- the worker is inside this function and no stream is
    open -- so the held reload happens here, and the measurement waits for it.
    """
    settings = config.Settings(path=str(tmp_path / "config.json"))
    engine = FakeEngine(current_model="medium.en")
    flushed = []

    def flush():
        flushed.append(True)
        engine.current_model = settings.get("model")
        return True

    bridge = BenchmarkBridge(settings, lambda: engine, flush_reload=flush)
    threading.Timer(0.05, lambda: bridge.deliver(settings.get("model"),
                                                 "cuda", 1.1)).start()
    result = bridge.run(settings.get("model"))
    assert flushed == [True]
    assert result["seconds"] == 1.1


def test_a_load_that_never_finishes_ends_the_tool_call(tmp_path):
    settings = config.Settings(path=str(tmp_path / "config.json"))
    engine = FakeEngine(current_model="medium.en")
    bridge = BenchmarkBridge(settings, lambda: engine, sleep=lambda _s: None,
                             load_timeout=1.0)
    result = bridge.run(settings.get("model"))
    assert result["error"] is True
    assert "did not finish loading" in result["reason"]
    assert engine.asked == 0


def test_a_measurement_that_never_arrives_ends_the_tool_call(tmp_path):
    """
    Bounded because this blocks the worker thread: a tool that never returns is
    a turn that never ends, and the turn timeout only bounds *generations*.
    """
    settings = config.Settings(path=str(tmp_path / "config.json"))
    engine = FakeEngine(current_model=settings.get("model"))
    bridge = BenchmarkBridge(settings, lambda: engine, timeout=0.05)
    result = bridge.run(settings.get("model"))
    assert result["error"] is True and "did not finish" in result["reason"]


def test_no_engine_is_a_refusal_rather_than_a_crash(tmp_path):
    settings = config.Settings(path=str(tmp_path / "config.json"))
    bridge = BenchmarkBridge(settings, lambda: None)
    assert bridge.run(settings.get("model"))["error"] is True


def test_the_benchmark_seam_reaches_the_tool(worker):
    """The registry's `run_benchmark` runs whatever the adapter injected."""
    result = worker.registry.call("run_benchmark", {"model": "large-v3-turbo"})
    assert result["seconds"] == 1.0
    assert result["llm_resident"] is False


# -- the startup reap is actually reached (FR-CG-9's backstop) ---------------

def test_the_controller_runs_the_startup_reap():
    """
    `server.reap_orphan` was built in session 1 and called by nothing until the
    adapter existed, so criterion v3-7's fourth audit -- simulate a build with
    no job object, confirm startup reaps via `concierge_state.json` + `/props`
    -- had no code path to exercise. Checked structurally, because the reap runs
    on its own thread and probes HTTP, neither of which belongs in L1.
    """
    source = open(os.path.join(REPO, "app", "ptt", "ui",
                               "qt_concierge_worker.py"), encoding="utf-8").read()
    tree = ast.parse(source)
    controller = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.ClassDef)
                      and node.name == "ConciergeController")
    init = next(node for node in controller.body
                if isinstance(node, ast.FunctionDef) and node.name == "__init__")
    assert any(getattr(node.func, "attr", "") == "_reap_orphan"
               for node in ast.walk(init) if isinstance(node, ast.Call))
    assert any(getattr(node.func, "attr", "") == "reap_orphan"
               for node in ast.walk(controller) if isinstance(node, ast.Call))


# -- the paths the adapter uses ----------------------------------------------

def test_saved_transcripts_live_beside_config_json():
    assert os.path.dirname(paths.concierge_sessions_path()) == paths.APP_DIR
    assert paths.concierge_sessions_path().endswith("concierge_sessions.json")


# -- V-CG-129: the download slot (FR-CG-7, session 4) -------------------------
#
# Everything here runs the real `on_download` against a fake transport. What is
# worth pinning is not that a file arrives -- `test_concierge_fetch.py` owns
# that -- but the four *outcomes* the adapter has to tell apart, because three
# of them look like "it did not download" from the outside and only one of them
# is worth reporting as a failure.

BODY = b"g" * (3 * fetch.Download.CHUNK + 7)
DIGEST = hashlib.sha256(BODY).hexdigest()


def fake_spec(name="fake.gguf"):
    return fetch.ModelSpec(
        key="gemma-4-12b-q4_k_m",
        repo="lmstudio-community/gemma-4-12B-it-GGUF",
        filename=name, sha256=DIGEST, size_bytes=len(BODY),
        label="Gemma 4 12B")


class FakeReader:
    def __init__(self, data, hook=None):
        self.data, self.hook = data, hook

    def read(self, size):
        if self.hook:
            self.hook()
        block, self.data = self.data[:size], self.data[size:]
        return block

    def close(self):
        pass


class FakeTransport:
    """The tree API and the CDN, without either."""

    def __init__(self, oid=None, hook=None, body=BODY):
        self.oid = DIGEST if oid is None else oid
        self.hook = hook
        self.body = body
        self.opened = []

    def get_json(self, url):
        return [{"path": "fake.gguf",
                 "lfs": {"oid": self.oid, "size": len(self.body)}}]

    def open_range(self, url, start=0):
        self.opened.append(start)
        return (206 if start else 200), len(self.body), \
            FakeReader(self.body[start:], self.hook)


@pytest.fixture
def downloader(worker, tmp_path, monkeypatch):
    """`worker`, with the pinned tier swapped for a three-megabyte fake."""
    monkeypatch.setitem(fetch.MODELS, "gemma-4-12b-q4_k_m", fake_spec())
    transport = FakeTransport()
    worker._make_download = lambda spec, directory, **kwargs: fetch.Download(
        spec, directory, transport=transport, **kwargs)
    worker.transport = transport
    return worker


def test_a_finished_download_leaves_a_verified_file_and_a_stopped_machine(downloader):
    states = collect(downloader.state_changed)
    finished = collect(downloader.download_finished)
    downloader.on_download()

    assert open(downloader.model_path(), "rb").read() == BODY
    assert downloader.machine.state == state_mod.STOPPED
    assert finished[-1] == (True, "", False)
    assert any(s[0] == state_mod.DOWNLOADING for s in states)


def test_progress_arrives_as_bytes_and_as_a_state_detail(downloader):
    """
    Two channels, one throttle. Design 8 makes the percentage a re-entry into
    `downloading` rather than eight more states, so the caption and the status
    bar read it there; the signal carries the numbers a determinate bar needs.
    """
    progress = collect(downloader.download_progress)
    states = collect(downloader.state_changed)
    downloader.on_download()

    assert progress, "the bar was never told anything"
    assert progress[-1] == (len(BODY), len(BODY))
    assert all(total == len(BODY) for _done, total in progress)
    downloading = [detail for state, detail in states
                   if state == state_mod.DOWNLOADING]
    assert any("of" in d and "%" in d for d in downloading)


def test_the_last_chunk_is_never_throttled_away(downloader, monkeypatch):
    """
    A bar that stops at 99 % because the final call landed inside the interval
    is the one position on it anybody looks at.
    """
    monkeypatch.setattr(worker_mod, "PROGRESS_INTERVAL_SEC", 3600.0)
    progress = collect(downloader.download_progress)
    downloader.on_download()
    assert progress[-1] == (len(BODY), len(BODY))


def test_the_signal_carries_a_size_a_32_bit_int_could_not(downloader):
    """
    The GGUF is 7 381 382 944 bytes. PySide6 marshals a signal declared `int`
    as a C++ 32-bit `int`, so a total declared that way arrives negative at 78 %
    of the way through -- and the bar runs backwards. `object` is why it does
    not.
    """
    seen = collect(downloader.download_progress)
    downloader._on_download_progress(7_000_000_000, 7_381_382_944)
    assert seen[-1] == (7_000_000_000, 7_381_382_944)


def test_a_substituted_upstream_file_is_refused_before_a_byte_is_fetched(downloader):
    """
    **FR-CG-7's whole point (Q26).** The `oid` is compared with the pin first,
    so a re-uploaded GGUF costs nothing and produces no partial file.
    """
    downloader.transport.oid = "0" * 64
    finished = collect(downloader.download_finished)

    downloader.on_download()

    ok, reason, refused = finished[-1]
    assert (ok, refused) == (False, True)
    assert "not the pinned" in reason
    assert downloader.transport.opened == [], "the CDN was reached anyway"
    assert not os.path.exists(downloader.model_path())
    assert not os.path.exists(downloader.model_path() + ".part")
    assert downloader.machine.state == state_mod.NOT_DOWNLOADED


def test_a_refusal_latches_and_a_retry_never_reaches_the_network(downloader):
    """
    The refusal is a re-qualification event, not a retryable failure: a second
    attempt must not so much as ask, because the answer cannot have changed and
    a request that looks like a retry invites a UI that offers one.
    """
    downloader.transport.oid = "0" * 64
    downloader.on_download()
    first = downloader.download_refusal
    assert first

    downloader.transport.oid = DIGEST          # even if upstream is fixed
    finished = collect(downloader.download_finished)
    downloader.on_download()

    assert downloader.download_refusal == first
    assert finished[-1] == (False, first, True)
    assert not os.path.exists(downloader.model_path())


def test_a_refusal_also_stops_the_panel_starting_one_by_itself(downloader):
    downloader.transport.oid = "0" * 64
    downloader.on_download()
    assert downloader.auto_download is False


def test_a_cancelled_download_keeps_its_partial_file_and_is_not_a_failure(downloader):
    """
    Criterion v3-5. The application exits during a 6.87 GB transfer far more
    often than it finishes one, and what makes the next launch a resume rather
    than a restart is exactly this file.
    """
    def stop_after_one_chunk():
        if os.path.exists(downloader.model_path() + ".part"):
            downloader.cancel_download.set()

    downloader.transport.hook = stop_after_one_chunk
    finished = collect(downloader.download_finished)
    downloader.on_download()

    ok, reason, refused = finished[-1]
    assert (ok, refused) == (False, False)
    assert reason == fetch.Download.CANCELLED
    assert os.path.getsize(downloader.model_path() + ".part") > 0
    assert not os.path.exists(downloader.model_path())
    assert downloader.machine.state == state_mod.NOT_DOWNLOADED
    assert "paused at" in downloader.machine.detail


def test_a_relaunch_resumes_from_the_partial_file(downloader):
    def stop_after_one_chunk():
        if os.path.exists(downloader.model_path() + ".part"):
            downloader.cancel_download.set()

    downloader.transport.hook = stop_after_one_chunk
    downloader.on_download()
    resumed_from = downloader.partial_bytes()
    assert resumed_from

    downloader.transport.hook = None
    downloader.cancel_download.clear()
    downloader.on_download()

    assert downloader.transport.opened[-1] == resumed_from
    assert open(downloader.model_path(), "rb").read() == BODY


def test_a_download_already_on_disk_is_not_fetched_again(downloader):
    downloader.on_download()
    downloader.transport.opened.clear()
    downloader.on_download()
    assert downloader.transport.opened == []
    assert downloader.machine.state == state_mod.STOPPED


def test_deleting_the_model_switches_the_automatic_download_off(downloader):
    """
    Handoff 8.2 starts the transfer when an opted-in panel opens with no model
    on disk. That is right for a first run and wrong straight after a delete: a
    6.87 GB file that comes back by itself is not a file the user deleted.
    """
    downloader.on_download()
    assert downloader.auto_download is True
    downloader.on_delete_model()
    assert downloader.auto_download is False
    assert downloader.machine.state == state_mod.NOT_DOWNLOADED
    assert not os.path.exists(downloader.model_path())


def test_the_download_slot_carries_the_gate_as_well_as_the_controller(downloader):
    """
    Defence in depth on the expensive action. The controller refuses to ask and
    the card that carries the button is not on screen -- but 6.87 GB is the
    wrong thing to protect with a UI state alone (`development_history.md` #42).
    """
    for opt_in in (config.OPT_IN_UNSET, config.OPT_IN_DECLINED):
        downloader._settings.set("concierge.opt_in", opt_in)
        downloader.transport.opened.clear()
        downloader.on_download()
        assert downloader.transport.opened == []
        assert not os.path.exists(downloader.model_path())

    downloader._settings.set("concierge.opt_in", config.OPT_IN_ACCEPTED)
    downloader._settings.set("concierge.enabled", False)
    downloader.on_download()
    assert not os.path.exists(downloader.model_path())


# -- V-CG-130: the two keys that decide whether anything runs (FR-CG-6, Q26) --

def place_model(worker):
    """An empty file where the GGUF would be, so only the gate can stop a start."""
    os.makedirs(os.path.dirname(worker.model_path()), exist_ok=True)
    open(worker.model_path(), "wb").close()


def test_an_unanswered_install_starts_no_runtime(worker, tmp_path):
    """
    A fresh `config.json` arrives `unset`, and `unset` starts nothing. This is
    criterion v3-8's other half: the upgrade must not opt anybody in, and "not
    opted in" has to mean something at the moment a panel opens.
    """
    worker._settings.set("concierge.opt_in", config.OPT_IN_UNSET)
    place_model(worker)
    states = collect(worker.state_changed)
    worker.on_start()
    assert worker.server is None
    assert states == []


def test_a_declined_install_starts_no_runtime(worker):
    worker._settings.set("concierge.opt_in", config.OPT_IN_DECLINED)
    place_model(worker)
    worker.on_start()
    assert worker.server is None


def test_switched_off_starts_no_runtime_even_though_it_was_accepted(worker):
    """`enabled: false` is not `declined`, and both stop the runtime."""
    worker._settings.set("concierge.enabled", False)
    place_model(worker)
    worker.on_start()
    assert worker.server is None


def test_the_adapter_reads_the_switch_through_config_and_not_by_hand(worker):
    for opt_in in config.OPT_IN_STATES:
        for enabled in (True, False):
            worker._settings.set("concierge.opt_in", opt_in)
            worker._settings.set("concierge.enabled", enabled)
            assert worker_mod.switched_on(worker._settings) == \
                config.concierge_switched_on(opt_in, enabled)


def test_no_cuda_starts_nothing_and_has_no_way_out(tmp_path, workspace):
    """FR-CG-12, and design 8's `disabled` having no outgoing edges."""
    settings = config.Settings(path=str(tmp_path / "config.json"))
    settings.set("concierge.opt_in", config.OPT_IN_ACCEPTED)
    made = ConciergeWorker(
        settings, state_provider=lambda: worker_mod.state_snapshot(UiState()),
        cuda_supported=False, exe_path=str(tmp_path / "llama-server.exe"),
        model_dir=str(tmp_path / "models"), pack_path=workspace["pack"],
        prompt_path=workspace["prompt"],
        memory=tools_mod.MemoryNote(str(tmp_path / "n.txt"),
                                    str(tmp_path / "n.prev.txt")))
    assert made.machine.state == state_mod.DISABLED
    made.on_start()
    made.on_download()
    assert made.server is None
    assert made.machine.state == state_mod.DISABLED
    assert not os.path.exists(str(tmp_path / "models"))


# -- V-CG-132: the controller's gates and the first run (session 4) -----------
#
# `ConciergeController` is built here against a **fake panel**, not a widget:
# what is under test is which of the worker's slots it asks for and which it
# refuses to, and every one of those decisions is taken before a pixel is
# involved. The worker is moved to a thread that is never started, so a queued
# request piles up rather than running -- which is exactly what makes the
# *request* observable and the side effect not.


class FakeSignal:
    """`connect`/`emit`, and a record of what went out."""

    def __init__(self):
        self.slots = []
        self.emissions = []

    def connect(self, slot, *_args, **_kwargs):
        self.slots.append(slot)

    def emit(self, *args):
        self.emissions.append(args)
        for slot in list(self.slots):
            slot(*args)


class FakePanel:
    """
    Every panel method the controller calls, recorded rather than drawn.

    `calls` is annotated at class level so it resolves as a list rather than
    through `__getattr__`, which is what a reader -- and a type checker -- would
    otherwise have to guess at.
    """

    calls: list

    SIGNALS = (
        "send_requested", "undo_requested", "restore_requested",
        "new_session_requested", "save_session_requested",
        "open_session_requested", "memory_open_requested",
        "memory_save_requested", "memory_restore_requested",
        "delete_model_requested", "opt_in_requested", "enabled_requested",
        "download_requested", "pause_download_requested", "residency_requested",
        "setup_requested",
    )

    def __init__(self):
        object.__setattr__(self, "calls", [])
        for name in self.SIGNALS:
            object.__setattr__(self, name, FakeSignal())
        self.view = panel_mod.ConciergeView()

    def status_segment(self):
        return ""

    def set_state(self, state, detail=""):
        self.calls.append(("set_state", state, detail))
        self.view.set_state(state, detail)

    def set_opt_in(self, opt_in, enabled=True):
        self.calls.append(("set_opt_in", opt_in, enabled))
        self.view.opt_in, self.view.enabled = opt_in, bool(enabled)

    def set_download_refusal(self, reason):
        self.calls.append(("set_download_refusal", reason))
        self.view.download_refusal = reason or ""

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def record(*args, **_kwargs):
            self.calls.append((name, *args))
        return record

    def named(self, name):
        return [c for c in self.calls if c[0] == name]


class FakeThread:
    """
    The `QThread` the controller owns, stood down.

    Started for real, it would run an event loop that delivers `on_start` --
    which launches a subprocess, writes `app/concierge_state.json` and waits on
    a health probe. None of that is what these tests are about, and all of it
    reaches outside `tmp_path`. What the tests need is the *request*, and a
    thread that never runs is what makes the request the only observable thing.
    """

    def __init__(self):
        self.started = []
        self.running = False

    def isRunning(self):
        return self.running

    def start(self):
        self.started.append(True)

    def quit(self):
        pass

    def wait(self, _ms=0):
        return True

    def setObjectName(self, _name):
        pass


@pytest.fixture
def controller(worker, tmp_path, monkeypatch):
    """A real controller over the `worker` fixture and a fake panel."""
    monkeypatch.setattr(worker_mod.server_mod, "reap_orphan",
                        lambda *a, **kw: (False, "stubbed"))
    panel = FakePanel()
    made = worker_mod.ConciergeController(
        worker._settings, panel,
        ui_state=UiState(), engine_provider=lambda: None,
        cuda_supported=True, worker=worker,
        store=sessions_mod.SessionStore(str(tmp_path / "sessions.json")))
    made.thread = FakeThread()
    return made


def emitted(controller, name):
    """Watch one controller-to-worker request without running the worker."""
    seen = []
    getattr(controller, name).connect(lambda *args: seen.append(args))
    return seen


def test_an_unanswered_panel_opens_to_the_card_and_asks_for_nothing(controller):
    """
    FR-CG-6, and the expensive half of it: `unset` must not start a 6.87 GB
    download on behalf of somebody who has not been asked.
    """
    controller._settings.set("concierge.opt_in", config.OPT_IN_UNSET)
    starts = emitted(controller, "start_requested")
    downloads = emitted(controller, "download_requested")

    controller.open()

    assert starts == [] and downloads == []
    assert controller.thread.started == [], "a worker thread nobody will use"
    assert controller._panel.named("set_opt_in")[-1][1] == config.OPT_IN_UNSET


def test_a_declined_panel_opens_to_the_off_card_and_asks_for_nothing(controller):
    controller._settings.set("concierge.opt_in", config.OPT_IN_DECLINED)
    starts = emitted(controller, "start_requested")
    downloads = emitted(controller, "download_requested")

    controller.open()

    assert starts == [] and downloads == []
    assert controller._panel.view.gate() == panel_mod.GATE_OFF


def test_accepting_the_card_writes_the_key_and_arms_the_guided_setup(controller):
    controller._settings.set("concierge.opt_in", config.OPT_IN_UNSET)
    controller._settings.set("concierge.enabled", False)
    applied = emitted(controller, "settings_applied")

    controller._panel.opt_in_requested.emit(True)

    assert controller._settings.get("concierge.opt_in") == config.OPT_IN_ACCEPTED
    # Accepting clears the switch's "off" as well: the button says "set it up",
    # and a yes that left the runtime disabled would have done nothing.
    assert controller._settings.get("concierge.enabled") is True
    assert controller._setup_owed is True
    assert ("concierge.opt_in",) in applied


def test_declining_writes_the_key_and_arms_nothing(controller):
    controller._settings.set("concierge.opt_in", config.OPT_IN_UNSET)
    controller._panel.opt_in_requested.emit(False)
    assert controller._settings.get("concierge.opt_in") == config.OPT_IN_DECLINED
    assert controller._setup_owed is False


def test_declining_does_not_switch_the_enabled_key(controller):
    """
    Q26 keeps the keys separate. Declining is an answer to a question; `enabled`
    is a switch, and conflating them loses the distinction the tri-state exists
    for.
    """
    controller._settings.set("concierge.opt_in", config.OPT_IN_UNSET)
    controller._panel.opt_in_requested.emit(False)
    assert controller._settings.get("concierge.enabled") is True


def test_an_accepted_panel_with_no_weights_starts_the_download_itself(controller):
    """Handoff 8.2: accepting is what starts it, not hunting for a button."""
    controller._settings.set("concierge.opt_in", config.OPT_IN_ACCEPTED)
    downloads = emitted(controller, "download_requested")
    starts = emitted(controller, "start_requested")

    controller.open()

    assert len(downloads) == 1
    assert starts == [], "the runtime cannot start before the weights arrive"


def test_a_deleted_model_is_not_re_fetched_by_reopening_the_panel(controller):
    controller._settings.set("concierge.opt_in", config.OPT_IN_ACCEPTED)
    controller.worker.auto_download = False
    downloads = emitted(controller, "download_requested")
    controller.open()
    assert downloads == []


def test_a_refused_download_is_not_re_attempted_by_reopening_the_panel(controller):
    controller._settings.set("concierge.opt_in", config.OPT_IN_ACCEPTED)
    controller.worker.download_refusal = "not the pinned digest"
    downloads = emitted(controller, "download_requested")
    controller.open()
    assert downloads == []


def test_deleting_the_model_cancels_a_transfer_from_the_gui_thread(controller):
    """
    The copy that matters. With a download in flight the worker thread is inside
    `on_download` for as long as the rest of 6.87 GB takes, so a queued
    `on_delete_model` could not run until something stopped it -- and this is
    what stops it, exactly as `_on_panel_send` sets `cancel` from here.
    """
    assert not controller.worker.cancel_download.is_set()
    controller._panel.delete_model_requested.emit()
    assert controller.worker.cancel_download.is_set()


def test_shutdown_interrupts_a_download_as_well_as_a_turn(controller):
    controller.shutdown()
    assert controller.worker.cancel.is_set()
    assert controller.worker.cancel_download.is_set()


def test_a_finished_download_starts_the_runtime_and_runs_the_setup(controller):
    """
    FR-CG-4's trigger. A real user message, in the transcript, because that is
    what it is: the person accepted an offer that said it would set them up.
    """
    controller._settings.set("concierge.opt_in", config.OPT_IN_ACCEPTED)
    controller._setup_owed = True
    place_model(controller.worker)
    controller.worker.machine.to(state_mod.STOPPED)
    sends = emitted(controller, "send_requested")

    controller._on_download_finished(True, "", False)

    assert sends == [(worker_mod.SETUP_KICKOFF,)]
    assert controller._setup_owed is False
    assert ("append_user", worker_mod.SETUP_KICKOFF) in controller._panel.calls


def test_the_guided_setup_runs_once_and_not_on_every_later_download(controller):
    controller._settings.set("concierge.opt_in", config.OPT_IN_ACCEPTED)
    place_model(controller.worker)
    controller.worker.machine.to(state_mod.STOPPED)
    sends = emitted(controller, "send_requested")

    controller._on_download_finished(True, "", False)

    assert sends == [], "nobody accepted a card in this run"


def test_a_refused_download_reaches_the_panel_as_a_latch(controller):
    controller._on_download_finished(False, "digest abc, not the pinned def", True)
    assert controller._panel.view.download_refusal.startswith("digest abc")
    assert controller._panel.view.can_download() is False


def test_a_paused_download_says_nothing_at_all(controller):
    """The user paused it; narrating their own click back at them is noise."""
    controller._on_download_finished(False, fetch.Download.CANCELLED, False)
    assert controller._panel.named("notify") == []
    assert controller._panel.view.download_refusal == ""


def test_a_broken_transfer_is_reported_and_does_not_latch(controller):
    controller._on_download_finished(False, "the connection dropped", False)
    assert controller._panel.named("notify")
    assert controller._panel.view.download_refusal == ""


def test_the_residency_slider_writes_through_the_validated_path(controller):
    applied = emitted(controller, "settings_applied")
    controller._panel.residency_requested.emit(12)
    assert controller._settings.get("concierge.idle_unload_minutes") == 12
    assert ("concierge.idle_unload_minutes",) in applied


def test_a_residency_the_field_rejects_is_reported_and_not_written(controller):
    """
    FR-CG-11's rule is the object's, not the caller's: the slider cannot produce
    31, but the write path is the same one the Concierge uses and it must reject
    rather than accept and revert.
    """
    before = controller._settings.get("concierge.idle_unload_minutes")
    controller._panel.residency_requested.emit(31)
    assert controller._settings.get("concierge.idle_unload_minutes") == before
    assert controller._panel.named("notify")


def test_closing_a_panel_that_never_started_a_thread_emits_nothing(controller):
    """
    A queued call to a worker with no event loop is delivered when one appears,
    which for a user who declined is never -- and a stop request sitting in a
    queue is a stop request that fires at the wrong moment if anything ever does
    start that thread.
    """
    controller._settings.set("concierge.idle_unload_minutes", 0)
    stops = emitted(controller, "stop_requested")
    controller.close()
    assert stops == []


def test_switching_the_concierge_off_stops_the_runtime_and_keeps_the_weights(controller):
    """
    FR-CG-6. The reason somebody reaches for this control is that they want the
    VRAM back, so "off, but still holding 9.4 GB until the residency timer
    expires" is not off -- and the 6.87 GB they waited for is not deleted by a
    switch, which is a separate confirmed action on the same page.
    """
    place_model(controller.worker)
    controller.thread.running = True
    stops = emitted(controller, "stop_requested")

    controller._panel.enabled_requested.emit(False)

    assert controller._settings.get("concierge.enabled") is False
    assert stops and "switched off" in stops[0][0]
    assert os.path.exists(controller.worker.model_path())
    assert controller._panel.view.gate() == panel_mod.GATE_OFF


def test_switching_it_back_on_reopens_without_a_second_opt_in(controller):
    controller._settings.set("concierge.enabled", False)
    controller._panel.enabled_requested.emit(True)
    assert controller._settings.get("concierge.enabled") is True
    assert controller._settings.get("concierge.opt_in") == config.OPT_IN_ACCEPTED


def test_the_off_card_reaches_the_same_place_from_either_reason(controller):
    """
    Declined and switched-off share a card, and its one button has to fix
    whichever of the two is true -- so it writes both keys.
    """
    for opt_in, enabled in ((config.OPT_IN_DECLINED, True),
                            (config.OPT_IN_ACCEPTED, False)):
        controller._settings.set("concierge.opt_in", opt_in)
        controller._settings.set("concierge.enabled", enabled)
        controller._publish_opt_in()
        assert controller._panel.view.gate() == panel_mod.GATE_OFF
        controller._panel.opt_in_requested.emit(True)
        assert controller._panel.view.gate() != panel_mod.GATE_OFF


# -- V-CG-133: the first-run offer (FR-CG-6, handoff 8.1 as amended) ----------

def test_the_offer_is_made_only_for_an_unanswered_install():
    for opt_in in config.OPT_IN_STATES:
        expected = opt_in == config.OPT_IN_UNSET
        assert window_mod.should_offer_concierge(False, opt_in) is expected


def test_the_offer_is_made_at_most_once_per_run():
    assert window_mod.should_offer_concierge(True, config.OPT_IN_UNSET) is False
