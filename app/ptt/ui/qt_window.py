"""
Layer 3: the settings window.

A real QMainWindow, so Windows draws the title bar and its own minimise,
maximise and close buttons. Top to bottom: the banner (dark, read-only), the tab
bar, the active panel (light, interactive), and a status bar.

That light/dark split is the whole colour scheme in one rule -- dark surfaces
are read-only, light surfaces are interactive -- and section 5 says not to
invert it.

The panels themselves arrive in later sessions. This session builds the shell
and six placeholders, so the frame can be reviewed before anything is wired into
the engine.

**There is no OK / Apply / Cancel.** Every control will apply instantly and
confirm in the status bar; `flash_saved` is that confirmation, already wired so
the panels have somewhere to report to.
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QLabel, QMainWindow, QStatusBar, QTabWidget, QVBoxLayout, QWidget,
)

from ptt.ui.qt_statusview import StatusView

#: Tab order, and the session that fills each one in.
PANELS = (
    ("Hotkey",      "the keyboard diagram and the compatibility warnings"),
    ("Model",       "the Whisper size tiers, measured on this machine"),
    ("Audio",       "input device, level meter and recording behaviour"),
    ("Vocabulary",  "replacement rules applied before the text is pasted"),
    ("Advanced",    "the engine constants, and why each one is what it is"),
    ("Diagnostics", "CUDA state, latency, and the tail of debug_log.txt"),
)

#: How long "Saved · HH:MM:SS" stays in the status bar.
SAVED_FLASH_MS = 4000


class PlaceholderPanel(QWidget):
    """A tab with nothing in it yet, saying so rather than looking broken."""

    def __init__(self, title, blurb, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")

        box = QVBoxLayout(self)
        box.setContentsMargins(28, 24, 28, 24)
        box.setSpacing(6)

        heading = QLabel(title)
        heading.setObjectName("panelTitle")
        blurb_label = QLabel(blurb)
        blurb_label.setObjectName("panelBlurb")
        blurb_label.setWordWrap(True)

        note = QLabel("Not built yet.")
        note.setObjectName("panelPlaceholder")

        box.addWidget(heading)
        box.addWidget(blurb_label)
        box.addSpacing(18)
        box.addWidget(note)
        box.addStretch(1)


class SettingsWindow(QMainWindow):
    """The only interactive surface. Hidden rather than destroyed when closed."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PTT Dictation — Settings")
        self.resize(880, 660)
        self.setMinimumSize(820, 620)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 1. the banner: the same class the popover uses, without the footer
        self.view = StatusView(show_footer=False)
        layout.addWidget(self.view)

        # 2. the tabs, 3. the panels
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        for title, blurb in PANELS:
            self.tabs.addTab(PlaceholderPanel(title, blurb), title.upper())
        layout.addWidget(self.tabs, 1)

        self.setCentralWidget(central)

        # 4. the status bar
        self._status = QStatusBar()
        self._summary = QLabel("")
        self._saved = QLabel("")
        self._saved.setObjectName("savedFlash")
        self._status.addWidget(self._summary, 1)
        self._status.addPermanentWidget(self._saved)
        self.setStatusBar(self._status)

        self._saved_timer = QTimer(self)
        self._saved_timer.setSingleShot(True)
        self._saved_timer.setInterval(SAVED_FLASH_MS)
        self._saved_timer.timeout.connect(lambda: self._saved.setText(""))

    # -- state --------------------------------------------------------------

    def apply(self, ui):
        """Update the banner and the status-bar summary from a UiState."""
        self.view.apply(ui)
        parts = [p for p in (ui.status_text, ui.model, ui.hotkey) if p]
        self._summary.setText("  ·  ".join(parts))

    def flash_saved(self, when):
        """Confirm a write to config.json. Used from the next session onwards."""
        self._saved.setText(f"Saved · {when}")
        self._saved_timer.start()

    # -- lifecycle ----------------------------------------------------------

    def show_and_raise(self):
        self.show()
        self.setWindowState(
            (self.windowState() & ~Qt.WindowState.WindowMinimized)
            | Qt.WindowState.WindowActive
        )
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent):
        """
        Hide instead of closing.

        The application lives in the tray, so closing the settings window must
        not end anything. QtApp also sets setQuitOnLastWindowClosed(False), which
        is what stops Qt quitting the process here; this keeps the built window
        around so reopening is instant and any panel state survives.
        """
        event.ignore()
        self.hide()
