"""
The red asterisks that confirm a double-tap (FR-13): the newest clip is being
flagged as wrong.

Two marks, for `FLASH_MS` each:

- **a big one in the middle of the active window**, shown at once;
- **a small one at the text cursor**, the size of a character, as if a red `*`
  had been typed there. It shows as soon as the cursor is found
  (`caret.cursor_cell`, on a thread of its own: UI Automation can be slow, and
  the GUI thread must not wait on it). No cursor found, no small mark.

Each is a small frameless window, on top of everything, that never takes focus
and lets every click through, so the next paste still lands where the user is
typing.
"""

import math
import threading

from PySide6.QtCore import QObject, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ptt import caret, inject
from ptt.logging_setup import log_debug

#: How long each mark stays up (FR-13's half second).
FLASH_MS = 500

#: The big mark's width and height, in logical pixels.
BIG_PX = 44

#: The smallest the cursor mark is drawn, in logical pixels.
SMALL_MIN_PX = 12

RED = QColor(220, 30, 30)
HALO = QColor(255, 255, 255, 230)


def _find(point, screens):
    """The screen `(x, y, width, height, ratio)` holding a physical point, else the first."""
    px, py = point
    for screen in screens:
        x, y, width, height, ratio = screen
        if x <= px < x + width * ratio and y <= py < y + height * ratio:
            return screen
    return screens[0] if screens else None


def to_logical(point, screens):
    """
    A physical-pixel screen point, as Qt's logical coordinates.

    `screens` holds `(x, y, width, height, ratio)` per screen, the geometry in
    logical pixels as `QScreen.geometry()` gives it. Qt on Windows keeps each
    screen's top-left at its physical position and divides only its size by the
    scale, so a point inside a screen maps by that screen's ratio from its
    top-left. A point on no screen maps by the first one's.
    """
    screen = _find(point, screens)
    if screen is None:
        return float(point[0]), float(point[1])
    x, y, _w, _h, ratio = screen
    return x + (point[0] - x) / ratio, y + (point[1] - y) / ratio


def ratio_at(point, screens):
    """The scale of the screen holding a physical point (1.0 with no screens)."""
    screen = _find(point, screens)
    return screen[4] if screen else 1.0


def _screens():
    return [(s.geometry().x(), s.geometry().y(), s.geometry().width(),
             s.geometry().height(), s.devicePixelRatio())
            for s in QGuiApplication.screens()]


class Asterisk(QWidget):
    """One red asterisk with a white halo, shown centred on a point."""

    def __init__(self, size):
        super().__init__(None, Qt.WindowType.Tool
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowTransparentForInput
                         | Qt.WindowType.WindowDoesNotAcceptFocus
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedSize(size, size)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def flash_at(self, x, y, size=None):
        if size is not None:
            self.setFixedSize(size, size)
        side = self.width()
        self.move(round(x - side / 2), round(y - side / 2))
        self.show()
        self.raise_()
        self._timer.start(FLASH_MS)

    def paintEvent(self, _event):
        side = self.width()
        halo_width, red_width = side * 0.25, side * 0.14
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        centre = QPointF(side / 2, side / 2)
        reach = side / 2 - halo_width / 2
        arms = []
        for degrees in (90, 30, -30):
            a = math.radians(degrees)
            dx, dy = reach * math.cos(a), reach * math.sin(a)
            arms.append((QPointF(centre.x() - dx, centre.y() - dy),
                         QPointF(centre.x() + dx, centre.y() + dy)))
        # A white halo under the red, so the mark shows on dark and light text.
        for colour, width in ((HALO, halo_width), (RED, red_width)):
            painter.setPen(QPen(colour, width, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap))
            for start, end in arms:
                painter.drawLine(start, end)
        painter.end()


class TapMark(QObject):
    """Both marks. `flash()` runs on the GUI thread and never raises."""

    _cursor_found = Signal(object)

    def __init__(self):
        super().__init__()
        self._big = Asterisk(BIG_PX)
        self._small = Asterisk(SMALL_MIN_PX)
        self._cursor_found.connect(self._show_small, Qt.ConnectionType.QueuedConnection)

    def flash(self):
        try:
            native = inject.window_centre()
            if native is None:
                cursor = QCursor.pos()
                x, y = cursor.x(), cursor.y()
            else:
                x, y = to_logical(native, _screens())
            self._big.flash_at(x, y)
        except Exception as e:
            log_debug(f"ERROR showing the double-tap mark: {e}")
        threading.Thread(target=lambda: self._cursor_found.emit(caret.cursor_cell()),
                         name="tap-mark-cursor", daemon=True).start()

    def _show_small(self, cell):
        if cell is None:
            return
        try:
            left, top, width, height = cell
            centre = (left + width / 2, top + height / 2)
            screens = _screens()
            x, y = to_logical(centre, screens)
            side = max(SMALL_MIN_PX, round(height / ratio_at(centre, screens)))
            self._small.flash_at(x, y, side)
        except Exception as e:
            log_debug(f"ERROR showing the cursor mark: {e}")
