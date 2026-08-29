"""
The read-only state display, built once and embedded twice.

`gui_handoff.md` section 5 is explicit that the settings window's banner and the
hover popover are the same layout: "Build it once and embed the same class in
both places. The user specifically asked for the transition from popover to
window to feel like the same object growing." So this is that class, and neither
`qt_popover` nor `qt_window` draws rows of its own.

Row order is fixed by section 5 and must not be reordered: header, State,
Hotkey, Model, Microphone, Last.

**This widget has no controls.** No buttons, no toggles, no links, in either
host. It is a display, and that is a hard requirement from the user.

Everything here is dark-on-steel, because the design rule that drives the whole
colour scheme is: dark surfaces are read-only, light surfaces are interactive.
"""

from dataclasses import dataclass

from PySide6.QtCore import Property, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from ptt.ui.qt_marks import RegistrationMarks

#: Placeholder for a value this build cannot obtain. Every row can be filled in
#: now -- Microphone and Last were the two that could not be until the Audio and
#: Diagnostics panels gave the engine somewhere to report them from -- but a
#: value that is genuinely unknown still shows an em dash rather than a
#: plausible invention.
UNKNOWN = "—"


def is_error(status_text):
    """
    Whether a status string represents a failure.

    The engine has no "error" state. Both of its failure paths emit **idle**
    with a different status string -- "Error loading model" from the model
    loader, and "Error: ..." from the poll loop's exception handler. So the only
    discriminator available is the text, and this is the single place that
    knows it. See docs/ptt-v2-gui/stage0_review.md section 3.3.
    """
    return bool(status_text) and status_text.startswith("Error")


def effective_state(state, status_text):
    """Map the engine's state plus its status text onto a dot colour key."""
    return "error" if is_error(status_text) else state


@dataclass
class UiState:
    """
    Everything the three UI layers display, in one place.

    Held by `QtApp` and pushed to every registered view, so the popover and the
    banner cannot drift apart.
    """
    state: str = "loading"
    status_text: str = "Initializing..."
    hotkey: str = UNKNOWN
    model: str = UNKNOWN
    device: str = ""
    microphone: str = UNKNOWN     # Engine.input_device_name(), once a stream is open
    last: str = UNKNOWN           # Engine.last_summary, once something has been said

    def detail(self):
        """
        The small muted line under the headline.

        Derived, never invented: section 5 allows the detail to be derived but
        requires the headline to be exactly what the engine reported.
        """
        if is_error(self.status_text):
            return "see debug_log.txt · reload from Diagnostics"
        if self.state == "loading":
            return f"loading {self.model}"
        if self.state == "recording":
            return "hotkey held"
        if (self.status_text or "").startswith("Measuring"):
            # Derived from the headline, the same way the two cases above and
            # below this one are: the Model panel's benchmark reuses the
            # transcribing state, and "then pastes at the cursor" would be a
            # plain lie about a measurement that pastes nothing.
            return "timing the bundled 30-second clip"
        if self.state == "transcribing":
            return "then pastes at the cursor"
        if "Fallback" in (self.status_text or ""):
            return "CUDA load failed · saved use_gpu=false"
        if self.device:
            return f"model resident on {self.device.upper()} · {self.model}"
        return ""


class ElidedLabel(QLabel):
    """
    A one-line label that ends in an ellipsis rather than wrapping or clipping.

    This exists because of a specific defect, and the defect is worth recording
    because the obvious fix is the one that caused it.

    The State row's detail line used to be a word-wrapping `QLabel` sitting in a
    `QVBoxLayout` inside a `QGridLayout` cell. In the settings window's banner,
    880 px wide, it fits on one line and looks right. In the popover, which is
    `setFixedWidth(340)`, it wraps -- and a wrapping label reports a **one-line**
    height through a nested layout, so the grid allocated one line and the second
    line was drawn on top of the headline above it. Overlapping text, in the
    surface the user looks at most. Confirmed against a pristine `HEAD` worktree,
    so it predated the session that found it; setting `setWordWrap(False)` in a
    probe made it go away, which is what pinned the cause.

    The row values had a second, quieter version of the same problem: at 340 px
    a real device name (72 characters on the machine this was found on) simply
    ran off the right-hand edge with nothing to say it had.

    Eliding fixes both with one mechanism, and it is the honest one: an ellipsis
    says text was cut, where clipping says nothing at all. Nothing is lost that
    the user cannot get -- the same widget in the settings window is wide enough
    to show the whole string, which is the popover-is-a-glance,
    window-is-the-detail split section 5 already describes.

    `text()` returns what is **painted**, which is Qt's contract for it and may
    be elided. `full_text()` returns what was set. Any caller comparing the two
    hosts' contents must use `full_text()`, or it is comparing two widths.
    """

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._full = text
        # Ignored horizontally: the label must never be the reason a row demands
        # width. Without this the grid sizes column 1 to the longest value and
        # the popover grows a horizontal scrollbar instead of eliding -- which
        # is the same failure as before wearing different clothes.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def setText(self, text):
        self._full = text or ""
        self._apply_elision()

    def full_text(self):
        """What was set, before elision. See the class docstring."""
        return self._full

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_elision()

    def _apply_elision(self):
        width = self.width()
        if width <= 0:
            # Before the first layout pass there is nothing to measure against.
            # Set the full string so the label has something to paint if it is
            # never resized, and let resizeEvent do the real work.
            super().setText(self._full)
            return
        painted = self.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideRight, width
        )
        if painted != super().text():
            super().setText(painted)


