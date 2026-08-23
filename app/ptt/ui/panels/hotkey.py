"""
The Hotkey panel: a keyboard diagram that is both the binding control and a
live view of the physical keyboard.

Two things about it are not obvious from a screenshot.

**Why it polls.** Every key the user presses shades on the diagram as it goes
down and unshades as it comes up, including keys that can never be bound, so the
board reads as live hardware and the user can see the app is registering their
keyboard at all. That is done by polling `GetAsyncKeyState` on a 30 ms timer,
through `hotkey.poll_vks`, not by `keyPressEvent`.

Qt key events are the obvious choice and are the wrong one. They arrive only
while this window has focus, so a key pressed here and released after alt-tab
never delivers its release and stays shaded forever; and Qt does not distinguish
left from right for modifiers without decoding the native scan code, which is
the distinction this entire panel is about. Polling also means the picker and
the detector share one code path and one failure mode -- the reason
`hotkey.chord_held` polls in the first place is that Windows silently
unregisters low-level keyboard hooks after UAC prompts, screen locks, sleep and
USB hotplug (retrospective issues #7 and #8), and a picker built on hooks would
have inherited that.

**Why the board carries virtual keys and not key names.** The bindable set is
`hotkey.BINDABLE_BY_VK`: a cap may be bound if the engine's own table has a
bindable name for that cap's virtual key. Nothing here lists the nine bindable
keys. Adding one to `hotkey.KEYS` lights up the matching cap with no edit to
this file, and removing one dims it.

Layout note: the main block is `QHBoxLayout` rows inside a `QVBoxLayout`, not
one `QGridLayout`. A grid aligns columns across rows and a keyboard's main block
deliberately does not align -- 1.5u and 1.75u caps stagger every row against the
one above it -- so a grid could only express it by giving every cap a column span
in some fine unit, which is a worse way to write the same pixels. The **keypad**
is a grid, because its cells genuinely do align and its `+` and `Enter` genuinely
are two rows tall.
"""

import json
from typing import NamedTuple

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from ptt import hotkey as hotkey_mod
from ptt.ui.panels import InstantApplyPanel

#: How often the physical keyboard is sampled. gui_handoff section 10's
#: acceptance criterion is "shades within ~50 ms"; 30 ms leaves headroom for the
#: restyle, and ~90 `GetAsyncKeyState` calls at 33 Hz is not measurable load.
POLL_MS = 30

#: A chord longer than this is not offered. The engine has no such limit and
#: `parse_chord` accepts any length, so a longer chord already in config.json is
#: displayed as it is; the cap applies to what a click may *build*.
MAX_CHORD_KEYS = 3

#: One keycap unit and the gap between caps, in pixels, from the mockup: a 1u
#: cap is 28 px with a 4 px gap, so an n-unit cap spans n caps and the n-1 gaps
#: between them. 1.5u is 44 px, 2.25u is 68 px, and so on.
UNIT_PX = 32
GAP_PX = 4
CAP_HEIGHT_PX = 28

#: Width of the compatibility panel. Wide enough that the two-warning chords --
#: any Alt combination, which is the common way to pick a bad hotkey -- are
#: readable without the panel scrolling. Three warnings at once needs a scroll,
#: and a chord that earns three is one worth pausing over anyway.
WARNING_BOX_PX = 420

#: Which side an unsided binding expands to when "match either side" is cleared.
#: The right-hand modifiers are the safe ones: ordinary typing reaches for the
#: left-hand Ctrl, Shift and Alt for every Ctrl+C and every capital letter, and
#: the shipped default is Right Ctrl for exactly that reason.
PREFERRED_SIDE = 1


def cap_width(units):
    """Pixel width of a cap `units` wide, gaps included."""
    return int(round(UNIT_PX * units)) - GAP_PX


class Cap(NamedTuple):
    """
    One key drawn on the board.

    `vk` is the Windows virtual-key code. It is what the live shading polls, and
    it is also how the cap finds out whether it may be bound -- see the module
    docstring. A `vk` of 0 is a spacer.
    """
    label: str
    vk: int
    units: float = 1.0


def _gap(units=0.5):
    return Cap("", 0, units)


def _letters(text):
    """Caps for a run of letters or digits, whose virtual key is their ASCII code."""
    return [Cap(c, ord(c)) for c in text]


