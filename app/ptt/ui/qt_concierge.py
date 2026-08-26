"""
Layer 4: the Concierge chat panel, docked right of the settings window.

`concierge_handoff.md` section 7 is the spec and mockup 5a is the picture. The
panel is a **renderer**: it holds a `ConciergeView`, which is a plain object with
no Qt in it, and paints whatever that object says. It computes no state of its
own, and in particular it does not compute the Concierge's *state* -- design 8's
machine owns those eight names and this file renders them verbatim, the same
split `qt_statusview.py` makes for the dictation banner.

That split is why the interesting half of this module is testable. `ConciergeView`
decides what a row is, when a streamed bubble is replaced by the settled reply,
which progress line a change chip has already said better, and whether a tool
result is a refusal; the widget below turns rows into frames. `tests/
test_concierge_panel.py` exercises the first of those with no `QApplication`.

**There is no thinking row.** Reasoning is disabled (`-rea off`, handoff section
1), so the former `thinking · N s` toggle from the mockups is gone; a future
reasoning-qualified model re-earns it through design section 6's record.

**Two confirmations exist here and nowhere else in this window** -- `session`
and `Delete model` -- which is `gui_handoff.md` section 6's allowance for
"deleting a vocabulary rule or a downloaded model" spent exactly once each.
Every other control applies instantly, like every other control in this window.

Where the streamed text comes from, and why it is provisional
-------------------------------------------------------------

`Agent.on_token` fires for each **content delta**. With `tool_mode: native` --
the qualified default (gate 2.5) -- a reply's content is the reply, so the live
bubble reads correctly as it arrives. With `tool_mode: grammar` the content is
the JSON decision envelope, so the live bubble shows JSON until the turn
settles. Either way `close_turn()` replaces the bubble with `Turn.reply`, which
is the authoritative text; the stream is a progress indicator that happens to be
readable, never the transcript of record.
"""

import json
from typing import NamedTuple

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from ptt.concierge import state as state_mod
from ptt.ui.qt_marks import RegistrationMarks

# -- row kinds ----------------------------------------------------------------
#
# The panel's whole vocabulary, and `concierge/sessions.py` stores these strings
# without knowing what any of them mean. Handoff section 7 names four of the
# five: user bubbles, agent bubbles, tool activity, change chips. `refusal` is
# the fifth and it is not decoration -- FR-CG-11 requires a rejected write to be
# reported *as a rejection*, and gate 2.5 made a refused `update_memory` an
# ordinary outcome rather than an exotic one (development_history.md #24).

USER = "user"
AGENT = "agent"
TOOL = "tool"
REFUSAL = "refusal"
CHANGE = "change"
NOTICE = "notice"

ROW_KINDS = (USER, AGENT, TOOL, REFUSAL, CHANGE, NOTICE)

#: Footer legend, handoff section 7, verbatim.
LEGEND = "Runs locally · no account · changes are undoable"

#: Panel geometry, handoff section 7.
DEFAULT_WIDTH = 360
MIN_WIDTH = 300
MAX_WIDTH = 520

#: How many lines the input grows to before it scrolls.
INPUT_MAX_LINES = 4

#: How much of the panel's width one bubble may take. Not 100 %, because a
#: transcript where both speakers use the full width stops looking like a
#: conversation and starts looking like a log.
BUBBLE_WIDTH_FRACTION = 0.82


class Row(NamedTuple):
    """
    One line of the transcript.

    `seq` is the undo journal's sequence number and is meaningful on `CHANGE`
    rows only; `undone` is what the chip's Undo button flips. Immutable, because
    the view replaces rows rather than mutating them -- which is what lets the
    widget diff its rendered copy against the current one and touch only what
    moved.
    """
    kind: str
    text: str
    detail: str = ""
    seq: int = 0
    undone: bool = False

    def to_dict(self):
        """The saved-transcript shape (`concierge/sessions.py`)."""
        return {"kind": self.kind, "text": self.text, "detail": self.detail}

    @classmethod
    def from_dict(cls, raw):
        kind = str(raw.get("kind", NOTICE))
        return cls(
            kind=kind if kind in ROW_KINDS else NOTICE,
            text=str(raw.get("text", "")),
            detail=str(raw.get("detail", "")),
        )


# -- narration ----------------------------------------------------------------

