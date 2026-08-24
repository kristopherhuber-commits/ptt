"""
Layer 3: the settings window.

A real QMainWindow, so Windows draws the title bar and its own minimise,
maximise and close buttons. Top to bottom: the banner (dark, read-only), the tab
bar, the active panel (light, interactive), and a status bar.

That light/dark split is the whole colour scheme in one rule -- dark surfaces
are read-only, light surfaces are interactive -- and section 5 says not to
invert it.

All six panels are real. Every one of them is an `InstantApplyPanel`, including
the two that write nothing: Advanced and Diagnostics need the engine hand-off
and the status-bar message channel, and nothing else in this window supplies
either.

**There is no OK / Apply / Cancel.** Every control applies instantly and
confirms in the status bar; `flash_saved` is that confirmation, and every panel
reaches it through the one `InstantApplyPanel.saved` signal rather than by
writing to the bar itself.
"""

from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QLabel, QMainWindow, QScrollArea, QStatusBar, QTabWidget, QVBoxLayout,
    QWidget,
)

from ptt.ui.panels import InstantApplyPanel
from ptt.ui.panels.advanced import AdvancedPanel
from ptt.ui.panels.audio import AudioPanel
from ptt.ui.panels.diagnostics import DiagnosticsPanel
from ptt.ui.panels.hotkey import HotkeyPanel
from ptt.ui.panels.model import ModelPanel
from ptt.ui.panels.vocabulary import VocabularyPanel
from ptt.ui.qt_statusview import StatusView

#: Default size. gui_handoff section 6 fixes the width at ~880 and gives a
#: minimum, not a height; this is the height the content actually needs. The
#: banner alone is 254 px because it is the popover's layout verbatim, the
#: Hotkey panel draws a whole keyboard, and the Model panel shows six tiers at
#: once. A window that opened already scrolled would hide the compatibility
#: warnings on one tab and the Measure button on the other.
WINDOW_SIZE = (880, 800)

#: The minimum section 6 asks for. Everything below it still works because each
#: tab is inside a scroll area -- shrinking the window scrolls a panel rather
#: than overlapping its rows, which is what a keyboard diagram does otherwise.
MINIMUM_SIZE = (820, 620)

#: How long "Saved · HH:MM:SS" stays in the status bar.
SAVED_FLASH_MS = 4000

#: How long a panel's transient note stays there.
MESSAGE_MS = 8000


class SettingsWindow(QMainWindow):
    """The only interactive surface. Hidden rather than destroyed when closed."""

    #: A panel wrote a setting. `QtApp` listens, so the banner, the status-bar
    #: summary and the tray menu pick up a new chord or model straight away
    #: instead of waiting for the engine's next state change.
    settings_changed = Signal()

    def __init__(self, settings, cuda_supported, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PTT Dictation — Settings")
        self.resize(*WINDOW_SIZE)
        self.setMinimumSize(*MINIMUM_SIZE)

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

        self._panels = []
        self._add_panel("Hotkey", HotkeyPanel(settings))
        self._model_panel = ModelPanel(settings, cuda_supported)
        self._add_panel("Model", self._model_panel)
        self._add_panel("Audio", AudioPanel(settings))
        self._add_panel("Vocabulary", VocabularyPanel(settings))
        self._add_panel("Advanced", AdvancedPanel(settings))
        self._add_panel("Diagnostics", DiagnosticsPanel(settings, cuda_supported))

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

    def _add_panel(self, title, panel: InstantApplyPanel):
        panel.saved.connect(self._on_panel_saved)
        panel.message.connect(self._on_panel_message)
        self._panels.append(panel)
        self._add_tab(title, panel)

    def _add_tab(self, title, panel):
        """
        Add one tab, inside a scroll area.

        The scroll area is what lets the window honour its stated minimum size
        without the Hotkey panel's keyboard collapsing: a `QHBoxLayout` row
        given less height than its widgets need does not clip them tidily, it
        overlaps them, and a keyboard drawn on top of itself looks like a
        rendering fault rather than a window that is too small.
        """
        area = QScrollArea()
        area.setObjectName("panelScroll")
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        area.setWidget(panel)
        self.tabs.addTab(area, title.upper())

    # -- panels -------------------------------------------------------------

    def attach(self, engine):
        """Hand the engine to every panel. The window is built before it exists."""
        for panel in self._panels:
            panel.attach(engine)

    def refresh_panels(self):
        """
        Re-read the settings object into every panel.

        Called when something outside this window changed a setting -- the tray
        menu's GPU/CPU items are the live case -- so the two surfaces cannot
        disagree about what is currently set.
        """
        for panel in self._panels:
            panel.refresh()

    def record_benchmark(self, model_name, device, seconds):
        """
        Forward a latency measurement to the Model panel.

        Held by the window rather than reached for through the tab widget so
        `QtApp` never has to know which tab index the Model panel sits at.
        """
        self._model_panel.record_benchmark(model_name, device, seconds)

    def _on_panel_saved(self, _field):
        self.flash_saved(datetime.now().strftime("%H:%M:%S"))
        self.settings_changed.emit()

    def _on_panel_message(self, text):
        """
        A panel's transient note.

        `showMessage` rather than a dialog: gui_handoff section 6 allows a
        confirmation box for two destructive actions and nothing else, and a
        modal on a window where every control applies instantly would be a
        contradiction in terms.
        """
        self._status.showMessage(text, MESSAGE_MS)

    # -- state --------------------------------------------------------------

    def apply(self, ui):
        """Update the banner and the status-bar summary from a UiState."""
        self.view.apply(ui)
        parts = [p for p in (ui.status_text, ui.model, ui.hotkey) if p]
        self._summary.setText("  ·  ".join(parts))

    def flash_saved(self, when):
        """Confirm a write to config.json."""
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
