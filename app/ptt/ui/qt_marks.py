"""
The `+` registration marks at panel corners (`gui_handoff` section 9).

A registration mark is the crosshair a printer uses to align colour plates. It
carries no information and nothing depends on it: it is the motif that makes the
window read as a technical drawing rather than a form, which is the same reason
every corner in this UI is square. `.blueprint` in `industry.css` is the
reference, and its geometry is reproduced here -- an 11 px box with two 1 px arms
crossing at its centre.

**Why this is a paintEvent.** Section 9 names two things a style sheet cannot
draw, and this is one of them. QSS has no primitive for two crossing hairlines;
`border-image` could fake it but needs a bitmap per colour, which puts a colour
in an asset instead of in the stylesheet.

**Why the marks sit inside the widget rather than straddling its edge.** The
reference offsets each mark by -6 px so it hangs half outside the box, and CSS
allows that with `overflow: visible` on the parent. Qt has no equivalent: a
QPainter on a widget is clipped to that widget's own rectangle, so anything
drawn at a negative coordinate is not merely hidden, it is never rasterised. The
alternatives are to paint each frame from its parent -- which makes every panel
depend on what contains it -- or to inset the marks. They read the same inset,
so they are inset. This is a deliberate deviation from the reference and the
only one.

**No colour lives in this module.** `markColour` is a Qt property written from
`style.qss` with `qproperty-`, exactly the indirection `StatusDot` uses and for
the same reason. It defaults to fully transparent, so a stylesheet that failed to
load produces no marks rather than black ones -- consistent with the rest of the
UI, where a missing stylesheet is survivable and logged.
"""

from PySide6.QtCore import Property
from PySide6.QtGui import QColor, QPainter, QPen

#: Side of one mark's bounding box, in pixels. `industry.css` uses 11, with the
#: two arms crossing at (5, 5) -- an odd number so the crossing lands on a whole
#: pixel and neither arm is drawn half-way between two.
MARK_PX = 11

#: Arm thickness. One device-independent pixel, like every hairline here.
ARM_PX = 1

#: Gap between the widget's edge and the mark's bounding box. The reference has
#: no equivalent -- it hangs the mark outside the border instead -- so this is
#: the value that replaces that offset. Small enough to read as "at the corner"
#: rather than "floating near it".
MARGIN_PX = 6


def mark_centres(width, height, mark=MARK_PX, margin=MARGIN_PX):
    """
    Where the four crossings go, for a widget of this size.

    Pure, and separated from the painting for exactly that reason: geometry is
    the half of this that can be checked without a screen.

    Returns four `(x, y)` pairs -- top-left, top-right, bottom-left,
    bottom-right -- or an **empty tuple** when the widget is too small to hold
    four marks without them running into each other. That is not hypothetical:
    every panel lives in a `QScrollArea`, and a panel narrower than its marks
    would otherwise draw a smear along its top edge rather than nothing.
    """
    half = mark // 2
    span = 2 * (margin + mark)
    if width < span or height < span:
        return ()

    left = margin + half
    right = width - 1 - margin - half
    top = margin + half
    bottom = height - 1 - margin - half
    return ((left, top), (right, top), (left, bottom), (right, bottom))


class RegistrationMarks:
    """
    Mixin that paints the four marks. List it **before** the QWidget base:

        class StatusView(RegistrationMarks, QFrame):

    A mixin rather than a common base class because the two hosts do not share
    one -- `StatusView` is a `QFrame` and `InstantApplyPanel` is a `QWidget` --
    and because it carries no state a widget would need to initialise. PySide6
    resolves a `Property` declared here onto the concrete class, so `style.qss`
    reaches it with `qproperty-markColour` the same as any other.
    """

    #: Overwritten per instance by the property setter below.
    _mark_colour = QColor(0, 0, 0, 0)

    def _get_mark_colour(self):
        return self._mark_colour

    def _set_mark_colour(self, colour):
        self._mark_colour = colour
        self.update()

    #: Written by style.qss via `qproperty-markColour`.
    markColour = Property(QColor, _get_mark_colour, _set_mark_colour)

    def paint_registration_marks(self):
        """Draw the marks over whatever the base class already painted."""
        colour = self._mark_colour
        if not colour.isValid() or colour.alpha() == 0:
            return

        centres = mark_centres(self.width(), self.height())
        if not centres:
            return

        half = MARK_PX // 2
        painter = QPainter(self)
        # No antialiasing: these are axis-aligned hairlines, and smoothing a
        # 1 px line across two rows is what makes it look grey and blurred
        # instead of thin.
        pen = QPen(colour)
        pen.setWidth(ARM_PX)
        pen.setCosmetic(True)
        painter.setPen(pen)
        for x, y in centres:
            painter.drawLine(x, y - half, x, y + half)
            painter.drawLine(x - half, y, x + half, y)
        painter.end()