def value_text(value, limit=60):
    """
    One config value, as a chip shows it.

    JSON rather than `repr`, so a boolean reads `false` the way the user will
    type it into a question about it, and a hotkey reads `["ctrl", "alt"]`
    rather than as a Python tuple.
    """
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(value)
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def describe_tool(name, arguments):
    """
    What the Concierge is doing, in the words handoff section 7 uses.

    Present progressive and lower case, so the rendered line reads
    `Concierge is reading the log…`. An unregistered name still gets a sentence
    rather than an empty one: the panel narrates whatever the harness ran, and a
    tool this function has not heard of is exactly when the user most needs to
    be told something happened.
    """
    args = arguments if isinstance(arguments, dict) else {}
    if name == "get_config":
        key = args.get("key")
        return f"reading the {key} setting" if key else "reading the settings"
    if name == "set_config":
        return f"changing {args.get('key', 'a setting')}"
    if name == "get_state":
        return "checking what the application is doing"
    if name == "list_audio_devices":
        return "listing this machine's microphones"
    if name == "list_models":
        return "listing the Whisper models"
    if name == "run_benchmark":
        return (f"measuring {args.get('model', 'a model')} against the bundled "
                f"30-second clip")
    if name == "read_log":
        return "reading the log"
    if name == "update_memory":
        return "updating its memory note"
    return f"running {name}"


def summarise_result(name, result):
    """
    The muted figure a settled tool line carries, or "".

    Only where there is a number the user would otherwise have to take on trust.
    `run_benchmark` is the case that matters: the measurement is the entire
    point of the call, and a line that said only "Concierge is measuring…" would
    leave the answer visible nowhere but inside the model's next sentence.
    """
    if not isinstance(result, dict):
        return ""
    if name == "run_benchmark" and "seconds" in result:
        return f"{result['seconds']} s"
    if name == "read_log":
        lines = result.get("lines")
        if isinstance(lines, list):
            return f"{len(lines)} lines"
    if name == "list_audio_devices":
        devices = result.get("devices")
        if isinstance(devices, list):
            return f"{len(devices)} devices"
    if name == "update_memory" and "characters" in result:
        return f"{result['characters']} characters"
    return ""


def is_refusal(result):
    """
    Whether a tool result is the structured error `tools.error()` produces.

    One predicate, used for every tool, which is the point. The temptation with
    `update_memory` is to handle `{"ok": true}` and let anything else fall
    through to nothing on screen -- and since gate 2.5 the harness refuses a
    note that copies text out of `read_log` (development_history.md #24), so
    `{"error": true, "reason": …}` is an ordinary outcome of that tool and not a
    corner case. FR-CG-11 says a rejection is reported as a rejection.
    """
    return isinstance(result, dict) and bool(result.get("error"))


def refusal_row(name, result):
    """A refused tool call, as a row. The reason is the harness's, verbatim."""
    reason = ""
    hint = ""
    if isinstance(result, dict):
        reason = str(result.get("reason", "") or "")
        hint = str(result.get("hint", "") or "")
    headline = {
        "set_config": "That change was refused",
        "update_memory": "That memory note was refused",
    }.get(name, f"{name} was refused")
    detail = reason if not hint else f"{reason} — {hint}"
    return Row(REFUSAL, headline, detail=detail or "no reason was given")


def chip_text(kind, key, old, new):
    """
    A change chip's words. Handoff section 5: `use_gpu: false → true`.

    The memory note gets a length rather than its text: the note is up to a
    thousand tokens and a chip is one line, so quoting it would push the Undo
    button off the panel to say something the memory viewer already shows.
    """
    if kind == "memory":
        return (f"memory note: {len(old or '')} → {len(new or '')} characters")
    return f"{key}: {value_text(old)} → {value_text(new)}"


def state_caption(state, detail):
    """
    The one line under the header, given a state and the machine's free detail.

    The state name itself is rendered verbatim beside it (`state_tag`); this is
    the sentence that says what it means. `ready` is the one worth spelling out:
    design 8 defines it as "the next message will be fast", which is only true
    because `loading` covers the knowledge pack's prewarm.
    """
    if detail:
        return detail
    return {
        state_mod.DISABLED: "no CUDA device — the Concierge needs a GPU",
        state_mod.NOT_DOWNLOADED: "the model has not been downloaded yet",
        state_mod.DOWNLOADING: "downloading the model",
        state_mod.STOPPED: "not running — no VRAM held",
        state_mod.LOADING: "loading the model and warming the knowledge pack",
        state_mod.READY: "ready — the first message will be fast",
        state_mod.GENERATING: "answering",
        state_mod.UNLOADING: "releasing VRAM",
    }.get(state, "")


def status_segment(state, model_label, idle_minutes):
    """
    The settings window's status-bar segment, or "" when there is nothing to say.

    Handoff section 7 asks for it "absent when not downloaded/disabled", and
    `stopped` joins them: the segment exists to tell the user that VRAM is being
    held on the Concierge's account, and in all three of those states none is.
    """
    label = model_label or "the Concierge model"
    if state in (state_mod.READY, state_mod.GENERATING):
        try:
            minutes = int(idle_minutes)
        except (TypeError, ValueError):
            minutes = 0
        residency = ("unloads on close" if minutes <= 0
                     else f"unloads after {minutes} min idle")
        return f"Concierge: {label} resident · {residency}"
    if state == state_mod.LOADING:
        return f"Concierge: loading {label}"
    if state == state_mod.DOWNLOADING:
        return f"Concierge: downloading {label}"
    if state == state_mod.UNLOADING:
        return f"Concierge: unloading {label}"
    return ""


