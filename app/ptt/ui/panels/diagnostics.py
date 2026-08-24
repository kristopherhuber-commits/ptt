"""
The Diagnostics panel: what the hardware is doing, and the tail of the log.

**The log is reached through `paths`, never constructed here.** That is not
tidiness -- `paths.py`'s module docstring explains that the application's
directories are anchored one level above the package, and any module deriving
them from its own `__file__` would name a file inside `app/ptt/` that does not
exist. This panel would then show an empty log on a working installation, which
is worse than showing nothing at all.

**The three readouts come from the engine, not from parsing the log.** All three
are logged today, and `OBS-4` guarantees that file is plain text -- not that any
line in it has a stable format. A median rebuilt by regex over `Transcription
finished in ...` would break silently the first time that message was reworded,
and a diagnostics panel that lies is worse than one that says "not yet". So the
engine keeps the two values it already computed (`Engine._record_transcription`,
`Engine.last_paste_target`) and this reads them.

The tail is re-read on a timer while the tab is visible, from the end of the
file rather than the start: `debug_log.txt` is rotated per session but a long
session with a lot of dictation still reaches megabytes, and reading all of it
thirty times a minute to show the last forty lines would be silly.
"""

import os

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from ptt import paths, transcribe
from ptt.ui.panels import InstantApplyPanel

#: Placeholder for a figure this session has not produced yet. The same em dash
#: the status view and the model table use, and for the same reason.
UNKNOWN = "—"

#: How many lines of the log the view holds.
TAIL_LINES = 200

#: Bytes read from the end of the file to find them. Comfortably more than 200
#: lines of a log whose lines run to about a hundred characters; if it is not,
#: fewer lines are shown rather than the whole file being read.
TAIL_BYTES = 96 * 1024

#: How often the tail and the readouts refresh while the tab is on screen.
REFRESH_MS = 1500


