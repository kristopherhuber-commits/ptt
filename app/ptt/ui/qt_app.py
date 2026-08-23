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

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from ptt import hotkey as hotkey_mod
from ptt import transcribe
from ptt.logging_setup import log_debug
from ptt.ui import qt_theme
from ptt.ui.qt_popover import Popover
from ptt.ui.qt_statusview import UiState
from ptt.ui.qt_tray import QtTray, _log_thread
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
    single line. That is the evidence that the hop is real; see `_log_thread`.
    """

    state_changed = Signal(str, str)     # state, status_text
    text_ready = Signal(str)             # wired to the engine in session 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread_checked = False

    def on_state(self, state, status_text=None):
        if not self._thread_checked:
            self._thread_checked = True
            _log_thread("callback EngineBridge.on_state", expect_gui=False)
        self.state_changed.emit(state, status_text or "")

    def on_text(self, text):
        self.text_ready.emit(text)


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
            model=transcribe.MODEL_SIZE,
        )

        self.bridge = EngineBridge()
        self._tray = QtTray(settings, cuda_supported)
        self._popover = Popover()
        self._window = SettingsWindow()

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

        self._push_ui()

    def attach(self, engine):
        self._engine = engine
        self._tray.attach(engine)

    # -- state --------------------------------------------------------------

    def _on_state_changed(self, state, status_text):
        self.ui.state = state
        self.ui.status_text = status_text or state.capitalize()
        self.ui.hotkey = hotkey_mod.chord_label(self._settings.hotkey)
        self.ui.model = transcribe.MODEL_SIZE
        if self._engine is not None:
            # A plain attribute the engine rebinds; reading it here is the same
            # safe hand-off config.py's Settings docstring describes.
            self.ui.device = self._engine.current_device
        self._push_ui()

    def _push_ui(self):
        """One UiState, pushed to both displays, so they cannot drift apart."""
        self._popover.apply(self.ui)
        self._window.apply(self.ui)

    def _open_settings(self):
        self._popover.hide()
        self._window.show_and_raise()

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
