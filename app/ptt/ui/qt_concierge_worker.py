"""
The thread adapter between the Concierge panel and the Qt-free harness.

`concierge_design.md` section 2 draws this box as `ptt.concierge.worker`, in the
column headed "harness (no Qt imports)". **It cannot live there.** A QThread
adapter is Qt by definition, and `CON-CG-6`'s import test walks every module in
`app/ptt/concierge/` and asserts PySide6 is absent from all of them -- the test
that keeps the CLI rig and the whole qualification suite runnable with Qt off
the machine. So the adapter is on the app's side of the seam, where the arrow in
that same diagram already points, and design section 2 is amended to say so.

Two objects, and the split matters:

- **`ConciergeWorker`** owns the harness -- the server, the client, the registry,
  the context, the agent, the journal -- and lives on a worker thread. Every one
  of its methods runs there.
- **`ConciergeController`** lives on the GUI thread, owns the `QThread`, and is
  the only place in the application where a cross-thread connection is made. It
  is also the only thing that touches the panel.

Threading, and the rule being extended
--------------------------------------

The rule is **v2.0 acceptance criterion 9** (`ptt-v2-gui/gui_handoff.md` section
10) -- not v3 criterion 9, which is "all ten v2.0 criteria re-pass"; the v3
thread audit is **v3-10**. The two numbering sets collide and every reference
has to say which.

v2's rule names *the engine thread*. The rule that generalises is: **no UI
object is touched from any thread other than the GUI thread.** Three consequences
are new in v3 and each is load-bearing:

1. **The hazard now runs the other way: worker-thread writes.** `set_config`
   must not call `InstantApplyPanel.apply_now`, which is a QWidget method on a
   QWidget. It calls `Settings.set()` (D-CG-13) and then the registry's
   `on_applied` seam; this adapter turns that into a queued signal the GUI
   thread receives and re-emits as the broadcast that already exists
   (`qt_app._on_settings_changed` -> `refresh_panels()` -> `tray.refresh_menu()`).
   **That hop is where FR-CG-2 is won or lost.**
2. **`UiState` is not a UI object.** `get_state()` is served from the worker
   thread by reading the dataclass `QtApp` holds. Reading plain attributes that
   the GUI thread rebinds wholesale is the same safe hand-off `config.Settings`'
   docstring describes, and it is not what the rule forbids -- a `QLabel` is.
3. **Cancellation is not a slot.** A queued slot cannot reach a thread that is
   blocked inside `agent.send()`, which is exactly when a cancel is wanted. So
   the controller sets a `threading.Event` directly, which is what
   `llm.Client(cancelled=…)` polls once per chunk, and what
   `Engine.request_model_reload` has always done for the same reason.

`QThread` is used the way it is meant to be used: **a plain `QThread` with the
worker moved onto it**, never a `QThread` subclass carrying slots. A slot defined
on a `QThread` subclass runs on the thread that *created* the QThread, not on the
thread it starts -- the classic inversion, and one that would put every line of
the agent loop back on the GUI thread while looking correct.

`THREAD-CHECK`, and one refinement to Q26
-----------------------------------------

Q26 says the check logs **once per signal type per session**, at first emission,
matching `qt_tray.py`. `qt_threadcheck.SignalAudit` is that, with one refinement
argued in its own docstring: the key is the signal *and the emitting thread*,
because otherwise v3-10's "harness idle-timer -> GUI" hop can never be shown.
Both ends of every hop are audited -- the emitting side expecting a non-GUI
thread and the receiving slot expecting the GUI one -- because the *pair* of
lines is the evidence, and one line on its own proves nothing about the other
end.
"""

import os
import threading

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot

from ptt import paths, transcribe
from ptt.concierge import (
    agent as agent_mod, fetch, llm, sessions as sessions_mod,
    server as server_mod, state as state_mod, tools as tools_mod,
)
from ptt.logging_setup import log_debug
from ptt.ui.qt_threadcheck import SignalAudit

#: The settings whose new value the engine only reads when it builds a model,
#: so a Concierge write to one of them has to be followed by a reload.
#:
#: **Derived, not a second opinion.** `test_concierge_worker.py` reads every
#: `apply_now(..., reload_model=True)` call site in `ptt/ui/panels/` and asserts
#: this tuple matches, because the panels are where the rule was first written
#: down and two copies of it would drift the moment a third setting joined them.
RELOAD_KEYS = ("model", "use_gpu")

#: How long a `run_benchmark` tool call waits for the engine's measurement.
#:
#: The engine measures the **resident** model on its poll thread, so the wait
#: covers a queued reload plus one transcription of the 30-second clip. Bounded
#: rather than indefinite because this blocks the worker thread, and a tool that
#: never returns is a turn that never ends.
BENCHMARK_TIMEOUT_SEC = 180.0

