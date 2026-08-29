"""
The system tray frontend: a QSystemTrayIcon, its menu, and the state-to-icon map.

This module may import the engine; the engine must never import this one
(design.md section 4).

Threading. Everything in this module runs on the GUI thread. `on_state_changed`
is a **slot**, reached only through `EngineBridge`'s queued signal -- it is never
called by the engine directly. That is not a style preference: `QPixmap` and
every widget here are usable only from the thread that owns the event loop, and
the engine's state callback runs on the engine thread. See `qt_app.py` for the
boundary and why crossing it fails silently.

Replaces the pystray implementation. Two deliberate differences from it, both
behaviour-preserving:

- The menu is built once and its actions are mutated, where pystray rebuilt the
  whole menu on every state change. Replacing a QMenu that the user currently has
  open destroys a widget the event loop is dispatching into.
- Icons are built once at startup rather than converted per state change, because
  the conversion needs a live QApplication and there is no reason to repeat it.
"""

from typing import TYPE_CHECKING

from PIL import Image, ImageDraw
from PySide6.QtCore import QObject, QRect, Signal, Slot
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ptt import hotkey as hotkey_mod
from ptt.logging_setup import log_debug
from ptt.ui.qt_threadcheck import log_thread

if TYPE_CHECKING:                    # import for typing only; at runtime the
    from ptt.engine import Engine    # tray never needs the class itself

#: The sizes baked into the tray icon, and the reason they are these sizes.
#:
#: pystray did not hand Windows the 64px image. `pystray/_win32.py` saved it as
#: an .ICO and called LoadImage(..., LR_DEFAULTSIZE), and PIL's ICO writer
#: generates one frame per size in this list -- every entry of its default list
#: that is not larger than the source -- each produced with
#: `thumbnail(size, LANCZOS)`. Windows then picked whichever frame it wanted.
#:
#: So reproducing these five sizes with the same filter *is* the faithful port.
#: Handing Qt a single 64px pixmap and letting it scale at paint time would be
#: the change in behaviour, not the conservative choice.
ICON_SIZES = (16, 24, 32, 48, 64)


