"""
Layer 2: the hover popover.

A QSystemTrayIcon tooltip is plain text only, so the state display is a separate
frameless window that follows the tray icon.

Three Windows-specific problems this has to survive:

1. **It must not steal keyboard focus.** The user is typing into something else
   when they glance at the tray; raising a window that takes focus would eat
   their keystrokes. `Qt.Tool` keeps it off the taskbar and out of the alt-tab
   order, and `WA_ShowWithoutActivating` stops it activating when shown.

2. **QSystemTrayIcon has no hover signal.** Qt documents exactly two signals,
   `activated` and `messageClicked`, and the tooltip QHelpEvent is X11-only. So
   hover is detected by polling the cursor against the icon's rectangle. There
   is nothing to install an event filter on.

   It is also never *activated*, which is what makes point 4 necessary.

3. **`geometry()` returns an empty QRect when the icon is not visible**, which
   includes the case the README documents: the icon sitting in the Windows 11
   overflow flyout behind the `^` chevron. Hover detection is therefore
   best-effort. Clicking the tray icon always works, because that arrives
   through `activated` regardless of geometry, and placement falls back to the
   cursor so the panel never lands in a corner of the screen by accident.

4. **`raise_()` alone leaves it occluded.** Windows keeps two Z-order bands, and
   nothing in the ordinary band can be placed above the topmost one. Measured
   against an always-on-top window owned by another process: without
   `WindowStaysOnTopHint` the panel is below it, with the hint it is above it,
   and the foreground window is unchanged either way.

   `raise_()` also only wins at the instant it is called, and nothing re-raises
   while the panel sits there through a hover. The hint removes the whole class
   rather than the one moment: a topmost window is above every ordinary window
   at all times, not merely when it was last raised.

   It costs nothing in focus. Topmost is a Z-order property, not an activation
   one -- the notification-area flyouts Windows draws itself sit in the same
   band, over the window the user is typing into, without taking their
   keystrokes.
"""

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget

from ptt.ui.qt_statusview import StatusView

#: How often the cursor is compared against the tray icon's rectangle. 150 ms is
#: responsive enough to feel like hover and cheap enough to ignore.
HOVER_POLL_MS = 150

#: Grace period before hiding once the pointer is outside both the icon and the
#: panel, so the pointer can travel from one to the other without it vanishing.
LEAVE_GRACE_MS = 400

#: Gap between the tray icon and the panel.
MARGIN_PX = 8


class Popover(QWidget):
    """
    Frameless, non-activating state panel.

    `clicked` is emitted on any mouse press anywhere on it, which is the only
    interaction it has: section 5 requires no buttons, no toggles and no links.
    """

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            # WS_EX_TOPMOST. `raise_()` in show_at_tray is not a substitute
            # and never was: it cannot cross into the topmost band, so an
            # always-on-top window from any other process covers this panel.
            # See point 4 of the module docstring for what was measured.
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowTitle("PTT Dictation")

        # An outer QFrame carries the border; the widget itself stays plain so
        # the frame's edge is not clipped by the window boundary.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QFrame()
        frame.setObjectName("popoverFrame")
        outer.addWidget(frame)

        inner = QVBoxLayout(frame)
        inner.setContentsMargins(0, 0, 0, 0)
        self.view = StatusView(show_footer=True)
        inner.addWidget(self.view)

        self.setFixedWidth(340)

        self._icon_rect = QRect()
        #: Returns True while the popover must stay down. Set by QtApp to the
        #: settings window's visibility: once that window is open its banner
        #: already shows this exact content, so raising the panel on top of it
        #: is redundant and reads as a bug. It also covers the case that looked
        #: like one -- choosing "Settings..." from the tray menu leaves the
        #: pointer near the icon, so the hover poll would otherwise raise the
        #: panel over the window that had just opened.
        self._suppressed = lambda: False
        self._leave_timer = QTimer(self)
        self._leave_timer.setSingleShot(True)
        self._leave_timer.setInterval(LEAVE_GRACE_MS)
        self._leave_timer.timeout.connect(self.hide)

        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(HOVER_POLL_MS)
        self._hover_timer.timeout.connect(self._poll_cursor)

    # -- public API ---------------------------------------------------------

    def apply(self, ui):
        self.view.apply(ui)
        if self.isVisible():
            # Content can change height (the detail line appears and vanishes),
            # so re-place rather than leaving the panel hanging off its anchor.
            self._place()

    def start_hover_watch(self, icon_rect_getter):
        """Begin polling the cursor. `icon_rect_getter` returns the live tray rect."""
        self._icon_rect_getter = icon_rect_getter
        self._hover_timer.start()

    def set_suppressor(self, predicate):
        """Install a callable that returns True while the panel must stay down."""
        self._suppressed = predicate

    def show_at_tray(self):
        """Raise the panel next to the tray icon without taking focus."""
        if self._suppressed():
            return
        self._leave_timer.stop()
        self._place()
        self.show()
        # Orders the panel among the *other* topmost windows, which is all
        # raise_() can do here. Clearing the ordinary ones is the window flag's
        # job; see the constructor.
        self.raise_()

    # -- hover --------------------------------------------------------------

    def _poll_cursor(self):
        if self._suppressed():
            if self.isVisible():
                self.hide()
            return

        rect = self._icon_rect_getter()
        self._icon_rect = rect
        pos = QCursor.pos()

        over_icon = bool(rect) and not rect.isEmpty() and rect.contains(pos)
        over_panel = self.isVisible() and self.frameGeometry().contains(pos)

        if over_icon or over_panel:
            self._leave_timer.stop()
            if not self.isVisible():
                self.show_at_tray()
        elif self.isVisible() and not self._leave_timer.isActive():
            self._leave_timer.start()

    # -- placement ----------------------------------------------------------

    def _place(self):
        """
        Anchor to the tray icon, clamped so the panel is always fully on screen.

        Falls back to the cursor when the icon has no usable rectangle, which is
        what Qt reports whenever the icon is not visible -- including the
        overflow-flyout case. Without this the panel would pin itself to (0, 0).
        """
        self.adjustSize()
        size = self.sizeHint()
        rect = self._icon_rect

        if rect is None or rect.isEmpty():
            anchor = QCursor.pos()
            rect = QRect(anchor.x(), anchor.y(), 1, 1)

        screen = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
        available = screen.availableGeometry()

        # Prefer above-left of the icon: the tray sits bottom-right on a default
        # Windows 11 taskbar, so that is the direction with room.
        x = rect.right() - size.width()
        y = rect.top() - size.height() - MARGIN_PX

        if y < available.top():                       # taskbar at the top
            y = rect.bottom() + MARGIN_PX
        x = max(available.left() + MARGIN_PX,
                min(x, available.right() - size.width() - MARGIN_PX))
        y = max(available.top() + MARGIN_PX,
                min(y, available.bottom() - size.height() - MARGIN_PX))

        self.move(QPoint(int(x), int(y)))

    # -- the one interaction ------------------------------------------------

    def mousePressEvent(self, event):
        self.hide()
        self.clicked.emit()
        event.accept()
