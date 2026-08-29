"""
The QApplication owner, and the boundary between the engine thread and the UI.

This module may import the engine; the engine must never import this one
(design.md section 4).

The thread boundary
-------------------

`Engine.run()` blocks its calling thread and invokes `on_state` from that same
thread. Its docstring is explicit that it makes **no promise this is the UI
thread** -- marshalling is the frontend's problem. pystray did not care: its
setters posted to the icon's own message loop, so the hop happened implicitly.
Qt has no such courtesy. A QObject belongs to the thread that created it, and its
widgets, pixmaps and paint state may only be touched from there.

So `EngineBridge.on_state` emits a signal and does nothing else. Qt copies the
arguments into a QMetaCallEvent, posts it to the GUI thread's queue, and the slot
runs there on the next spin of the event loop. Everything that touches a widget
lives on the far side of that hop.

Why this is worth being pedantic about: `Engine._emit` wraps the callback in
`try/except Exception` and only writes the traceback to debug_log.txt. A
violation that raises therefore produces **no visible symptom** -- the poll loop
keeps running and dictation keeps working while the UI silently stops updating.
And the worst violations (building a QPixmap off-thread, replacing a QMenu the
event loop is dispatching into) are not Python exceptions at all, so they never
reach even that log line. This is the same shape as retrospective issue #11: the
primary symptom of the bug is the absence of evidence.

Connections are made with an explicit `Qt.QueuedConnection`. `AutoConnection`
would resolve correctly today, but it resolves by comparing the emitting thread
against the *receiver's* thread affinity -- so it degrades silently to a direct
call if a later change ever moves the bridge or the tray off the GUI thread. The
explicit form cannot degrade.
"""

import sys
import threading
from datetime import datetime

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from ptt import hotkey as hotkey_mod
from ptt.logging_setup import log_debug
from ptt.ui import qt_theme
from ptt.ui.qt_concierge_worker import ConciergeController
from ptt.ui.qt_popover import Popover
from ptt.ui.qt_statusview import UNKNOWN, UiState
from ptt.ui.qt_threadcheck import log_thread
from ptt.ui.qt_tray import QtTray
from ptt.ui.qt_window import SettingsWindow

#: Windows initialises the notification area asynchronously at login, and the
#: installer's Startup shortcut launches this app into that race. pystray blocked
#: until the tray existed; QSystemTrayIcon.show() on an unavailable tray simply
#: does nothing. Retry rather than vanish.
TRAY_RETRY_MS = 1000
TRAY_RETRY_LIMIT = 30


class EngineBridge(QObject):
    """
    The engine's state callback, marshalled onto the GUI thread.

    `on_state` is called from the engine thread. It must do nothing but emit --
    no formatting that could raise, no widget access, no blocking. The one
    exception is the one-shot thread check below, which fires exactly once per
    process (on the first "loading" emit, before the model load) and writes a
    single line. That is the evidence that the hop is real; see
    `qt_threadcheck.log_thread`.
    """

    state_changed = Signal(str, str)     # state, status_text
    text_ready = Signal(str)             # wired to the engine in session 2
    benchmark_done = Signal(str, str, float)  # model, device, seconds

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread_checked = False

    def on_state(self, state, status_text=None):
        if not self._thread_checked:
            self._thread_checked = True
            log_thread("callback EngineBridge.on_state", expect_gui=False)
        self.state_changed.emit(state, status_text or "")

    def on_text(self, text):
        self.text_ready.emit(text)

    def on_benchmark(self, model_name, device, seconds):
        """
        A latency measurement, from the engine thread. Emits and nothing else,
        for exactly the reason `on_state` does -- the slot on the far side
        writes config.json and repaints a table, and both belong on the GUI
        thread.

        **The model name is carried (v3.0), where it used to be dropped.** The
        reasoning for dropping it -- "by the time this fires it is
        `settings.model`, which the receiving panel already reads" -- was true
        while the Model tab was the only thing that could ask for a measurement.
        The Concierge can write the setting and have the reload that follows it
        deferred behind a turn, so the setting and the resident model disagree
        for a few seconds, and a measurement filed under the setting is filed
        under the wrong tier. `Engine.current_model` is the one that produced
        the number.
        """
        self.benchmark_done.emit(model_name, device, seconds)


