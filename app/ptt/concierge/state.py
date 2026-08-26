"""
D-CG-7 -- the Concierge state machine (`concierge_design.md` section 8).

Eight states and the transitions between them, as pure Python. The panel
**renders** a state; it never computes one. That split is the same one
`qt_statusview.py` already makes for the dictation banner, and it is what lets
the whole lifecycle be unit-tested with no Qt, no GPU and no server.

    disabled       no CUDA device; the runtime is never started (FR-CG-12)
    not_downloaded the GGUF is absent
    downloading    fetching it, with a percentage (FR-CG-7)
    stopped        downloaded, no server process
    loading        server starting, or the knowledge-pack prefix not yet warm
    ready          the next message will be fast
    generating     a turn is in flight
    unloading      killing the server, releasing VRAM (FR-CG-8)

**`loading` covers the pack prewarm, and that is the whole point of it.** Spike
C3 measured the cost of the 8k knowledge pack: it is not paid at model load, it
is paid on the first request that carries it, at 7.17 s to first token. Showing
`ready` before that prefix is warm would mean the first message of every session
hangs for seven seconds with nothing on screen to explain it. A visible loading
state is honest; a hanging first message feels broken. So `ready` here means
exactly one thing -- the next message will be fast.
"""

from ptt.logging_setup import log_debug

DISABLED = "disabled"
NOT_DOWNLOADED = "not_downloaded"
DOWNLOADING = "downloading"
STOPPED = "stopped"
LOADING = "loading"
READY = "ready"
GENERATING = "generating"
UNLOADING = "unloading"

#: Declaration order is display order for anything that lists them.
STATES = (
    DISABLED, NOT_DOWNLOADED, DOWNLOADING, STOPPED,
    LOADING, READY, GENERATING, UNLOADING,
)

#: What may follow what. Stated as data rather than as branches so the L1 suite
#: can assert the whole graph instead of the paths someone happened to write a
#: test for -- and so an illegal transition is a *reported* defect rather than a
#: state nobody can explain later (`OBS-1`).
#:
#: `disabled` has no outgoing edges on purpose. FR-CG-12 is decided once, from
#: the hardware, before anything starts; a machine does not grow a CUDA device
#: while the app is open, and a `disabled` that could be left is a `disabled`
#: something will eventually leave by accident.
TRANSITIONS = {
    DISABLED: (),
    NOT_DOWNLOADED: (DOWNLOADING,),
    DOWNLOADING: (NOT_DOWNLOADED, STOPPED),
    STOPPED: (LOADING, NOT_DOWNLOADED),
    LOADING: (READY, STOPPED),
    READY: (GENERATING, UNLOADING, STOPPED),
    GENERATING: (READY, UNLOADING, STOPPED),
    UNLOADING: (STOPPED,),
}


class Machine:
    """
    The current state, and the only thing allowed to change it.

    `on_change(state, detail)` is a plain-Python callback -- the Qt adapter is
    what turns it into a queued signal, because this module may not import Qt
    (CON-CG-6). It fires on every accepted transition including a re-entry with
    a new detail, which is how `downloading` reports its percentage without
    inventing eight more states.
    """

    def __init__(self, state=NOT_DOWNLOADED, on_change=None):
        if state not in STATES:
            raise ValueError(f"{state!r} is not a Concierge state")
        self._state = state
        self._detail = ""
        self._on_change = on_change or (lambda _state, _detail: None)

    @property
    def state(self):
        return self._state

    @property
    def detail(self):
        """
        The one-line reason or measurement beside the state.

        Free text, and deliberately so: `stopped` after a failed launch needs to
        say *why* it stopped, and enumerating those reasons as states would be
        the panel computing what it should be rendering.
        """
        return self._detail

    def can(self, target):
        """Whether `target` is reachable from here."""
        return target == self._state or target in TRANSITIONS[self._state]

    def to(self, target, detail=""):
        """
        Move to `target`. Returns True if it happened.

        An illegal transition is refused and logged rather than raised: this is
        called from a health-poll thread and an idle timer, and a state machine
        that can take the harness down when a server dies during a download is
        worse than one that reports the disagreement and stays where it is.
        """
        if target not in STATES:
            log_debug(f"Concierge: refused a move to unknown state {target!r}")
            return False
        if not self.can(target):
            log_debug(
                f"Concierge: refused an illegal transition "
                f"{self._state} -> {target} ({detail or 'no detail'})"
            )
            return False
        if target == self._state and detail == self._detail:
            return True

        previous = self._state
        self._state = target
        self._detail = detail
        if previous != target:
            log_debug(f"Concierge state: {previous} -> {target}"
                      + (f" ({detail})" if detail else ""))
        self._emit()
        return True

    def _emit(self):
        try:
            self._on_change(self._state, self._detail)
        except Exception as e:
            # A frontend bug must not kill the health poll, exactly as
            # `Engine._emit` refuses to let `on_state` kill the dictation loop.
            log_debug(f"ERROR in Concierge on_change callback: {str(e)}")


def can_serve(state):
    """
    Whether a user message can be sent right now.

    `generating` is excluded because the harness serialises sends (`-np 1`,
    design 10 Q14): a new send cancels the current generation rather than
    landing on a second slot and re-paying the knowledge pack in full.
    """
    return state == READY