def can_send(state):
    """
    Whether the input accepts a message.

    `ready` comes from the state machine rather than from a literal here, so the
    two cannot drift. `generating` is added deliberately and is not a second
    opinion about `can_serve`: design 2 says a new send **cancels** the current
    generation, because `-np 1` means a concurrent request would either queue or
    land somewhere that re-pays the knowledge pack in full. So the panel accepts
    the message and the controller cancels what is running.
    """
    return state_mod.can_serve(state) or state == state_mod.GENERATING


def placeholder(state, detail=""):
    """What the empty input box says, which is also why it is empty."""
    if state == state_mod.READY:
        return "Ask about any setting, or tell me to change one"
    if state == state_mod.GENERATING:
        return "Send to interrupt and ask something else"
    if state == state_mod.LOADING:
        return "Loading — the first message will be fast"
    if state == state_mod.DISABLED:
        return detail or "the Concierge is disabled on this machine"
    if state == state_mod.NOT_DOWNLOADED:
        return "The model has not been downloaded yet"
    if state == state_mod.DOWNLOADING:
        return "Downloading the model — dictation is unaffected"
    return detail or "The Concierge is not running"


# -- the view model -----------------------------------------------------------

class ConciergeView:
    """
    Everything the panel draws, as plain Python.

    Held by the panel and mutated only from the GUI thread. The rules below are
    the ones worth stating, because each of them stops a row that would other-
    wise be there:

    1. A **streamed bubble is provisional**. It is discarded whenever a tool
       call, a chip or a progress line interrupts it, and replaced outright by
       the settled reply at the end of the turn.
    2. A **progress line is live**. `run_benchmark` takes seconds and says so
       while it runs; when the call settles, the lines it produced are replaced
       by one narrating row carrying the measurement.
    3. A **chip is the narration** of the call that produced it. `set_config`
       emits a change, then a progress line saying the same thing in worse
       words; the chip wins, because the chip has the Undo on it.
    """

    def __init__(self, state=state_mod.NOT_DOWNLOADED, detail="",
                 model_label="", idle_minutes=5, session_name=""):
        self.state = state
        self.detail = detail
        self.model_label = model_label
        self.idle_minutes = idle_minutes
        self.session_name = session_name
        self.rows = []
        self.memory_text = ""
        self.memory_has_previous = False
        #: Index of the agent bubble currently being streamed into, or None.
        self._streaming = None
        #: How many trailing rows are live progress lines (rule 2).
        self._pending = 0

    # -- state --------------------------------------------------------------

    def set_state(self, state, detail=""):
        self.state = state
        self.detail = detail or ""

    def caption(self):
        return state_caption(self.state, self.detail)

    def status_segment(self):
        return status_segment(self.state, self.model_label, self.idle_minutes)

    def can_send(self):
        return can_send(self.state)

    def placeholder(self):
        return placeholder(self.state, self.detail)

    # -- rows ---------------------------------------------------------------

    def clear(self):
        """A fresh session (FR-CG-13). The memory note survives; nothing else."""
        self.rows = []
        self._streaming = None
        self._pending = 0

    def _drop_pending(self):
        if self._pending:
            del self.rows[len(self.rows) - self._pending:]
            self._pending = 0

    def _close_stream(self, discard=False):
        if self._streaming is None:
            return
        index, self._streaming = self._streaming, None
        if discard and 0 <= index < len(self.rows):
            del self.rows[index]

    def add_user(self, text):
        """
        The user's message. **Discards a stream still in flight.**

        `close_turn` has already run if the previous turn finished, so this is a
        no-op in the ordinary case. The case it is not a no-op in is the one
        design 2 names: a new send *cancels* the current generation, and the
        panel appends the new question before the cancellation has reached the
        worker. Without the discard, two thirds of an abandoned answer stays
        stranded above the question that interrupted it.
        """
        self._drop_pending()
        self._close_stream(discard=True)
        self.rows.append(Row(USER, text))

    def add_token(self, text):
        """
        One content delta. Coalesced into the open agent bubble.

        Called about thirty times a second, so it appends to a string and does
        not rebuild anything -- the widget diffs and updates one label.
        """
        if not text:
            return
        if self._streaming is None:
            self.rows.append(Row(AGENT, text))
            self._streaming = len(self.rows) - 1
            return
        current = self.rows[self._streaming]
        self.rows[self._streaming] = current._replace(text=current.text + text)

    def add_progress(self, text):
        """
        A live line from the harness while a tool runs (rule 2).

        The harness's own sentence, capitalised, rather than glued behind
        "Concierge is": `tools.py` emits both tenses -- "measuring medium.en
        against the bundled clip" and "changed use_gpu to True" -- and a fixed
        prefix makes a sentence of one and nonsense of the other. The settled
        row that replaces this one carries the attribution.
        """
        self._close_stream(discard=True)
        text = str(text or "")
        self.rows.append(Row(TOOL, text[:1].upper() + text[1:] + "…"))
        self._pending += 1

    def add_tool(self, name, arguments, result):
        """
        One settled tool call (rules 1-3). Returns the row added, or None.

        The three outcomes are: a refusal, which is always shown; a call that
        already produced a chip, which is shown as the chip and nothing else;
        and everything else, which gets one narrating line.
        """
        self._drop_pending()
        self._close_stream(discard=True)
        if is_refusal(result):
            row = refusal_row(name, result)
            self.rows.append(row)
            return row
        if self.rows and self.rows[-1].kind == CHANGE:
            return None
        row = Row(TOOL, f"Concierge is {describe_tool(name, arguments)}…",
                  detail=summarise_result(name, result))
        self.rows.append(row)
        return row

    def add_change(self, seq, kind, key, old, new):
        """An Undo chip, one per journalled change (FR-CG-3)."""
        self._drop_pending()
        self._close_stream(discard=True)
        row = Row(CHANGE, chip_text(kind, key, old, new), seq=int(seq))
        self.rows.append(row)
        return row

    def add_notice(self, text):
        """A forced stop, a timeout, or anything else the harness had to say."""
        self._drop_pending()
        self._close_stream()
        self.rows.append(Row(NOTICE, text))

    def close_turn(self, reply, forced=""):
        """
        The authoritative end of one turn.

        `reply` replaces whatever was streamed, for the reason the module
        docstring gives. A cancelled turn carries no reply and leaves no bubble:
        the user who interrupted it is looking at their next message, not at
        half of an answer to the previous one.
        """
        reply = reply or ""
        if forced == "cancelled":
            self._close_stream(discard=True)
            self._drop_pending()
            return
        self._drop_pending()
        if self._streaming is not None:
            index, self._streaming = self._streaming, None
            self.rows[index] = self.rows[index]._replace(text=reply)
            if not reply:
                del self.rows[index]
            return
        if reply:
            self.rows.append(Row(AGENT, reply))

    def mark_undone(self, seq, ok, reason):
        """
        The outcome of one chip's Undo.

        A refused undo **stays pending** (`V-CG-40`…`V-CG-45`): the chip keeps
        its button, and the reason arrives as a notice, because a chip that
        greyed itself out after failing would claim a restore that did not
        happen.
        """
        for index, row in enumerate(self.rows):
            if row.kind == CHANGE and row.seq == int(seq):
                if ok:
                    self.rows[index] = row._replace(undone=True)
                break
        if not ok:
            self.add_notice(f"That could not be undone: {reason}")

    def pending_changes(self):
        """Chips a session restore would still have to put back."""
        return tuple(r for r in self.rows if r.kind == CHANGE and not r.undone)

    # -- saving -------------------------------------------------------------

    def save_payload(self):
        """The rows, in `concierge/sessions.py`'s shape."""
        return [row.to_dict() for row in self.rows]


