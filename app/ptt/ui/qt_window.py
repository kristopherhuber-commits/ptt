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

v3.0 adds a seventh surface that is **not** a tab: the Concierge chat panel,
docked to the right of the tabs inside a `QSplitter` and collapsed by default
(`concierge_handoff.md` section 7). It sits beside the tabs rather than beside
the whole window so the banner keeps spanning the width, and it is reached from
the `Concierge` button at the right-hand end of the tab strip -- the strip's
corner widget, which is the one place Qt offers there. This window knows nothing
about the harness: it builds the panel, shows and hides it, and reports when
that happened. `qt_concierge_worker.ConciergeController` owns everything else.
"""

from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QLabel, QMainWindow, QPushButton, QScrollArea, QSplitter, QStatusBar,
    QTabWidget, QVBoxLayout, QWidget,
)

from ptt import config
from ptt.ui.panels import InstantApplyPanel
from ptt.ui.panels.advanced import AdvancedPanel
from ptt.ui.panels.audio import AudioPanel
from ptt.ui.panels.diagnostics import DiagnosticsPanel
from ptt.ui.panels.hotkey import HotkeyPanel
from ptt.ui.panels.model import ModelPanel
from ptt.ui.panels.vocabulary import VocabularyPanel
from ptt.ui.qt_concierge import ConciergePanel, DEFAULT_WIDTH as CONCIERGE_WIDTH
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


def restored_width(before, current, panel_width=CONCIERGE_WIDTH,
                   minimum=MINIMUM_SIZE[0]):
    """
    How wide the window should be once the Concierge panel is collapsed.

    Pure, and separated for the reason `qt_marks.mark_centres` is: this is the
    half of the behaviour that can be checked without a screen, and the rule it
    encodes is not obvious. `before` is the width when the panel was expanded,
    `current` is the width now. The difference between `current` and where the
    expansion left the window is the user's own resizing, and that is kept --
    ours is the only width given back.
    """
    manual = current - (before + panel_width)
    return max(before + manual, minimum)


def should_offer_concierge(already_offered, opt_in):
    """
    Whether to expand the Concierge panel unasked. Pure, for `restored_width`'s
    reason: this is the half of the behaviour that can be checked without a
    screen, and it is the more consequential half.

    **This is a deliberate amendment to `concierge_handoff.md` 8.1**, which says
    the opt-in card appears at the first *app launch* after the upgrade.
    `install.ps1` puts a shortcut in the Startup folder, so "app launch" is
    "login" on most installations, and a settings window arriving over whatever
    the user is doing at login would be the first thing every upgrading v2.0
    user saw -- which is what FR-CG-6's "strictly optional" is written against.

    So the offer is made inside a window the user opened themselves, once per
    run, and it is one click to decline. The tray's `Concierge...` and the tab
    strip's button reach the same card at any time, which keeps "prompt once" a
    promise about frequency rather than about timing.

    `unset` and nothing else: `accepted` needs no offer and `declined` is the
    answer that means never again.
    """
    return not already_offered and opt_in == config.OPT_IN_UNSET


class SettingsWindow(QMainWindow):
    """The only interactive surface. Hidden rather than destroyed when closed."""

    #: A panel wrote a setting. `QtApp` listens, so the banner, the status-bar
    #: summary and the tray menu pick up a new chord or model straight away
    #: instead of waiting for the engine's next state change.
    settings_changed = Signal()

    #: The Concierge panel was expanded or collapsed. The controller starts the
    #: runtime on the first and, when residency is 0, unloads it on the second
    #: -- which is the one residency case `Server.start_idle_timer` deliberately
    #: refuses to own, because "unload when the chat panel closes" is a fact
    #: only this window knows.
    concierge_visible = Signal(bool)

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

        # 2b. the Concierge, beside the tabs and collapsed until asked for
        self.concierge = ConciergePanel()
        self.concierge.hide()
        self.concierge.close_requested.connect(
            lambda: self.set_concierge_visible(False))
        # The same status-bar channel every tab uses for something that is not a
        # save. A Save that found nothing to save leaves no mark on the panel
        # except one muted line in a transcript that is empty by definition,
        # which is not a report.
        self.concierge.message.connect(self._on_panel_message)

        #: The window's width the last time the panel was expanded, so closing
        #: it gives the pixels back. See `set_concierge_visible`.
        self._width_before_concierge = None
        #: Whether the first-run offer has been made in this run. See
        #: `offer_concierge_once`.
        self._offered_concierge = False

        self._split = QSplitter(Qt.Orientation.Horizontal)
        self._split.setObjectName("conciergeSplitter")
        self._split.setChildrenCollapsible(False)
        self._split.addWidget(self.tabs)
        self._split.addWidget(self.concierge)
        self._split.setStretchFactor(0, 1)
        self._split.setStretchFactor(1, 0)
        layout.addWidget(self._split, 1)

        self._concierge_button = QPushButton("Concierge  ▸")
        self._concierge_button.setObjectName("conciergeToggle")
        self._concierge_button.setFlat(True)
        self._concierge_button.setCheckable(True)
        self._concierge_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._concierge_button.toggled.connect(self.set_concierge_visible)
        self.tabs.setCornerWidget(self._concierge_button,
                                  Qt.Corner.TopRightCorner)

        self.setCentralWidget(central)

        # 4. the status bar
        self._status = QStatusBar()
        self._summary = QLabel("")
        self._concierge_segment = QLabel("")
        self._concierge_segment.setObjectName("conciergeSegment")
        self._concierge_segment.hide()
        self._saved = QLabel("")
        self._saved.setObjectName("savedFlash")
        self._status.addWidget(self._summary, 1)
        self._status.addPermanentWidget(self._concierge_segment)
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

    # -- the Concierge ------------------------------------------------------

    def set_concierge_visible(self, visible):
        """
        Expand or collapse the chat panel. Idempotent; emits on a real change.

        The window **grows** by the panel's width rather than squeezing the tabs
        into what is left: `MINIMUM_SIZE` is 820 px because the Hotkey tab draws
        a 104-key keyboard, and taking 360 px away from that would put the tabs
        below their own minimum and start the overlapping that scroll area
        exists to prevent. A maximised window is left alone -- there is nowhere
        for it to grow -- and so is one the user has already made wide enough.

        **Closing gives the pixels back.** The width before the expansion is
        remembered and restored, carrying forward any resizing the user did
        while the panel was open, so opening and closing repeatedly leaves the
        window where it started rather than one panel wider each time.
        """
        visible = bool(visible)
        if visible == self.concierge.isVisible():
            self._sync_concierge_button(visible)
            return

        if visible:
            self._grow_for_concierge()
        self.concierge.setVisible(visible)
        if visible:
            self._split.setSizes([max(self.width() - CONCIERGE_WIDTH,
                                      MINIMUM_SIZE[0] - CONCIERGE_WIDTH),
                                  CONCIERGE_WIDTH])
        else:
            self._shrink_after_concierge()
        self._sync_concierge_button(visible)
        self.concierge_visible.emit(visible)

    def _grow_for_concierge(self):
        self._width_before_concierge = None
        maximised = bool(self.windowState() & Qt.WindowState.WindowMaximized)
        if maximised or self.width() >= WINDOW_SIZE[0] + CONCIERGE_WIDTH:
            return
        self._width_before_concierge = self.width()
        self.resize(self.width() + CONCIERGE_WIDTH, self.height())

    def _shrink_after_concierge(self):
        """
        Give back exactly what expanding took, and nothing the user added.

        The delta is measured against where the expansion left the window, so a
        window the user widened by 200 px while the panel was open closes 200 px
        wider than it started -- their change survives, ours does not. Never
        below the stated minimum, and never on a maximised window, which was
        not grown in the first place.
        """
        before, self._width_before_concierge = self._width_before_concierge, None
        if before is None:
            return
        if bool(self.windowState() & Qt.WindowState.WindowMaximized):
            return
        self.resize(restored_width(before, self.width()), self.height())

    def _sync_concierge_button(self, visible):
        """Keep the corner button's arrow and check state honest."""
        blocked = self._concierge_button.blockSignals(True)
        self._concierge_button.setChecked(visible)
        self._concierge_button.setText(
            "Concierge  ◂" if visible else "Concierge  ▸")
        self._concierge_button.blockSignals(blocked)

    def set_concierge_segment(self, text):
        """
        The status bar's Concierge segment: `<model> resident · unloads after N`.

        Hidden rather than blanked when there is nothing to say, so the bar does
        not carry an empty gap between the summary and the saved flash.
        """
        self._concierge_segment.setText(text or "")
        self._concierge_segment.setVisible(bool(text))

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

    def offer_concierge_once(self, opt_in):
        """
        Expand the panel the first time this window is opened un-answered.

        Returns whether it expanded, so the caller can tell a first run from a
        later one without asking a widget. The decision is
        `should_offer_concierge`; this is the half that needs a screen.
        """
        if not should_offer_concierge(self._offered_concierge, opt_in):
            return False
        self._offered_concierge = True
        self.set_concierge_visible(True)
        return True

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
