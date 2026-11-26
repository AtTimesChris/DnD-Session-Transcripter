from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
                               QDialogButtonBox, QAbstractItemView, QPlainTextEdit, QCheckBox, QHBoxLayout, QPushButton)

from .edit_learning import mine_corrections


class ShiftCheckTable(QTableWidget):
    """Tick a row's box, then Shift+click another: every box in between is set the same way."""

    def __init__(self, *args):
        super().__init__(*args)
        self._anchor = None

    def mousePressEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        if index.isValid() and index.column() == 0:
            if (event.modifiers() & Qt.ShiftModifier) and self._anchor is not None and self.item(self._anchor, 0) is not None:
                state = self.item(self._anchor, 0).checkState()
                lo, hi = sorted((self._anchor, index.row()))
                for r in range(lo, hi + 1):
                    if self.item(r, 0) is not None:
                        self.item(r, 0).setCheckState(state)
                event.accept()
                return
            super().mousePressEvent(event)
            self._anchor = index.row()
            return
        super().mousePressEvent(event)


class LearnedCorrectionsDialog(QDialog):
    """Review word corrections found in the user's own edits. Nothing is applied until approved."""

    def __init__(self, db, campaign_id, parent=None):
        super().__init__(parent)
        self.db, self.campaign_id = db, campaign_id
        self.setWindowTitle("Learn from my edits")
        self.resize(760, 520)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Words you corrected in the transcript editor, grouped by how often. Tick the ones that are real "
            "corrections: the correct spelling is added to this campaign's names (given to Whisper only if Campaign context is on), "
            "the editor will suggest the fix, and Correct names… will find it. Select rows and press \u201cNever offer selected rows again\u201d to hide mistakes for good."))
        layout.itemAt(0).widget().setWordWrap(True)
        self.table = ShiftCheckTable(0, 4)
        self.table.setHorizontalHeaderLabels(["Use", "Heard as", "Should be", "Times corrected"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self.table, 1)
        controls = QHBoxLayout()
        self.show_single = QCheckBox("Also show corrections made only once")
        self.show_single.toggled.connect(self._load)
        tick_all, untick_all = QPushButton("Tick all"), QPushButton("Untick all")
        tick_all.clicked.connect(lambda: self._set_all(Qt.Checked)); untick_all.clicked.connect(lambda: self._set_all(Qt.Unchecked))
        controls.addWidget(self.show_single); controls.addStretch(); controls.addWidget(tick_all); controls.addWidget(untick_all)
        layout.addLayout(controls)
        hint = QLabel("Tip: tick one box, then hold Shift and click another to tick everything between them.")
        layout.addWidget(hint)
        self.empty = QLabel("")
        layout.addWidget(self.empty)
        buttons = QDialogButtonBox()
        self.apply_btn = buttons.addButton("Add ticked to campaign vocabulary", QDialogButtonBox.AcceptRole)
        self.dismiss_btn = buttons.addButton("Never offer selected rows again", QDialogButtonBox.ActionRole)
        buttons.addButton(QDialogButtonBox.Close)
        self.apply_btn.clicked.connect(self._apply)
        self.dismiss_btn.clicked.connect(self._dismiss)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._load()

    def _load(self):
        events = self.db.campaign_text_correction_events(self.campaign_id)
        found = mine_corrections(events, self.db.known_correction_pairs(self.campaign_id),
                                 min_count=1 if self.show_single.isChecked() else 2)
        self.table._anchor = None
        self.table.setRowCount(len(found))
        for r, item in enumerate(found):
            use = QTableWidgetItem()
            use.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            use.setCheckState(Qt.Unchecked)  # nothing is ticked until you choose
            self.table.setItem(r, 0, use)
            for c, key in ((1, "heard"), (2, "intended")):
                cell = QTableWidgetItem(item[key])
                cell.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                self.table.setItem(r, c, cell)
            count = QTableWidgetItem(str(item["count"]))
            count.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(r, 3, count)
        self.empty.setText("" if found else "No corrections repeated at least twice yet. Tick \u201cAlso show corrections made only once\u201d to see the rest, or correct more words in the editor.")
        self.apply_btn.setEnabled(bool(found)); self.dismiss_btn.setEnabled(bool(found))

    def _set_all(self, state):
        for r in range(self.table.rowCount()):
            self.table.item(r, 0).setCheckState(state)

    def _row_pair(self, row):
        return self.table.item(row, 1).text().strip(), self.table.item(row, 2).text().strip()

    def _apply(self):
        added = 0
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).checkState() == Qt.Checked:
                heard, intended = self._row_pair(r)
                if heard and intended:
                    self.db.record_learned_correction(self.campaign_id, heard, intended, True)
                    added += 1
        self.added = added
        self.accept()

    def _dismiss(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            heard, intended = self._row_pair(r)
            if heard and intended:
                self.db.record_learned_correction(self.campaign_id, heard, intended, False)
            self.table.removeRow(r)


class TextReportDialog(QDialog):
    """Read-only, scrollable plain-text report (re-check / import summaries can be long)."""

    def __init__(self, title, text, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(640, 520)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        layout = QVBoxLayout(self)
        box = QPlainTextEdit(text)
        box.setReadOnly(True)
        layout.addWidget(box)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class ImportClipsDialog(QDialog):
    """Tick which reviewed clips should bring their text and timestamps into the new transcript.
    Unticked clips still give their speaker; the new transcript keeps its own text there."""

    def __init__(self, rows, parent=None):
        super().__init__(parent)
        self.rows = rows
        self.setWindowTitle("Choose clips to carry over")
        self.resize(1000, 600)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        layout = QVBoxLayout(self)
        note = QLabel("Tick the clips whose TEXT and timestamps should replace the new transcript. Clips you edited are ticked "
                      "for you; the rest keep the new transcript's text and only take their speaker.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(len(rows), 6)
        self.table.setHorizontalHeaderLabels(["Use", "Time", "Speaker", "You edited it", "Your reviewed text", "New transcript text"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(4, QHeaderView.Stretch); header.setSectionResizeMode(5, QHeaderView.Stretch)
        for r, row in enumerate(rows):
            use = QTableWidgetItem(); use.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            use.setCheckState(Qt.Checked if row["edited"] else Qt.Unchecked)
            self.table.setItem(r, 0, use)
            m, s = divmod(int(row["start"]), 60); h, m = divmod(m, 60)
            cells = [f"{h}:{m:02d}:{s:02d}", row["speaker_id"], "yes" if row["edited"] else "", row["text"], row["new_text"]]
            for c, text in enumerate(cells, start=1):
                item = QTableWidgetItem(text); item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self.table.setItem(r, c, item)
        self.table.resizeColumnToContents(1); self.table.resizeColumnToContents(2); self.table.setColumnWidth(0, 50); self.table.setColumnWidth(3, 90)
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox()
        edited_btn = buttons.addButton("Tick edited only", QDialogButtonBox.ActionRole)
        all_btn = buttons.addButton("Tick all", QDialogButtonBox.ActionRole)
        none_btn = buttons.addButton("Tick none", QDialogButtonBox.ActionRole)
        buttons.addButton("Continue", QDialogButtonBox.AcceptRole)
        buttons.addButton(QDialogButtonBox.Cancel)
        edited_btn.clicked.connect(lambda: self._set_all(lambda r: r["edited"]))
        all_btn.clicked.connect(lambda: self._set_all(lambda r: True))
        none_btn.clicked.connect(lambda: self._set_all(lambda r: False))
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_all(self, predicate):
        for r, row in enumerate(self.rows):
            self.table.item(r, 0).setCheckState(Qt.Checked if predicate(row) else Qt.Unchecked)

    def chosen_keys(self):
        return {row["key"] for r, row in enumerate(self.rows) if self.table.item(r, 0).checkState() == Qt.Checked}