class QtApp:
    """
    Built in two phases, because the app and the engine each need the other:

        app = QtApp(settings, cuda_supported)
        engine = Engine(settings, cuda_supported, on_state=app.bridge.on_state)
        app.attach(engine)
        app.run()

    `bridge.on_state` is safe to call before `attach` and before `run`: it only
    emits, and a signal with no live connection is a no-op.
    """

    def __init__(self, settings, cuda_supported):
        self._settings = settings
        self.cuda_supported = cuda_supported
        self._engine = None
        self._tray_attempts = 0

        # QApplication first: QtTray builds QPixmaps in its constructor, and a
        # QPixmap is backed by a platform surface that does not exist yet.
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setApplicationName("PTT Dictation")

        # Closing the settings window must not end the application -- it lives
        # in the tray, and Qt would otherwise quit with the last window.
        self._app.setQuitOnLastWindowClosed(False)

        # Fonts need a live QGuiApplication, so this cannot move earlier. It
        # must still happen before any widget is built, or the first widgets
        # measure themselves against the wrong face.
        qt_theme.apply_theme(self._app)

        self.ui = UiState(
            hotkey=hotkey_mod.chord_label(settings.hotkey),
            model=settings.model,
        )

        self.bridge = EngineBridge()
        self._tray = QtTray(settings, cuda_supported)
        self._popover = Popover()
        self._window = SettingsWindow(settings, cuda_supported)

        self.bridge.state_changed.connect(
            self._tray.on_state_changed, Qt.ConnectionType.QueuedConnection
        )
        # Second receiver on the same signal: the tray updates the icon, this
        # refreshes the two state displays. Both run on the GUI thread.
        self.bridge.state_changed.connect(
            self._on_state_changed, Qt.ConnectionType.QueuedConnection
        )

        self._tray.popover_requested.connect(self._popover.show_at_tray)
        self._tray.settings_requested.connect(self._open_settings)
        self._popover.clicked.connect(self._open_settings)

        # The settings window's banner already shows what the popover shows, so
        # the popover stands down whenever that window is up.
        self._popover.set_suppressor(self._window.isVisible)

        # A panel wrote a setting: refresh the two places that display one but
        # do not own it, without waiting for the engine's next state change.
        self._window.settings_changed.connect(self._on_settings_changed)
        self.bridge.benchmark_done.connect(
            self._on_benchmark_done, Qt.ConnectionType.QueuedConnection
        )

        # The Concierge (v3.0). Everything about the worker thread lives in the
        # controller; what this class supplies is the three things only it has:
        # the `UiState` the `get_state` tool is served from, the engine, and the
        # settings-changed broadcast a worker-thread write has to arrive at.
        self._concierge = ConciergeController(
            settings, self._window.concierge,
            ui_state=self.ui,
            engine_provider=lambda: self._engine,
            cuda_supported=cuda_supported,
        )
        self._concierge.settings_applied.connect(self._on_concierge_applied)
        self._concierge.reload_requested.connect(self._on_concierge_reload)
        self._concierge.status_changed.connect(
            self._window.set_concierge_segment)
        self._window.concierge_visible.connect(self._on_concierge_visible)
        self._tray.concierge_requested.connect(self._open_concierge)
        self._app.aboutToQuit.connect(self._concierge.shutdown)

        self._push_ui()

    def attach(self, engine):
        self._engine = engine
        self._tray.attach(engine)
        self._window.attach(engine)

    # -- state --------------------------------------------------------------

    def _on_state_changed(self, state, status_text):
        self.ui.state = state
        self.ui.status_text = status_text or state.capitalize()
        self.ui.hotkey = hotkey_mod.chord_label(self._settings.hotkey)
        self.ui.model = self._settings.model
        if self._engine is not None:
            # Plain attributes the engine rebinds; reading them here is the same
            # safe hand-off config.py's Settings docstring describes. The
            # microphone and last-transcription rows showed an em dash until
            # session 4, because nothing reported either -- the Audio panel's
            # device selection and the Diagnostics panel's latency history are
            # what make them obtainable, and section 5 asks for both.
            self.ui.device = self._engine.current_device
            self.ui.microphone = (
                self._engine.input_device_name() or "— stream closed —"
            )
            self.ui.last = self._engine.last_summary or UNKNOWN
        self._push_ui()
        # The engine may have overridden a setting the panels display -- a CUDA
        # load failure persists use_gpu=False from the engine thread -- so the
        # panels re-read rather than continuing to show what the user chose.
        self._window.refresh_panels()

    def _on_settings_changed(self):
        """
        A panel saved. Repaint what displays a setting without owning it.

        The other panels are refreshed too, because settings now cross tabs:
        the Audio tab's two checkboxes decide whether the constants the Advanced
        tab lists are being applied, and a page that still said a value was in
        force after it had been switched off elsewhere would be the two surfaces
        disagreeing -- the thing this window exists to make impossible.
        """
        self.ui.hotkey = hotkey_mod.chord_label(self._settings.hotkey)
        self.ui.model = self._settings.model
        self._push_ui()
        self._window.refresh_panels()
        self._tray.refresh_menu()

    def _on_benchmark_done(self, model, device, seconds):
        """
        Hand a measurement to the Model panel, on the GUI thread.

        And to the Concierge, if it is the one that asked. `run_benchmark` is a
        tool call blocked on a `threading.Event` over in the worker thread; this
        is the hop that releases it. Delivering unconditionally is deliberate --
        a measurement the user started from the Model tab is still the answer to
        "how fast is this model", and the bridge ignores one nobody is waiting
        for.
        """
        self._window.record_benchmark(model, device, seconds)
        self._concierge.benchmark.deliver(model, device, seconds)

    # -- the Concierge --------------------------------------------------------

    def _on_concierge_applied(self, key):
        """
        **FR-CG-2.** A worker-thread write, arriving on the GUI thread at last.

        The whole point of the hop: `set_config` ran on the Concierge's thread,
        went through `Settings.set()` and could not touch a widget; this is
        where the banner, the tabs, the status bar and the tray menu find out,
        through the same broadcast a panel's own write uses. Without it the
        change is on disk and in the settings object and nowhere on screen until
        the engine's next state change happens to repaint things.
        """
        log_debug(f"Concierge applied {key or 'a restore'}; refreshing the UI.")
        self._on_settings_changed()
        self._window.flash_saved(datetime.now().strftime("%H:%M:%S"))

    def _on_concierge_reload(self):
        """
        The Concierge changed a setting the engine only reads at model build.

        `InstantApplyPanel.apply_now(reload_model=True)` is the path a panel
        takes; a worker thread cannot take it, because that method is on a
        QWidget. `request_model_reload` is thread-safe, but it is called from
        here anyway so that the ordering is the one the panels already prove --
        the write is persisted first, then the engine is told.
        """
        if self._engine is not None:
            self._engine.request_model_reload()
        else:
            log_debug("WARNING: the Concierge changed a model setting before an "
                      "engine was attached; it will apply at the next load.")

    def _on_concierge_visible(self, visible):
        if visible:
            self._concierge.open()
        else:
            self._concierge.close()

    def _open_concierge(self):
        """The tray's `Concierge…`: Settings, with the panel already expanded."""
        self._open_settings()
        self._window.set_concierge_visible(True)

    def _push_ui(self):
        """One UiState, pushed to both displays, so they cannot drift apart."""
        self._popover.apply(self.ui)
        self._window.apply(self.ui)

    def _open_settings(self):
        """
        Show the window, and make the first-run Concierge offer if it is owed.

        The offer is deliberately not made at launch: see
        `SettingsWindow.offer_concierge_once`. Here rather than inside that
        method because expanding the panel has to reach the controller too, and
        the window knows nothing about the controller by design.
        """
        self._popover.hide()
        self._window.show_and_raise()
        if self._window.offer_concierge_once(
                self._settings.get("concierge.opt_in")):
            log_debug("Concierge: making the first-run offer; opt_in is unset.")

    # -- run ----------------------------------------------------------------

    def run(self):
        """Start the event loop. Blocks the calling thread until Exit."""
        # Deferred to the first turn of the event loop so that the engine thread
        # cannot emit before there is a loop to deliver to, mirroring the way
        # pystray started the engine from its icon-ready callback.
        QTimer.singleShot(0, self._on_event_loop_started)
        log_debug("Starting Qt event loop...")
        self._app.exec()

    def _on_event_loop_started(self):
        self._try_show_tray()
        log_debug("Starting engine background thread...")
        threading.Thread(target=self._engine.run, daemon=True).start()

    def _try_show_tray(self):
        """
        Show the icon, retrying while the notification area is still coming up.

        Deliberately does not gate the engine: if the tray never appears the
        application still dictates, which is a better failure than doing nothing
        at all.
        """
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray.show()
            log_debug(
                f"System tray icon made visible after {self._tray_attempts} "
                f"retries. Starting background thread..."
            )
            # Hover polling only makes sense once there is an icon to hover.
            self._popover.start_hover_watch(self._tray.icon_geometry)
            return

        self._tray_attempts += 1
        if self._tray_attempts > TRAY_RETRY_LIMIT:
            log_debug(
                "WARNING: no system tray after "
                f"{TRAY_RETRY_LIMIT * TRAY_RETRY_MS / 1000:.0f}s. Continuing "
                "without an icon; dictation and the hotkey still work."
            )
            return

        log_debug(
            f"System tray not available yet (attempt {self._tray_attempts}); "
            f"retrying in {TRAY_RETRY_MS} ms."
        )
        QTimer.singleShot(TRAY_RETRY_MS, self._try_show_tray)