#: How long the exit path waits for the runtime to stop before leaving it to the
#: job object. Short: the kernel is holding the same guarantee, and a settings
#: application that takes ten seconds to quit is a settings application the user
#: kills in Task Manager -- which is the very case FR-CG-9's job object covers.
SHUTDOWN_GRACE_SEC = 5.0


# -- the state seam (Q26) -----------------------------------------------------

def state_snapshot(ui):
    """
    Fill `tools.STATE_KEYS` from a `UiState`. The Qt half of Q26's seam.

    Derived from the harness's declaration rather than written out, so a key
    added there arrives here as an empty string rather than being silently
    absent -- `get_state` replaces a missing key with `"unknown"`, and a key the
    adapter forgot is a key the model invents a value for.

    A `UiState` method (`detail`) is called; a field is read. Both happen on the
    worker thread, which is allowed: see this module's docstring, point 2.
    """
    snapshot = {}
    for key in tools_mod.STATE_KEYS:
        value = getattr(ui, key, None)
        if callable(value):
            try:
                value = value()
            except Exception:                        # pragma: no cover - defensive
                value = None
        snapshot[key] = "" if value is None else value
    return snapshot


# -- the benchmark handshake --------------------------------------------------

class BenchmarkBridge:
    """
    `run_benchmark`, across the engine's own asynchronous measurement.

    The engine measures **the model already resident** and says why in
    `Engine.request_benchmark`: two `WhisperModel`s on one card is a plausible
    CUDA OOM, and an allocation failure while *measuring* must not take down the
    model dictation depends on. So a request for a tier that is not the current
    one is refused with a reason the model can act on -- change the setting
    first, then measure -- rather than quietly measuring something else.

    The wait is a `threading.Event` on the worker thread, released by
    `QtApp`'s existing `benchmark_done` hop on the GUI thread. Nothing here
    touches the engine except `request_benchmark`, which is thread-safe by its
    own docstring.
    """

    def __init__(self, settings, engine_provider, timeout=BENCHMARK_TIMEOUT_SEC):
        self._settings = settings
        self._engine = engine_provider
        self._timeout = timeout
        self._done = threading.Event()
        self._result = None

    def deliver(self, device, seconds):
        """Called on the GUI thread when the engine reports a measurement."""
        self._result = {"seconds": float(seconds), "device": device}
        self._done.set()

    def run(self, model):
        """The registry's `benchmark` seam. Runs on the worker thread."""
        engine = self._engine()
        if engine is None:
            return {"error": True,
                    "reason": "the dictation engine is not running yet"}
        current = self._settings.get("model")
        if model != current:
            return {"error": True,
                    "reason": (f"only the loaded model can be measured, and "
                               f"{current!r} is loaded, not {model!r}"),
                    "hint": (f"set_config('model', {model!r}) first; the engine "
                             f"loads it and then it can be measured")}
        self._result = None
        self._done.clear()
        engine.request_benchmark()
        if not self._done.wait(self._timeout):
            return {"error": True,
                    "reason": (f"the measurement did not finish within "
                               f"{int(self._timeout)} seconds")}
        return self._result


# -- the worker ---------------------------------------------------------------