# -- the widget ---------------------------------------------------------------

class GrowingInput(QPlainTextEdit):
    """
    The message box: one line that grows to four, Enter sends.

    A `QPlainTextEdit` rather than a `QLineEdit` because handoff section 7 asks
    for the growth, and rather than a `QTextEdit` because nothing here is rich
    text and a paste carrying formatting would arrive carrying formatting.

    Shift+Enter inserts a newline. That pairing is the one every chat input uses
    and it has to be stated somewhere, because the alternative -- Enter for a
    newline and a button for send -- makes the common action the slow one.
    """

    submitted = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("conciergeInput")
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setTabChangesFocus(True)
        self.document().documentLayout().documentSizeChanged.connect(
            lambda _size: self.updateGeometry())

    def keyPressEvent(self, event):
        enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        shifted = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if enter and not shifted:
            self.submitted.emit()
            return
        super().keyPressEvent(event)

    def _height_for(self, lines):
        metrics = self.fontMetrics()
        margins = self.contentsMargins()
        frame = int(self.frameWidth() * 2)
        return int(metrics.lineSpacing() * lines + margins.top()
                   + margins.bottom() + frame + 12)

    def sizeHint(self):
        """One line, or as many as the text needs, up to `INPUT_MAX_LINES`."""
        needed = max(1.0, self.document().size().height())
        return QSize(super().sizeHint().width(),
                     self._height_for(min(needed, INPUT_MAX_LINES)))

    def minimumSizeHint(self):
        return QSize(super().minimumSizeHint().width(), self._height_for(1))


def _wrapping_label(text, object_name):
    label = QLabel(text)
    label.setObjectName(object_name)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    return label


