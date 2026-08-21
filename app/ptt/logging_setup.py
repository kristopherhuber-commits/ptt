"""
The debug_log.txt writer (OBS-4).

Plain text, next to the application, readable without tooling.

`log_debug` never raises and works before `init` has run. An unwritable log
must never be the reason the application fails to start -- and the log is the
only evidence a silent failure leaves, which is why issue #11 went undiagnosed
for months.
"""

import os
import sys
import threading
import time
import traceback

from ptt import paths

# Serialises appends. The tray writes from its engine thread while the UI thread
# writes from menu callbacks; without this the two interleave mid-line, and this
# log is the evidence every behaviour-neutrality check rests on.
_LOCK = threading.Lock()

_ECHO = False


def init(echo=False):
    """
    Rotate the previous log aside, start a fresh one, and write the banner.

    Rotated rather than truncated because both entry points write to this same
    file: launching the console frontend while the tray is running would
    otherwise destroy the tray's log. Rotation spends the older session instead
    of the current one, and keeps the log of the crash you are diagnosing.

    `echo=True` mirrors every line to stdout. The console frontend uses it; the
    tray, which runs under pythonw.exe with no console, does not.
    """
    global _ECHO
    _ECHO = echo

    try:
        current = paths.debug_log_path()
        if os.path.exists(current):
            os.replace(current, paths.previous_debug_log_path())
    except Exception:
        # A locked or unwritable log is not a reason to refuse to start.
        pass

    # These four lines are reproduced verbatim from the pre-split tray so that
    # a refactored log can be diffed against a captured baseline.
    log_debug("=== App Started ===")
    log_debug(f"sys.frozen: {getattr(sys, 'frozen', False)}")
    log_debug(f"sys.executable: {sys.executable}")
    log_debug(f"app_dir: {paths.APP_DIR}")


def log_debug(msg):
    """Append one timestamped line. Never raises."""
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        with _LOCK:
            # utf-8 explicitly: the default here is the locale codepage, which
            # raises UnicodeEncodeError on a transcription containing any
            # character outside cp1252.
            with open(paths.debug_log_path(), "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass

    if _ECHO:
        try:
            print(line, flush=True)
        except Exception:
            pass


def log_exception(prefix=""):
    """Log a caught exception's traceback. Call from inside an except block."""
    if prefix:
        log_debug(prefix)
    try:
        log_debug(traceback.format_exc())
    except Exception:
        pass
