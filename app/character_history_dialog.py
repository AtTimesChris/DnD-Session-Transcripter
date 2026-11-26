"""Character history (v2.34): every D&D Beyond refresh of each character, newest first, with what changed."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QTableWidget,
                               QTableWidgetItem, QHeaderView, QAbstractItemView, QPlainTextEdit, QDialogButtonBox, QSplitter)

from .ddb_character import changes, sheet_text

COLS = ["First seen", "Last checked", "Level", "HP", "AC ≈", "Spells", "Magic items"]


def _when(stamp):
    return str(stamp or "")[:16].replace("T", " ")


class CharacterHistoryDialog(QDialog):
    def __init__(self, db, campaign_id, parent=None, names=None):
        """names: {D&D Beyond id: the name typed in Campaign names} (v2.36), shown instead of D&D Beyond's name."""
        super().__init__(parent)
        self.db, self.campaign_id = db, campaign_id
        if names is None:
            from .ddb_import import character_id_from_url
            names = {}
            for e in db.campaign_dictionary_entries(campaign_id):
                try: names[character_id_from_url(e["source_url"])] = e["canonical_name"]
                except Exception: pass
        self.names = names
        self.setWindowTitle("Character history (D&D Beyond refreshes)")
        self.resize(1150, 650)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        layout = QVBoxLayout(self)
        intro = QLabel("Each time a character is refreshed from D&D Beyond (Refresh selected D&D Beyond, or D&D Beyond spells & items) "
                       "a dated copy of the sheet's main numbers, spells and items is kept here, so you can see what it was at a "
                       "session and what changed since. A refresh that finds no change only updates “Last checked”. "
                       "AC is an estimate: select a row to see how it was worked out and compare it with the sheet.")
        intro.setWordWrap(True); layout.addWidget(intro)
        split = QSplitter(Qt.Horizontal)
        self.people = QListWidget(); self.people.currentRowChanged.connect(self._show_person)
        split.addWidget(self.people)
        right = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, len(COLS)); self.table.setHorizontalHeaderLabels(COLS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.currentCellChanged.connect(lambda r, *_: self._show_snapshot(r))
        right.addWidget(self.table)
        self.detail = QPlainTextEdit(); self.detail.setReadOnly(True)
        right.addWidget(self.detail)
        right.setSizes([260, 300])
        split.addWidget(right); split.setSizes([260, 880])
        layout.addWidget(split, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self._load()

    def _load(self):
        snaps = self.db.character_snapshots(self.campaign_id)
        self.by_person = {}
        for s in snaps:
            self.by_person.setdefault(s["ddb_id"], []).append(s)
        self.people.clear()
        for ddb_id, items in sorted(self.by_person.items(), key=lambda kv: self._label(kv[0], kv[1][0]).casefold()):
            latest = items[0]
            item = QListWidgetItem(f"{self._label(ddb_id, latest)}\n  {len(items)} version(s) · last checked {_when(latest['last_checked'])}")
            item.setData(Qt.UserRole, ddb_id)
            self.people.addItem(item)
        if self.people.count():
            self.people.setCurrentRow(0)
        else:
            self.detail.setPlainText("No characters refreshed yet. Use “Refresh selected D&D Beyond” or “D&D Beyond spells & items” "
                                     "in Campaign names.")

    def _label(self, ddb_id, snap):
        ddb_name = snap["summary"].get("name") or snap["character_name"]
        typed = self.names.get(str(ddb_id))
        if typed and typed.casefold() != ddb_name.casefold():
            return f"{typed} (D&D Beyond: {ddb_name})"
        return typed or ddb_name

    def _show_person(self, row):
        if row < 0:
            return
        self.snaps = self.by_person[self.people.item(row).data(Qt.UserRole)]
        self.table.setRowCount(len(self.snaps))
        for r, s in enumerate(self.snaps):
            m = s["summary"]
            hp = f"{m.get('hp_current')}/{m.get('hp_max')}" + (f" +{m['hp_temp']}" if m.get("hp_temp") else "")
            for c, v in enumerate((_when(s["fetched_at"]), _when(s["last_checked"]), m.get("level"), hp, m.get("ac_estimate"),
                                   len(m.get("spells") or []), len(m.get("magic_items") or []))):
                self.table.setItem(r, c, QTableWidgetItem(str(v)))
        if self.snaps:
            self.table.selectRow(0); self._show_snapshot(0)

    def _show_snapshot(self, r):
        if not getattr(self, "snaps", None) or not (0 <= r < len(self.snaps)):
            return
        s = self.snaps[r]; m = s["summary"]
        previous = self.snaps[r + 1]["summary"] if r + 1 < len(self.snaps) else None
        diff = changes(previous, m)
        lines = [f"{self._label(s['ddb_id'], s)} · {m.get('race', '')} · Level {m.get('level')} {m.get('classes', '')}",
                 f"First seen {_when(s['fetched_at'])} · last checked {_when(s['last_checked'])}", "",
                 f"HP {m.get('hp_current')}/{m.get('hp_max')}" + (f" (+{m['hp_temp']} temporary)" if m.get("hp_temp") else ""),
                 f"AC ≈ {m.get('ac_estimate')}  ←  " + "  ".join(m.get("ac_steps") or []),
                 sheet_text(m), ""]
        if previous is None:
            lines.append("First version kept for this character.")
        else:
            lines.append(f"Changes since {_when(self.snaps[r + 1]['fetched_at'])}:")
            lines += [f"  • {d}" for d in diff] or ["  (none)"]
        lines += ["", "Spells: " + (", ".join(m.get("spells") or []) or "—"),
                  "Magic items: " + (", ".join(m.get("magic_items") or []) or "—"),
                  "Feats: " + (", ".join(m.get("feats") or []) or "—")]
        self.detail.setPlainText("\n".join(lines))
