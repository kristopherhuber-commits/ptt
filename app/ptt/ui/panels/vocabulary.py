"""
The Vocabulary panel: replacement rules applied to the transcript before it is
pasted.

The matching itself is not here. It lives in `ptt.vocabulary`, which is pure and
has its own test module, for the same reason `hotkey.classify` is not in the
Hotkey panel: the rules of a feature are a fact about the application, not about
the widgets that edit them, and every one of them is checkable without a
microphone.

Two decisions this panel makes visible.

**Deleting offers an undo instead of asking first.** gui_handoff section 6 allows
a confirmation dialog for exactly two destructive actions and prefers an undo
"where practical". Restoring one row to the index it came from is practical, so
there is no dialog -- the row goes, the button beside it lights up, and pressing
it puts the rule back where it was.

**The Scope column is present, read-only and says Always.** Section 6.4 describes
scopes of "Always / specific app classes"; section 11 puts per-application
behaviour rules out of scope for this pass. So the field is stored and validated
and the column is drawn, and a rule carrying any other scope is dropped on load
with a logged reason rather than being widened to Always -- see
`vocabulary.parse_rule` for why a fallback that makes a rule apply *more* widely
than it was written is the wrong direction.

The preview line is the only way to see what a rule does without speaking into
the microphone, which is why it is here: the substitution is otherwise invisible
until it has already altered something the user dictated.
"""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QTableView, QVBoxLayout,
)

from ptt import vocabulary as vocabulary_mod
from ptt.ui.panels import InstantApplyPanel

#: Heard / Typed of a rule created by the Add button. Filled in rather than
#: blank so the new row is visible in the table and immediately editable; an
#: empty `heard` is not a valid rule and `parse_rule` would reject it.
NEW_RULE = ("new phrase", "replacement")

#: What the preview shows before anything has been typed into it.
PREVIEW_HINT = "Type a sentence to see what would be pasted."

ROW_HEIGHT_PX = 30