class RowWidget(QWidget):
    """
    One transcript row, and the way to update it without rebuilding it.

    `apply(row)` exists because the streaming bubble changes about thirty times
    a second: rebuilding a frame, a layout and a label at that rate inside a
    scroll area is a repaint storm, and the text is the only thing that moved.
    The panel rebuilds a row only when its *kind* changes, which never happens
    to a row that already exists.
    """

    def __init__(self, row, parent=None):
        super().__init__(parent)
        self.kind = row.kind
        self._label = None
        self._detail = None
        self._button = None
        self._bubble_frame = None
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        self._build(row, outer)
        self.apply(row)

    # -- construction, one branch per kind ----------------------------------

    def _build(self, row, outer):
        if row.kind == USER:
            outer.addStretch(1)
            outer.addWidget(self._bubble("userBubble"), 0)
            return
        if row.kind == AGENT:
            outer.addWidget(self._bubble("agentBubble"), 0)
            outer.addStretch(1)
            return
        if row.kind == CHANGE:
            frame = QFrame()
            frame.setObjectName("changeChip")
            inner = QHBoxLayout(frame)
            inner.setContentsMargins(9, 5, 6, 5)
            inner.setSpacing(8)
            self._label = _wrapping_label("", "changeChipText")
            self._button = QPushButton("Undo")
            self._button.setObjectName("undoChip")
            self._button.setCursor(Qt.CursorShape.PointingHandCursor)
            inner.addWidget(self._label, 1)
            inner.addWidget(self._button, 0)
            outer.addWidget(frame, 1)
            return
        if row.kind == REFUSAL:
            frame = QFrame()
            frame.setObjectName("refusalRow")
            inner = QVBoxLayout(frame)
            inner.setContentsMargins(9, 6, 9, 6)
            inner.setSpacing(2)
            self._label = _wrapping_label("", "refusalHeadline")
            self._detail = _wrapping_label("", "refusalDetail")
            inner.addWidget(self._label)
            inner.addWidget(self._detail)
            outer.addWidget(frame, 1)
            return
        # TOOL and NOTICE: one muted line, no frame around it.
        self._label = _wrapping_label(
            "", "toolLine" if row.kind == TOOL else "noticeLine")
        outer.addWidget(self._label, 1)

    def _bubble(self, object_name):
        frame = QFrame()
        frame.setObjectName(object_name)
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(10, 7, 10, 7)
        inner.setSpacing(0)
        self._label = _wrapping_label("", object_name + "Text")
        inner.addWidget(self._label)
        self._bubble_frame = frame
        return frame

    # -- update -------------------------------------------------------------

    def apply(self, row):
        """Push a row's current contents onto the widgets this row already has."""
        text = row.text
        if row.kind == TOOL and row.detail:
            text = f"{row.text}  {row.detail}"
        if self._label is not None:
            self._label.setText(text)
        if self._detail is not None:
            self._detail.setText(row.detail)
            self._detail.setVisible(bool(row.detail))
        if self._button is not None:
            self._button.setEnabled(not row.undone)
            self._button.setText("Undone" if row.undone else "Undo")

    def on_undo(self, handler):
        if self._button is not None:
            self._button.clicked.connect(handler)

    def set_bubble_width(self, pixels):
        """
        Cap a bubble at a fraction of the panel (`BUBBLE_WIDTH_FRACTION`).

        Applied from the transcript's `resizeEvent` rather than fixed at
        construction, because the splitter makes the panel's width a thing the
        user changes. A transcript where both speakers use the full width stops
        looking like a conversation and starts looking like a log.
        """
        if self._bubble_frame is not None and pixels > 0:
            self._bubble_frame.setMaximumWidth(pixels)


