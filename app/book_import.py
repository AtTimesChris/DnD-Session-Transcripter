"""Import books (v2.46): one window for any number of book files, in place of about six pop-ups per book.

Every chosen file is read and its names found at once; with "Look up on D&D Beyond" ticked, each title is matched in
D&D Beyond's public catalogue (one request for the whole list; only the request is sent, never your files). You check
the title, the match and the category in the table, then press Import once. Nothing is stored before that.
"""
import os
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
                               QAbstractItemView, QPushButton, QDialogButtonBox, QCheckBox, QComboBox, QMessageBox)

COLS = ["Use", "File", "Title", "D&D Beyond book", "Rules", "Category", "Names", ""]
C_USE, C_FILE, C_TITLE, C_MATCH, C_RULES, C_CAT, C_NAMES, C_REVIEW = range(len(COLS))


def read_book(path):
    """(text, terms, names) for one file, off the GUI thread."""
    from .reference_library import read_reference, term_review
    text = read_reference(path)
    terms, names = term_review(text) if text.strip() else ([], set())
    return text, terms, names


class BookImportDialog(QDialog):
    def __init__(self, paths, categories, parent=None, existing=()):
        """existing: the source paths already in the library (they are updated, not added twice)."""
        super().__init__(parent)
        self.categories = categories
        self.existing = {os.path.normcase(str(Path(p).resolve())) for p in existing}
        self.setWindowTitle(f"Import books ({len(paths)})")
        self.resize(1350, 620)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        layout = QVBoxLayout(self)
        tip = QLabel("Check each book's title, D&D Beyond match and category, untick any you do not want, then press Import. "
                     "Names are the words each book writes as names: they are ticked for Correct names and the editor's "
                     "Suggestions (Review… to change them). A file that is one part of a book (“Strixhaven_ Hunt for Mage "
                     "Tower”) is matched to the whole book and marked “part of”. Your files are only read, never changed.")
        tip.setWordWrap(True); layout.addWidget(tip)
        top = QHBoxLayout()
        self.lookup = QCheckBox("Look up on D&&D Beyond (public catalogue; only the request is sent)")
        self.lookup.setChecked(True); self.lookup.toggled.connect(self._lookup_all)
        top.addWidget(self.lookup); top.addStretch(); layout.addLayout(top)
        self.table = QTableWidget(len(paths), len(COLS)); self.table.setHorizontalHeaderLabels(COLS)
        h = self.table.horizontalHeader()
        for c in range(len(COLS)):
            h.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(C_MATCH, QHeaderView.Stretch)
        h.setSectionResizeMode(C_TITLE, QHeaderView.Interactive); self.table.setColumnWidth(C_TITLE, 300)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self.table, 1)
        self.status = QLabel(); layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = buttons.button(QDialogButtonBox.Ok); self.ok.setText("Import ticked books")
        buttons.accepted.connect(self._accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.rows = []
        from .reference_library import guess_book_title, suggest_dnd_category
        for r, path in enumerate(paths):
            use = QCheckBox(); use.setChecked(True)
            self.table.setCellWidget(r, C_USE, use)
            again = os.path.normcase(str(Path(path).resolve())) in self.existing
            f = QTableWidgetItem(Path(path).name + ("  (already imported: updated)" if again else ""))
            f.setFlags(f.flags() & ~Qt.ItemIsEditable); f.setToolTip(path)
            self.table.setItem(r, C_FILE, f)
            title = guess_book_title(path)
            self.table.setItem(r, C_TITLE, QTableWidgetItem(title))
            match = QComboBox(); match.addItem("(not looked up)", None)
            match.currentIndexChanged.connect(lambda _i, rr=r: self._match_changed(rr))
            self.table.setCellWidget(r, C_MATCH, match)
            cat = QComboBox(); cat.addItems(categories)
            guess = suggest_dnd_category({"title": title})
            cat.setCurrentText(guess if guess in categories else categories[-1])
            self.table.setCellWidget(r, C_CAT, cat)
            for c in (C_RULES, C_NAMES):
                it = QTableWidgetItem("…"); it.setFlags(it.flags() & ~Qt.ItemIsEditable); self.table.setItem(r, c, it)
            review = QPushButton("Review…"); review.setEnabled(False)
            review.clicked.connect(lambda _c=False, rr=r: self._review(rr))
            self.table.setCellWidget(r, C_REVIEW, review)
            self.rows.append({"path": path, "use": use, "match": match, "cat": cat, "review": review, "text": None,
                              "terms": [], "names": set(), "approved": None, "error": ""})
        QTimer.singleShot(0, self._read_all)

    # ---- reading and looking up ----
    def _read_all(self):
        from .background import run_blocking_in_thread

        def work():
            out = []
            for row in self.rows:
                try:
                    out.append(read_book(row["path"]))
                except Exception as exc:
                    out.append(exc)
            return out
        results = run_blocking_in_thread(work, parent=self, label=f"Reading {len(self.rows)} book(s) and finding their names…")
        for r, (row, res) in enumerate(zip(self.rows, results)):
            if isinstance(res, Exception) or not res[0].strip():
                row["error"] = str(res) if isinstance(res, Exception) else "no text the app can read (a scanned PDF needs OCR)"
                row["use"].setChecked(False); row["use"].setEnabled(False)
                self.table.item(r, C_NAMES).setText("—")
                self.table.item(r, C_FILE).setToolTip(row["error"])
                self.table.item(r, C_FILE).setForeground(Qt.red)
                continue
            row["text"], row["terms"], row["names"] = res
            row["review"].setEnabled(True)
            self._show_names(r)
        self._lookup_all()

    def _show_names(self, r):
        row = self.rows[r]
        ticked = len(row["approved"]) if row["approved"] is not None else len(row["names"])
        self.table.item(r, C_NAMES).setText(f"{ticked} of {len(row['terms'])}")

    def _lookup_all(self, *_):
        self._status()
        if not self.lookup.isChecked():
            return
        from .background import run_blocking_in_thread
        from .reference_library import ddb_book_details

        def work():
            out = []
            for r, row in enumerate(self.rows):
                try:
                    out.append(ddb_book_details(self.table.item(r, C_TITLE).text().strip(), read_page=False))
                except Exception as exc:
                    out.append(exc)
            return out
        try:
            results = run_blocking_in_thread(work, parent=self, label="Matching the titles in D&D Beyond's catalogue…")
        except Exception as exc:
            QMessageBox.warning(self, "D&D Beyond", f"D&D Beyond could not be reached:\n{exc}"); return
        for r, res in enumerate(results):
            combo = self.rows[r]["match"]
            combo.blockSignals(True); combo.clear()
            if isinstance(res, Exception) or not res:
                combo.addItem("no match" + (f" ({res})" if isinstance(res, Exception) else ""), None)
            else:
                for m in res:
                    combo.addItem(("part of " if m.get("part_of") else "") + f"{m['title']}  ({m['abbreviation']})", m)
                combo.addItem("(none: do not use D&D Beyond details)", None)
            combo.blockSignals(False)
            self._match_changed(r)
        self._status()

    def _match_changed(self, r):
        m = self.rows[r]["match"].currentData()
        self.table.item(r, C_RULES).setText((m or {}).get("rules", ""))
        if m and m.get("suggested_category") in self.categories:
            self.rows[r]["cat"].setCurrentText(m["suggested_category"])

    def _review(self, r):
        from .reference_terms_dialog import ReferenceTermReviewDialog
        row = self.rows[r]
        dlg = ReferenceTermReviewDialog(Path(row["path"]).name, row["terms"], self,
                                        ticked=row["approved"] if row["approved"] is not None else row["names"], names=row["names"])
        if dlg.exec():
            row["approved"] = dlg.approved_terms()
            self._show_names(r)

    def _status(self):
        n = sum(1 for row in self.rows if row["use"].isChecked())
        bad = sum(1 for row in self.rows if row["error"])
        self.status.setText(f"{n} of {len(self.rows)} book(s) ticked" + (f" · {bad} could not be read" if bad else ""))

    # ---- result ----
    def _accept(self):
        if not any(row["use"].isChecked() for row in self.rows):
            QMessageBox.information(self, "Import books", "Tick at least one book."); return
        self.accept()

    def chosen(self):
        """[{path, text, title, category, metadata, terms, approved}] for the ticked books."""
        out = []
        for r, row in enumerate(self.rows):
            if not row["use"].isChecked() or row["text"] is None:
                continue
            m = row["match"].currentData()
            meta = {k: v for k, v in (m or {}).items() if k != "score"}
            meta["title_given"] = self.table.item(r, C_TITLE).text().strip()
            out.append({"path": row["path"], "text": row["text"], "title": meta["title_given"],
                        "category": row["cat"].currentText(), "metadata": meta, "terms": row["terms"],
                        "approved": row["approved"] if row["approved"] is not None else row["names"]})
        return out