class VocabularyTableModel(QAbstractTableModel):
    """
    One row per rule, over a tuple of `vocabulary.Rule`.

    Holds no settings: the panel hands it the current tuple and receives edits
    back through `edited`, so this class never writes config.json and the panel
    remains the only thing that touches `Settings`.
    """

    COLUMNS = ("Heard", "Typed", "Scope")

    def __init__(self, on_edit, parent=None):
        super().__init__(parent)
        self._rules = ()
        self._on_edit = on_edit

    def set_rules(self, rules):
        self.beginResetModel()
        self._rules = tuple(rules)
        self.endResetModel()

    def rule_at(self, row):
        return self._rules[row] if 0 <= row < len(self._rules) else None

    # -- QAbstractTableModel ------------------------------------------------

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rules)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.COLUMNS[section]
        return None

    def flags(self, index):
        base = (Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        # Scope is displayed, not chosen: this build honours one value.
        if index.column() in (0, 1):
            return base | Qt.ItemFlag.ItemIsEditable
        return base

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        rule = self._rules[index.row()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return (rule.heard, rule.typed, rule.scope.title())[index.column()]
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        """
        Commit one cell edit, or refuse it.

        Refusing is the interesting half. An empty `heard` is not a rule -- it
        would match nothing, or everything, depending on how it was compiled --
        so the edit is rejected and the cell keeps its old text rather than the
        table holding a row the engine silently ignores.
        """
        if role != Qt.ItemDataRole.EditRole or index.column() not in (0, 1):
            return False

        rule = self._rules[index.row()]
        text = str(value)
        if index.column() == 0:
            heard = vocabulary_mod.normalise_phrase(text)
            if not heard:
                return False
            updated = rule._replace(heard=heard)
        else:
            updated = rule._replace(typed=text)

        if updated == rule:
            return False
        self._on_edit(index.row(), updated)
        return True


class VocabularyPanel(InstantApplyPanel):
    """Add, edit and remove replacement rules. Every change applies instantly."""

    def __init__(self, settings, parent=None):
        super().__init__(settings, parent)

        #: The last rule deleted and where it was, so the undo can put it back
        #: at its own index rather than on the end.
        self._undo = None

        box = QVBoxLayout(self)
        box.setContentsMargins(28, 22, 28, 18)
        box.setSpacing(0)

        heading = QLabel("Vocabulary")
        heading.setObjectName("panelTitle")
        blurb = QLabel(
            "Replacements applied to the transcript before it is pasted. "
            "Matched whole-word and case-insensitively, in one pass — a "
            "replacement is never itself replaced — and where two rules could "
            "match the same words the longer phrase wins. The Typed column is "
            "literal text."
        )
        blurb.setObjectName("panelBlurb")
        blurb.setWordWrap(True)
        box.addWidget(heading)
        box.addWidget(blurb)
        box.addSpacing(14)

        box.addWidget(self._build_table())
        box.addSpacing(10)
        box.addLayout(self._build_buttons())
        box.addSpacing(14)
        box.addLayout(self._build_preview())
        box.addSpacing(10)

        note = QLabel(
            "Runs after the model's own cleanup, which already strips the runs "
            "of full stops large-v3 produces on trailing silence, and before "
            "the text reaches the clipboard — so what the log reports as the "
            "transcript is what was pasted. Per-application scopes are not in "
            "this build; a rule saved with any other scope is dropped on load "
            "and the reason is written to debug_log.txt."
        )
        note.setObjectName("panelNote")
        note.setWordWrap(True)
        box.addWidget(note)
        box.addStretch(1)

        self.refresh()

    # -- construction -------------------------------------------------------

    def _build_table(self):
        self._model = VocabularyTableModel(self._on_cell_edited, self)
        self._table = QTableView(self)
        self._table.setObjectName("vocabTable")
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT_PX)
        self._table.setMinimumHeight(200)

        header = self._table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(2, 100)
        return self._table

    def _build_buttons(self):
        row = QHBoxLayout()
        row.setSpacing(10)

        hint = QLabel("Double-click a cell to edit it.")
        hint.setObjectName("panelNote")

        add = QPushButton("Add rule")
        add.clicked.connect(self._on_add)
        self._delete = QPushButton("Delete rule")
        self._delete.clicked.connect(self._on_delete)
        self._undo_button = QPushButton("Undo delete")
        self._undo_button.setEnabled(False)
        self._undo_button.clicked.connect(self._on_undo)

        row.addWidget(hint, 1)
        row.addWidget(add)
        row.addWidget(self._delete)
        row.addWidget(self._undo_button)
        return row

    def _build_preview(self):
        column = QVBoxLayout()
        column.setSpacing(6)

        caption = QLabel("PREVIEW")
        caption.setObjectName("caption")
        column.addWidget(caption)

        self._preview_input = QLineEdit()
        self._preview_input.setPlaceholderText(
            "e.g. see translate two runs on the GPU"
        )
        self._preview_input.textChanged.connect(self._update_preview)
        column.addWidget(self._preview_input)

        self._preview_output = QLabel(PREVIEW_HINT)
        self._preview_output.setObjectName("panelValue")
        self._preview_output.setWordWrap(True)
        column.addWidget(self._preview_output)
        return column

    # -- state --------------------------------------------------------------

    def refresh(self):
        """Re-read the rules from the settings object; see the base class."""
        self._model.set_rules(self._settings.vocabulary)
        self._update_preview()

    def _commit(self, rules):
        """
        Write the whole tuple back.

        A new tuple every time, never an edit of the one already on `Settings`:
        the engine reads that attribute on the transcription path while this
        runs on the GUI thread, and a whole-value rebind is what makes that safe
        without a lock. `config.Settings`' docstring is the long version.

        `reload_model` stays False -- the vocabulary is re-read on every
        transcription, so an edit applies to the next thing said.
        """
        self.apply_now("vocabulary", tuple(rules))
        self.refresh()

    # -- controls -----------------------------------------------------------

    def _on_cell_edited(self, row, rule):
        """
        Called from inside `setData`, which is why the commit is deferred.

        `_commit` ends in `beginResetModel`, and resetting a model while Qt is
        still unwinding the edit it asked for invalidates the indexes the view
        is holding at that moment. One turn of the event loop is enough: the
        editor has closed and the view is idle by the time it fires.
        """
        rules = list(self._settings.vocabulary)
        if not 0 <= row < len(rules):
            return
        rules[row] = rule
        QTimer.singleShot(0, lambda: self._commit(rules))

    def _on_add(self):
        rules = list(self._settings.vocabulary)
        rules.append(vocabulary_mod.Rule(*NEW_RULE))
        self._commit(rules)
        # Land the user in the cell they have to change, so a placeholder rule
        # is never left behind by someone who did not notice it needed editing.
        index = self._model.index(len(rules) - 1, 0)
        self._table.setCurrentIndex(index)
        self._table.edit(index)

    def _on_delete(self):
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            self.message.emit("Select a rule to delete it.")
            return
        row = rows[0].row()
        rules = list(self._settings.vocabulary)
        if not 0 <= row < len(rules):
            return
        removed = rules.pop(row)
        self._undo = (row, removed)
        self._undo_button.setEnabled(True)
        self._commit(rules)
        self.message.emit(
            f"Deleted “{removed.heard}” → “{removed.typed}”. Undo delete puts it back."
        )

    def _on_undo(self):
        if self._undo is None:
            return
        row, removed = self._undo
        rules = list(self._settings.vocabulary)
        rules.insert(min(row, len(rules)), removed)
        self._undo = None
        self._undo_button.setEnabled(False)
        self._commit(rules)

    def _update_preview(self):
        text = self._preview_input.text()
        if not text:
            self._preview_output.setText(PREVIEW_HINT)
            return
        result = vocabulary_mod.apply_rules(text, self._settings.vocabulary)
        self._preview_output.setText(
            result if result != text else f"{result}   (no rule matched)"
        )

    # -- lifecycle ----------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