class Transcript(QScrollArea):
    """
    The scrolling list of rows, reconciled against a `ConciergeView`.

    Diffed rather than rebuilt, for `RowWidget.apply`'s reason. The diff is
    index-by-index and stops at the first row whose kind changed, which is
    correct because rows are only ever appended, replaced in place, or dropped
    from the end -- `ConciergeView` never inserts into the middle.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("conciergeScroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._body = QWidget()
        self._body.setObjectName("conciergeTranscript")
        self._layout = QVBoxLayout(self._body)
        self._layout.setContentsMargins(12, 12, 12, 12)
        self._layout.setSpacing(8)
        self._layout.addStretch(1)
        self.setWidget(self._body)

        self._rendered = []
        self._widgets = []
        self._on_undo = None

        #: Whether new rows should scroll into view. Set while the user is
        #: already at the bottom and cleared the moment they scroll up: a
        #: transcript that yanks itself back down while someone is re-reading
        #: an answer is worse than one that does not follow at all.
        self._follow = True
        self.verticalScrollBar().rangeChanged.connect(self._on_range_changed)

    def set_undo_handler(self, handler):
        self._on_undo = handler

    def _at_bottom(self):
        bar = self.verticalScrollBar()
        return bar.value() >= bar.maximum() - 4

    def _on_range_changed(self, _minimum, maximum):
        """
        Scroll after the layout has settled, not before.

        Inserting a widget does not resize the scroll area synchronously, so
        setting the scrollbar to its maximum inside `sync` moves it to the
        *old* maximum and leaves the newest row half off the bottom. The range
        signal is the event that says the new maximum exists.
        """
        if self._follow:
            self.verticalScrollBar().setValue(maximum)

    def sync(self, rows):
        """Make the widgets match `rows`. Cheap when only the last row moved."""
        self._follow = self._at_bottom()

        first_change = len(self._rendered)
        for index in range(min(len(rows), len(self._rendered))):
            if rows[index] == self._rendered[index]:
                continue
            if rows[index].kind == self._rendered[index].kind:
                self._widgets[index].apply(rows[index])
                self._rendered[index] = rows[index]
                continue
            first_change = index
            break
        else:
            first_change = min(len(rows), len(self._rendered))

        for index in range(len(self._widgets) - 1, first_change - 1, -1):
            widget = self._widgets.pop(index)
            self._layout.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        del self._rendered[first_change:]

        for row in rows[first_change:]:
            widget = RowWidget(row)
            if row.kind == CHANGE and self._on_undo is not None:
                seq = row.seq
                widget.on_undo(lambda _checked=False, seq=seq: self._on_undo(seq))
            self._layout.insertWidget(self._layout.count() - 1, widget)
            self._widgets.append(widget)
            self._rendered.append(row)

        self._apply_bubble_width()
        if self._follow:
            bar = self.verticalScrollBar()
            bar.setValue(bar.maximum())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_bubble_width()

    def _apply_bubble_width(self):
        pixels = int(self.viewport().width() * BUBBLE_WIDTH_FRACTION)
        for widget in self._widgets:
            widget.set_bubble_width(pixels)


class ConciergePanel(RegistrationMarks, QWidget):
    """
    The chat panel (handoff section 7, mockup 5a).

    Emits **intents** and renders **outcomes**. It never calls the harness, owns
    no thread, and holds no reference to a worker: `qt_concierge_worker.
    ConciergeController` is the only thing that connects the two, and it is the
    only place in the app where a cross-thread connection is made.
    """

    #: GUI -> controller. Every one of these is a request, not an action.
    send_requested = Signal(str)
    undo_requested = Signal(int)
    restore_requested = Signal()
    new_session_requested = Signal()
    save_session_requested = Signal(str)
    open_session_requested = Signal(str)
    memory_open_requested = Signal()
    memory_save_requested = Signal(str)
    memory_restore_requested = Signal()
    delete_model_requested = Signal()
    close_requested = Signal()

    def __init__(self, model_label="", parent=None):
        super().__init__(parent)
        self.setObjectName("conciergePanel")
        self.setMinimumWidth(MIN_WIDTH)
        self.setMaximumWidth(MAX_WIDTH)

        self.view = ConciergeView(model_label=model_label)
        self._saved = ()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header())

        self._pages = QStackedWidget()
        self._pages.addWidget(self._build_chat_page())
        self._pages.addWidget(self._build_memory_page())
        self._pages.addWidget(self._build_archive_page())
        outer.addWidget(self._pages, 1)

        self._render_header()

    # -- construction -------------------------------------------------------

    def _build_header(self):
        header = QFrame()
        header.setObjectName("conciergeHeader")
        box = QVBoxLayout(header)
        box.setContentsMargins(12, 10, 8, 8)
        box.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(8)
        brand = QLabel("CONCIERGE")
        brand.setObjectName("conciergeBrand")
        self._tag = QLabel("")
        self._tag.setObjectName("conciergeTag")
        top.addWidget(brand)
        top.addWidget(self._tag)
        top.addStretch(1)

        self._restore = self._header_button("↺ session", self._on_restore)
        self._restore.setToolTip(
            "Put back every setting the Concierge changed in this session")
        self._more = self._header_button("…", None)
        self._more.setToolTip("Sessions, the memory note, and the model file")
        self._more.setMenu(self._build_menu())
        close = self._header_button("×", self.close_requested.emit)
        close.setToolTip("Close the Concierge panel")
        for button in (self._restore, self._more, close):
            top.addWidget(button)
        box.addLayout(top)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self._name = QLineEdit()
        self._name.setObjectName("conciergeName")
        self._name.setPlaceholderText("Name this session")
        self._name.editingFinished.connect(
            lambda: setattr(self.view, "session_name", self._name.text().strip()))
        self._state_tag = QLabel("")
        self._state_tag.setObjectName("conciergeStateTag")
        bottom.addWidget(self._name, 1)
        bottom.addWidget(self._state_tag, 0)
        box.addLayout(bottom)

        self._caption = _wrapping_label("", "conciergeCaption")
        box.addWidget(self._caption)
        return header

    def _header_button(self, text, handler):
        button = QPushButton(text)
        button.setObjectName("conciergeHeaderButton")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFlat(True)
        if handler is not None:
            button.clicked.connect(lambda _checked=False: handler())
        return button

    def _build_menu(self):
        """
        The header's overflow menu.

        `Delete model` is here rather than on the Advanced tab (Q25): the
        download lives in this panel too, so one surface owns the model's whole
        lifecycle, and Advanced keeps the never-writes invariant `V-UI-12`
        checks.
        """
        menu = QMenu(self)
        menu.addAction("New session", self.new_session_requested.emit)
        menu.addAction("Save this session", self._on_save_session)
        self._saved_menu = menu.addMenu("Saved sessions")
        self._saved_menu.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Memory note…", self._on_open_memory)
        menu.addSeparator()
        self._delete_action = menu.addAction("Delete model…", self._on_delete_model)
        self._menu = menu
        return menu

    def _build_chat_page(self):
        page = QWidget()
        page.setObjectName("conciergeChatPage")
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)

        self._transcript = Transcript()
        self._transcript.set_undo_handler(self.undo_requested.emit)
        box.addWidget(self._transcript, 1)

        composer = QFrame()
        composer.setObjectName("conciergeComposer")
        row = QHBoxLayout(composer)
        row.setContentsMargins(12, 8, 12, 4)
        row.setSpacing(8)
        self._input = GrowingInput()
        self._input.submitted.connect(self._on_submit)
        self._send = QPushButton("Send")
        self._send.setObjectName("conciergeSend")
        self._send.clicked.connect(lambda _checked=False: self._on_submit())
        row.addWidget(self._input, 1)
        row.addWidget(self._send, 0, Qt.AlignmentFlag.AlignBottom)
        box.addWidget(composer)

        legend = QLabel(LEGEND)
        legend.setObjectName("conciergeLegend")
        legend.setContentsMargins(12, 0, 12, 8)
        box.addWidget(legend)
        return page

    def _build_memory_page(self):
        """
        The memory-note viewer (FR-CG-14, Q22).

        Editable, because the requirement says "viewable and editable by the
        user", and with `Restore previous` beside it, because the journal is
        session-scoped and the `.prev` file is the only thing that can repair a
        bad note discovered tomorrow.
        """
        page = QWidget()
        page.setObjectName("conciergeMemoryPage")
        box = QVBoxLayout(page)
        box.setContentsMargins(12, 12, 12, 12)
        box.setSpacing(8)

        title = QLabel("MEMORY NOTE")
        title.setObjectName("conciergeSectionTitle")
        box.addWidget(title)

        note = _wrapping_label(
            "What the Concierge remembers about you and this machine. It is "
            "loaded at the start of every session; nothing else from a previous "
            "conversation is.", "conciergeSectionNote")
        box.addWidget(note)

        self._memory_edit = QPlainTextEdit()
        self._memory_edit.setObjectName("conciergeMemoryEdit")
        box.addWidget(self._memory_edit, 1)

        self._memory_status = _wrapping_label("", "conciergeSectionNote")
        box.addWidget(self._memory_status)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        back = QPushButton("Back")
        back.clicked.connect(lambda _checked=False: self._show_page(0))
        self._memory_restore = QPushButton("Restore previous")
        self._memory_restore.clicked.connect(
            lambda _checked=False: self.memory_restore_requested.emit())
        save = QPushButton("Save note")
        save.setObjectName("conciergeSend")
        save.clicked.connect(
            lambda _checked=False: self.memory_save_requested.emit(
                self._memory_edit.toPlainText()))
        buttons.addWidget(back)
        buttons.addStretch(1)
        buttons.addWidget(self._memory_restore)
        buttons.addWidget(save)
        box.addLayout(buttons)
        return page

    def _build_archive_page(self):
        page = QWidget()
        page.setObjectName("conciergeArchivePage")
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)

        head = QFrame()
        head.setObjectName("conciergeArchiveHead")
        head_row = QHBoxLayout(head)
        head_row.setContentsMargins(12, 10, 12, 10)
        head_row.setSpacing(8)
        self._archive_title = QLabel("")
        self._archive_title.setObjectName("conciergeSectionTitle")
        back = QPushButton("Back")
        back.clicked.connect(lambda _checked=False: self._show_page(0))
        head_row.addWidget(self._archive_title, 1)
        head_row.addWidget(back, 0)
        box.addWidget(head)

        self._archive = Transcript()
        box.addWidget(self._archive, 1)
        return page

    # -- handlers -----------------------------------------------------------

    def _show_page(self, index):
        self._pages.setCurrentIndex(index)

    def _on_submit(self):
        text = self._input.toPlainText().strip()
        if not text or not self.view.can_send():
            return
        self._input.clear()
        self.send_requested.emit(text)

    def _on_save_session(self):
        self.save_session_requested.emit(self._name.text().strip())

    def _on_open_memory(self):
        self.memory_open_requested.emit()
        self._show_page(1)

    def _on_restore(self):
        """
        `↺ session`, behind the first of this window's two confirmations.

        The dialog says **which** changes, because Q24 exists precisely because
        the earlier design reverted the user's own panel edits behind a dialog
        that did not mention them.
        """
        pending = self.view.pending_changes()
        if not pending:
            self.append_notice("The Concierge has not changed anything this "
                               "session.")
            return
        answer = QMessageBox.question(
            self, "Restore this session's settings",
            f"Put back the {len(pending)} setting(s) the Concierge changed in "
            f"this session?\n\nOnly the settings it wrote are touched. Anything "
            f"you changed yourself in the tabs is left alone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Yes:
            self.restore_requested.emit()

    def _on_delete_model(self):
        """`Delete model`, behind the second of the two confirmations (Q25)."""
        answer = QMessageBox.question(
            self, "Delete the Concierge model",
            f"Delete the downloaded {self.view.model_label or 'Concierge'} "
            f"weights from this machine?\n\nThe Concierge stops working until "
            f"the file is downloaded again. Dictation is unaffected.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Yes:
            self.delete_model_requested.emit()

    # -- rendering ----------------------------------------------------------

    def _render_header(self):
        self._tag.setText(f"{self.view.model_label} · local"
                          if self.view.model_label else "local")
        self._state_tag.setText(self.view.state)
        self._caption.setText(self.view.caption())
        self._input.setPlaceholderText(self.view.placeholder())
        self._input.setEnabled(self.view.can_send())
        self._send.setEnabled(self.view.can_send())
        self._restore.setEnabled(bool(self.view.pending_changes()))

    def _render(self):
        self._transcript.sync(self.view.rows)
        self._render_header()

    # -- what the controller calls, all of it on the GUI thread -------------

    def set_model_label(self, label, size_gb=None):
        self.view.model_label = label
        if size_gb:
            self._delete_action.setText(f"Delete model ({size_gb:.2f} GB)…")
        self._render_header()

    def set_idle_minutes(self, minutes):
        self.view.idle_minutes = minutes
        self._render_header()

    def set_state(self, state, detail=""):
        self.view.set_state(state, detail)
        self._render_header()

    def append_token(self, text):
        self.view.add_token(text)
        self._render()

    def append_progress(self, text):
        self.view.add_progress(text)
        self._render()

    def append_tool(self, name, arguments, result):
        self.view.add_tool(name, arguments, result)
        self._render()

    def append_change(self, seq, kind, key, old, new):
        self.view.add_change(seq, kind, key, old, new)
        self._render()

    def append_notice(self, text):
        self.view.add_notice(text)
        self._render()

    def append_user(self, text):
        self.view.add_user(text)
        self._render()

    def close_turn(self, reply, forced=""):
        self.view.close_turn(reply, forced)
        self._render()

    def undo_finished(self, seq, ok, reason):
        self.view.mark_undone(seq, ok, reason)
        self._render()

    def new_session(self):
        self.view.clear()
        self._name.clear()
        self.view.session_name = ""
        self._show_page(0)
        self._render()

    def set_memory(self, text, has_previous):
        """
        The note, as the harness holds it.

        Only written into the editor when the user is not looking at it, so an
        `update_memory` landing mid-edit does not delete what the user is
        typing. The status line says what happened either way.
        """
        self.view.memory_text = text
        self.view.memory_has_previous = bool(has_previous)
        if self._pages.currentIndex() != 1 or not self._memory_edit.hasFocus():
            self._memory_edit.setPlainText(text)
        self._memory_restore.setEnabled(bool(has_previous))
        self._memory_status.setText(
            f"{len(text)} characters"
            + ("  ·  a previous version is kept" if has_previous
               else "  ·  no previous version yet"))

    def set_sessions(self, saved):
        """Rebuild the saved-sessions submenu from the store's listing."""
        self._saved = tuple(saved or ())
        self._saved_menu.clear()
        self._saved_menu.setEnabled(bool(self._saved))
        for entry in self._saved:
            label = f"{entry.name} · {entry.saved_at}"
            self._saved_menu.addAction(
                label,
                lambda _checked=False, sid=entry.id: self.open_session_requested.emit(sid))

    def show_saved_session(self, saved):
        """Render one stored transcript, read-only, on the archive page."""
        if saved is None:
            self.append_notice("That saved session could not be read.")
            return
        self._archive_title.setText(f"{saved.name} · {saved.saved_at}")
        self._archive.sync([Row.from_dict(raw) for raw in saved.rows])
        self._show_page(2)

    def session_name(self):
        return self._name.text().strip()

    def set_session_name(self, name):
        self._name.setText(name or "")
        self.view.session_name = name or ""

    def status_segment(self):
        return self.view.status_segment()

    def paintEvent(self, event):
        """The light ground, then section 9's corner marks over it."""
        super().paintEvent(event)
        self.paint_registration_marks()
