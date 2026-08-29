"""
`THREAD-CHECK`: the evidence that a queued hop is really happening.

Extracted from `qt_tray.py` in v3.0, unchanged in behaviour. It moved because
the Concierge's worker adapter needs the same function and `qt_tray` imports
PIL at column 0 to draw the tray icon -- so importing the thread check from
there would have made a unit test of the adapter depend on Pillow, which the
test environment deliberately does not install (`requirements-dev.txt`). A
thread check was never a tray concern anyway; it belongs beside nothing in
particular, which is what this module is.

The rule being checked is **v2.0 acceptance criterion 9** (`ptt-v2-gui/
gui_handoff.md` section 10), generalised in v3 to: *no UI object is touched from
any thread other than the GUI thread*. v3's own thread audit is criterion
**v3-10** -- the two numbering sets collide and every reference has to say which.
"""

import threading

from PySide6.QtWidgets import QApplication

from ptt.logging_setup import log_debug


def log_thread(where, expect_gui):
    """
    Record which thread a bridge endpoint ran on. Writes one line; never raises.

    Deliberately a log and not an `assert`. `Engine._emit` wraps the state
    callback in `try/except Exception` and only logs, so an AssertionError raised
    on the engine side is swallowed whole and leaves no visible symptom -- the
    same shape of silent failure as retrospective issue #11. Asserts are also
    stripped under -O.

    The pair of lines this produces is the actual evidence: the callback side
    must NOT be the GUI thread and the slot side must be, so the two recorded
    thread ids must differ. Equal ids mean the queued hop is not happening.
    """
    try:
        from PySide6.QtCore import QThread
        app = QApplication.instance()
        current = QThread.currentThread()
        gui = app.thread() if app is not None else None
        is_gui = current == gui
        verdict = "OK" if is_gui == expect_gui else "WRONG THREAD"
        log_debug(
            f"THREAD-CHECK [{verdict}] {where}: "
            f"qt_thread={current} gui_thread={gui} "
            f"python_thread={threading.current_thread().name} "
            f"(expected {'GUI' if expect_gui else 'non-GUI'})"
        )
    except Exception as e:
        log_debug(f"THREAD-CHECK failed for {where}: {e}")


class SignalAudit:
    """
    One `THREAD-CHECK` line per signal, per emitting thread, per session (Q26).

    Q26 says "once per signal type per session, at first emission", matching the
    pattern `EngineBridge` and `QtTray` already use, and the bound exists for a
    concrete reason: the Concierge's token signal fires about thirty times a
    second into the same `debug_log.txt` that `read_log` reads and the
    Diagnostics tab tails every 1.5 s.

    **The key is the signal and the emitting thread.** Without the second half,
    v3-10's "harness idle-timer -> GUI" hop can never appear: the idle timer
    emits `state_changed`, which the worker thread already emitted and logged,
    so the line proving the second hop would be suppressed by the first. Adding
    the thread keeps Q26's bound where it matters and produces the evidence the
    criterion asks to see.

    **The thread is identified by `get_ident()`, not by its name, and that is
    the whole of `development_history.md` #48.** A `QThread` is not a Python
    thread: PySide6 enters and leaves the interpreter around each queued slot
    invocation, and each entry mints a fresh `_DummyThread` -- so
    `threading.current_thread().name` on one worker QThread reads `Dummy-1`,
    then `Dummy-2`, then `Dummy-3`, a new name per delivery. Keyed on the name,
    nothing is ever a repeat and every emission logs. Keyed on the OS thread id,
    which does not change, the bound is the one Q26 asks for. The name is still
    recorded, because it is what a person reads in the log.
    """

    def __init__(self, log=log_thread):
        self._log = log
        #: `{(what, thread ident): thread name}`.
        self._seen = {}

    def check(self, what, expect_gui):
        """Log once for this `(what, thread)`. Returns whether it logged."""
        key = (what, threading.get_ident())
        if key in self._seen:
            return False
        self._seen[key] = threading.current_thread().name
        try:
            self._log(f"Concierge {what}", expect_gui)
        except Exception as e:                       # pragma: no cover - defensive
            log_debug(f"Concierge: the thread check itself failed: {str(e)}")
        return True

    def seen(self):
        """Every `(what, thread name)` already logged. For the L1 suite."""
        return frozenset((what, name) for (what, _ident), name
                         in self._seen.items())