#: The main block, one list per row. The numeric keypad is separate; see
#: `NUMPAD` below.
#:
#: The virtual-key codes are the standard Win32 ones. Letters and digits are
#: their ASCII value; the punctuation keys are the `VK_OEM_*` block, which is
#: laid out for a US keyboard and is what `GetAsyncKeyState` reports regardless
#: of the labels printed on the user's actual keycaps.
#:
#: The arrow caps are labelled `< ^ v >` rather than with the arrow characters,
#: which is what the mockup does and is not only a style choice: the bundled
#: Barlow faces have no glyph at U+2190-2193, and a keycap rendered by
#: QPushButton does not fall back to another family the way a QLabel does, so
#: the real arrows come out as four empty boxes.
ROWS = (
    [Cap("Esc", 0x1B), _gap(1.0)]
    + [Cap(f"F{n}", 0x6F + n) for n in range(1, 5)] + [_gap()]
    + [Cap(f"F{n}", 0x6F + n) for n in range(5, 9)] + [_gap()]
    + [Cap(f"F{n}", 0x6F + n) for n in range(9, 13)] + [_gap()]
    + [Cap("PrtSc", 0x2C), Cap("ScrLk", 0x91), Cap("Pause", 0x13)],

    [Cap("`", 0xC0)] + _letters("1234567890")
    + [Cap("-", 0xBD), Cap("=", 0xBB), Cap("Backspace", 0x08, 2.0), _gap(),
       Cap("Ins", 0x2D), Cap("Home", 0x24), Cap("PgUp", 0x21)],

    [Cap("Tab", 0x09, 1.5)] + _letters("QWERTYUIOP")
    + [Cap("[", 0xDB), Cap("]", 0xDD), Cap("\\", 0xDC, 1.5), _gap(),
       Cap("Del", 0x2E), Cap("End", 0x23), Cap("PgDn", 0x22)],

    [Cap("Caps", 0x14, 1.75)] + _letters("ASDFGHJKL")
    + [Cap(";", 0xBA), Cap("'", 0xDE), Cap("Enter", 0x0D, 2.25)],

    [Cap("Shift", 0xA0, 2.25)] + _letters("ZXCVBNM")
    + [Cap(",", 0xBC), Cap(".", 0xBE), Cap("/", 0xBF), Cap("Shift", 0xA1, 2.5),
       _gap(1.75), Cap("^", 0x26)],

    [Cap("Ctrl", 0xA2, 1.5), Cap("Win", 0x5B, 1.5), Cap("Alt", 0xA4, 1.5),
     Cap("Space", 0x20, 4.5), Cap("Alt", 0xA5, 1.5), Cap("Win", 0x5C, 1.5),
     Cap("Menu", 0x5D, 1.5), Cap("Ctrl", 0xA3, 1.5), _gap(),
     Cap("<", 0x25), Cap("v", 0x28), Cap(">", 0x27)],
)


class Pad(NamedTuple):
    """One numeric-keypad cap. The keypad is a grid, so it is placed by cell."""
    label: str
    vk: int
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1


#: The numeric keypad. A `QGridLayout`, unlike the main block: a keypad's rows
#: and columns really do align, and `+` and `Enter` really are two rows tall,
#: which is exactly what spans are for.
#:
#: The digits report `VK_NUMPAD0`-`9` only while Num Lock is **on**. With it off
#: the same physical keys report `VK_HOME`, `VK_END` and the rest, so pressing
#: keypad 7 shades `Home` over on the main block instead. That is the hardware
#: telling the truth about itself, and it is worth being able to see.
NUMPAD = (
    Pad("NumLk", 0x90, 0, 0), Pad("/", 0x6F, 0, 1),
    Pad("*", 0x6A, 0, 2), Pad("-", 0x6D, 0, 3),

    Pad("7", 0x67, 1, 0), Pad("8", 0x68, 1, 1),
    Pad("9", 0x69, 1, 2), Pad("+", 0x6B, 1, 3, rowspan=2),

    Pad("4", 0x64, 2, 0), Pad("5", 0x65, 2, 1), Pad("6", 0x66, 2, 2),

    Pad("1", 0x61, 3, 0), Pad("2", 0x62, 3, 1), Pad("3", 0x63, 3, 2),
    # No virtual key of its own: Windows reports VK_RETURN for both Enter keys
    # and separates them only with an extended-key flag GetAsyncKeyState does
    # not carry. Both caps therefore shade together, which is what the OS sees.
    Pad("Enter", 0x0D, 3, 3, rowspan=2),

    Pad("0", 0x60, 4, 0, colspan=2), Pad(".", 0x6E, 4, 2),
)

#: Width of the blank column between the main block and the keypad.
BLOCK_GAP_PX = cap_width(0.5) + GAP_PX


