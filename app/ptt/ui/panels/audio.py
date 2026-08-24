"""
The Audio panel: which microphone, how loud it is right now, and when the
stream is held open.

Three things here are worth stating.

**The picker is a combo box, and it is the only kind of control that could be.**
The device count is not known at design time, so a radio group cannot be laid out
in advance. Everything else on the panel is a checkbox, which is the other half
of gui_handoff section 9's rule that radio buttons and checkboxes never share a
view.

**What it lists is a filtered enumeration, and that is not cosmetic.** PortAudio
reports every device once per Windows audio API: on the development laptop, one
array microphone arrives as fourteen entries, including two `PC Speaker` outputs
whose kernel-streaming pins advertise input channels, a `Stereo Mix` loopback,
two placeholders that mean "the default device", and one entry that cannot be
opened at all. A user cannot be asked to choose between fourteen rows describing
one microphone. `audio.pickable_devices` reduces that to the copies from a single
host API -- see `audio.PICKER_HOST_APIS` for why WASAPI is not among them and why
the order was measured rather than assumed. Nothing is lost: the whole
enumeration goes to debug_log.txt once per run with each index, `audio_device`
accepts any of them, and a device set that way is shown here with its API named.

**The meter polls; nothing signals it.** The peak amplitude is computed in
PortAudio's realtime callback and left on a plain attribute, and this panel
reads it on a 30 Hz timer. `audio.Recorder._callback` explains why: emitting a
Qt signal from that thread allocates, locks and wakes the GUI thread several
hundred times a second, and a callback that overruns its deadline drops audio.
The audio buffer itself is never touched from here.

**Both behaviour checkboxes are booleans, not thresholds set to zero.**
`IDLE_THRESHOLD_SEC` and `MIN_RECORD_SEC` are durations, and the tempting
reading of section 6.3 -- a checkbox that writes 0 into each -- switches off
`NFR-4` and `FR-3` respectively and re-enters retrospective issue #6 at twenty
times a second. So each checkbox gates whether its constant applies, and the
constant itself keeps its value and stays visible on the Advanced tab, which
says when it is currently bypassed.
"""

import math

from PySide6.QtCore import Property, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout,
    QWidget,
)

from ptt import audio as audio_mod
from ptt.ui.panels import InstantApplyPanel

#: What the combo box's first entry means, and the default. Not a device index:
#: `None` is the value every configuration written before this build carries by
#: omission, so an existing installation behaves exactly as it did.
FOLLOW_DEFAULT = "Follow the Windows default device"

#: Meter refresh. The same 30 ms the keyboard diagram polls at, for the same
#: reason -- fast enough to read as live, slow enough to cost nothing.
METER_MS = 30

#: Bars in the meter, and how fast a peak falls away. Without the decay the
#: meter is a strobe: speech is not continuous at 33 Hz and the bars would drop
#: to nothing between syllables.
METER_BARS = 14
METER_DECAY = 0.82

#: Quietest level the readout names, in dBFS. Below this the microphone is
#: reporting its own noise floor and a number would be false precision.
METER_FLOOR_DB = -60.0


def to_dbfs(level):
    """
    A 0.0-1.0 peak as dBFS, floored at `METER_FLOOR_DB`.

    Pure, so the floor and the silent case are testable without an audio device.
    Digital full scale is 0 dB, so every real value is negative; silence is not
    "-inf dB" on a readout, it is nothing worth printing.
    """
    if level <= 0:
        return METER_FLOOR_DB
    return max(METER_FLOOR_DB, 20.0 * math.log10(min(level, 1.0)))


def meter_fill(level, bars=METER_BARS):
    """
    How many bars a level lights, on the dBFS scale the readout uses.

    Linear amplitude is the wrong scale for a meter: ordinary speech peaks
    around 0.05-0.2, which on a linear bar is a twitch at the left-hand end and
    reads as a microphone that is barely working. Mapping the floor-to-full-scale
    dB range across the bars puts normal speech in the middle, which is what
    "speak to test" is asking the user to confirm.
    """
    if level <= 0:
        return 0
    fraction = 1.0 - (to_dbfs(level) / METER_FLOOR_DB)
    return max(1, min(bars, int(round(fraction * bars))))


