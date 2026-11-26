"""Review window for D&D Beyond spells, items and feats (v2.32). Nothing is added until the user ticks
rows and presses Add; the Campaign names window then still needs Save."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
                               QAbstractItemView, QPushButton, QDialogButtonBox, QCheckBox, QComboBox)

from .ddb_spells_items import GEAR, whisper_default
from .theme import tone   # v2.46.1: shared colours

COLS = ["Add", "Name", "Category", "From", "Use in Whisper", "Original text on the sheet"]
C_ADD, C_NAME, C_CAT, C_FROM, C_WHISPER, C_RAW = range(len(COLS))


class SpellsItemsDialog(QDialog):
    """rows: [{'name','category','raw','default','who'}]; existing: names already in the campaign list.
    chosen() -> [{'name','category','who','whisper'}] for the ticked rows."""

    def __init__(self, rows, existing, problems=(), parent=None, changed=()):
        super().__init__(parent)
        self.rows = rows
        known = {e.casefold() for e in existing}
        self.setWindowTitle("D&D Beyond spells, items and feats")
        self.resize(1100, 640)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        layout = QVBoxLayout(self)
        who = sorted({w for r in rows for w in str(r.get("who") or "").split(", ") if w})
        intro = QLabel(f"Found {len(rows)} name(s) on {len(who)} character(s): {', '.join(who) or '—'}. Tick the ones to add to Campaign names. "
                       "Names already in your list are greyed out. Ordinary gear (rope, daggers) starts unticked and hidden. "
                       "“Use in Whisper” starts ticked only for names of two or more words: single words such as Shield or Haste are "
                       "ordinary words, and hinting them can make Whisper insert them. Everything is still used by Correct names.")
        intro.setWordWrap(True); layout.addWidget(intro)
        if problems:
            warn = QLabel("Not read: " + "; ".join(problems) + "\nFor campaign-only characters: connect your D&D Beyond account (D&D Beyond account… in Campaign names) "
                          "if you are in that campaign, ask the owner to set the character to Public (Character → Settings → Privacy), "
                          "or paste the names with Bulk import names.")
            warn.setWordWrap(True); tone(warn,"warn"); layout.addWidget(warn)
        if changed:
            note = QLabel("Changed on D&D Beyond since the last refresh (names no longer on a sheet stay in your list; remove them "
                          "there if you want):\n" + "\n".join(changed))
            note.setWordWrap(True); tone(note,"info"); layout.addWidget(note)
        top = QHBoxLayout()
        self.show_gear = QCheckBox("Show ordinary gear"); self.show_gear.toggled.connect(self._filter)
        self.cat = QComboBox(); self.cat.addItem("All categories", "")
        for c in sorted({r["category"] for r in rows}):
            self.cat.addItem(c, c)
        self.cat.currentIndexChanged.connect(self._filter)
        tick, untick = QPushButton("Tick all shown"), QPushButton("Untick all")
        tick.clicked.connect(lambda: self._tick(True)); untick.clicked.connect(lambda: self._tick(False))
        for w in (QLabel("Show"), self.cat, self.show_gear, tick, untick):
            top.addWidget(w)
        top.addStretch(); layout.addLayout(top)
        self.table = QTableWidget(len(rows), len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        header = self.table.horizontalHeader()
        for c in (C_ADD, C_CAT, C_FROM, C_WHISPER):
            header.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(C_NAME, QHeaderView.Stretch); header.setSectionResizeMode(C_RAW, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        for r, row in enumerate(rows):
            have = row["name"].casefold() in known
            add = QTableWidgetItem(); add.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            add.setCheckState(Qt.Checked if row["default"] and not have else Qt.Unchecked)
            name = QTableWidgetItem(row["name"]); name.setToolTip("Double-click to correct the spelling before adding.")
            name.setFlags(name.flags() | Qt.ItemIsEditable)
            items = [add, name, QTableWidgetItem(row["category"]), QTableWidgetItem(row.get("who", "")), None,
                     QTableWidgetItem(row["raw"] if row["raw"] != row["name"] else "")]
            whisper = QTableWidgetItem(); whisper.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            whisper.setCheckState(Qt.Checked if whisper_default(row["name"]) else Qt.Unchecked)
            items[C_WHISPER] = whisper
            for c, item in enumerate(items):
                if c not in (C_NAME,) and c not in (C_ADD, C_WHISPER):
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if have:
                    item.setForeground(Qt.gray)
                    item.setToolTip("Already in Campaign names")
                self.table.setItem(r, c, item)
            if have:
                add.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)  # not tickable
        layout.addWidget(self.table, 1)
        self.status = QLabel(); layout.addWidget(self.status)
        self.table.itemChanged.connect(lambda *_: self._update())
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.ok = buttons.addButton("Add ticked", QDialogButtonBox.AcceptRole)
        self.ok.clicked.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._filter()

    def _filter(self, *_):
        want = self.cat.currentData()
        for r, row in enumerate(self.rows):
            hide = (row["category"] == GEAR and not self.show_gear.isChecked() and want != GEAR) or (want and row["category"] != want)
            self.table.setRowHidden(r, bool(hide))
        self._update()

    def _tick(self, on):
        for r in range(len(self.rows)):
            item = self.table.item(r, C_ADD)
            if not self.table.isRowHidden(r) and item.flags() & Qt.ItemIsEnabled:
                item.setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _update(self):
        n = len(self.chosen())
        self.status.setText(f"{n} ticked to add")
        self.ok.setText(f"Add {n} ticked" if n else "Add ticked"); self.ok.setEnabled(bool(n))

    def chosen(self):
        out = []
        for r, row in enumerate(self.rows):
            if self.table.item(r, C_ADD).checkState() == Qt.Checked:
                name = self.table.item(r, C_NAME).text().strip()
                if name:
                    out.append({"name": name, "category": row["category"], "who": row.get("who", ""),
                                "whisper": self.table.item(r, C_WHISPER).checkState() == Qt.Checked})
        return out
