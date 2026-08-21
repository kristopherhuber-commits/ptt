"""
The process entry guard.

This is the only module in the codebase permitted to call `os._exit`.
"""

import os

from ptt.logging_setup import log_debug, log_exception


def main_guard(entry):
    """
    Run an entry point, then terminate the process hard.

    `os._exit` is required, not stylistic. CTranslate2's thread pools and the
    `keyboard` library's listener keep non-daemon threads alive, which blocks
    normal interpreter shutdown and leaves zombie ptt_dictate.exe processes
    accumulating in memory (FR-9, retrospective issue #8).

    KeyboardInterrupt and SystemExit are caught for the same reason: letting
    either unwind through normal shutdown reintroduces exactly the hang this
    exists to prevent.
    """
    try:
        entry()
        log_debug("App main execution finished. Forcing process exit.")
        os._exit(0)
    except KeyboardInterrupt:
        os._exit(0)
    except SystemExit as e:
        os._exit(e.code if isinstance(e.code, int) else 0)
    except Exception as e:
        log_debug(f"Unhandled crash in __main__: {e}")
        log_exception()
        os._exit(1)
