"""
The settings window's panels -- one module per tab.

Everything below the tab bar is a light, interactive surface (gui_handoff
section 1). Nothing here draws a read-only state display; that is `StatusView`,
which the window embeds above the tabs.

`InstantApplyPanel` exists for one reason. **There is no OK, Apply or Cancel
anywhere in this window** -- every control applies the moment it is touched --
and that means the three steps which make a change real must happen in the same
order in every handler: write the field, `Settings.save()`, then tell the engine
if it needs to know. One method that every control routes through is what stops
the next control someone adds from saving before it writes, or from writing and
never saving at all.

The engine arrives after construction, the same two-phase wiring `QtApp` and
`QtTray` already use: the window is built before there is an engine to attach.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

from ptt.logging_setup import log_debug
from ptt.ui.qt_marks import RegistrationMarks


class InstantApplyPanel(RegistrationMarks, QWidget):
    """
    Base for a tab that writes settings.

    `saved` reaches the window's status bar; `message` is for the things a panel
    needs to say that are not a save, such as a button that is not implemented
    yet. Neither is a dialog: gui_handoff section 6 allows a confirmation box
    for exactly two destructive actions and nothing else, so a panel that wants
    to tell the user something puts it in the status bar.
    """

    #: A setting was written. Carries the field name, for the log line.
    saved = Signal(str)

    #: Transient one-line note for the status bar.
    message = Signal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self._settings = settings
        self._engine = None

    def attach(self, engine):
        """Give the panel the engine. Called once, before the window is shown."""
        self._engine = engine

    def refresh(self):
        """
        Re-read the settings object into the widgets.

        Called when something outside this panel changed a setting it displays
        -- the tray menu's GPU/CPU items are the live example -- and when the
        tab becomes visible. Panels that display nothing changeable elsewhere
        may leave it alone.
        """

    def apply_now(self, field, value, reload_model=False):
        """
        Write one setting and make it live. The only way a panel changes state.

        The order is the contract: the field is written and persisted first, so
        anything that re-reads the settings object sees the new value, then the
        engine is told. Reversing those would mean a reload racing a disk write
        and a crash in between losing the setting the user can see has already
        taken effect.

        Both halves of the write are `config.Settings.set`'s now, not this
        method's (`concierge_design.md` 4.6, D-CG-13). It validates against the
        one `FIELDS` table `load()` reads, rebinds whole values rather than
        reaching into a tuple, list or dict already on the object, and saves.
        The panels went through here first, and the Concierge's tool code was
        never going to: an invariant that only holds while every caller
        remembers it is not an invariant, so it belongs to the object.

        A rejected write is a **bug in this panel**, not a user error -- every
        control is built from the same catalogue `FIELDS` validates against, so
        a widget can only offer a value that passes. It is reported in the
        status bar and logged rather than swallowed, because a control that
        appears to work and changes nothing is the failure OBS-3 exists to
        close, and it returns False so a caller that refreshes from the
        settings object does not redraw a change that did not happen.

        `reload_model` is False for the hotkey and the vocabulary, which the
        engine re-reads on its own on every poll iteration, and True for the
        model and the device, which it only reads when it builds a model.
        """
        ok, reason = self._settings.set(field, value)
        if not ok:
            log_debug(f"WARNING: the {type(self).__name__} was refused a write: {reason}")
            self.message.emit(f"Could not change {field}: {reason}")
            return False
        if reload_model:
            if self._engine is not None:
                self._engine.request_model_reload()
            else:
                log_debug(
                    f"WARNING: {field} changed to {value!r} before an engine was "
                    f"attached; it will apply at the next model load."
                )
        self.saved.emit(field)
        return True

    def paintEvent(self, event):
        """The light ground, then section 9's corner marks over it."""
        super().paintEvent(event)
        self.paint_registration_marks()
