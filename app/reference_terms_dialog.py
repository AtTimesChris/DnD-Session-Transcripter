"""Review of a book's names (v2.46: names ticked, ordinary words unticked; was "Whisper words", all ticked)."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QDialogButtonBox,
                               QLineEdit, QPushButton, QCheckBox)


class ReferenceTermReviewDialog(QDialog):
    """terms: the book's names and terms. ticked: the ones to tick (None = all, as before v2.46). names: the ones the
    book writes as names (shown with a mark)."""

    def __init__(self, source_name, terms, parent=None, ticked=None, names=None):
        super().__init__(parent)
        self.setWindowTitle(f"Book names · {source_name}")
        self.resize(640, 680)
        self.names = set(names if names is not None else terms)
        layout = QVBoxLayout(self)
        info = QLabel(
            f"Names and terms found in {source_name}. The ones the book writes as names (with a capital letter in the middle of "
            "a sentence: “Aboleth”, “Waterdeep”, “Chromatic Orb”) are ticked; words that are only capitalised at the start of "
            "a sentence or heading (“About”, “Has”) are not. Ticked names are used by Correct names (at least 85% close, only "
            "when no campaign name fits) and the editor's Suggestions panel. Whisper does not get them: its prompt holds about "
            "700 characters, which your Campaign names fill. Nothing from the book's text goes anywhere.")
        info.setWordWrap(True)
        layout.addWidget(info)
        row = QHBoxLayout()
        self.filter = QLineEdit(); self.filter.setPlaceholderText("filter")
        self.filter.textChanged.connect(self._filter)
        self.only_ticked = QCheckBox("Ticked only"); self.only_ticked.toggled.connect(self._filter)
        names_btn = QPushButton("Tick names only"); names_btn.clicked.connect(self._names_only)
        all_btn = QPushButton("Tick all"); all_btn.clicked.connect(lambda: self._set_all(True))
        none_btn = QPushButton("Untick all"); none_btn.clicked.connect(lambda: self._set_all(False))
        for w in (self.filter, self.only_ticked, names_btn, all_btn, none_btn):
            row.addWidget(w)
        layout.addLayout(row)
        self.list = QListWidget()
        want = set(terms) if ticked is None else set(ticked)
        for term in terms:
            item = QListWidgetItem(term if term in self.names else f"{term}   (ordinary word)")
            item.setData(Qt.UserRole, term)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if term in want else Qt.Unchecked)
            self.list.addItem(item)
        self.list.itemChanged.connect(lambda *_: self._count())
        layout.addWidget(self.list, 1)
        self.status = QLabel(); layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._count()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.filter.hasFocus():
            event.accept(); return
        super().keyPressEvent(event)

    def _items(self):
        return [self.list.item(i) for i in range(self.list.count())]

    def _filter(self, *_):
        needle = self.filter.text().strip().casefold()
        for it in self._items():
            hide = (bool(needle) and needle not in it.text().casefold()) or (self.only_ticked.isChecked() and it.checkState() != Qt.Checked)
            it.setHidden(hide)

    def _set_all(self, on):
        for it in self._items():
            if not it.isHidden():
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _names_only(self):
        for it in self._items():
            it.setCheckState(Qt.Checked if it.data(Qt.UserRole) in self.names else Qt.Unchecked)

    def _count(self):
        n = sum(1 for it in self._items() if it.checkState() == Qt.Checked)
        self.status.setText(f"{n} of {self.list.count()} ticked · {len(self.names)} written as names in the book")

    def approved_terms(self):
        return {it.data(Qt.UserRole) for it in self._items() if it.checkState() == Qt.Checked}
