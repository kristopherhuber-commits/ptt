"""
The Advanced panel: the engine's constants, what each one is, and why it is not
editable here.

**Nothing on this panel writes anything.** gui_handoff section 6.5 calls it
"read-mostly" and then says what making one of them editable costs: it becomes a
validated `Settings` field with a logged fallback, not a raw write. Every value
below was chosen to fix a specific reported failure, and none of them is a
preference:

- `BEAM_SIZE` and `LANGUAGE` change what the model produces, and nothing else in
  this window would explain a transcript that got worse.
- `MIN_RECORD_SEC` and `IDLE_THRESHOLD_SEC` are durations whose useful range is
  bounded at both ends -- issue #6 at one end, `FR-3` and `NFR-4` at the other.
  What a user actually wants to say about them is "on" or "off", and that is
  what the Audio tab's two checkboxes are. The values stay here, and each row
  says when its constant is currently bypassed, so the two panels cannot
  disagree about what is in force.
- `Shift+Insert` is load-bearing: `Ctrl+V` does not paste in WSL or in a bash
  terminal, which is where this application is mostly used (`inject.py`'s module
  docstring, `FR-C1`). Section 6.5 says to warn on change if it is exposed. It
  is not exposed, so the row states the constraint instead.

**The values are read from the modules, not transcribed beside them.** `rows()`
imports the constants it reports, so a change to `transcribe.BEAM_SIZE` shows up
here with no edit to this file and the panel cannot drift into describing a
build that no longer exists. That is also what makes it testable without Qt.

"Start with Windows" is a readout rather than the checkbox section 6.5 draws.
Setting it means creating a `.lnk` through `WScript.Shell` COM and re-applying
`install.ps1`'s run-as-admin byte patch -- the installer's job, duplicated inside
the app -- and a checkbox that cannot be clicked explains nothing, because
Windows delivers no mouse events to a disabled widget and its tooltip therefore
never appears. The same lesson the Model panel's Delete button already learned.
"""

import os
from typing import NamedTuple

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from ptt import engine as engine_mod
from ptt import inject, paths, transcribe
from ptt.ui.panels import InstantApplyPanel

#: Appended to a row whose constant the Audio tab has switched off. The value
#: itself is still shown -- it has not changed, it is simply not being applied.
BYPASSED = " · bypassed from the Audio tab"


class Row(NamedTuple):
    """One line of the table: what it is, why, and what it is set to."""
    name: str
    note: str
    value: str


def rows(settings):
    """
    The panel's contents, read from the modules that own each value.

    Pure and Qt-free, so a test can assert that every row reports the live
    constant rather than a string someone typed next to it -- which is the
    failure mode this whole panel exists to prevent.
    """
    return (
        Row("Beam size",
            "higher is slower and slightly more accurate",
            str(transcribe.BEAM_SIZE)),
        Row("Voice activity filter",
            "trims silence before inference",
            "On" if transcribe.VAD_FILTER else "Off"),
        Row("Minimum hold",
            "shorter holds are treated as an accidental tap (FR-3)",
            f"{engine_mod.MIN_RECORD_SEC:.2f} s"
            + ("" if settings.ignore_short_holds else BYPASSED)),
        Row("Release microphone when idle",
            "keeps the stream warm until then (NFR-2, NFR-4)",
            f"{engine_mod.IDLE_THRESHOLD_SEC:.0f} s"
            + ("" if settings.keep_stream_warm else BYPASSED)),
        Row("Paste method",
            "Ctrl+V does not paste in WSL or a bash terminal; this does",
            inject.PASTE_CHORD_LABEL),
        Row("Language",
            "a fixed language is faster than autodetect",
            str(transcribe.LANGUAGE or "autodetect")),
        Row("Start with Windows",
            "shortcut in the Startup folder, created by the installer",
            "Present" if os.path.exists(paths.startup_shortcut_path())
            else "Not present"),
    )


class AdvancedPanel(InstantApplyPanel):
    """
    A read-only list. It subclasses `InstantApplyPanel` for the two things every
    tab needs and nothing else supplies -- the engine hand-off and the
    status-bar message channel -- and never calls `apply_now`.
    """

    def __init__(self, settings, parent=None):
        super().__init__(settings, parent)

        box = QVBoxLayout(self)
        box.setContentsMargins(28, 22, 28, 18)
        box.setSpacing(0)

        heading = QLabel("Advanced")
        heading.setObjectName("panelTitle")
        blurb = QLabel(
            "Defaults are safe. Every one of these was chosen to fix a specific "
            "documented failure, so they are shown rather than offered — the "
            "two that are genuinely a choice are the Audio tab's checkboxes, "
            "and this page says when one of them has switched a value off."
        )
        blurb.setObjectName("panelBlurb")
        blurb.setWordWrap(True)
        box.addWidget(heading)
        box.addWidget(blurb)
        box.addSpacing(16)

        self._frame = QFrame()
        self._frame.setObjectName("panelBox")
        self._list = QVBoxLayout(self._frame)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(0)
        box.addWidget(self._frame)

        table = rows(settings)
        self._values = [
            self._add_row(row, last=(index == len(table) - 1))
            for index, row in enumerate(table)
        ]

        box.addSpacing(12)
        note = QLabel(
            "Changing one of these means it becomes a saved setting with its "
            "own validation and its own logged fallback, not a value written "
            "straight into the engine — so none of them is editable until it "
            "has one. The paste method in particular: it is Shift+Insert "
            "because Ctrl+V is swallowed by WSL and bash terminals, which is "
            "where most of what this application types ends up."
        )
        note.setObjectName("panelNote")
        note.setWordWrap(True)
        box.addWidget(note)
        box.addStretch(1)

    def _add_row(self, row, last=False):
        line = QFrame()
        line.setObjectName("settingRow")
        # Qt style sheets have no `:last-child`, and a hairline under the final
        # row would draw a second line on top of the frame's own border.
        line.setProperty("last", last)
        across = QHBoxLayout(line)
        across.setContentsMargins(14, 9, 14, 9)
        across.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(1)
        name = QLabel(row.name)
        note = QLabel(row.note)
        note.setObjectName("panelNote")
        left.addWidget(name)
        left.addWidget(note)

        value = QLabel(row.value)
        value.setObjectName("panelValue")

        across.addLayout(left, 1)
        across.addWidget(value)
        self._list.addWidget(line)
        return value

    def refresh(self):
        """
        Re-read the values. Two of them can change while this tab is open -- the
        Audio checkboxes bypass a constant, and the Startup shortcut can appear
        or vanish under the app -- so the whole list is re-read rather than
        assuming a read-only panel is also a static one.
        """
        for label, row in zip(self._values, rows(self._settings)):
            label.setText(row.value)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
