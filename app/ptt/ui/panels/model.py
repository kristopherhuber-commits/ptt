"""
The Model panel: the Whisper size tiers, and what each one costs on this machine.

Three decisions here are worth stating, because all three are about not
presenting a guess as a fact.

**No accuracy column.** Word error rate cannot be measured without a labelled
corpus, and quoting a published figure measured on someone else's dataset in a
settings window would be presenting their benchmark as this machine's. The
`Character` column carries the qualitative trade-off instead, which is honest
and is what the choice actually turns on.

**Latency is measured, never predicted.** A model that has never been timed on
this machine shows an em dash, and the relative bars are scaled across only the
rows that have real numbers. Every measurement is stored with the digest of the
clip it was taken against, so re-recording the sample invalidates the old
figures rather than leaving them on screen looking comparable.

**Measuring times the model that is already loaded.** Selecting a row loads that
model, so the button never needs a second `WhisperModel` beside the working one
-- see `Engine.request_benchmark` for why two of them on one card is a problem
that ends with dictation broken rather than with a failed measurement.

Downloading and deleting models are out of scope for this pass (gui_handoff
section 11). The button is here because the mockup has it; it says what it does
not do rather than half-doing it.
"""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRect, Qt, Property
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QRadioButton, QStyledItemDelegate, QTableView, QVBoxLayout,
)

from ptt import config, paths, transcribe
from ptt.ui.panels import InstantApplyPanel

#: Placeholder for a model this machine has never timed. The same em dash the
#: status view uses for a value it cannot honestly supply.
UNMEASURED = "—"

#: Extra roles the table model answers, for the things a delegate draws rather
#: than the default text renderer.
PARAMS_ROLE = Qt.ItemDataRole.UserRole + 1
SECONDS_ROLE = Qt.ItemDataRole.UserRole + 2
RATIO_ROLE = Qt.ItemDataRole.UserRole + 3
LOCAL_ROLE = Qt.ItemDataRole.UserRole + 4

ROW_HEIGHT_PX = 36


#: Re-exported from `config`, which owns the `benchmarks` schema and therefore
#: owns how one measurement is keyed. `V-UI-09` still names it here because this
#: is where the panel reads it.
benchmark_key = config.benchmark_key


def _format_bytes(count):
    """Bytes as MB or GB, matching the static table's units."""
    mb = count / (1024 * 1024)
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB"


class ModelTableModel(QAbstractTableModel):
    """
    One row per size tier, over `transcribe.MODELS`.

    Holds no settings of its own: `refresh` is handed everything that can
    change -- which model is current, which device the app is on, and the
    measurement cache -- so the panel remains the only thing that reads
    `Settings`.
    """

    COLUMNS = ("Model", "Disk", "Measured", "Character", "State")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []
        self.refresh({}, "cpu", "")

    def refresh(self, benchmarks, device, clip_id):
        """Rebuild every row: disk state is filesystem truth and can change."""
        self.beginResetModel()
        self._rows = []
        installed = transcribe.installed_sizes()
        for info in transcribe.MODELS:
            local = installed.get(info.name)
            entry = benchmarks.get(benchmark_key(info.name, device)) or {}
            # A measurement taken against a different recording of the sample
            # is not comparable to one taken against this one, so it does not
            # count as a measurement at all.
            seconds = entry.get("seconds") if entry.get("clip") == clip_id else None
            self._rows.append({
                "name": info.name,
                "params": info.params,
                "character": info.character,
                "disk": _format_bytes(local) if local is not None else info.disk,
                "local": local is not None,
                "seconds": seconds,
            })

        measured = [r["seconds"] for r in self._rows if r["seconds"]]
        slowest = max(measured) if measured else 0
        for row in self._rows:
            row["ratio"] = (row["seconds"] / slowest) if (row["seconds"] and slowest) else 0.0
        self.endResetModel()

    def row_of(self, model_name):
        """Which row holds `model_name`, or -1. Used to restore the selection."""
        for i, row in enumerate(self._rows):
            if row["name"] == model_name:
                return i
        return -1

    def name_at(self, row):
        return self._rows[row]["name"] if 0 <= row < len(self._rows) else ""

    # -- QAbstractTableModel ------------------------------------------------

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.COLUMNS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        column = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            return (
                row["name"], row["disk"],
                f"{row['seconds']:.2f} s" if row["seconds"] else UNMEASURED,
                row["character"],
                "Downloaded" if row["local"] else "Not on disk",
            )[column]
        if role == PARAMS_ROLE:
            return row["params"]
        if role == SECONDS_ROLE:
            return row["seconds"]
        if role == RATIO_ROLE:
            return row["ratio"]
        if role == LOCAL_ROLE:
            return row["local"]
        if role == Qt.ItemDataRole.TextAlignmentRole and column in (1, 4):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None