class ConciergeWorker(QObject):
    """
    The harness, on its own thread.

    Constructed on the GUI thread and then moved, so **nothing expensive happens
    in `__init__`**: the pack and the prompt are read, the server is launched and
    the knowledge-pack prefix is warmed inside `on_start`, which runs on the
    worker thread. A constructor that read two files and probed a socket would
    be a settings window that took several seconds to open.
    """

    #: worker -> GUI. Every one is connected with `Qt.QueuedConnection`.
    state_changed = Signal(str, str)
    token = Signal(str)
    progress = Signal(str)
    tool_activity = Signal(str, object, object)
    change_recorded = Signal(int, str, str, object, object)
    settings_applied = Signal(str, object, object)
    notice = Signal(str)
    turn_finished = Signal(str, str)
    undo_finished = Signal(int, bool, str)
    memory_changed = Signal(str, bool)
    runtime_output = Signal(str)

    def __init__(self, settings, *, state_provider, devices=None,
                 benchmark=None, cuda_supported=True, exe_path=None,
                 model_dir=None, pack_path=None, prompt_path=None,
                 memory=None, server_factory=None, client_factory=None,
                 audit=None, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._state_provider = state_provider
        self._devices = devices
        self._benchmark = benchmark
        self._cuda = bool(cuda_supported)
        self._exe = exe_path or paths.llama_server_path()
        self._model_dir = model_dir or paths.concierge_model_dir()
        self._pack_path = pack_path or paths.knowledge_pack_path()
        self._prompt_path = prompt_path or paths.concierge_prompt_path()
        self._make_server = server_factory or server_mod.Server
        self._make_client = client_factory or llm.Client
        self._audit = audit or SignalAudit()

        self.memory = memory or tools_mod.MemoryNote(
            paths.memory_note_path(), paths.previous_memory_note_path())
        self.machine = state_mod.Machine(self._initial_state(),
                                         on_change=self._on_machine_change)
        self.journal = agent_mod.Journal(settings=settings, memory=self.memory,
                                         on_change=self._on_journal_change)
        #: Polled once per SSE chunk by `llm.Client`. Set from the GUI thread.
        self.cancel = threading.Event()

        self.registry = None
        self.context = None
        self.agent = None
        self.server = None

    # -- state --------------------------------------------------------------

    def model_path(self):
        spec = fetch.spec_for(self._settings.get("concierge.model"))
        if spec is None:
            return ""
        return os.path.join(self._model_dir, spec.filename)

    def _initial_state(self):
        """
        Where the machine starts, from the hardware and the filesystem.

        `disabled` has no outgoing edges by design, so this is the only place it
        can be chosen: FR-CG-12 is decided once, from the hardware, and a
        machine does not grow a CUDA device while the application is open.
        """
        if not self._cuda:
            return state_mod.DISABLED
        path = self.model_path()
        if path and os.path.exists(path):
            return state_mod.STOPPED
        return state_mod.NOT_DOWNLOADED

    def _emit(self, signal, what, *args):
        """Emit one worker-side signal, auditing the first from this thread."""
        self._audit.check(what, expect_gui=False)
        signal.emit(*args)

    def _on_machine_change(self, state, detail):
        self._emit(self.state_changed, "state_changed", state, detail)

    def _on_journal_change(self, change):
        self._emit(self.change_recorded, "change_recorded", change.seq,
                   change.kind, change.key, change.old, change.new)

    def _on_applied(self, key, old, new):
        """
        FR-CG-2's hop. The **only** thing a tool write does about the UI.

        No widget is touched, no panel method is called and nothing here knows
        what a tab is. The controller receives this on the GUI thread and turns
        it into the broadcast that already existed before the Concierge did.
        """
        self._emit(self.settings_applied, "settings_applied", key, old, new)

    # -- assembly -----------------------------------------------------------

    def _ensure_context(self):
        """Build the registry and the context once. `(ok, reason)`."""
        if self.registry is not None:
            return True, None
        try:
            pack = agent_mod.load_pack(self._pack_path)
            prompt = agent_mod.load_system_prompt(self._prompt_path)
        except Exception as e:
            return False, f"could not read the Concierge's knowledge: {str(e)}"

        self.registry = tools_mod.Registry(
            self._settings,
            state_provider=self._state_provider,
            devices=self._devices,
            benchmark=self._benchmark,
            memory=self.memory,
            journal=self.journal,
            on_applied=self._on_applied,
            progress=lambda text: self._emit(self.progress, "progress", text),
            log_path=paths.debug_log_path(),
            previous_log_path=paths.previous_debug_log_path(),
            llm_resident=self.resident,
            installed_sizes=transcribe.installed_sizes,
        )
        self.context = agent_mod.Context(pack, prompt, self.registry,
                                         memory=self.memory)
        return True, None

    def resident(self):
        """Whether a Concierge model is in VRAM on our account."""
        return self.server is not None and self.server.process is not None

    def _client(self, base_url, api_key, **kwargs):
        """
        One `llm.Client`, carrying the transport it cannot work without.

        **`llm.Client(transport=None)` is not a client with a default
        transport** -- it is a client whose first `stream()` raises
        `'NoneType' object has no attribute 'post_stream'`. The seam is
        deliberately left unfilled so that no L1 test can open a socket by
        forgetting to inject a fake, which makes filling it the caller's job;
        `rig.py` fills it in both of its own call sites for the same reason,
        and this adapter is the only other caller in the shipped application.

        Every client this class builds goes through here, so there is one place
        to forget rather than two.
        """
        kwargs.setdefault("transport", llm.HttpTransport())
        return self._make_client(base_url, api_key, **kwargs)

    def _prewarm(self, port, api_key):
        """
        Pay the knowledge pack's cost inside `loading` (design 5).

        The rig's `_time_prewarm` argues the shape: the prefix sent here must be
        the **real** one, because llama-server's KV cache is a prefix cache and
        warming different bytes warms something nothing will ever hit again.
        """
        client = self._client(f"http://127.0.0.1:{port}", api_key)
        messages = [{"role": "system", "content": self.context.prefix()},
                    {"role": "user", "content": "ready?"}]
        client.stream(messages, None, self._settings.get("concierge.tool_mode"),
                      max_tokens=1)

    # -- slots: everything below runs on the worker thread ------------------

    @Slot()
    def on_start(self):
        self._audit.check("on_start", expect_gui=False)
        if self.machine.state == state_mod.DISABLED:
            return
        if self.server is not None and self.server.process is not None:
            return
        path = self.model_path()
        if not path or not os.path.exists(path):
            self.machine.to(state_mod.NOT_DOWNLOADED,
                            "the model has not been downloaded yet")
            return

        ok, reason = self._ensure_context()
        if not ok:
            self.machine.to(state_mod.STOPPED, reason)
            self._emit(self.notice, "notice", reason)
            return

        self.server = self._make_server(
            self._exe, path, self.machine,
            prewarm=self._prewarm,
            on_stderr=lambda line: self._emit(self.runtime_output,
                                              "runtime_output", line))
        ok, reason = self.server.start()
        if not ok:
            self.server = None
            self._emit(self.notice, "notice",
                       f"The Concierge could not start: {reason}")
            return

        self.agent = agent_mod.Agent(
            self._client(self.server.base_url(), self.server.api_key,
                         cancelled=self.cancel.is_set),
            self.registry, self.context, self.journal,
            tool_mode=self._settings.get("concierge.tool_mode"),
            on_token=lambda text: self._emit(self.token, "token", text),
            on_tool=self._on_tool,
            on_notice=lambda text: self._emit(self.notice, "notice", text))
        self.server.start_idle_timer(
            lambda: self._settings.get("concierge.idle_unload_minutes"))
        self.on_memory_open()

    @Slot(str)
    def on_stop(self, reason="closed"):
        """
        Unload the runtime. **Deliberately not audited**, unlike every other
        slot here: `ConciergeController.shutdown` calls it directly from the GUI
        thread, because at exit there may be no more turns of the event loop for
        a queued call to arrive on. A thread expectation on a method that is
        correct from either side produces a `WRONG THREAD` line that is a false
        alarm, and v3-10's audit is worth nothing if it cries wolf.
        """
        if self.server is None:
            return
        self.server.stop(reason)
        self.server = None
        self.agent = None

    @Slot(str)
    def on_send(self, text):
        """
        One user message, start to finish. Blocks this thread, never the GUI's.

        `ContextOverflow` is caught here because design 5.0 rule 4 says that
        failure is *visible*: a traceback out of a worker thread is a line in
        the log and nothing on screen.
        """
        self._audit.check("on_send", expect_gui=False)
        if self.agent is None:
            self._emit(self.notice, "notice",
                       "The Concierge is not running yet.")
            self._emit(self.turn_finished, "turn_finished", "", "not-running")
            return

        self.cancel.clear()
        if not self.machine.to(state_mod.GENERATING, "answering"):
            self._emit(self.notice, "notice",
                       f"The Concierge cannot answer while it is "
                       f"{self.machine.state}.")
            self._emit(self.turn_finished, "turn_finished", "", "not-ready")
            return
        if self.server is not None:
            self.server.touch()

        forced, reply = "", ""
        try:
            turn = self.agent.send(text)
            reply, forced = turn.reply, turn.forced
        except agent_mod.ContextOverflow as overflow:
            self._emit(self.notice, "notice", overflow.message)
            forced = "context-overflow"
        except Exception as e:
            log_debug(f"Concierge: the turn failed: {type(e).__name__}: {str(e)}")
            self._emit(self.notice, "notice",
                       f"That turn failed: {type(e).__name__}: {str(e)}")
            forced = "failed"
        finally:
            if self.server is not None:
                self.server.touch()
            self.machine.to(state_mod.READY, "")
            self._emit(self.turn_finished, "turn_finished", reply, forced)

    def _on_tool(self, name, arguments, result):
        self._emit(self.tool_activity, "tool_activity", name, arguments, result)

    # -- undo ---------------------------------------------------------------

    @Slot(int)
    def on_undo(self, seq):
        """
        One chip's Undo.

        The change is looked up **before** the undo so that the broadcast can
        name the key. An undo is a write like any other, and the two keys the
        engine only reads at model build need the reload just as much on the way
        back as they did on the way out -- an empty key here means undoing
        "switch me to the medium model" leaves the medium model loaded.
        """
        self._audit.check("on_undo", expect_gui=False)
        seq = int(seq)
        change = next((c for c in self.journal.changes() if c.seq == seq), None)
        ok, reason = self.journal.undo(seq)
        self._emit(self.undo_finished, "undo_finished", seq, bool(ok),
                   reason or "")
        if ok:
            self._announce(change)

    @Slot()
    def on_restore(self):
        """
        `session`, replaying inverses in reverse order (Q24).

        Only keys this journal recorded are touched. Each chip is told its own
        outcome, so a partially refused restore shows exactly which change is
        still standing rather than one summary line that averages them.
        """
        self._audit.check("on_restore", expect_gui=False)
        restored, failures = self.journal.restore()
        for change in restored:
            self._emit(self.undo_finished, "undo_finished", change.seq, True, "")
        for change, reason in failures:
            self._emit(self.undo_finished, "undo_finished", change.seq, False,
                       reason or "")
        self._emit(self.notice, "notice",
                   f"Restored {len(restored)} change(s)"
                   + (f"; {len(failures)} could not be put back."
                      if failures else "."))
        for change in restored:
            self._announce(change)

    def _announce(self, change):
        """Tell the GUI that a reversal changed something, and name what."""
        if change is None or change.kind == "config":
            self._emit(self.settings_applied, "settings_applied",
                       change.key if change is not None else "",
                       change.new if change is not None else None,
                       change.old if change is not None else None)
        if change is None or change.kind == "memory":
            self.on_memory_open()

    # -- the memory note ----------------------------------------------------
    #
    # Read and written here rather than on the GUI thread, and the asymmetry
    # with saved transcripts (which the controller writes) is deliberate: this
    # file has a second writer -- the `update_memory` tool, on this thread -- and
    # keeping both on one thread is what stops a user's Save interleaving with a
    # tool's rotation of the same file. A saved transcript has one writer.

    @Slot()
    def on_memory_open(self):
        self._emit(self.memory_changed, "memory_changed", self.memory.read(),
                   bool(self.memory.read_previous()))

    @Slot(str)
    def on_memory_save(self, text):
        self._audit.check("on_memory_save", expect_gui=False)
        ok, reason = self.memory.write(text)
        if not ok:
            self._emit(self.notice, "notice",
                       f"The memory note was not saved: {reason}")
        self.on_memory_open()

    @Slot()
    def on_memory_restore(self):
        """
        Put the kept previous version back (FR-CG-14, Q22).

        A swap rather than a one-way door: `MemoryNote.write` rotates the
        current note into `.prev` as it writes, so restoring twice returns to
        where it started. That is worth having, because "restore previous" is
        the control someone reaches for while guessing.
        """
        self._audit.check("on_memory_restore", expect_gui=False)
        previous = self.memory.read_previous()
        if not previous:
            self._emit(self.notice, "notice",
                       "There is no previous memory note to restore.")
            return
        ok, reason = self.memory.write(previous)
        self._emit(self.notice, "notice",
                   "The previous memory note was restored." if ok
                   else f"The note could not be restored: {reason}")
        self.on_memory_open()

    # -- the model file -----------------------------------------------------

    @Slot()
    def on_delete_model(self):
        """
        Delete the downloaded weights and return to `not_downloaded` (Q25).

        The runtime is stopped first, because the file is open while it runs and
        Windows will not delete an open file -- and a failure here has to say so
        rather than leaving the state machine claiming a model that is gone.
        """
        self._audit.check("on_delete_model", expect_gui=False)
        self.on_stop("the model is being deleted")
        path = self.model_path()
        removed = []
        for candidate in (path, path + ".part"):
            if candidate and os.path.exists(candidate):
                try:
                    os.remove(candidate)
                    removed.append(os.path.basename(candidate))
                except OSError as e:
                    self._emit(self.notice, "notice",
                               f"Could not delete {os.path.basename(candidate)}: "
                               f"{str(e)}")
                    return
        log_debug(f"Concierge: deleted {removed or 'nothing'} from "
                  f"{self._model_dir}.")
        self.machine.to(state_mod.NOT_DOWNLOADED, "the model was deleted")
        self._emit(self.notice, "notice",
                   "The Concierge model was deleted from this machine."
                   if removed else "There was no downloaded model to delete.")


# -- the controller -----------------------------------------------------------

class ConciergeController(QObject):
    """
    The GUI-thread half: owns the thread, wires the panel, and nothing else.

    Every connection made here that crosses the boundary is explicitly
    `Qt.QueuedConnection`. `AutoConnection` would resolve correctly today and
    would degrade silently to a direct call if a later change moved either end's
    thread affinity -- the argument `qt_app.py` already makes for the engine
    bridge, and the reason v3-10 asks to see the identities rather than to be
    told about them.

    Panel-to-controller connections are ordinary: both objects are created by
    the GUI thread and neither can be anywhere else, so a queued connection
    between them would only defer a call by one turn of the event loop.
    """

    #: controller -> worker. Declared as signals rather than called directly so
    #: that the hop is a queued connection and not a blocking call into a thread
    #: that may be several seconds inside a turn.
    start_requested = Signal()
    stop_requested = Signal(str)
    send_requested = Signal(str)
    undo_requested = Signal(int)
    restore_requested = Signal()
    memory_open_requested = Signal()
    memory_save_requested = Signal(str)
    memory_restore_requested = Signal()
    delete_model_requested = Signal()

    #: controller -> app. The FR-CG-2 broadcast, already on the GUI thread.
    settings_applied = Signal(str)
    #: A key the engine only reads at model build was written; reload it.
    reload_requested = Signal()
    #: The status bar's Concierge segment changed.
    status_changed = Signal(str)

    def __init__(self, settings, panel, *, ui_state, engine_provider,
                 cuda_supported=True, store=None, worker=None, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._panel = panel
        self._audit = SignalAudit()
        self._engine = engine_provider
        self.benchmark = BenchmarkBridge(settings, engine_provider)
        #: A `RELOAD_KEYS` write that landed mid-turn, waiting for the turn to
        #: end. See `_on_settings_applied`.
        self._reload_pending = False
        self.store = store or sessions_mod.SessionStore(
            paths.concierge_sessions_path(),
            limit_provider=lambda: settings.get("concierge.history_limit"))
        self._session_id = ""

        self.worker = worker or ConciergeWorker(
            settings,
            state_provider=lambda: state_snapshot(ui_state),
            devices=self._devices,
            benchmark=self.benchmark.run,
            cuda_supported=cuda_supported)
        #: Created here, **started on first open** (FR-CG-6). A user who
        #: declined the Concierge, or whose machine has no CUDA device, gets
        #: every v2.0 behaviour unaffected -- including not carrying a worker
        #: thread they will never use. Moving the worker before the thread runs
        #: is correct: affinity is set on the call, and anything queued before
        #: the loop starts is delivered when it does.
        self.thread = QThread()
        self.thread.setObjectName("concierge-worker")
        self.worker.moveToThread(self.thread)

        self._wire_to_worker()
        self._wire_from_worker()
        self._wire_panel()
        self._reap_orphan()

        spec = fetch.spec_for(settings.get("concierge.model"))
        self._panel.set_model_label(spec.label if spec else "",
                                    spec.gigabytes if spec else None)
        self._panel.set_idle_minutes(settings.get("concierge.idle_unload_minutes"))
        self._panel.set_state(self.worker.machine.state,
                              self.worker.machine.detail)
        self._panel.set_sessions(self.store.list())

    # -- the startup reap (FR-CG-9's backstop) -------------------------------

    def _reap_orphan(self):
        """
        Kill a llama-server left behind by a previous run, at **app** startup.

        Here, and not in `open()`, because the whole point of the backstop is
        that it does not wait for the user: an orphan from a crash holds about
        9.4 GB of VRAM until something reaps it, and "until you next open the
        chat panel" is not a bound. Design 8.1's primary mechanism is the job
        object; this is the case that predates it or where its assignment
        failed.

        On its own short-lived thread, because `server.reap_orphan` probes
        `/props` over HTTP with a two-second timeout when the state file is
        present -- which is a two-second stall on the startup path of an
        application whose whole design is about not adding latency. It touches
        no Qt and returns nothing anyone waits for.
        """
        def run():
            try:
                killed, note = server_mod.reap_orphan()
                if killed or note:
                    log_debug(f"Concierge startup reap: {note or 'killed an orphan'}")
            except Exception as e:
                log_debug(f"Concierge: the startup reap failed: {str(e)}")

        threading.Thread(target=run, name="concierge-reap", daemon=True).start()

    # -- seams --------------------------------------------------------------

    @staticmethod
    def _devices():
        """
        PortAudio's enumeration, imported late.

        `audio` initialises and terminates PortAudio around the query and is
        serialised internally from v3.0, because this call now happens on the
        worker thread while the Audio tab may be making the same one.
        """
        from ptt import audio
        return audio.input_devices()

    # -- wiring -------------------------------------------------------------

    def _wire_to_worker(self):
        queued = Qt.ConnectionType.QueuedConnection
        self.start_requested.connect(self.worker.on_start, queued)
        self.stop_requested.connect(self.worker.on_stop, queued)
        self.send_requested.connect(self.worker.on_send, queued)
        self.undo_requested.connect(self.worker.on_undo, queued)
        self.restore_requested.connect(self.worker.on_restore, queued)
        self.memory_open_requested.connect(self.worker.on_memory_open, queued)
        self.memory_save_requested.connect(self.worker.on_memory_save, queued)
        self.memory_restore_requested.connect(self.worker.on_memory_restore, queued)
        self.delete_model_requested.connect(self.worker.on_delete_model, queued)

    def _wire_from_worker(self):
        queued = Qt.ConnectionType.QueuedConnection
        self.worker.state_changed.connect(self._on_state, queued)
        self.worker.token.connect(self._on_token, queued)
        self.worker.progress.connect(self._on_progress, queued)
        self.worker.tool_activity.connect(self._on_tool, queued)
        self.worker.change_recorded.connect(self._on_change, queued)
        self.worker.settings_applied.connect(self._on_settings_applied, queued)
        self.worker.notice.connect(self._on_notice, queued)
        self.worker.turn_finished.connect(self._on_turn_finished, queued)
        self.worker.undo_finished.connect(self._on_undo_finished, queued)
        self.worker.memory_changed.connect(self._on_memory_changed, queued)
        self.worker.runtime_output.connect(self._on_runtime_output, queued)

    def _wire_panel(self):
        panel = self._panel
        panel.send_requested.connect(self._on_panel_send)
        panel.undo_requested.connect(self._on_panel_undo)
        panel.restore_requested.connect(self._on_panel_restore)
        panel.new_session_requested.connect(self.new_session)
        panel.save_session_requested.connect(self.save_session)
        panel.open_session_requested.connect(self.open_session)
        panel.memory_open_requested.connect(self.memory_open_requested.emit)
        panel.memory_save_requested.connect(self.memory_save_requested.emit)
        panel.memory_restore_requested.connect(self.memory_restore_requested.emit)
        panel.delete_model_requested.connect(self._on_panel_delete_model)

    # -- panel -> worker ----------------------------------------------------

    def _on_panel_send(self, text):
        """
        A message. Starts a stopped runtime, or cancels a running generation.

        The event, not a slot: the worker thread is inside `agent.send()` and a
        queued call could not be delivered until that returned, which is the
        opposite of what a cancel is for.
        """
        self._audit.check("send (GUI emit)", expect_gui=True)
        self._panel.append_user(text)
        state = self.worker.machine.state
        if state == state_mod.GENERATING:
            self.worker.cancel.set()
            self._audit.check("cancel (GUI emit)", expect_gui=True)
        elif state == state_mod.STOPPED:
            # Queued ahead of the send and on the same thread, so `on_start`
            # runs to completion -- launch, health, prewarm -- before `on_send`
            # is dispatched. The panel shows `loading` throughout.
            self.open()
        self.send_requested.emit(text)

    def _on_panel_undo(self, seq):
        self._audit.check("undo (GUI emit)", expect_gui=True)
        self.undo_requested.emit(int(seq))

    def _on_panel_restore(self):
        self._audit.check("restore (GUI emit)", expect_gui=True)
        self.restore_requested.emit()

    def _on_panel_delete_model(self):
        self._audit.check("delete_model (GUI emit)", expect_gui=True)
        self.delete_model_requested.emit()

    # -- worker -> panel ----------------------------------------------------

    def _on_state(self, state, detail):
        self._audit.check("state_changed (GUI slot)", expect_gui=True)
        self._panel.set_state(state, detail)
        self.status_changed.emit(self._panel.status_segment())

    def _on_token(self, text):
        self._audit.check("token (GUI slot)", expect_gui=True)
        self._panel.append_token(text)

    def _on_progress(self, text):
        self._audit.check("progress (GUI slot)", expect_gui=True)
        self._panel.append_progress(text)

    def _on_tool(self, name, arguments, result):
        self._audit.check("tool_activity (GUI slot)", expect_gui=True)
        self._panel.append_tool(name, arguments, result)

    def _on_change(self, seq, kind, key, old, new):
        self._audit.check("change_recorded (GUI slot)", expect_gui=True)
        self._panel.append_change(seq, kind, key, old, new)

    def _on_settings_applied(self, key, _old, _new):
        """
        **FR-CG-2, on the GUI thread at last.**

        Two things happen and they are not the same thing: the broadcast that
        repaints the banner, the tabs and the tray, and -- for the two settings
        the engine only reads when it builds a model -- a reload request. The
        panels do both through `apply_now(reload_model=True)`; a worker thread
        cannot, because that method is on a QWidget.
        """
        self._audit.check("settings_applied (GUI slot)", expect_gui=True)
        self.settings_applied.emit(key)
        if key in RELOAD_KEYS:
            self._request_reload()
        self._panel.set_idle_minutes(
            self._settings.get("concierge.idle_unload_minutes"))
        self.status_changed.emit(self._panel.status_segment())

    def _request_reload(self):
        """
        Reload the dictation model -- but **not while a turn is in flight**.

        Measured the hard way. `set_config("model", …)` lands mid-turn: the tool
        has run and the model is still composing the sentence that says so. A
        reload at that instant loads a second Whisper onto a card already
        holding llama-server's 9.4 GB and the resident 2.3 GB, and the LLM's
        decode stops long enough to trip the 30 s stall timeout -- the turn ends
        with "The Concierge stopped responding" **after** the write it was
        reporting had already succeeded.

        The broadcast is not deferred, only the reload: the banner, the tabs and
        the status bar still update on the same event, which is what FR-CG-2
        asks for. What waits is the seconds-long CUDA allocation, and it waits
        for as long as one sentence takes.
        """
        if self.worker.machine.state == state_mod.GENERATING:
            self._reload_pending = True
            log_debug("Concierge: a model reload is held until this turn ends.")
            return
        self.reload_requested.emit()

    def _on_notice(self, text):
        self._audit.check("notice (GUI slot)", expect_gui=True)
        self._panel.append_notice(text)

    def _on_turn_finished(self, reply, forced):
        self._audit.check("turn_finished (GUI slot)", expect_gui=True)
        self._panel.close_turn(reply, forced)
        if self._reload_pending:
            self._reload_pending = False
            self.reload_requested.emit()

    def _on_undo_finished(self, seq, ok, reason):
        self._audit.check("undo_finished (GUI slot)", expect_gui=True)
        self._panel.undo_finished(seq, ok, reason)

    def _on_memory_changed(self, text, has_previous):
        self._audit.check("memory_changed (GUI slot)", expect_gui=True)
        self._panel.set_memory(text, has_previous)

    def _on_runtime_output(self, line):
        """
        One line of llama-server's stderr, from its reader thread.

        Straight to the GUI rather than through the worker thread, which is
        usually blocked inside a turn by the time anything interesting is
        printed. It is shown as the `loading` state's detail and nowhere else --
        `server.py` has already written every line to `debug_log.txt`.
        """
        self._audit.check("runtime_output (GUI slot)", expect_gui=True)
        if self.worker.machine.state == state_mod.LOADING:
            self._panel.set_state(state_mod.LOADING, line[:120])

    # -- saved transcripts --------------------------------------------------
    #
    # On the GUI thread, unlike the memory note. One writer, one small atomic
    # file, and a Save that queued behind a 180-second turn would look broken
    # for the entire length of the answer the user is trying to keep.

    def new_session(self):
        self._panel.new_session()
        self._session_id = ""
        if self.worker.agent is not None:
            self.worker.agent.reset()

    def save_session(self, name):
        saved, reason = self.store.save(name, self._panel.view.save_payload(),
                                        session_id=self._session_id)
        if saved is None:
            self._panel.notify(f"Not saved: {reason}")
            return
        self._session_id = saved.id
        self._panel.set_session_name(saved.name)
        self._panel.set_sessions(self.store.list())
        self._panel.notify(f"Saved as {saved.name!r}.")

    def open_session(self, session_id):
        saved = self.store.load(session_id)
        if saved is None:
            self._panel.notify("That saved session could not be read.")
            return
        self._panel.show_saved_session(saved)

    # -- lifecycle ----------------------------------------------------------

    def open(self):
        """The panel was shown. Start the thread, then the runtime."""
        if not self.thread.isRunning():
            self.thread.start()
        self.start_requested.emit()

    def close(self):
        """
        The panel was hidden.

        Residency 0 means "unload when the chat panel closes", which
        `Server.start_idle_timer` explicitly leaves to the panel: the timer
        treats 0 as "never, on my account" precisely so this decision lives
        where the panel is.
        """
        try:
            minutes = int(self._settings.get("concierge.idle_unload_minutes"))
        except (TypeError, ValueError):
            minutes = 0
        if minutes <= 0:
            self.stop_requested.emit("the chat panel was closed")

    def shutdown(self):
        """
        Stop the runtime and join the thread. Called from the application exit.

        `stop()` is called directly rather than queued: at exit there may be no
        more turns of the event loop, so a queued call would never be delivered
        -- and the job object would then be the only thing that killed
        llama-server, which is a backstop rather than the plan.

        It is called on a short-lived thread with a bounded wait, because
        `Server.stop()` takes the same lock `Server.start()` holds for the whole
        of a launch: quitting during a model load would otherwise block the GUI
        thread for up to the 60 s ready timeout. If the grace period runs out
        the job object does it, which is exactly the case FR-CG-9 built it for.
        """
        self.worker.cancel.set()
        if not self.thread.isRunning():
            return
        stopper = threading.Thread(
            target=self._stop_for_exit, name="concierge-shutdown", daemon=True)
        stopper.start()
        stopper.join(SHUTDOWN_GRACE_SEC)
        if stopper.is_alive():
            log_debug(
                f"Concierge: the runtime did not stop within "
                f"{SHUTDOWN_GRACE_SEC:.0f} s; leaving it to the job object, "
                f"which kills llama-server when this process dies (FR-CG-9).")
        self.thread.quit()
        self.thread.wait(int(SHUTDOWN_GRACE_SEC * 1000))

    def _stop_for_exit(self):
        try:
            self.worker.on_stop("the application is exiting")
        except Exception as e:                       # pragma: no cover - exit path
            log_debug(f"Concierge: shutdown failed: {str(e)}")