class HotkeyPanel(InstantApplyPanel):
    """
    The push-to-talk key picker.

    Chord edits are whole-tuple rebinds through `apply_now`, never a mutation of
    the tuple already on `Settings` -- `config.Settings`' docstring explains why
    that distinction is what lets the engine pick the new chord up on its next
    poll iteration with no restart, and why adding a lock or freezing the
    dataclass would break it.
    """

    def __init__(self, settings, parent=None):
        super().__init__(settings, parent)

        #: The chord exactly as `Settings` holds it -- sided names, unsided
        #: aliases, or a mix. Never normalised on load: rewriting a setting the
        #: user did not touch is churn, and an unsided name they put in
        #: config.json by hand must survive being looked at.
        self._chord = tuple(settings.hotkey)

        #: cap virtual key -> the caps drawn for it. A list, not one button:
        #: the two Enter keys share `VK_RETURN` because Windows gives them one
        #: virtual key, so a single poll result has to shade both.
        self._buttons = {}

        #: Which virtual keys are currently shaded. Diffed against each poll so
        #: only the caps that actually changed are re-polished -- restyling
        #: ninety widgets thirty times a second would not be free.
        self._held = set()

        box = QVBoxLayout(self)
        box.setContentsMargins(28, 24, 28, 20)
        box.setSpacing(0)

        heading = QLabel("Push-to-talk key")
        heading.setObjectName("panelTitle")
        blurb = QLabel(
            "Click a key to bind it. Only keys that type nothing on their own "
            "can be bound; everything else is dimmed, and shades as you press "
            "it. Hold the bound key to record, release to transcribe — "
            "immediately, with no restart."
        )
        blurb.setObjectName("panelBlurb")
        blurb.setWordWrap(True)
        box.addWidget(heading)
        box.addWidget(blurb)
        box.addSpacing(16)

        box.addWidget(self._build_board())
        box.addSpacing(18)
        box.addLayout(self._build_readout())
        box.addStretch(1)

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)

        self.refresh()

    # -- construction -------------------------------------------------------

    def _build_board(self):
        """The main block and the keypad, side by side and row-aligned."""
        board = QWidget()
        board.setObjectName("keyboard")
        board.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        across = QHBoxLayout(board)
        across.setContentsMargins(0, 0, 0, 0)
        across.setSpacing(BLOCK_GAP_PX)
        across.addWidget(self._build_main_block())
        across.addWidget(self._build_numpad())
        return board

    def _build_main_block(self):
        block = QWidget()
        block.setFixedWidth(cap_width(max(sum(c.units for c in row) for row in ROWS)))

        rows = QVBoxLayout(block)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(GAP_PX)

        for row in ROWS:
            line = QHBoxLayout()
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(GAP_PX)
            for cap in row:
                line.addWidget(self._build_cap(cap))
            # The rows are not all the same length -- a keyboard's are not -- so
            # the short ones stay left-aligned rather than being spread to fit.
            line.addStretch(1)
            rows.addLayout(line)

        return block

    def _build_numpad(self):
        column = QWidget()
        column.setFixedWidth(cap_width(4))

        stack = QVBoxLayout(column)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(0)
        # The keypad starts one row down: there is nothing above it on a real
        # board, and this is what keeps its rows level with the main block's.
        stack.addSpacing(CAP_HEIGHT_PX + GAP_PX)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(GAP_PX)
        for pad in NUMPAD:
            button = self._build_cap(
                Cap(pad.label, pad.vk, pad.colspan), height_units=pad.rowspan
            )
            grid.addWidget(button, pad.row, pad.col, pad.rowspan, pad.colspan)
        stack.addLayout(grid)
        stack.addStretch(1)

        return column

    def _build_cap(self, cap, height_units=1):
        """
        One keycap. `height_units` is for the keypad's two-row `+` and `Enter`.

        A cap `n` units tall spans `n` caps and the `n-1` gaps between them,
        which is the same arithmetic as its width -- hence `cap_width` for both.

        Spacers are plain widgets, not disabled buttons: a gap in a keyboard is
        a gap, and drawing it as a key with no label would make the board look
        like it had holes in it.
        """
        height = cap_width(height_units) if height_units > 1 else CAP_HEIGHT_PX

        if cap.vk == 0:
            spacer = QWidget()
            spacer.setFixedSize(cap_width(cap.units), height)
            return spacer

        button = QPushButton(cap.label)
        button.setObjectName("keycap")
        button.setFixedSize(cap_width(cap.units), height)
        # Clicking a key must not move the focus ring onto the board: this is a
        # picture of a keyboard, and a focus rectangle wandering around it reads
        # as a rendering fault.
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setProperty("held", False)

        name = hotkey_mod.BINDABLE_BY_VK.get(cap.vk)
        if name is None:
            button.setEnabled(False)
            button.setToolTip("Not bindable — types a character or acts on its own.")
        else:
            button.setCheckable(True)
            button.setToolTip(hotkey_mod.KEY_LABELS[name])
            button.clicked.connect(lambda _checked=False, n=name: self._on_cap_clicked(n))

        self._buttons.setdefault(cap.vk, []).append(button)
        return button

    def _build_readout(self):
        """The bound chord, what lands in config.json, and the warnings."""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(28)

        left = QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(self._caption("Bound chord"))

        # The chip and the JSON sit on separate lines, not side by side as the
        # mockup draws them. A three-key chord makes that one line about 540 px
        # wide, which competes with the compatibility box for the panel's width
        # and forces a horizontal scrollbar -- and a settings window that
        # scrolls sideways is worse than one that stacks two short lines.
        chord_line = QHBoxLayout()
        chord_line.setSpacing(10)
        self._chord_label = QLabel("")
        self._chord_label.setObjectName("chordChip")
        chord_line.addWidget(self._chord_label)
        chord_line.addStretch(1)
        left.addLayout(chord_line)

        self._json_label = QLabel("")
        self._json_label.setObjectName("chordJson")
        left.addWidget(self._json_label)

        self._either = QCheckBox("Match either side")
        self._either.setToolTip(
            "Writes the unsided name, so the chord fires from the left-hand key "
            "as well as the right."
        )
        self._either.clicked.connect(self._on_either_clicked)
        left.addSpacing(10)
        left.addWidget(self._either)
        left.addStretch(1)

        self._warning_box = QFrame()
        warn = self._warning_box
        warn.setObjectName("warningBox")
        warn.setFixedWidth(WARNING_BOX_PX)
        warn.setProperty("risky", False)
        warn_box = QVBoxLayout(warn)
        warn_box.setContentsMargins(14, 12, 14, 12)
        warn_box.setSpacing(6)
        warn_box.addWidget(self._caption("Compatibility"))
        self._warnings = QLabel("")
        self._warnings.setObjectName("warningText")
        self._warnings.setWordWrap(True)
        self._warnings.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        warn_box.addWidget(self._warnings)
        warn_box.addStretch(1)

        row.addLayout(left, 1)
        row.addWidget(warn, 0, Qt.AlignmentFlag.AlignTop)
        return row

    @staticmethod
    def _caption(text):
        label = QLabel(text.upper())
        label.setObjectName("caption")
        return label

    # -- editing ------------------------------------------------------------

    def _on_cap_clicked(self, name):
        """
        Toggle one physical key in the chord.

        Three cases, and the middle one is the interesting one:

        - the key is bound by its own name -> unbind it, unless it is the only
          key left. A chord may never be empty: `parse_chord` rejects an empty
          list and the engine would silently fall back to the default, so the
          last bound key stays bound and the click does nothing.
        - the key's family is bound *unsided*, so both of its caps are lit ->
          the click means "not this side", and the unsided name narrows to the
          other side. Nothing is lost and the chord keeps its length.
        - otherwise -> bind it. A fourth key replaces the chord outright rather
          than growing it, because a four-finger chord is not a hotkey.
        """
        chord = self._chord
        family = hotkey_mod.FAMILY.get(name)
        unsided = hotkey_mod.EITHER_SIDE.get(family) if family else None

        if name in chord:
            if len(chord) == 1:
                self._sync()
                return
            new = tuple(k for k in chord if k != name)
        elif unsided is not None and unsided in chord:
            other = next(s for s in hotkey_mod.SIDES[family] if s != name)
            new = tuple(other if k == unsided else k for k in chord)
        elif len(chord) >= MAX_CHORD_KEYS:
            new = (name,)
        else:
            new = chord + (name,)

        self._commit(hotkey_mod.canonical(new))

    def _on_either_clicked(self, checked):
        """
        Swap the whole chord between its sided and unsided spellings.

        Ticking is lossless. Clearing has to choose a side, because the unsided
        name does not record one; see `PREFERRED_SIDE` for why it picks the
        right-hand key.
        """
        if checked:
            new = tuple(
                hotkey_mod.EITHER_SIDE.get(hotkey_mod.FAMILY.get(k, ""), k)
                for k in self._chord
            )
        else:
            new = tuple(
                hotkey_mod.SIDES[k][PREFERRED_SIDE] if k in hotkey_mod.SIDES else k
                for k in self._chord
            )
        self._commit(hotkey_mod.canonical(new))

    def _commit(self, chord):
        """Rebind the chord and persist it. A no-op change writes nothing."""
        if not chord or chord == self._chord:
            self._sync()
            return
        self._chord = chord
        # A whole-tuple rebind, which is what makes the engine's live re-read
        # safe without a lock. See config.Settings' docstring.
        self.apply_now("hotkey", chord)
        self._sync()

    # -- display ------------------------------------------------------------

    def refresh(self):
        """Re-read the chord from the settings object; see the base class."""
        self._chord = tuple(self._settings.hotkey)
        self._sync()

    def _is_bound(self, name):
        """
        Whether a cap is part of the chord.

        An unsided name lights both of its caps, because both of them really are
        bound: `hotkey.chord_held` fires on either one.
        """
        if name in self._chord:
            return True
        family = hotkey_mod.FAMILY.get(name)
        return bool(family) and hotkey_mod.EITHER_SIDE.get(family) in self._chord

    def _sync(self):
        """Push the chord onto the board, the readout and the warning box."""
        for vk, name in hotkey_mod.BINDABLE_BY_VK.items():
            for button in self._buttons.get(vk, ()):
                button.setChecked(self._is_bound(name))

        self._chord_label.setText(hotkey_mod.chord_label(self._chord))
        self._json_label.setText('"hotkey": ' + json.dumps(list(self._chord)))

        families = {hotkey_mod.FAMILY.get(k) for k in self._chord} - {None}
        unsided = {hotkey_mod.EITHER_SIDE[f] for f in families}
        # Checked only when every sided key in the chord is already unsided, so
        # a mixed chord shows the box clear and the board still tells the truth
        # about which caps are bound.
        self._either.setEnabled(bool(families))
        self._either.setChecked(bool(families) and unsided <= set(self._chord))

        # Classified on the chord as it will be written, not as it was clicked.
        # Ticking "match either side" is the one action that can turn a safe
        # binding into a hazardous one, and the box has to say so at the moment
        # it happens rather than describing the chord the user no longer has.
        warnings = hotkey_mod.classify(self._chord)
        self._warnings.setText(
            "\n\n".join(hotkey_mod.WARNING_PREFIX + w for w in warnings)
            if warnings else hotkey_mod.SAFE_NOTE
        )
        # Both the label and the frame carry the flag: the text goes amber and
        # the box outlines itself, so an unsafe chord is visible from across the
        # panel rather than only once you are reading the paragraph.
        for widget in (self._warnings, self._warning_box):
            widget.setProperty("risky", bool(warnings))
            _restyle(widget)

    # -- live shading -------------------------------------------------------

    def _poll(self):
        """
        Sample the physical keyboard and shade what is down.

        The visibility and activation check is the whole of the "clear on focus
        loss" requirement, and it is done here rather than in an event handler
        on purpose: `focusOutEvent` never fires for this widget, because focus
        lives on the caps and the checkbox rather than on the panel, and a
        parent being hidden does not reliably deliver a hide event to a child.
        Asking the two questions on a timer that is already running cannot miss
        a case -- alt-tab, minimise, switching tabs and closing the window all
        land in the same branch.
        """
        if not self.isVisible() or not self.isActiveWindow():
            self._clear_held()
            return
        self._apply_held(hotkey_mod.poll_vks(self._buttons.keys()))

    def _apply_held(self, down):
        """Restyle only the caps whose state actually changed."""
        for vk in self._held ^ down:
            for button in self._buttons.get(vk, ()):
                button.setProperty("held", vk in down)
                _restyle(button)
        self._held = down

    def _clear_held(self):
        if self._held:
            self._apply_held(set())

    # -- lifecycle ----------------------------------------------------------

    def showEvent(self, event):
        """Poll only while this tab is the one on screen."""
        super().showEvent(event)
        self._sync()
        self._timer.start()

    def hideEvent(self, event):
        """
        Stop polling and unshade everything.

        Clearing here as well as in `_poll` is not redundant: switching tabs
        leaves the board built and off screen, and a key that was down at that
        moment would otherwise still be shaded when the tab came back.
        """
        self._timer.stop()
        self._clear_held()
        super().hideEvent(event)


def _restyle(widget):
    """
    Re-resolve a widget's stylesheet after a dynamic property changed.

    Qt matches `[held="true"]` when the widget is polished and not again, so a
    property written afterwards has no visible effect until the style is
    unpolished and re-polished. This is the standard dance and it is the reason
    `_apply_held` bothers to diff.
    """
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
