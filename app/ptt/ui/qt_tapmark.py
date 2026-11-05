"""
The red asterisk that confirms a double-tap (v4): the newest clip is being
deleted.

A small frameless window, on top of everything, that never takes focus and lets
every click through, so the next paste still lands where the user is typing.
It shows for `FLASH_MS` over the text caret when Windows reports one, else over
the centre of the foreground window (`inject.mark_point`).
"""

import math

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ptt import inject
from ptt.logging_setup import log_debug

#: How long the mark stays up (FR-13's half second).
FLASH_MS = 500

#: The mark's width and height, in logical pixels.
SIZE_PX = 44

RED = QColor(220, 30, 30)
HALO = QColor(255, 255, 255, 230)


def to_logical(point, screens):
    """
    A physical-pixel screen point, as Qt's logical coordinates.

    `screens` holds `(x, y, width, height, ratio)` per screen, the geometry in
    logical pixels as `QScreen.geometry()` gives it. Qt on Windows keeps each
    screen's top-left at its physical position and divides only its size by the
    scale, so a point inside a screen maps by that screen's ratio from its
    top-left. A point on no screen maps by the first one's.
    """
    px, py = point
    for x, y, width, height, ratio in screens:
        if x <= px < x + width * ratio and y <= py < y + height * ratio:
            return x + (px - x) / ratio, y + (py - y) / ratio
    if screens:
        x, y, _w, _h, ratio = screens[0]
        return x + (px - x) / ratio, y + (py - y) / ratio
    return float(px), float(py)


class TapMark(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowTransparentForInput
                         | Qt.WindowType.WindowDoesNotAcceptFocus
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedSize(SIZE_PX, SIZE_PX)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def flash(self):
        """Show the mark where the user is typing, for `FLASH_MS`. Never raises."""
        try:
            native = inject.mark_point()
            if native is None:
                cursor = QCursor.pos()
                x, y = cursor.x(), cursor.y()
            else:
                screens = [(s.geometry().x(), s.geometry().y(), s.geometry().width(),
                            s.geometry().height(), s.devicePixelRatio())
                           for s in QGuiApplication.screens()]
                x, y = to_logical(native, screens)
            self.move(round(x - SIZE_PX / 2), round(y - SIZE_PX / 2))
            self.show()
            self.raise_()
            self._timer.start(FLASH_MS)
        except Exception as e:
            log_debug(f"ERROR showing the double-tap mark: {e}")

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        centre = QPointF(SIZE_PX / 2, SIZE_PX / 2)
        reach = SIZE_PX / 2 - 6
        arms = []
        for degrees in (90, 30, -30):
            a = math.radians(degrees)
            dx, dy = reach * math.cos(a), reach * math.sin(a)
            arms.append((QPointF(centre.x() - dx, centre.y() - dy),
                         QPointF(centre.x() + dx, centre.y() + dy)))
        # A white halo under the red, so the mark shows on dark and light text.
        for colour, width in ((HALO, 11), (RED, 6)):
            painter.setPen(QPen(colour, width, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap))
            for start, end in arms:
                painter.drawLine(start, end)
        painter.end()
