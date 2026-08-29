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

The five things this panel can be showing (session 4)
-----------------------------------------------------

A chat is only one of them, and the other four are not modes the user chooses.
`gate_for` decides, from the state machine plus the two opt-in keys, which of
them the panel is *for* right now -- and it is a pure function for the same
reason `state_caption` is: "why is there no chat here" is exactly the question a
screenshot cannot answer and a unit test can.

    disabled   no CUDA device, so nothing will ever run here (FR-CG-12)
    opt in     nobody has been asked yet (FR-CG-6, Q26's `unset`)
    off        asked and declined, or accepted and since switched off
    download   opted in, weights absent or arriving (FR-CG-7, handoff 8)
    chat       the panel section 7 describes

The user's own pages -- the memory note, a saved transcript, the Concierge's
settings -- sit on top of whichever of those is current and are left alone by a
state change, because a download finishing must not close the note somebody is
part-way through editing.
"""

import json
from typing import NamedTuple

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider,
    QStackedWidget, QVBoxLayout, QWidget,
)

from ptt import config
from ptt.concierge import fetch, state as state_mod
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

#: The residency slider's bounds (FR-CG-8). Read off `config.FIELDS` rather than
#: written here, because the field is validated against its own minimum and
#: maximum on every write and a slider that offered 45 would be a control whose
#: right-hand end is rejected.
#:
#: A `Field`'s bounds are optional, and `QAbstractSlider.setRange` takes two
#: ints, so the fallback is FR-CG-8's own numbers rather than `None` -- a field
#: that lost its bounds should leave the slider well-formed and let the write
#: path do the refusing. `V-CG-128` asserts the two agree, so the fallback can
#: never be silently in force.
_RESIDENCY_RULE = config.FIELDS["concierge.idle_unload_minutes"]
RESIDENCY_MIN = 0 if _RESIDENCY_RULE.minimum is None else int(_RESIDENCY_RULE.minimum)
RESIDENCY_MAX = 30 if _RESIDENCY_RULE.maximum is None else int(_RESIDENCY_RULE.maximum)

#: How much of the panel's width one bubble may take. Not 100 %, because a
#: transcript where both speakers use the full width stops looking like a
#: conversation and starts looking like a log.
BUBBLE_WIDTH_FRACTION = 0.82

#: The states in which the panel is *doing something the user is waiting for*.
#: They drive two things and nothing else: the indeterminate bar under the
#: header, and the amber the state tag turns. Everything else about a state
#: comes from the machine.
BUSY_STATES = (state_mod.LOADING, state_mod.DOWNLOADING, state_mod.GENERATING,
               state_mod.UNLOADING)


def is_busy(state):
    """Whether the panel should show that it is working. See `BUSY_STATES`."""
    return state in BUSY_STATES


def shows_busy_bar(state):
    """
    Whether the header's *indeterminate* bar is the right indicator.

    `downloading` is busy and does not get one: it is the only state that knows
    how far along it is, and the download card carries a determinate bar with
    the figures beside it. An indeterminate stripe above a bar reading 41 %
    claims less than the panel already knows.
    """
    return is_busy(state) and state != state_mod.DOWNLOADING


# -- what the panel is for right now ------------------------------------------

GATE_DISABLED = "disabled"
GATE_OPT_IN = "opt_in"
GATE_OFF = "off"
GATE_DOWNLOAD = "download"
GATE_CHAT = "chat"

#: Declaration order is precedence order, and the order is the argument.
GATES = (GATE_DISABLED, GATE_OPT_IN, GATE_OFF, GATE_DOWNLOAD, GATE_CHAT)

#: Indices into the panel's `QStackedWidget`, in the order they are added.
PAGE_CHAT, PAGE_MEMORY, PAGE_ARCHIVE, PAGE_SETTINGS = 0, 1, 2, 3
PAGE_OPT_IN, PAGE_DOWNLOAD, PAGE_BLOCKED = 4, 5, 6
PAGES = (PAGE_CHAT, PAGE_MEMORY, PAGE_ARCHIVE, PAGE_SETTINGS,
         PAGE_OPT_IN, PAGE_DOWNLOAD, PAGE_BLOCKED)

#: The three the *user* opens, which a state change may not close under them.
USER_PAGES = (PAGE_MEMORY, PAGE_ARCHIVE, PAGE_SETTINGS)

#: One gate, one page. Two gates share `PAGE_BLOCKED`; `blocked_card` is what
#: makes them different, and it is a function of the gate rather than of the
#: page for exactly that reason.
GATE_PAGES = {
    GATE_DISABLED: PAGE_BLOCKED,
    GATE_OPT_IN: PAGE_OPT_IN,
    GATE_OFF: PAGE_BLOCKED,
    GATE_DOWNLOAD: PAGE_DOWNLOAD,
    GATE_CHAT: PAGE_CHAT,
}


def gate_for(state, opt_in, enabled):
    """
    Which of the five things the panel is showing. Pure; see the module docstring.

    The precedence is the whole content of this function and each step of it is
    a decision:

    1. **No CUDA device wins outright** (FR-CG-12). Offering to download 6.87 GB
       for a runtime that cannot start would be the worst possible order, and
       asking somebody to opt in to it is barely better -- so a machine with no
       GPU is never asked, and `opt_in` stays `unset` on it, truthfully.
    2. **The opt-in card comes before the download** (Q26). `unset` means nobody
       has been asked; a Download button is not a question.
    3. **Declined or switched off** is a card, not a chat. `config.
       concierge_switched_on` owns that pair so the thread adapter, which
       refuses to launch the runtime, and this, which explains why, cannot
       disagree.
    4. **No weights, or weights arriving**, is the download card.
    5. Everything else is the chat.
    """
    if state == state_mod.DISABLED:
        return GATE_DISABLED
    if opt_in == config.OPT_IN_UNSET:
        return GATE_OPT_IN
    if not config.concierge_switched_on(opt_in, enabled):
        return GATE_OFF
    if state in (state_mod.NOT_DOWNLOADED, state_mod.DOWNLOADING):
        return GATE_DOWNLOAD
    return GATE_CHAT


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


# -- the download card (FR-CG-7, handoff 8) -----------------------------------

def download_caption(state, done, total, partial, refusal):
    """
    The line under the download card's heading. One sentence, four cases.

    A refusal is stated in full and is the only one of the four that does not
    end in an offer, because there is nothing to offer: the file published under
    that name is not the file this build was qualified against, and no button
    here can change that.
    """
    if refusal:
        return refusal
    if state == state_mod.DOWNLOADING:
        return fetch.progress_text(done, total)
    if partial:
        return (f"{fetch.human_bytes(partial)} is already downloaded. "
                f"Continuing picks up where it stopped.")
    return ("The Concierge runs a language model on this machine. The weights "
            "are downloaded once and never leave it.")


def download_button_text(state, partial, refusal):
    """
    What the card's one button says, or `""` when it should not be there.

    `""` is the refusal and nothing else: the button is then hidden *and*
    disabled, because a control the user is deliberately not able to click past
    must not still be clickable from a keyboard or from code.
    """
    if refusal:
        return ""
    if state == state_mod.DOWNLOADING:
        return "Pause"
    return "Continue the download" if partial else "Download"


def percent_downloaded(view):
    """
    The bar's position, `0`-`100`.

    Falls back to the partial file's share of the whole before a transfer
    starts, so a card offering `Continue the download` opens with the bar
    already where the last run left it rather than at zero.
    """
    if view.download_total and view.download_done:
        return fetch.percent_of(view.download_done, view.download_total)
    if view.download_partial and view.model_gigabytes:
        return fetch.percent_of(view.download_partial,
                                view.model_gigabytes * (1024 ** 3))
    return 0


def can_download(state, refusal):
    """
    Whether the download may be *started* right now.

    A refusal is latched by the harness and reported here as a control that is
    not offered at all rather than one that is offered and fails: FR-CG-7 calls
    a digest mismatch a re-qualification event, and a Retry button would invite
    the user to click past exactly the thing the check exists to stop (Q26).
    """
    return (not refusal) and state == state_mod.NOT_DOWNLOADED


def can_send(state):
    """
    Whether the input accepts a message.

    `ready` comes from the state machine rather than from a literal here, so the
    two cannot drift. Two states are added deliberately and neither is a second
    opinion about `can_serve`, which answers "can this message be served *now*":

    - `generating`, because design 2 says a new send **cancels** the current
      generation -- with `-np 1` a concurrent request would either queue or land
      somewhere that re-pays the knowledge pack in full. The panel takes the
      message and the controller cancels what is running.
    - `stopped`, because the residency timer unloads the runtime after N minutes
      **whether or not the panel is open** (FR-CG-8), and the panel it leaves
      behind had no way back: every control that starts the runtime is on the
      open/close path, so the user had to close the panel and reopen it. Typing
      the next question is the obvious way to ask for it back, so that is what
      it now does -- the controller starts the runtime, then sends.
    """
    return (state_mod.can_serve(state)
            or state in (state_mod.GENERATING, state_mod.STOPPED))


# -- the card that says why there is no chat ----------------------------------

#: FR-CG-12, in the Model tab's own words. Criterion v2-7's pattern is not a
#: layout, it is this: name the hardware fact, say what the consequence is, and
#: point at the tab that has the detail. Copied in shape, not in wording, because
#: the consequence is a different one -- the Model tab degrades to the CPU and
#: this does not run at all.
NO_CUDA_TEXT = (
    "No CUDA device was found, so the Concierge cannot run on this machine. It "
    "needs a GPU to hold a 12-billion-parameter model; there is no CPU fallback, "
    "because one would answer a question in several minutes.\n\n"
    "Dictation is unaffected — it falls back to the CPU on its own. See the "
    "Diagnostics tab.")

OFF_TEXT = (
    "The Concierge is switched off. Nothing about dictation changes while it is: "
    "no model is downloaded, no process runs, and no VRAM is held.\n\n"
    "Turning it on downloads a 6.87 GB model once, and you can switch it off "
    "again from this panel at any time.")


def blocked_card(gate, detail=""):
    """
    `(heading, body, button)` for the card that stands in for the chat.

    One page rather than two, because the two differ only in their words and in
    whether there is a way out: the no-CUDA case has no button, and a page that
    renders a disabled button beside "cannot run on this machine" would be
    offering something.
    """
    if gate == GATE_DISABLED:
        # The machine's detail is appended rather than substituted. It is one
        # short clause and the card is the only place the consequence and the
        # pointer to Diagnostics are stated, so replacing the paragraph with the
        # clause would trade the explanation for the reason.
        return ("THE CONCIERGE IS UNAVAILABLE",
                f"{NO_CUDA_TEXT}\n\n{detail}" if detail else NO_CUDA_TEXT,
                "")
    return ("THE CONCIERGE IS OFF", OFF_TEXT, "Turn the Concierge on")


def residency_text(minutes):
    """
    What the residency slider's value means, in a sentence (FR-CG-8).

    `0` is not "unload immediately" and the slider must not let anybody read it
    that way: it is the one value whose meaning is a different event entirely --
    the panel closing rather than a timer expiring -- and the harness's idle
    timer explicitly declines to own it (`Server.start_idle_timer`).
    """
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        minutes = 0
    if minutes <= 0:
        return "Unloads as soon as this panel is closed."
    return (f"Unloads {minutes} minute{'s' if minutes != 1 else ''} after the "
            f"last message, or when the application exits.")


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
    if state == state_mod.STOPPED:
        return "Send to start the Concierge — it takes about ten seconds"
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
                 model_label="", idle_minutes=5, session_name="",
                 opt_in=config.OPT_IN_ACCEPTED, enabled=True):
        self.state = state
        self.detail = detail
        self.model_label = model_label
        self.model_gigabytes = 0.0
        self.idle_minutes = idle_minutes
        self.session_name = session_name
        #: Q26's tri-state and the switch beside it. Held here rather than read
        #: from `Settings` because this object is the panel's whole truth and a
        #: widget that reached past it for one value would be the split this
        #: class exists to make.
        self.opt_in = opt_in
        self.enabled = bool(enabled)
        #: The download's two numbers, the resumable remainder on disk, and the
        #: latched refusal. `download_refusal` is never cleared here: the
        #: harness latches it and the panel renders the latch (FR-CG-7, Q26).
        self.download_done = 0
        self.download_total = 0
        self.download_partial = 0
        self.download_refusal = ""
        self.rows = []
        self.memory_text = ""
        self.memory_has_previous = False
        #: Index of the agent bubble currently being streamed into, or None.
        self._streaming = None
        #: How many trailing rows are live progress lines (rule 2).
        self._pending = 0
        #: Whether the chip at the end of `rows` belongs to the tool call that
        #: has not settled yet (rule 3). Without it, the *next* call's narration
        #: is absorbed too: a turn that changes a setting and then measures
        #: something shows the chip and never says it measured anything.
        self._chip_pending = False

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

    # -- the gate (session 4) -----------------------------------------------

    def gate(self):
        """Which of the five things this panel is for. See `gate_for`."""
        return gate_for(self.state, self.opt_in, self.enabled)

    def set_download(self, done, total):
        self.download_done = int(done or 0)
        self.download_total = int(total or 0)

    def download_caption(self):
        return download_caption(self.state, self.download_done,
                                self.download_total, self.download_partial,
                                self.download_refusal)

    def download_button_text(self):
        return download_button_text(self.state, self.download_partial,
                                    self.download_refusal)

    def can_download(self):
        return can_download(self.state, self.download_refusal)

    def blocked_card(self):
        return blocked_card(self.gate(), self.detail)

    def residency_text(self):
        return residency_text(self.idle_minutes)

    # -- rows ---------------------------------------------------------------

    def clear(self):
        """A fresh session (FR-CG-13). The memory note survives; nothing else."""
        self.rows = []
        self._streaming = None
        self._pending = 0
        self._chip_pending = False

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
        self._chip_pending = False
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
        absorbed, self._chip_pending = self._chip_pending, False
        if is_refusal(result):
            row = refusal_row(name, result)
            self.rows.append(row)
            return row
        if absorbed and self.rows and self.rows[-1].kind == CHANGE:
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
        self._chip_pending = True
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
    #: session 4. `opt_in_requested(True)` is the accept, `(False)` the decline
    #: -- one signal rather than two, because the card asks one question and the
    #: controller writes one key (Q26).
    opt_in_requested = Signal(bool)
    #: `concierge.enabled`, which had no control anywhere before session 4 --
    #: the same gap the residency slider had, and the reason the off card can
    #: honestly say "you can switch it off again from this panel".
    enabled_requested = Signal(bool)
    download_requested = Signal()
    pause_download_requested = Signal()
    residency_requested = Signal(int)
    setup_requested = Signal()

    #: A transient one-line note for the window's status bar, exactly as
    #: `InstantApplyPanel.message` is. Paired with a notice row rather than
    #: replacing it: the transcript is the record, the status bar is what makes
    #: an outcome with no other visible effect -- a Save that found nothing to
    #: save -- something the user actually notices.
    message = Signal(str)

    def __init__(self, model_label="", parent=None):
        super().__init__(parent)
        self.setObjectName("conciergePanel")
        self.setMinimumWidth(MIN_WIDTH)
        self.setMaximumWidth(MAX_WIDTH)

        self.view = ConciergeView(model_label=model_label)
        self._saved = ()
        #: Guards the residency slider while it is being written *from* the
        #: settings object, so a programmatic set is not read back as a drag and
        #: written straight to disk -- `ModelPanel._syncing`'s argument, one
        #: control down.
        self._syncing = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header())

        # Added in `PAGES` order, which is what makes the constants above
        # indices rather than a second list to keep in step.
        self._pages = QStackedWidget()
        builders = {
            PAGE_CHAT: self._build_chat_page,
            PAGE_MEMORY: self._build_memory_page,
            PAGE_ARCHIVE: self._build_archive_page,
            PAGE_SETTINGS: self._build_settings_page,
            PAGE_OPT_IN: self._build_opt_in_page,
            PAGE_DOWNLOAD: self._build_download_page,
            PAGE_BLOCKED: self._build_blocked_page,
        }
        for index in PAGES:
            self._pages.addWidget(builders[index]())
        outer.addWidget(self._pages, 1)

        self._render_header()
        # The cards too, not just the page choice: a panel built and never
        # handed a controller -- which is every path in `qt_window.py` -- would
        # otherwise show three blank cards.
        self._render_cards()

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
        # Right-aligned explicitly, because the name field is hidden on the
        # gate pages (there is no session to name on an opt-in card) and a tag
        # that jumped to the left margin whenever it did would read as a
        # different control.
        bottom.addWidget(self._state_tag, 0, Qt.AlignmentFlag.AlignRight)
        box.addLayout(bottom)

        self._caption = _wrapping_label("", "conciergeCaption")
        box.addWidget(self._caption)

        # An indeterminate bar, shown only while the panel is working. The
        # states are honest about what is happening -- `loading`, `generating`
        # -- but a word that does not move is a word that might be stale, and
        # the two things it covers take ten seconds and several seconds. Range
        # 0..0 is Qt's indeterminate mode; it animates itself.
        self._busy = QProgressBar()
        self._busy.setObjectName("conciergeBusy")
        self._busy.setRange(0, 0)
        self._busy.setTextVisible(False)
        self._busy.setFixedHeight(2)
        self._busy.hide()
        box.addWidget(self._busy)
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

        Held on `self` as well as parented, for the reason `qt_tray._build_menu`
        gives: `QPushButton::setMenu` does not take ownership, and a menu with
        no reference is collected out from under the button that shows it.
        """
        menu = QMenu(self)
        menu.addAction("New session", self.new_session_requested.emit)
        menu.addAction("Save this session", self._on_save_session)
        self._saved_menu = menu.addMenu("Saved sessions")
        self._saved_menu.setEnabled(False)
        menu.addSeparator()
        self._setup_action = menu.addAction("Guided setup", self._on_setup)
        menu.addAction("Memory note…", self._on_open_memory)
        menu.addAction("Concierge settings…",
                       lambda: self._show_page(PAGE_SETTINGS))
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
            "Every conversation starts from nothing — the Concierge does not "
            "read old chats. This note is the one exception: a few lines it "
            "keeps about you and this machine, loaded at the start of every "
            "session, so you are not introducing yourself each time. "
            "Ask it to remember something (try ‘remember that I use a Jabra "
            "headset’) and it writes here. You can edit or clear it yourself, "
            "and every version it writes leaves the one before it recoverable.",
            "conciergeSectionNote")
        box.addWidget(note)

        self._memory_edit = QPlainTextEdit()
        self._memory_edit.setObjectName("conciergeMemoryEdit")
        box.addWidget(self._memory_edit, 1)

        self._memory_status = _wrapping_label("", "conciergeSectionNote")
        box.addWidget(self._memory_status)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        back = QPushButton("Back")
        back.clicked.connect(lambda _checked=False: self._show_base_page())
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
        back.clicked.connect(lambda _checked=False: self._show_base_page())
        head_row.addWidget(self._archive_title, 1)
        head_row.addWidget(back, 0)
        box.addWidget(head)

        self._archive = Transcript()
        box.addWidget(self._archive, 1)
        return page

    # -- the four session-4 pages -------------------------------------------

    def _card_page(self, object_name):
        """
        The shell the three cards share: heading, body, buttons, centred.

        A card rather than a dialog, and inside the panel rather than over the
        window, because `gui_handoff.md` section 6 spends this window's entire
        modal budget on two destructive confirmations. "Would you like the
        Concierge?" is not one of them, and a modal at first run would be the
        one thing every v2.0 user sees before anything they asked for.
        """
        page = QWidget()
        page.setObjectName(object_name)
        box = QVBoxLayout(page)
        box.setContentsMargins(18, 18, 18, 18)
        box.setSpacing(10)
        box.addStretch(1)
        title = QLabel("")
        title.setObjectName("conciergeSectionTitle")
        title.setWordWrap(True)
        body = _wrapping_label("", "conciergeCardBody")
        # Selectable, for the refusal above all: a digest mismatch is evidence
        # somebody has to be able to copy into a bug report, and the panel is
        # where they are looking when it happens.
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(title)
        box.addWidget(body)
        return page, box, title, body

    def _build_opt_in_page(self):
        """
        The first-run card (FR-CG-6, handoff 8.1). Asked once, answered once.

        Both answers are final in the sense Q26 means: `accepted` and `declined`
        are both written to `concierge.opt_in`, and neither leaves the card able
        to come back. Declining is **not** a dead end -- the Concierge entries
        stay in the tray menu and the tab strip, and opening the panel then
        shows the off card with a way back in -- but nothing asks again.
        """
        page, box, title, body = self._card_page("conciergeOptInPage")
        title.setText("MEET THE CONCIERGE")
        body.setText(
            "PTT Dictation 3.0 can run a local assistant beside these settings. "
            "Ask it what a setting does and it explains; tell it to change one "
            "and it does, with an Undo beside every change.\n\n"
            "It runs on this machine: no account, no subscription, and nothing "
            "you type or dictate leaves the computer. Saying yes downloads a "
            "6.87 GB model once, in the background, while dictation carries on "
            "as normal.")
        self._opt_in_note = _wrapping_label("", "conciergeSectionNote")
        box.addWidget(self._opt_in_note)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        decline = QPushButton("No thanks")
        decline.clicked.connect(
            lambda _checked=False: self.opt_in_requested.emit(False))
        accept = QPushButton("Yes — set it up")
        accept.setObjectName("conciergeSend")
        accept.clicked.connect(
            lambda _checked=False: self.opt_in_requested.emit(True))
        buttons.addStretch(1)
        buttons.addWidget(decline)
        buttons.addWidget(accept)
        box.addLayout(buttons)
        box.addWidget(_wrapping_label(
            "You are asked this once. Either way, the Concierge stays in the "
            "tray menu and at the end of the tab strip, so you can change your "
            "mind later.", "conciergeSectionNote"))
        box.addStretch(2)
        return page

    def _build_download_page(self):
        """
        The download card (FR-CG-7, handoff 8.2, mockup 5b).

        A **determinate** bar, because the total is known before the first byte:
        the pinned spec carries the exact size, so a resumed transfer opens at
        41 % rather than at 0 % and the user can see it resumed.
        """
        page, box, title, body = self._card_page("conciergeDownloadPage")
        self._download_title = title
        self._download_body = body
        title.setText("THE CONCIERGE MODEL")

        self._download_bar = QProgressBar()
        self._download_bar.setObjectName("conciergeDownloadBar")
        self._download_bar.setRange(0, 100)
        self._download_bar.setTextVisible(False)
        self._download_bar.setFixedHeight(4)
        box.addWidget(self._download_bar)

        self._download_note = _wrapping_label(
            "Dictation is unaffected while this runs, and the download picks up "
            "where it left off if the application is closed part-way.",
            "conciergeSectionNote")
        box.addWidget(self._download_note)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self._download_button = QPushButton("Download")
        self._download_button.setObjectName("conciergeSend")
        self._download_button.clicked.connect(self._on_download_clicked)
        buttons.addStretch(1)
        buttons.addWidget(self._download_button)
        box.addLayout(buttons)
        box.addStretch(2)
        return page

    def _build_blocked_page(self):
        """The no-CUDA card and the switched-off card. See `blocked_card`."""
        page, box, title, body = self._card_page("conciergeBlockedPage")
        self._blocked_title = title
        self._blocked_body = body

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self._blocked_button = QPushButton("")
        self._blocked_button.setObjectName("conciergeSend")
        self._blocked_button.clicked.connect(
            lambda _checked=False: self.opt_in_requested.emit(True))
        buttons.addStretch(1)
        buttons.addWidget(self._blocked_button)
        box.addLayout(buttons)
        box.addStretch(2)
        return page

    def _build_settings_page(self):
        """
        The Concierge's own controls (FR-CG-8, Q25).

        On this panel and nowhere else, which is what the shipped knowledge pack
        already tells the user -- `concierge_narrative.md` lists the residency
        slider, `Delete model`, Undo, the memory note and sessions as "its
        controls, all on this panel". Putting the slider on the Advanced tab
        would have made the Concierge's own answer about itself wrong, and would
        have broken that tab's never-writes invariant (`V-UI-12`) besides.
        """
        page = QWidget()
        page.setObjectName("conciergeSettingsPage")
        box = QVBoxLayout(page)
        box.setContentsMargins(12, 12, 12, 12)
        box.setSpacing(8)

        title = QLabel("CONCIERGE SETTINGS")
        title.setObjectName("conciergeSectionTitle")
        box.addWidget(title)

        box.addSpacing(4)
        residency = QLabel("VRAM RESIDENCY")
        residency.setObjectName("caption")
        box.addWidget(residency)
        box.addWidget(_wrapping_label(
            "How long the language model stays in video memory after the last "
            "message. Resident and idle it costs dictation nothing measurable; "
            "while it is answering, a dictation takes about 1.5 times as long.",
            "conciergeSectionNote"))

        row = QHBoxLayout()
        row.setSpacing(10)
        self._residency = QSlider(Qt.Orientation.Horizontal)
        self._residency.setObjectName("conciergeResidency")
        self._residency.setRange(RESIDENCY_MIN, RESIDENCY_MAX)
        self._residency.setPageStep(5)
        self._residency.setTickPosition(QSlider.TickPosition.NoTicks)
        self._residency.valueChanged.connect(self._on_residency_changed)
        self._residency.sliderReleased.connect(self._on_residency_settled)
        self._residency_value = QLabel("")
        self._residency_value.setObjectName("conciergeResidencyValue")
        self._residency_value.setMinimumWidth(58)
        self._residency_value.setAlignment(Qt.AlignmentFlag.AlignRight
                                           | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._residency, 1)
        row.addWidget(self._residency_value, 0)
        box.addLayout(row)

        self._residency_note = _wrapping_label("", "conciergeSectionNote")
        box.addWidget(self._residency_note)

        box.addSpacing(12)
        model = QLabel("MODEL")
        model.setObjectName("caption")
        box.addWidget(model)
        self._settings_model = _wrapping_label("", "conciergeSectionNote")
        box.addWidget(self._settings_model)

        box.addSpacing(12)
        switch = QLabel("THE CONCIERGE ITSELF")
        switch.setObjectName("caption")
        box.addWidget(switch)
        box.addWidget(_wrapping_label(
            "Switching it off stops the runtime and leaves every other part of "
            "the application exactly as it is. The downloaded model stays on "
            "disk; delete it below to reclaim the space as well.",
            "conciergeSectionNote"))
        self._settings_off = QPushButton("Switch the Concierge off")
        self._settings_off.clicked.connect(
            lambda _checked=False: self.enabled_requested.emit(False))
        off_row = QHBoxLayout()
        off_row.addWidget(self._settings_off)
        off_row.addStretch(1)
        box.addLayout(off_row)

        box.addStretch(1)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        back = QPushButton("Back")
        back.clicked.connect(lambda _checked=False: self._show_base_page())
        self._settings_delete = QPushButton("Delete model")
        self._settings_delete.clicked.connect(
            lambda _checked=False: self._on_delete_model())
        buttons.addWidget(back)
        buttons.addStretch(1)
        buttons.addWidget(self._settings_delete)
        box.addLayout(buttons)
        return page

    # -- handlers -----------------------------------------------------------

    def _show_page(self, index):
        self._pages.setCurrentIndex(index)

    def _base_page(self):
        """The page the current gate asks for. See `gate_for`."""
        return GATE_PAGES[self.view.gate()]

    def _show_base_page(self):
        self._show_page(self._base_page())

    def _sync_page(self):
        """
        Put the right page up, without closing one the user opened.

        A download that finishes while somebody is reading the memory note must
        not throw them out of it; the note's own `Back` is what returns them,
        and by then it returns them to the chat.
        """
        if self._pages.currentIndex() in USER_PAGES:
            return
        self._show_base_page()

    def _on_download_clicked(self):
        if self.view.state == state_mod.DOWNLOADING:
            self.pause_download_requested.emit()
        elif self.view.can_download():
            self.download_requested.emit()

    def _on_setup(self):
        """`Guided setup` from the menu (FR-CG-4)."""
        if self.view.can_send():
            self.setup_requested.emit()
        else:
            self.notify("The Concierge has to be running before it can walk "
                        "you through setup.")

    def _on_residency_changed(self, value):
        """
        Live label on every tick; a write only when the drag has settled.

        `config.json` is rewritten and broadcast on every accepted write, and a
        slider dragged from 0 to 30 emits thirty-one of them. The label follows
        the thumb because a slider whose readout lags is a slider nobody trusts;
        the write waits for `sliderReleased`, or happens immediately when the
        value moved by keyboard, where there is no drag to end.
        """
        self._residency_value.setText(f"{int(value)} min" if value else "on close")
        self._residency_note.setText(residency_text(value))
        if self._syncing or self._residency.isSliderDown():
            return
        self.residency_requested.emit(int(value))

    def _on_residency_settled(self):
        if not self._syncing:
            self.residency_requested.emit(int(self._residency.value()))

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
        self._show_page(PAGE_MEMORY)

    def _confirm(self, title, text):
        """
        One helper for both, because the only thing that differs between them
        is the words. Parented, so the box is modal to this window and centred
        on it; `Cancel` is the default button, so a stray Return key cannot
        delete 6.87 GB.
        """
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(QMessageBox.StandardButton.Yes
                               | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return box.exec() == QMessageBox.StandardButton.Yes

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
        if self._confirm(
                "Restore this session's settings",
                f"Put back the {len(pending)} setting(s) the Concierge changed in "
                f"this session?\n\nOnly the settings it wrote are touched. Anything "
                f"you changed yourself in the tabs is left alone."):
            self.restore_requested.emit()

    def _on_delete_model(self):
        """`Delete model`, behind the second of the two confirmations (Q25)."""
        if self._confirm(
                "Delete the Concierge model",
                f"Delete the downloaded {self.view.model_label or 'Concierge'} "
                f"weights from this machine?\n\nThe Concierge stops working until "
                f"the file is downloaded again. Dictation is unaffected."):
            self.delete_model_requested.emit()

    # -- rendering ----------------------------------------------------------

    def _render_header(self):
        self._tag.setText(f"{self.view.model_label} · local"
                          if self.view.model_label else "local")
        self._state_tag.setText(self.view.state)
        # The tag's colour comes from the stylesheet, selected on this dynamic
        # property -- the same indirection `StatusDot` uses, and for the same
        # reason: no colour lives in Python. `qproperty-` and selectors on a
        # dynamic property are both resolved at polish time, so changing the
        # property means re-polishing.
        if self._state_tag.property("state") != self.view.state:
            self._state_tag.setProperty("state", self.view.state)
            style = self._state_tag.style()
            style.unpolish(self._state_tag)
            style.polish(self._state_tag)
        self._busy.setVisible(shows_busy_bar(self.view.state))
        self._caption.setText(self.view.caption())
        self._input.setPlaceholderText(self.view.placeholder())
        self._input.setEnabled(self.view.can_send())
        self._send.setEnabled(self.view.can_send())
        self._setup_action.setEnabled(self.view.can_send())
        # Disabled when there is nothing to put back, with a tooltip that says
        # so: a greyed control that does not explain its greying is the failure
        # `gui_handoff.md` section 6 names for a disabled button, and Qt does
        # not deliver hover events to a disabled widget's *children*, only to
        # the widget, so the tooltip is the one channel that still works.
        pending = self.view.pending_changes()
        self._restore.setEnabled(bool(pending))
        self._restore.setToolTip(
            f"Put back the {len(pending)} setting(s) the Concierge changed in "
            f"this session" if pending
            else "Nothing to put back: the Concierge has not changed anything "
                 "this session")
        # The two session controls are hidden, not disabled, on the gate pages.
        # There is no session on an opt-in card and there never was one, so a
        # greyed control would be claiming something is temporarily unavailable
        # that has never applied here.
        chatting = self.view.gate() == GATE_CHAT
        self._name.setVisible(chatting)
        self._restore.setVisible(chatting)

    def _render_cards(self):
        """
        The three gate pages, repainted from the view. Cheap and unconditional.

        Three labels and a progress bar; running it on every state change costs
        nothing and removes the whole class of bug where a card is correct
        except on the path that forgot to refresh it.
        """
        gate = self.view.gate()
        heading, body, button = self.view.blocked_card()
        self._blocked_title.setText(heading)
        self._blocked_body.setText(body)
        self._blocked_button.setText(button)
        self._blocked_button.setVisible(bool(button))

        self._download_body.setText(self.view.download_caption())
        # Hidden **and** disabled when there is nothing to offer. Qt delivers
        # `click()` to a hidden-but-enabled button, so hiding alone would leave
        # the one control FR-CG-7 says the user must not be able to click past
        # reachable from code and from a keyboard shortcut.
        label = self.view.download_button_text()
        self._download_button.setText(label or "Download")
        self._download_button.setVisible(bool(label))
        self._download_button.setEnabled(bool(label))
        self._download_note.setVisible(not self.view.download_refusal)
        downloading = self.view.state == state_mod.DOWNLOADING
        self._download_bar.setVisible(downloading or bool(self.view.download_partial))
        self._download_bar.setValue(percent_downloaded(self.view))

        size = (f"about {self.view.model_gigabytes:.2f} GB"
                if self.view.model_gigabytes else "about 6.9 GB")
        self._download_title.setText(
            f"THE CONCIERGE MODEL · {self.view.model_label or 'local'}")
        self._opt_in_note.setText(
            f"Download size {size}. It is fetched once and kept on this machine.")

        self._settings_model.setText(
            f"{self.view.model_label or 'The Concierge model'} — {size}, "
            f"currently {self.view.state.replace('_', ' ')}.")
        self._settings_delete.setEnabled(
            self.view.state not in (state_mod.NOT_DOWNLOADED,
                                    state_mod.DOWNLOADING,
                                    state_mod.DISABLED))
        self._settings_off.setEnabled(self.view.enabled
                                      and self.view.state != state_mod.DISABLED)
        self._sync_page()
        return gate

    def _render(self):
        self._transcript.sync(self.view.rows)
        self._render_header()
        self._render_cards()

    # -- what the controller calls, all of it on the GUI thread -------------

    def set_model_label(self, label, size_gb=None):
        self.view.model_label = label
        if size_gb:
            self.view.model_gigabytes = float(size_gb)
            self._delete_action.setText(f"Delete model ({size_gb:.2f} GB)…")
            self._settings_delete.setText(f"Delete model ({size_gb:.2f} GB)")
        self._render_header()
        self._render_cards()

    def set_idle_minutes(self, minutes):
        """
        The residency setting, from `config.json`. Writes the slider, guarded.

        `_syncing` is what stops this becoming a write of its own: the slider's
        `valueChanged` fires on a programmatic set exactly as it does on a drag,
        and without the guard the broadcast that follows a Concierge-made write
        would bounce straight back through `Settings.set` (FR-CG-2's hop is a
        loop unless somebody breaks it).
        """
        self.view.idle_minutes = minutes
        self._syncing = True
        try:
            value = int(minutes)
        except (TypeError, ValueError):
            value = 0
        try:
            self._residency.setValue(max(RESIDENCY_MIN, min(RESIDENCY_MAX, value)))
            self._on_residency_changed(self._residency.value())
        finally:
            self._syncing = False
        self._render_header()

    def set_opt_in(self, opt_in, enabled=True):
        """The two keys that decide whether there is a chat here at all (Q26)."""
        self.view.opt_in = opt_in
        self.view.enabled = bool(enabled)
        self._render_header()
        self._render_cards()

    def set_download_progress(self, done, total):
        self.view.set_download(done, total)
        self._render_cards()

    def set_download_partial(self, partial):
        """How much of a resumable transfer is on disk (criterion v3-5)."""
        self.view.download_partial = int(partial or 0)
        self._render_cards()

    def set_download_refusal(self, reason):
        """
        A pinned-digest mismatch, latched (FR-CG-7, Q26).

        The card loses its button rather than gaining a disabled one: there is
        nothing to retry, and this is the one thing in the panel the user is
        deliberately not able to click past.
        """
        self.view.download_refusal = reason or ""
        self._render_cards()

    def set_state(self, state, detail=""):
        self.view.set_state(state, detail)
        self._render_header()
        self._render_cards()

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

    def notify(self, text):
        """A notice that also flashes in the status bar. See `message`."""
        self.append_notice(text)
        self.message.emit(text)

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
        self._show_base_page()
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
        if self._pages.currentIndex() != PAGE_MEMORY or not self._memory_edit.hasFocus():
            self._memory_edit.setPlainText(text)
        self._memory_restore.setEnabled(bool(has_previous))
        self._memory_status.setText(
            f"{len(text)} characters"
            + ("  ·  a previous version is kept" if has_previous
               else "  ·  no previous version yet"))
        # Restoring **swaps**: every write rotates the current note into the
        # `.prev` file, so restoring twice returns to where it started. That is
        # deliberate -- the alternative destroys the current note irrecoverably
        # -- but it looks like a loop unless the control says so.
        self._memory_restore.setToolTip(
            "Swap this note with the kept previous version. Doing it twice "
            "returns to where you started." if has_previous
            else "There is no previous version of this note yet")

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
        self._show_page(PAGE_ARCHIVE)

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