class LevelMeter(QWidget):
    """
    The input level, drawn rather than assembled from widgets.

    Fourteen `QFrame`s whose heights were restyled at 33 Hz would be fourteen
    style re-resolutions per frame. Colours come off Qt properties written by
    style.qss, the same indirection the status dot and the model table's
    delegates use, so no colour lives in Python.
    """

    BAR_WIDTH = 5
    BAR_GAP = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("levelMeter")
        self.setFixedSize(
            METER_BARS * (self.BAR_WIDTH + self.BAR_GAP) - self.BAR_GAP, 32
        )
        # Only reached if style.qss failed to load, which qt_theme logs.
        fallback = self.palette().windowText().color()
        self._bar = fallback
        self._hot = fallback
        self._track = fallback
        self._level = 0.0

    def _get_bar(self):
        return self._bar

    def _set_bar(self, colour):
        self._bar = colour

    def _get_hot(self):
        return self._hot

    def _set_hot(self, colour):
        self._hot = colour

    def _get_track(self):
        return self._track

    def _set_track(self, colour):
        self._track = colour

    barColour = Property(QColor, _get_bar, _set_bar)
    hotColour = Property(QColor, _get_hot, _set_hot)
    trackColour = Property(QColor, _get_track, _set_track)

    def set_level(self, level):
        self._level = level
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        lit = meter_fill(self._level)
        height = self.height()
        for index in range(METER_BARS):
            # Each bar is taller than the last, so the shape says "louder" even
            # in a screenshot with no colour.
            bar_height = max(4, int(height * (index + 1) / METER_BARS))
            x = index * (self.BAR_WIDTH + self.BAR_GAP)
            rect_top = height - bar_height
            if index >= lit:
                colour = self._track
            elif index >= METER_BARS - 2:
                colour = self._hot
            else:
                colour = self._bar
            painter.fillRect(x, rect_top, self.BAR_WIDTH, bar_height, colour)


