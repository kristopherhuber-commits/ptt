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
import os
import threading

import pytest

from ptt import config, paths
from ptt.concierge import llm
from ptt.concierge import state as state_mod
from ptt.concierge import tools as tools_mod
from ptt.ui import qt_concierge_worker as worker_mod
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
    def __init__(self):
        self.asked = 0

    def request_benchmark(self):
        self.asked += 1


def test_a_tier_that_is_not_loaded_is_refused_with_the_step_that_fixes_it(tmp_path):
    settings = config.Settings(path=str(tmp_path / "config.json"))
    engine = FakeEngine()
    bridge = BenchmarkBridge(settings, lambda: engine)
    result = bridge.run("tiny.en")
    assert result["error"] is True
    assert "only the loaded model" in result["reason"]
    assert "set_config" in result["hint"]
    assert engine.asked == 0


def test_the_measurement_the_engine_reports_is_what_comes_back(tmp_path):
    settings = config.Settings(path=str(tmp_path / "config.json"))
    engine = FakeEngine()
    bridge = BenchmarkBridge(settings, lambda: engine)
    threading.Timer(0.05, lambda: bridge.deliver("cuda", 2.34)).start()
    result = bridge.run(settings.get("model"))
    assert result == {"seconds": 2.34, "device": "cuda"}
    assert engine.asked == 1


def test_a_measurement_that_never_arrives_ends_the_tool_call(tmp_path):
    """
    Bounded because this blocks the worker thread: a tool that never returns is
    a turn that never ends, and the turn timeout only bounds *generations*.
    """
    settings = config.Settings(path=str(tmp_path / "config.json"))
    bridge = BenchmarkBridge(settings, lambda: FakeEngine(), timeout=0.05)
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