class ModelTable(QTableView):
    """
    The view, and the only place the delegates below get a colour from.

    Delegates paint, and a `QStyledItemDelegate` is not a widget, so no style
    sheet selector can reach one. The same indirection `StatusDot` uses solves
    it: the view carries the colours as Qt properties, style.qss writes them
    with `qproperty-`, and the delegates read them back off `option.widget`.
    That keeps the session-2 rule literally true -- every colour in the UI lives
    in the stylesheet -- for the parts of the UI that are drawn by hand.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("modelTable")
        fallback = self.palette().windowText().color()
        self._bar = fallback
        self._track = fallback
        self._muted = fallback
        self._tag_fill = fallback
        self._tag_ink = fallback

    def _get_bar(self):
        return self._bar

    def _set_bar(self, colour):
        self._bar = colour

    def _get_track(self):
        return self._track

    def _set_track(self, colour):
        self._track = colour

    def _get_muted(self):
        return self._muted

    def _set_muted(self, colour):
        self._muted = colour

    def _get_tag_fill(self):
        return self._tag_fill

    def _set_tag_fill(self, colour):
        self._tag_fill = colour

    def _get_tag_ink(self):
        return self._tag_ink

    def _set_tag_ink(self, colour):
        self._tag_ink = colour

    barColour = Property(QColor, _get_bar, _set_bar)
    trackColour = Property(QColor, _get_track, _set_track)
    mutedColour = Property(QColor, _get_muted, _set_muted)
    tagFillColour = Property(QColor, _get_tag_fill, _set_tag_fill)
    tagInkColour = Property(QColor, _get_tag_ink, _set_tag_ink)


def _muted_font(base):
    font = QFont(base)
    font.setPointSizeF(max(base.pointSizeF() - 1.5, 6.0))
    return font


class NameDelegate(QStyledItemDelegate):
    """The model name over its parameter count, which is one cell, not two."""

    def paint(self, painter, option, index):
        self.initStyleOption(option, index)
        style = option.widget.style() if option.widget else None
        if style is not None:
            # Draws the row background and the selection, so the two lines below
            # sit on the same ground every other column does.
            option.text = ""
            style.drawControl(style.ControlElement.CE_ItemViewItem, option, painter, option.widget)

        rect = option.rect.adjusted(8, 4, -8, -4)
        painter.save()
        painter.setPen(option.palette.text().color())
        painter.setFont(option.font)
        top = QRect(rect.left(), rect.top(), rect.width(), rect.height() // 2)
        painter.drawText(top, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                         index.data(Qt.ItemDataRole.DisplayRole) or "")
        painter.setPen(_colour(option.widget, "mutedColour", option))
        painter.setFont(_muted_font(option.font))
        bottom = QRect(rect.left(), rect.center().y(), rect.width(), rect.height() // 2)
        painter.drawText(bottom, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                         index.data(PARAMS_ROLE) or "")
        painter.restore()


class MeasuredDelegate(QStyledItemDelegate):
    """
    A bar and a figure, or an em dash.

    The bar is drawn, not a widget in the cell: a `QProgressBar` per row would
    be six live widgets inside a view that already knows how to paint itself.
    Rows with no measurement draw no track at all, so an unmeasured model is
    visibly absent from the comparison rather than showing an empty bar that
    reads as "fast".
    """

    BAR_WIDTH = 90
    BAR_HEIGHT = 4

    def paint(self, painter, option, index):
        self.initStyleOption(option, index)
        style = option.widget.style() if option.widget else None
        if style is not None:
            option.text = ""
            style.drawControl(style.ControlElement.CE_ItemViewItem, option, painter, option.widget)

        seconds = index.data(SECONDS_ROLE)
        rect = option.rect.adjusted(8, 0, -8, 0)
        painter.save()

        if seconds:
            mid = rect.center().y()
            track = QRect(rect.left(), mid - self.BAR_HEIGHT // 2,
                          self.BAR_WIDTH, self.BAR_HEIGHT)
            painter.fillRect(track, _colour(option.widget, "trackColour", option))
            filled = QRect(track)
            filled.setWidth(max(2, int(self.BAR_WIDTH * (index.data(RATIO_ROLE) or 0))))
            painter.fillRect(filled, _colour(option.widget, "barColour", option))
            text_rect = rect.adjusted(self.BAR_WIDTH + 10, 0, 0, 0)
            painter.setPen(option.palette.text().color())
        else:
            text_rect = rect
            painter.setPen(_colour(option.widget, "mutedColour", option))

        painter.setFont(option.font)
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                         index.data(Qt.ItemDataRole.DisplayRole) or "")
        painter.restore()


class StateDelegate(QStyledItemDelegate):
    """`Downloaded` / `Not on disk` as a tag, matching the design system's chip."""

    def paint(self, painter, option, index):
        self.initStyleOption(option, index)
        style = option.widget.style() if option.widget else None
        if style is not None:
            option.text = ""
            style.drawControl(style.ControlElement.CE_ItemViewItem, option, painter, option.widget)

        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        local = bool(index.data(LOCAL_ROLE))
        metrics = option.fontMetrics
        width = metrics.horizontalAdvance(text) + 16
        height = metrics.height() + 4
        chip = QRect(option.rect.right() - width - 8,
                     option.rect.center().y() - height // 2, width, height)

        painter.save()
        if local:
            painter.fillRect(chip, _colour(option.widget, "tagFillColour", option))
            painter.setPen(_colour(option.widget, "tagInkColour", option))
        else:
            painter.fillRect(chip, _colour(option.widget, "trackColour", option))
            painter.setPen(_colour(option.widget, "mutedColour", option))
        painter.setFont(option.font)
        painter.drawText(chip, int(Qt.AlignmentFlag.AlignCenter), text)
        painter.restore()


def _colour(widget, name, option):
    """
    One of the view's stylesheet-supplied colours, falling back to the palette.

    The fallback is only reached if style.qss failed to load, which `qt_theme`
    logs; an unstyled-but-legible table is the right outcome there.
    """
    value = widget.property(name) if widget is not None else None
    return value if isinstance(value, QColor) else option.palette.text().color()


class ModelPanel(InstantApplyPanel):
    """
    Pick the model and the device. Both apply instantly and reload the engine.

    Hardware has the last word: when `cuda_supported` is False the engine forces
    `use_gpu` off, so the GPU radio is disabled and says why rather than
    accepting a click and springing back.
    """

    def __init__(self, settings, cuda_supported, parent=None):
        super().__init__(settings, parent)
        self.cuda_supported = cuda_supported

        #: Set while the widgets are being written from the settings object, so
        #: a programmatic selection change is not mistaken for a user's click
        #: and written straight back to disk.
        self._syncing = False

        box = QVBoxLayout(self)
        box.setContentsMargins(28, 22, 28, 18)
        box.setSpacing(0)

        heading = QLabel("Transcription model")
        heading.setObjectName("panelTitle")
        blurb = QLabel(
            "Bigger models are more accurate and slower. Selecting one loads it "
            "immediately — the app is unavailable for a few seconds while it "
            "does, and downloads it first if it is not already on disk."
        )
        blurb.setObjectName("panelBlurb")
        blurb.setWordWrap(True)
        box.addWidget(heading)
        box.addWidget(blurb)
        box.addSpacing(14)

        box.addLayout(self._build_device_row())
        box.addSpacing(8)
        box.addWidget(self._build_table())
        box.addSpacing(10)
        box.addLayout(self._build_buttons())
        box.addStretch(1)

        self.refresh()

    # -- construction -------------------------------------------------------

    def _build_device_row(self):
        row = QHBoxLayout()
        row.setSpacing(18)

        caption = QLabel("RUN ON")
        caption.setObjectName("caption")
        self._gpu = QRadioButton("GPU (CUDA)")
        self._cpu = QRadioButton("CPU")
        self._device_group = QButtonGroup(self)
        self._device_group.addButton(self._gpu)
        self._device_group.addButton(self._cpu)
        self._gpu.clicked.connect(lambda: self._set_device(True))
        self._cpu.clicked.connect(lambda: self._set_device(False))

        self._device_note = QLabel("")
        self._device_note.setObjectName("panelNote")

        if not self.cuda_supported:
            self._gpu.setEnabled(False)
            self._device_note.setText(
                "No CUDA device was found, so this build runs on the CPU. "
                "See the Diagnostics tab."
            )

        row.addWidget(caption)
        row.addWidget(self._gpu)
        row.addWidget(self._cpu)
        row.addWidget(self._device_note, 1)
        return row

    def _build_table(self):
        self._model = ModelTableModel(self)
        self._table = ModelTable(self)
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT_PX)
        self._table.setItemDelegateForColumn(0, NameDelegate(self._table))
        self._table.setItemDelegateForColumn(2, MeasuredDelegate(self._table))
        self._table.setItemDelegateForColumn(4, StateDelegate(self._table))

        header = self._table.horizontalHeader()
        header.setHighlightSections(False)
        for column, mode, width in (
            (0, QHeaderView.ResizeMode.Fixed, 150),
            (1, QHeaderView.ResizeMode.Fixed, 80),
            (2, QHeaderView.ResizeMode.Fixed, 170),
            (3, QHeaderView.ResizeMode.Stretch, 0),
            (4, QHeaderView.ResizeMode.Fixed, 120),
        ):
            header.setSectionResizeMode(column, mode)
            if width:
                self._table.setColumnWidth(column, width)

        self._table.selectionModel().selectionChanged.connect(self._on_row_selected)

        # Exactly tall enough for every tier at once, and no taller. Six rows
        # is the whole list and it is not going to grow, so scrolling them
        # would hide part of a comparison whose entire job is to be seen side
        # by side -- and letting the view stretch instead leaves an empty band
        # under the last row that reads as a missing tier.
        self._table.setFixedHeight(
            header.sizeHint().height() + self._model.rowCount() * ROW_HEIGHT_PX + 2
        )
        return self._table

    def _build_buttons(self):
        row = QHBoxLayout()
        row.setSpacing(12)

        self._note = QLabel(
            "Nothing in the MEASURED column is guessed or quoted from anyone "
            "else's benchmark. Pressing Measure times one transcription of a "
            "bundled 30-second clip on this machine, so the figures are your "
            "hardware's — which is the only thing that tells you whether a "
            "bigger model is worth the wait here. Models never measured show no "
            "figure at all."
        )
        self._note.setObjectName("panelNote")
        self._note.setWordWrap(True)

        self._measure = QPushButton("Measure on this machine")
        self._measure.setToolTip(
            "Times one transcription of the bundled 30-second sample clip using "
            "the model selected above, and records the result against this "
            "device. Dictation pauses until the run finishes."
        )
        self._measure.clicked.connect(self._on_measure)

        self._delete = QPushButton("Delete from disk")
        self._delete.clicked.connect(self._on_delete)

        row.addWidget(self._note, 1)
        row.addWidget(self._measure)
        row.addWidget(self._delete)
        return row

    # -- state --------------------------------------------------------------

    def device(self):
        """Which device the model is loaded on, by the same rule the engine uses."""
        return "cuda" if (self._settings.use_gpu and self.cuda_supported) else "cpu"

    def refresh(self):
        """Re-read everything from the settings object; see the base class.

        Called on every engine state change as well as on show, which is what
        keeps this panel and the tray menu's GPU/CPU items agreeing without a
        signal between them.
        """
        self._syncing = True
        try:
            # Check the one that should be on, never uncheck the other.
            # `setChecked(False)` on the checked member of an exclusive
            # QButtonGroup is silently ignored, so clearing first and setting
            # second leaves both radios showing the *old* choice while the
            # setting says otherwise -- the panel and config.json disagreeing
            # about what is running, which is the whole thing this window is
            # supposed to make unambiguous.
            on_gpu = bool(self._settings.use_gpu) and self.cuda_supported
            (self._gpu if on_gpu else self._cpu).setChecked(True)
            self._model.refresh(
                self._settings.benchmarks, self.device(), transcribe.benchmark_clip_id()
            )
            row = self._model.row_of(self._settings.model)
            if row >= 0:
                self._table.selectRow(row)
            self._measure.setEnabled(True)
        finally:
            self._syncing = False

    def record_benchmark(self, model_name, device, seconds):
        """
        Store one measurement. Called on the GUI thread from `QtApp`'s bridge.

        A **new** dict, not an insert into the existing one: `Settings` holds
        values that are replaced wholesale, and the engine thread can be reading
        this object at any moment. See `config.Settings`' docstring.
        """
        entry = {
            "seconds": round(float(seconds), 3),
            "at": _now(),
            "clip": transcribe.benchmark_clip_id(),
        }
        merged = dict(self._settings.benchmarks)
        merged[benchmark_key(model_name, device)] = entry
        self.apply_now("benchmarks", merged)
        self.refresh()
        self.message.emit(f"Measured {model_name} on {device.upper()}: {seconds:.2f} s")

    # -- controls -----------------------------------------------------------

    def _on_row_selected(self, _selected, _deselected):
        if self._syncing:
            return
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return
        name = self._model.name_at(rows[0].row())
        if not name or name == self._settings.model:
            return
        self.apply_now("model", name, reload_model=True)
        self.refresh()

    def _set_device(self, use_gpu):
        if self._syncing or bool(self._settings.use_gpu) == use_gpu:
            return
        self.apply_now("use_gpu", use_gpu, reload_model=True)
        self.refresh()

    def _on_measure(self):
        if self._engine is None:
            return
        self._measure.setEnabled(False)
        self.message.emit(
            f"Measuring {self._settings.model} on {self.device().upper()} — "
            f"dictation pauses until it finishes."
        )
        self._engine.request_benchmark()

    def _on_delete(self):
        """
        The stub gui_handoff section 11 asks for.

        A message rather than a disabled button: Qt does not deliver mouse
        events to a disabled widget, so its tooltip never appears and the button
        would explain nothing to anyone who did not already know.
        """
        rows = self._table.selectionModel().selectedRows()
        name = self._model.name_at(rows[0].row()) if rows else self._settings.model
        self.message.emit(
            f"Deleting a downloaded model is not implemented yet — remove "
            f"{paths.local_model_dir(name)} by hand."
        )

    # -- lifecycle ----------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()


def _now():
    """Local timestamp for a stored measurement, seconds resolution."""
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")