class AudioPanel(InstantApplyPanel):
    """Pick the microphone, watch it work, and choose when it is held open."""

    def __init__(self, settings, parent=None):
        super().__init__(settings, parent)

        #: Set while the widgets are written from the settings object, so a
        #: programmatic change is not mistaken for the user's and saved back.
        self._syncing = False
        self._devices = ()
        self._offered = ()
        self._level = 0.0

        box = QVBoxLayout(self)
        box.setContentsMargins(28, 22, 28, 18)
        box.setSpacing(0)

        heading = QLabel("Microphone")
        heading.setObjectName("panelTitle")
        blurb = QLabel(
            "The stream stays open while you are at the machine and is released "
            "after four minutes idle, so the first word is never clipped. "
            "Plugging a headset in mid-session does not break the hotkey."
        )
        blurb.setObjectName("panelBlurb")
        blurb.setWordWrap(True)
        box.addWidget(heading)
        box.addWidget(blurb)
        box.addSpacing(16)

        columns = QHBoxLayout()
        columns.setSpacing(22)
        columns.addLayout(self._build_device_column(), 1)
        columns.addWidget(self._build_behaviour_box(), 0, Qt.AlignmentFlag.AlignTop)
        box.addLayout(columns)
        box.addStretch(1)

        self._timer = QTimer(self)
        self._timer.setInterval(METER_MS)
        self._timer.timeout.connect(self._poll_level)

        self.reload_devices()

    # -- construction -------------------------------------------------------

    def _build_device_column(self):
        column = QVBoxLayout()
        column.setSpacing(6)

        column.addWidget(_caption("Input device"))
        self._combo = QComboBox()
        self._combo.setObjectName("deviceCombo")
        # Windows device names run to seventy characters -- "Microphone Array
        # (Intel Smart Sound Technology for Digital Microphones) · Windows
        # DirectSound" -- and a combo box sized to its longest entry would set a
        # minimum width of 585 px here, which forces the whole window past its
        # stated 820 px minimum and puts a horizontal scrollbar under every tab.
        # The popup still lays itself out to fit the names in full.
        self._combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._combo.setMinimumContentsLength(28)
        self._combo.activated.connect(self._on_device_chosen)
        column.addWidget(self._combo)

        self._device_note = QLabel("")
        self._device_note.setObjectName("panelNote")
        self._device_note.setWordWrap(True)
        column.addWidget(self._device_note)

        column.addSpacing(12)
        column.addWidget(self._build_meter_box())
        column.addStretch(1)
        return column

    def _build_meter_box(self):
        frame = QFrame()
        frame.setObjectName("panelBox")
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(14, 12, 14, 12)
        inner.setSpacing(8)
        inner.addWidget(_caption("Input level"))

        row = QHBoxLayout()
        row.setSpacing(16)
        self._meter = LevelMeter()
        self._readout = QLabel("")
        self._readout.setObjectName("panelValue")
        row.addWidget(self._meter, 0, Qt.AlignmentFlag.AlignBottom)
        row.addWidget(self._readout, 1, Qt.AlignmentFlag.AlignBottom)
        inner.addLayout(row)
        return frame

    def _build_behaviour_box(self):
        frame = QFrame()
        frame.setObjectName("panelBox")
        frame.setFixedWidth(340)
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(14, 12, 14, 12)
        inner.setSpacing(4)
        inner.addWidget(_caption("Behaviour"))
        inner.addSpacing(4)

        # The note under each box is not decoration. Every one of these three
        # switches something off that was turned on to fix a specific reported
        # failure, and a checkbox with only a label gives the user no way to
        # know that. A tooltip would not do it either -- it appears only if you
        # already suspected there was something to read.
        self._warm = self._add_check(
            inner, "Keep the stream warm while active",
            "On: the microphone is held open while you are at the machine and "
            "released after four minutes idle. Off: it is opened when you press "
            "the key and closed when you let go, which costs about a second of "
            "hardware wake-up and makes some headsets chime every time.",
            self._on_warm_toggled,
        )
        self._short = self._add_check(
            inner, "Ignore holds shorter than 0.30 s",
            "On: a brush of the key is discarded instead of being transcribed. "
            "Off: every hold is sent to the model, including accidental ones.",
            self._on_short_toggled,
        )
        self._click = self._add_check(
            inner, "Play a click when recording starts",
            "Plays through the Windows output device, so an open desktop "
            "microphone can hear it and it lands in the recording. Off by "
            "default for that reason; with a headset it is free.",
            self._on_click_toggled,
        )
        inner.addStretch(1)
        return frame

    def _add_check(self, layout, label, note, handler):
        box = QCheckBox(label)
        box.clicked.connect(handler)
        layout.addWidget(box)
        caption = QLabel(note)
        caption.setObjectName("panelNote")
        caption.setWordWrap(True)
        caption.setContentsMargins(22, 0, 0, 10)
        layout.addWidget(caption)
        return box

    # -- devices ------------------------------------------------------------

    def reload_devices(self):
        """
        Re-enumerate PortAudio and rebuild the combo box.

        Called when the tab is shown rather than on every settings change: the
        query initialises and terminates PortAudio around itself, and doing that
        on each of the engine's state changes would be a lot of work to discover
        that nothing had been plugged in.

        Both lists are kept. The combo offers `pickable_devices`, which is one
        host API's copies of the hardware; the full enumeration stays because a
        saved index the picker does not offer still has to resolve to a name
        rather than showing as missing.
        """
        self._devices = audio_mod.input_devices()
        self._offered = audio_mod.pickable_devices(self._devices)
        self._syncing = True
        try:
            self._combo.clear()
            self._combo.addItem(FOLLOW_DEFAULT, None)
            for device in self._offered:
                self._combo.addItem(device.label(), device.index)
        finally:
            self._syncing = False
        self.refresh()

    def refresh(self):
        """Re-read the settings object into the widgets; see the base class."""
        self._syncing = True
        try:
            self._select_saved_device()
            self._warm.setChecked(bool(self._settings.keep_stream_warm))
            self._short.setChecked(bool(self._settings.ignore_short_holds))
            self._click.setChecked(bool(self._settings.start_click))
        finally:
            self._syncing = False
        self._update_device_note()

    def _select_saved_device(self):
        """
        Show the saved choice, including the two cases the picker cannot offer.

        A saved index the combo does not list is either a device on a host API
        the picker hides -- someone set `audio_device` by hand from the log --
        or one that no longer enumerates at all. Both get a row of their own
        rather than the combo quietly settling on the first one. The setting has
        not changed and neither has what will happen (`Recorder` falls back to
        the Windows default at open time and logs that it did), so a picker
        showing "Follow the Windows default device" would be presenting the
        fallback as the user's choice.
        """
        saved = self._settings.audio_device
        if saved is None:
            # Row 0 always is "follow the default", and asking `findData` about
            # None is asking it about a null QVariant, which is not a question
            # worth relying on.
            self._combo.setCurrentIndex(0)
            return

        index = self._combo.findData(saved)
        if index < 0:
            hidden = next((d for d in self._devices if d.index == saved), None)
            self._combo.addItem(
                hidden.label_with_api() if hidden
                else f"Device {saved} — not connected",
                saved,
            )
            index = self._combo.count() - 1
        self._combo.setCurrentIndex(index)

    def _update_device_note(self):
        if self._settings.audio_device is None:
            parts = ["Follows Windows, so changing the default there changes it here."]
        else:
            parts = ["Fixed to this device. If it stops resolving — Windows "
                     "renumbers when hardware changes — the app records from "
                     "the default and says so in debug_log.txt."]

        hidden = len(self._devices) - len(self._offered)
        if hidden:
            parts.append(
                f"{hidden} duplicates of the same hardware, seen through other "
                f"Windows audio APIs, are hidden; debug_log.txt lists them all."
            )

        active = self._engine.input_device_name() if self._engine else ""
        if active:
            parts.append(f"Recording from {active}.")
        elif self._engine is not None and not self._engine.stream_is_open():
            parts.append("The stream is closed at the moment.")

        self._device_note.setText("  ".join(parts))

    # -- controls -----------------------------------------------------------

    def _on_device_chosen(self, index):
        """
        No `reload_model=True`: switching microphone must not reload Whisper.

        The engine re-reads `settings.audio_device` on every poll iteration and
        reopens the stream itself, exactly as it does for the chord, so the
        change is live within one iteration and the resident model is untouched.
        """
        if self._syncing:
            return
        device = self._combo.itemData(index)
        if device == self._settings.audio_device:
            return
        self.apply_now("audio_device", device)
        self._update_device_note()

    def _on_warm_toggled(self, checked):
        self._toggle("keep_stream_warm", checked)

    def _on_short_toggled(self, checked):
        self._toggle("ignore_short_holds", checked)

    def _on_click_toggled(self, checked):
        self._toggle("start_click", checked)

    def _toggle(self, field, checked):
        if self._syncing or bool(getattr(self._settings, field)) == bool(checked):
            return
        self.apply_now(field, bool(checked))

    # -- the meter ----------------------------------------------------------

    def _poll_level(self):
        """
        Read the peak the audio callback left behind and repaint.

        Peak-hold with decay rather than the raw value: speech is not continuous
        at 33 Hz, so the bars would drop to nothing between syllables and the
        meter would read as a fault.
        """
        if self._engine is None:
            return
        self._level = max(self._engine.input_level(), self._level * METER_DECAY)
        self._meter.set_level(self._level)

        if not self._engine.stream_is_open():
            self._readout.setText("stream released · press the hotkey to open it")
        elif self._level <= 0:
            self._readout.setText("silent · speak to test")
        else:
            self._readout.setText(f"{to_dbfs(self._level):.0f} dBFS · speak to test")

    # -- lifecycle ----------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        self.reload_devices()
        self._timer.start()

    def hideEvent(self, event):
        """Stop metering off screen: nothing repaints a hidden widget usefully."""
        self._timer.stop()
        self._level = 0.0
        super().hideEvent(event)


def _caption(text):
    label = QLabel(text.upper())
    label.setObjectName("caption")
    label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    return label