def create_icon_image(state):
    """Draw a status icon programmatically without needing external image assets."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    if state == "recording":
        d.ellipse((4, 4, 60, 60), fill=(239, 68, 68), outline=(185, 28, 28), width=4)
        d.ellipse((20, 20, 44, 44), fill=(255, 255, 255))
    elif state == "transcribing":
        d.ellipse((4, 4, 60, 60), fill=(245, 158, 11), outline=(217, 119, 6), width=4)
        d.rounded_rectangle((22, 22, 42, 42), radius=4, fill=(255, 255, 255))
    elif state == "loading":
        d.ellipse((4, 4, 60, 60), fill=(59, 130, 246), outline=(29, 78, 216), width=4)
        d.arc((16, 16, 48, 48), start=0, end=270, fill=(255, 255, 255), width=5)
    else:  # "idle"
        d.ellipse((4, 4, 60, 60), fill=(13, 148, 136), outline=(15, 118, 110), width=4)
        d.rounded_rectangle((26, 16, 38, 36), radius=6, fill=(255, 255, 255))
        d.arc((20, 22, 44, 38), start=0, end=180, fill=(255, 255, 255), width=3)
        d.line((32, 38, 32, 46), fill=(255, 255, 255), width=3)
        d.line((24, 46, 40, 46), fill=(255, 255, 255), width=3)

    return img


def _pixmap_from_pil(img):
    """
    Convert one PIL RGBA image to a QPixmap.

    The `.copy()` is load-bearing and must not be removed as a redundant step.
    QImage constructed over a Python buffer does **not** take ownership of it: it
    holds a bare pointer. Once the `raw` bytes object is collected the QImage
    refers to freed memory, and the symptom is not an exception -- it is garbage
    pixels, or a crash minutes later, or nothing at all on the machine it was
    written on. `.copy()` forces Qt to own the pixels.

    Requires a live QApplication: QPixmap is backed by a platform surface.
    """
    raw = img.tobytes("raw", "RGBA")
    qimg = QImage(
        raw, img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888
    ).copy()
    return QPixmap.fromImage(qimg)


def create_icon(state):
    """Build the multi-resolution QIcon for one engine state (see ICON_SIZES)."""
    master = create_icon_image(state)
    icon = QIcon()
    for size in ICON_SIZES:
        if size == master.width:
            frame = master
        else:
            # thumbnail() is in-place and preserves aspect ratio, matching what
            # PIL's ICO writer does for every non-native size.
            frame = master.copy()
            frame.thumbnail((size, size), Image.Resampling.LANCZOS, reducing_gap=None)
        icon.addPixmap(_pixmap_from_pil(frame))
    return icon


class QtTray(QObject):
    """
    Built in two phases, because the tray and the engine each need the other:

        tray = QtTray(settings, cuda_supported)
        engine = Engine(settings, cuda_supported, on_state=bridge.on_state)
        tray.attach(engine)
        tray.show()

    A QObject, not a plain class, and that matters: the queued connection in
    `qt_app.py` decides where to deliver by the *receiver's* thread affinity. A
    non-QObject receiver has none, and the slot would run on the engine thread --
    which is the exact failure this whole arrangement exists to prevent.
    """

    #: Left click on the icon. The reliable way to raise the popover: it arrives
    #: through QSystemTrayIcon.activated, which works even when geometry() is
    #: empty because the icon is in the Windows 11 overflow flyout.
    popover_requested = Signal()

    #: Double click, or the Settings... menu item.
    settings_requested = Signal()

    #: Open Settings **with the Concierge panel expanded** (handoff section 1).
    #: A separate signal rather than a flag on `settings_requested`, because the
    #: two menu items mean different things and `QtApp` does different things
    #: with them. It is present even when the Concierge has been declined: that
    #: is what handoff section 1 means by "declining leaves a Concierge entry in
    #: Settings and the tray menu" -- declining is not the same as hiding.
    concierge_requested = Signal()

    def __init__(self, settings, cuda_supported, parent=None):
        super().__init__(parent)
        self._settings = settings
        self.cuda_supported = cuda_supported
        self._engine: "Engine"   # assigned by attach(), always before show()
        self._status = "Initializing..."
        self._thread_checked = False

        self._icons = {s: create_icon(s)
                       for s in ("idle", "recording", "transcribing", "loading")}

        self._tray = QSystemTrayIcon(self._icons["loading"])
        self._tray.setToolTip("PTT Dictation (Initializing...)")
        self._tray.activated.connect(self._on_activated)
        self._build_menu()

    def attach(self, engine):
        self._engine = engine

    def show(self):
        self._tray.show()

    def hide(self):
        self._tray.hide()

    def icon_geometry(self):
        """
        The icon's rectangle in screen coordinates, for placing the popover.

        Qt returns an empty QRect whenever the icon is not visible, which
        includes the icon sitting in the overflow flyout behind the `^` chevron.
        Callers must treat an empty rect as "unknown", not as (0, 0).
        """
        try:
            return self._tray.geometry()
        except Exception:
            return QRect()

    @Slot(QSystemTrayIcon.ActivationReason)
    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.popover_requested.emit()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.settings_requested.emit()

    # -- the bridged Engine callback ----------------------------------------

    @Slot(str, str)
    def on_state_changed(self, state, status_text):
        """
        Update icon, tooltip and menu. Runs on the GUI thread -- see the class
        docstring and `qt_app.EngineBridge`.

        The status fallback matches the pre-Qt tray exactly: an empty status text
        falls back to the capitalised state name.
        """
        # Development-time assertion. Safe to raise *here*, unlike on the
        # callback side: this is a slot reached through the event loop, so an
        # AssertionError surfaces normally instead of being swallowed by
        # Engine._emit's except clause. It is close to tautological -- a queued
        # connection guarantees it -- but it does catch the one regression that
        # would otherwise be silent: someone changing the connection type to
        # DirectConnection. The paired log lines below are the real evidence.
        from PySide6.QtCore import QThread
        assert QThread.currentThread() == QApplication.instance().thread(), (
            "on_state_changed ran off the GUI thread; the queued connection in "
            "qt_app.py is not doing its job"
        )

        if not self._thread_checked:
            self._thread_checked = True
            log_thread("slot QtTray.on_state_changed", expect_gui=True)

        self._status = status_text if status_text else state.capitalize()
        self._tray.setIcon(self._icons.get(state, self._icons["idle"]))
        self._tray.setToolTip(f"PTT Dictation ({self._status})")
        self.refresh_menu()

    # -- menu ---------------------------------------------------------------

    def _build_menu(self):
        """
        Build the menu once. `refresh_menu` mutates it in place afterwards.

        Held on self deliberately: a QMenu with no parent and no reference is
        collected out from under the tray icon.
        """
        self._menu = QMenu()

        self._act_status = self._menu.addAction(f"Status: {self._status}")
        self._act_status.setEnabled(False)

        self._act_hotkey = self._menu.addAction("Hotkey: ")
        self._act_hotkey.setEnabled(False)

        self._menu.addSeparator()

        self._act_gpu = self._menu.addAction("Use GPU (CUDA)")
        self._act_gpu.setCheckable(True)
        self._act_gpu.setEnabled(self.cuda_supported)
        self._act_gpu.triggered.connect(self._set_device_gpu)

        self._act_cpu = self._menu.addAction("Use CPU")
        self._act_cpu.setCheckable(True)
        self._act_cpu.triggered.connect(self._set_device_cpu)

        self._menu.addSeparator()

        self._act_concierge = self._menu.addAction("Concierge…")
        self._act_concierge.triggered.connect(
            lambda _checked=False: self.concierge_requested.emit()
        )

        self._act_settings = self._menu.addAction("Settings…")
        # Swallow QAction.triggered's `checked` bool rather than forwarding it
        # into a zero-argument signal, which raises TypeError if emitted directly.
        self._act_settings.triggered.connect(
            lambda _checked=False: self.settings_requested.emit()
        )

        self._menu.addSeparator()

        self._act_exit = self._menu.addAction("Exit")
        self._act_exit.triggered.connect(self._on_exit)

        self._tray.setContextMenu(self._menu)
        self.refresh_menu()

    def refresh_menu(self):
        """
        Bring the menu's labels and check marks up to date. Never rebuilds.

        Public because the settings window changes the same two settings this
        menu shows -- the chord and the device -- and the menu would otherwise
        keep displaying what was set when the app started.
        """
        self._act_status.setText(f"Status: {self._status}")
        self._act_hotkey.setText(
            f"Hotkey: {hotkey_mod.chord_label(self._settings.hotkey)}"
        )
        self._act_gpu.setChecked(bool(self._settings.use_gpu))
        self._act_cpu.setChecked(not self._settings.use_gpu)

    @Slot()
    def _set_device_gpu(self):
        if not self._settings.use_gpu:
            self._settings.set("use_gpu", True)
            self._engine.request_model_reload()
        self.refresh_menu()

    @Slot()
    def _set_device_cpu(self):
        if self._settings.use_gpu:
            self._settings.set("use_gpu", False)
            self._engine.request_model_reload()
        self.refresh_menu()

    @Slot()
    def _on_exit(self):
        log_debug("Exit requested by user.")
        self._engine.stop()
        # Do NOT join the engine thread here. It is a daemon and the process
        # exits via os._exit immediately after exec() returns; joining would
        # block for an in-flight transcription -- up to 30 s on CPU -- turning
        # "Exit" into "hang".
        #
        # hide() is required and was not needed under pystray, which removed the
        # icon in icon.stop(). runtime.main_guard calls os._exit, which skips
        # every Qt destructor including the one that issues NIM_DELETE, so
        # without this Windows leaves a dead icon in the tray until the user
        # happens to hover over it.
        self._tray.hide()
        QApplication.quit()