class StatusDot(QWidget):
    """
    A small filled circle. Painted, because a round dot is the one thing the
    otherwise-square design keeps round.

    The colour is **not** hard-coded here. It comes from style.qss through the
    `dotColour` Qt property, selected on a dynamic `state` property:

        QWidget#statusDot[state="recording"] { qproperty-dotColour: #ef4444; }

    That indirection exists so the session 2 rule holds literally -- every
    colour lives in the stylesheet, not in Python -- even for something a
    paintEvent draws. `qproperty-` is resolved when the widget is polished, so
    changing the state means re-polishing.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusDot")
        # Only reached if the stylesheet failed to load, which qt_theme logs.
        # Taken from the palette rather than written as a literal, so this file
        # defines no colour of its own.
        self._colour = self.palette().windowText().color()
        self.setFixedSize(10, 10)
        self.set_state("loading")

    def _get_dot_colour(self):
        return self._colour

    def _set_dot_colour(self, colour):
        self._colour = colour
        self.update()

    #: Written by style.qss via `qproperty-dotColour`.
    dotColour = Property(QColor, _get_dot_colour, _set_dot_colour)

    def set_state(self, state_key):
        self.setProperty("state", state_key)
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._colour)
        p.drawEllipse(self.rect())


class StatusView(RegistrationMarks, QFrame):
    """
    The dark state panel. Used as the popover's body and as the window's banner.

    `show_footer` is the only difference between the two: the popover invites a
    click, the banner is already inside the thing that click opens.
    """

    def __init__(self, show_footer=False, parent=None):
        super().__init__(parent)
        self.setObjectName("statusView")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(10)

        # -- header: dot + name, state code on the right --------------------
        header = QHBoxLayout()
        header.setSpacing(9)
        self._dot = StatusDot()
        self._brand = QLabel("PTT Dictation")
        self._brand.setObjectName("brand")
        self._state_tag = QLabel("")
        self._state_tag.setObjectName("stateTag")
        header.addWidget(self._dot)
        header.addWidget(self._brand)
        header.addStretch(1)
        header.addWidget(self._state_tag)
        outer.addLayout(header)

        # -- the five rows, in the order section 5 fixes --------------------
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(0)
        grid.setColumnMinimumWidth(0, 96)
        grid.setColumnStretch(1, 1)
        outer.addLayout(grid)
        row = 0

        def rule():
            nonlocal row
            line = QFrame()
            line.setObjectName("rowRule")
            line.setFrameShape(QFrame.Shape.NoFrame)
            grid.addWidget(line, row, 0, 1, 3)
            row += 1

        def label(text):
            lbl = QLabel(text)
            lbl.setObjectName("rowLabel")
            lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            return lbl

        rule()

        # State spans two lines: the engine's headline, then a derived detail.
        grid.addWidget(label("STATE"), row, 0)
        state_box = QVBoxLayout()
        state_box.setContentsMargins(0, 8, 0, 8)
        state_box.setSpacing(1)
        self._headline = QLabel("")
        self._headline.setObjectName("stateHeadline")
        self._detail = ElidedLabel("")
        self._detail.setObjectName("stateDetail")
        state_box.addWidget(self._headline)
        state_box.addWidget(self._detail)
        grid.addLayout(state_box, row, 1, 1, 2)
        row += 1
        rule()

        def value_row(caption, with_tag=False):
            nonlocal row
            grid.addWidget(label(caption), row, 0)
            val = ElidedLabel(UNKNOWN)
            val.setObjectName("rowValue")
            val.setContentsMargins(0, 9, 0, 9)
            tag = None
            if with_tag:
                tag = QLabel("")
                tag.setObjectName("deviceTag")
                tag.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                grid.addWidget(val, row, 1)
                grid.addWidget(tag, row, 2, Qt.AlignmentFlag.AlignRight)
            else:
                grid.addWidget(val, row, 1, 1, 2)
            row += 1
            rule()
            return val, tag

        self._hotkey, _ = value_row("HOTKEY")
        self._model, self._device_tag = value_row("MODEL", with_tag=True)
        self._microphone, _ = value_row("MICROPHONE")
        self._last, _ = value_row("LAST")

        # -- footer, popover only ------------------------------------------
        self._footer = None
        if show_footer:
            self._footer = QLabel("CLICK ANYWHERE TO CONFIGURE  →")
            self._footer.setObjectName("popoverFooter")
            outer.addWidget(self._footer)

    def apply(self, ui: UiState):
        """Push a UiState onto the labels. Cheap enough to call on every change."""
        key = effective_state(ui.state, ui.status_text)
        self._dot.set_state(key)
        self._state_tag.setText(key)
        self._headline.setText(ui.status_text or ui.state.capitalize())
        detail = ui.detail()
        self._detail.setText(detail)
        self._detail.setVisible(bool(detail))
        self._hotkey.setText(ui.hotkey)
        self._model.setText(ui.model)
        self._device_tag.setText(ui.device.upper() if ui.device else "")
        self._device_tag.setVisible(bool(ui.device))
        self._microphone.setText(ui.microphone)
        self._last.setText(ui.last)

    def paintEvent(self, event):
        """The dark ground, then section 9's corner marks over it."""
        super().paintEvent(event)
        self.paint_registration_marks()