def tail_lines(path, limit=TAIL_LINES, window=TAIL_BYTES):
    """
    The last `limit` lines of a text file, oldest first. Never raises.

    Seeks to `window` bytes from the end rather than reading the file, so the
    cost does not grow with the length of the session. The first line of that
    window is dropped when the file is longer than the window, because a seek to
    a byte offset lands in the middle of a line and half a log entry reads as a
    corrupted log.

    Returns an empty list if the file is missing or unreadable, which the panel
    shows as "no log yet" -- the log being absent is itself worth seeing, and it
    must not take the window down.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            truncated = size > window
            if truncated:
                f.seek(size - window)
            text = f.read()
    except Exception:
        return []

    lines = text.splitlines()
    if truncated and lines:
        lines = lines[1:]
    return lines[-limit:]


class DiagnosticsPanel(InstantApplyPanel):
    """
    Read-only. It subclasses `InstantApplyPanel` for the engine hand-off and the
    status-bar message channel, and never calls `apply_now`; the one button that
    changes anything asks the engine to reload, which is not a setting.
    """

    def __init__(self, settings, cuda_supported, parent=None):
        super().__init__(settings, parent)
        self.cuda_supported = cuda_supported

        #: Counted once. `cuda_device_count` imports CTranslate2 and asks the
        #: driver; the answer does not change while the process is running, and
        #: this panel refreshes every 1.5 seconds.
        self._cuda_count = None

        box = QVBoxLayout(self)
        box.setContentsMargins(28, 22, 28, 18)
        box.setSpacing(0)

        header = QHBoxLayout()
        header.setSpacing(12)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        heading = QLabel("Diagnostics")
        heading.setObjectName("panelTitle")
        blurb = QLabel(
            "The tail of debug_log.txt. Every fallback is logged with the "
            "reason that caused it, so this is where a setting that did not "
            "take effect explains itself."
        )
        blurb.setObjectName("panelBlurb")
        blurb.setWordWrap(True)
        titles.addWidget(heading)
        titles.addWidget(blurb)

        self._open_folder = QPushButton("Open log folder")
        self._open_folder.clicked.connect(self._on_open_folder)
        self._reload = QPushButton("Reload model")
        self._reload.setToolTip(
            "Rebuilds the model on the current device. Dictation is unavailable "
            "for a few seconds while it loads."
        )
        self._reload.clicked.connect(self._on_reload)

        header.addLayout(titles, 1)
        header.addWidget(self._open_folder, 0)
        header.addWidget(self._reload, 0)
        box.addLayout(header)
        box.addSpacing(16)

        readouts = QHBoxLayout()
        readouts.setSpacing(12)
        self._cuda = self._add_readout(readouts, "CUDA devices")
        self._latency = self._add_readout(readouts, "Median latency")
        self._target = self._add_readout(readouts, "Paste target")
        box.addLayout(readouts)
        box.addSpacing(12)

        self._log = QPlainTextEdit()
        self._log.setObjectName("logView")
        self._log.setReadOnly(True)
        self._log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._log.setMinimumHeight(240)
        box.addWidget(self._log, 1)

        path = QLabel(paths.debug_log_path())
        path.setObjectName("panelNote")
        path.setWordWrap(True)
        box.addSpacing(6)
        box.addWidget(path)

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)

    def _add_readout(self, layout, caption_text):
        frame = QFrame()
        frame.setObjectName("panelBox")
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(14, 10, 14, 10)
        inner.setSpacing(2)
        caption = QLabel(caption_text.upper())
        caption.setObjectName("caption")
        value = QLabel(UNKNOWN)
        value.setObjectName("panelValue")
        inner.addWidget(caption)
        inner.addWidget(value)
        layout.addWidget(frame, 1)
        return value

    # -- state --------------------------------------------------------------

    def refresh(self):
        """
        Re-read the readouts and the log tail.

        Guarded on visibility because the base class calls this on every engine
        state change, for every panel: re-reading 96 KB of log because the state
        went from `recording` to `transcribing` on a tab nobody is looking at is
        work for nothing.
        """
        if not self.isVisible():
            return
        self._cuda.setText(self._cuda_text())
        self._latency.setText(self._latency_text())
        self._target.setText(
            (self._engine.last_paste_target if self._engine else "") or UNKNOWN
        )
        self._refresh_log()

    def _cuda_text(self):
        """Device count and the compute type actually in use, as the mockup has it."""
        if self._cuda_count is None:
            self._cuda_count = transcribe.cuda_device_count() if self.cuda_supported else 0
        device = self._engine.current_device if self._engine else "cpu"
        compute = (transcribe.CUDA_COMPUTE_TYPE if device == "cuda"
                   else transcribe.CPU_COMPUTE_TYPE)
        return f"{self._cuda_count} · {device.upper()} {compute}"

    def _latency_text(self):
        median = self._engine.median_latency() if self._engine else None
        if median is None:
            return f"{UNKNOWN} · nothing dictated yet"
        return f"{median:.2f} s"

    def _refresh_log(self):
        """
        Replace the view's contents, keeping the newest line in sight.

        Rewritten wholesale rather than appended to, because the log is rotated
        at startup and appending would splice two sessions together. The scroll
        position is only forced to the bottom when it was already there, so
        reading back through the log is not yanked away every 1.5 seconds.
        """
        lines = tail_lines(paths.debug_log_path())
        text = "\n".join(lines) if lines else "No log yet."
        if text == self._log.toPlainText():
            return

        scroll = self._log.verticalScrollBar()
        at_bottom = scroll.value() >= scroll.maximum() - 4
        self._log.setPlainText(text)
        if at_bottom:
            scroll.setValue(scroll.maximum())

    # -- controls -----------------------------------------------------------

    def _on_open_folder(self):
        """
        Open the directory the log lives in.

        `paths.APP_DIR` rather than a directory derived from the log's path:
        `paths` is the sole owner of every application-relative path, and
        `os.path.dirname` on one of its results would be this module computing
        a directory.
        """
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(paths.APP_DIR))
        if not opened:
            self.message.emit(f"Could not open {paths.APP_DIR}.")

    def _on_reload(self):
        if self._engine is None:
            return
        self.message.emit("Reloading the model — dictation pauses until it is up.")
        self._engine.request_model_reload()

    # -- lifecycle ----------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self._timer.start()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)
