"""D&D books window (v2.43): every book in the campaign's reference library with its file, category and date,
and what looks wrong with it: the file is missing, the file has changed since it was imported, a newer copy with
the same name is in another folder, or the same book is in the list more than once.

You can re-link a book to another file, change its category, re-read it from its file, review its names, combine the
parts of one book into one (v2.46), and remove entries (after confirming). Only the app's copy is changed: the files
on disk are never touched, except that Combine writes one NEW file next to the parts (never over an existing file).
v2.46: a books folder (Check compares the library with it; Re-link all to folder).
`book_status`, `folder_status` and `combine_parts` are Qt-free and unit-tested."""
import hashlib
import os
import re
from datetime import datetime
from pathlib import Path


def _hash_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def book_status(sources, read_text=None, exists=os.path.isfile, mtime=os.path.getmtime, books_folder=""):
    """sources: dicts with id, source_path, source_name, content_hash. -> {id: [problems, as short sentences]}.
    read_text(path) gives a Markdown / text file's text (PDFs are not re-read: too slow, so not checked).
    books_folder (v2.46): a book whose file is elsewhere while a file of the same name is in this folder is flagged."""
    if read_text is None:
        def read_text(path):
            return Path(path).read_text(encoding="utf-8-sig", errors="replace")
    tree = book_folder_files(books_folder) if books_folder else {}   # v2.50: subfolders too
    folders = sorted({str(Path(s["source_path"]).parent) for s in sources} | {str(Path(p).parent) for p in tree.values()}
                     | ({books_folder} if books_folder else set()), key=str.casefold)
    in_tree = lambda folder: bool(books_folder) and (os.path.normcase(os.path.abspath(folder)) + os.sep).startswith(
        os.path.normcase(os.path.abspath(books_folder)) + os.sep)
    by_hash, by_name = {}, {}
    for n, s in enumerate(sources, 1):
        by_hash.setdefault(s["content_hash"], []).append(n)
        by_name.setdefault(str(s["source_name"]).casefold(), []).append(n)
    out = {}
    for n, s in enumerate(sources, 1):
        path, problems = s["source_path"], []
        text_file = Path(path).suffix.lower() in (".md", ".markdown", ".txt")
        if not exists(path):
            problems.append("file not found")
        elif text_file:
            try:
                if _hash_text(read_text(path)) != s["content_hash"]:
                    problems.append("the file has changed since it was imported: Re-read it")
            except OSError:
                problems.append("the file could not be read")
        try:
            mine = mtime(path) if exists(path) else 0
        except OSError:
            mine = 0
        for folder in folders:
            other = os.path.join(folder, s["source_name"])
            if os.path.normcase(os.path.abspath(other)) == os.path.normcase(os.path.abspath(path)) or not exists(other):
                continue
            try:
                newer = mtime(other) > mine + 1
            except OSError:
                newer = False
            if newer:
                stamp = datetime.fromtimestamp(mtime(other)).strftime("%d %b %H:%M")
                problems.append(f"a newer file with this name is in {folder} ({stamp}): Re-link to it?")
            elif in_tree(folder):
                problems.append("not from your books folder, which has a file of this name: Re-link all to folder?")
        same = [m for m in by_hash[s["content_hash"]] if m != n]
        if same:
            problems.append("same text as row " + ", ".join(map(str, same)))
        else:
            named = [m for m in by_name[str(s["source_name"]).casefold()] if m != n]
            if named:
                problems.append("same file name as row " + ", ".join(map(str, named)) + " (different text)")
        out[s["id"]] = problems
    return out


BOOK_SUFFIXES = (".md", ".markdown", ".txt", ".pdf")


def book_folder_files(books_folder):
    """v2.50: {file name casefolded: path} of every book file in the books folder and its subfolders (the first one found
    when two have the same name). Hidden folders are skipped."""
    out = {}
    if not books_folder or not os.path.isdir(books_folder):
        return out
    for root, dirs, files in os.walk(books_folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for f in sorted(files):
            if f.lower().endswith(BOOK_SUFFIXES):
                out.setdefault(f.casefold(), os.path.join(root, f))
    return out


def folder_status(sources, books_folder):
    """v2.46: (files in the books folder that are not in the library, library books that could be re-linked to the
    folder: [(source, path in folder)]). v2.50: subfolders are included."""
    if not books_folder:
        return [], []
    by_name = book_folder_files(books_folder)
    in_library = {str(s["source_name"]).casefold() for s in sources}
    missing = sorted((os.path.basename(p) for k, p in by_name.items() if k not in in_library), key=str.casefold)
    relink = []
    for s in sources:
        target = by_name.get(str(s["source_name"]).casefold())
        if target and os.path.normcase(os.path.abspath(target)) != os.path.normcase(os.path.abspath(s["source_path"])):
            relink.append((s, target))
    return missing, relink


_PLACEHOLDER = re.compile(r"(?m)^#[ \t]+(?P<title>[^\n]+?)[ \t]*\n(?:[ \t]*\n)*See the \*(?P<ref>[^*\n]+)\* adventure\.[ \t]*$")


def _norm_title(s):
    s = str(s or "").casefold().replace("’", "'")
    s = re.sub(r"^the\s+", "", s.strip())
    return " ".join(re.findall(r"[a-z0-9']+", s))


def part_title(name, text):
    """A part's title: its first "# " heading, else the file name after "_" ("Strixhaven_ Hunt for Mage Tower")."""
    m = re.search(r"(?m)^#[ \t]+(.+?)[ \t]*$", text or "")
    if m:
        return m.group(1).strip()
    stem = Path(name).stem
    return stem.split("_", 1)[-1].strip()


def _without_credits(text):
    """A part's text without its closing "# Credits" section (the whole book keeps one)."""
    m = re.search(r"(?m)^#[ \t]+Credits[ \t]*$", text or "")
    return (text[:m.start()] if m else text).rstrip() + "\n"


def combine_parts(main_text, parts):
    """v2.46: put the parts of a book into its main file. main_text: the book's main file ("Strixhaven_ A Curriculum of
    Chaos"), where each adventure is a heading with "See the *Hunt for Mage Tower* adventure." parts: [(title, text)].
    Each placeholder whose title matches a part is replaced by that part (without its own Credits section); parts with
    no placeholder go at the end in the order given. -> (text, placed titles, appended titles, placeholders left)."""
    by_title = {_norm_title(t): (t, x) for t, x in parts}
    placed, used = [], set()

    def swap(m):
        key = _norm_title(m.group("ref")) if _norm_title(m.group("ref")) in by_title else _norm_title(m.group("title"))
        if key not in by_title or key in used:
            return m.group(0)
        used.add(key)
        title, text = by_title[key]
        placed.append(title)
        return _without_credits(text).rstrip()
    text = _PLACEHOLDER.sub(swap, main_text or "")
    appended = []
    for t, x in parts:
        if _norm_title(t) not in used:
            text = text.rstrip() + "\n\n------\n\n" + _without_credits(x)
            appended.append(t)
    left = [m.group("title") for m in _PLACEHOLDER.finditer(text)]
    return text if text.endswith("\n") else text + "\n", placed, appended, left


def combined_path(main_path):
    """Where Combine writes: next to the main file, "<name> (combined).md", never over an existing file."""
    p = Path(main_path)
    n = 1
    while True:
        out = p.with_name(f"{p.stem} (combined{'' if n == 1 else ' ' + str(n)}).md")
        if not out.exists():
            return out
        n += 1


try:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
                                   QAbstractItemView, QPushButton, QDialogButtonBox, QMessageBox, QInputDialog, QFileDialog,
                                   QListWidget, QListWidgetItem, QGridLayout)
except ImportError:   # the tests import the helpers without Qt
    QDialog = object

COLS = ["#", "Book", "Level", "Category", "D&D Beyond", "File", "Imported", "Size", "Names", "Check"]   # v2.49: Level
C_LEVEL, C_FILE, C_CHECK = 2, 5, 9


def ddb_summary(metadata_json):
    """'PHB-2024 · 2024 rules (5.5e) · 2024-09-17' from a book's stored details, or ''."""
    import json
    try:
        m = json.loads(metadata_json or "{}")
    except Exception:
        return ""
    if m.get("provider") != "D&D Beyond":
        return ""
    return " · ".join(x for x in (("part of " if m.get("part_of") else "") + str(m.get("abbreviation") or ""), m.get("rules"),
                                  m.get("published_date")) if x)


class BookManagerDialog(QDialog):
    def __init__(self, db, campaign_id, parent=None, logger=None, config=None):
        super().__init__(parent)
        from .reference_library import DND_BOOK_CATEGORIES
        self.db, self.campaign_id, self.logger, self.categories = db, campaign_id, logger, DND_BOOK_CATEGORIES
        self.config = config if config is not None else {}
        self.setWindowTitle("D&D books")
        self.resize(1350, 660)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        layout = QVBoxLayout(self)
        intro = QLabel("The books in this campaign's reference library. Check says when the file is missing, has changed since "
                       "it was imported, has a newer copy in another folder, is in the list twice, or is not the copy in your "
                       "books folder. Only the app's copy changes here: your files are never touched. Select one or more rows, "
                       "then use the buttons below.")
        intro.setWordWrap(True); layout.addWidget(intro)
        frow = QHBoxLayout()
        self.folder_label = QLabel(); self.folder_label.setWordWrap(True)
        choose = QPushButton("Books folder…"); choose.clicked.connect(self._choose_folder)
        choose.setToolTip("The folder you keep your book files in (its subfolders are included). Check compares the library "
                          "with it, Import starts there, and Re-link all to folder points every book at its copy there.")
        relink_all = QPushButton("Re-link all to folder…"); relink_all.clicked.connect(self._relink_all)
        relink_all.setToolTip("Point every book whose file name is also in your books folder at the copy there, and read it "
                              "(after showing the list). Category and names are kept.")
        frow.addWidget(QLabel("Books folder:")); frow.addWidget(self.folder_label, 1); frow.addWidget(choose); frow.addWidget(relink_all)
        layout.addLayout(frow)
        self.table = QTableWidget(0, len(COLS)); self.table.setHorizontalHeaderLabels(COLS)
        h = self.table.horizontalHeader()
        for c in range(len(COLS)):
            h.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(C_FILE, QHeaderView.Interactive); h.setSectionResizeMode(C_CHECK, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setWordWrap(True)
        layout.addWidget(self.table, 1)
        grid = QGridLayout()
        buttons_spec = (
            ("Re-link to file…", self._relink, "Point the selected book at another file (for example the fixed copy) and read its text."),
            ("Re-read from file", self._reread, "Read the selected books again from their own files (after you fixed a file in place)."),
            ("Change category…", self._category, "Set the category of the selected books."),
            ("Set level…", self._level, "How much the selected books count for Correct names and the Suggestions panel in this "
             "campaign: Campaign book (the adventure you are running, 80% close, wins over the others), Rules (the core books "
             "of the campaign's edition, 85%), Other (92%) or Off (never used, and not read for spell dice). “Default” "
             "follows the campaign's Game system."),
            ("D&&D Beyond details…", self._ddb, "Look the selected books (or all) up in D&D Beyond's public catalogue (no sign-in "
             "needed; only the request is sent, never your files): the matching book, its rules edition (2014 / 2024), "
             "publisher and a suggested category. The window opens at once; dates fill in as the pages are read."),
            ("Review names…", self._terms, "Choose which of the book's names are used by Correct names and the Suggestions panel."),
            ("Find names again…", self._refind, "Find the selected books' names again (or all books' when none is selected) with "
             "the v2.46 finder: the whole book, names ticked and ordinary words unticked. Words you unticked stay unticked."),
            ("Combine into one book…", self._combine, "The selected files are parts of one book (Strixhaven: A Curriculum of Chaos "
             "and its adventures): write them into one new file in the right order and use that instead (asks first)."),
            ("Remove…", self._remove, "Remove the selected books from the library, after confirming. The files stay on disk."))
        for i, (label, fn, tip) in enumerate(buttons_spec):
            b = QPushButton(label); b.clicked.connect(fn); b.setToolTip(tip); grid.addWidget(b, i // 4, i % 4)
        layout.addLayout(grid)
        self.status = QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Close); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self._fill()
        from .window_state import remember; remember(self)   # v2.46.1: opens at the size and place you left it
        from .notices import attach; attach(self)   # v2.47: yellow note bar at the bottom

    # ---- the list ----
    def _folder(self):
        f = str(self.config.get("books_folder", "") or "")
        return f if f and os.path.isdir(f) else ""

    def _fill(self):
        self.sources = self.db.reference_source_list(self.campaign_id)
        folder = self._folder()
        self.folder_label.setText(folder or "(not set: choose the folder you keep your book files in)")
        problems = book_status(self.sources, books_folder=folder)
        from .rules_edition import level_of, LEVEL_LABEL
        cname, edition = self.db.campaign_rules(self.campaign_id) if hasattr(self.db, "campaign_rules") else ("", "")
        self.levels = {s["id"]: level_of(s, cname, edition) for s in self.sources}
        self.table.setRowCount(len(self.sources))
        for r, s in enumerate(self.sources):
            when = str(s["indexed_at"] or "")[:16].replace("T", " ")
            level, mine = self.levels[s["id"]]
            values = [r + 1, s["source_name"], LEVEL_LABEL[level] + ("" if mine else " (default)"), s["source_category"],
                      ddb_summary(s.get("metadata_json")), s["source_path"], when,
                      f"{(s['size'] or 0) / 1e6:.1f} MB", f"{s['approved']} of {s['terms']}",
                      "; ".join(problems[s["id"]]) or "OK"]
            for c, v in enumerate(values):
                item = QTableWidgetItem(str(v))
                if c == C_CHECK and problems[s["id"]]:
                    item.setForeground(Qt.red if any(p.startswith(("file not", "the file", "a newer")) for p in problems[s["id"]]) else Qt.darkYellow)
                if c == C_FILE:
                    item.setToolTip(s["source_path"])
                if c == C_LEVEL:
                    item.setToolTip("Set by you." if mine else "The default from the campaign's Game system and this book (Set level… "
                                    "to choose).")
                    if level == "off":
                        item.setForeground(Qt.gray)
                    elif level == "campaign":
                        f = item.font(); f.setBold(True); item.setFont(f)
                self.table.setItem(r, c, item)
        self.table.setColumnWidth(C_FILE, 380)
        self.table.resizeRowsToContents()
        flagged = sum(1 for p in problems.values() if p)
        text = f"{len(self.sources)} book(s) · {flagged} with something to check"
        names = {"2024": "2024 rules (5.5e)", "2014": "2014 rules (5e)", "mixed-2024": "both editions, 2024 first",
                 "mixed-2014": "both editions, 2014 first"}
        text += f" · this campaign uses {names.get(edition, 'no D&D edition set (Campaign → Edit…)')}"
        missing, relink = folder_status(self.sources, folder)
        if missing:
            text += (f" · {len(missing)} file(s) in your books folder are not imported: " + ", ".join(missing[:8])
                     + (" …" if len(missing) > 8 else "") + " (Import D&D books… to add them)")
        if relink:
            text += f" · {len(relink)} book(s) can be re-linked to your books folder"
        self.status.setText(text)

    def _selected(self):
        return [self.sources[r] for r in sorted({i.row() for i in self.table.selectedIndexes()})]

    def _need(self, one=False, at_least=1):
        rows = self._selected()
        if len(rows) < at_least:
            QMessageBox.information(self, "D&D books", "Select a book first." if at_least == 1 else
                                    f"Select at least {at_least} books (Ctrl+click)."); return None
        if one and len(rows) > 1:
            QMessageBox.information(self, "D&D books", "Select just one book for this."); return None
        return rows

    def _read(self, path):
        from .reference_library import read_reference
        from .background import run_blocking_in_thread
        return run_blocking_in_thread(read_reference, path, parent=self, label=f"Reading {Path(path).name}…")

    def _log(self, msg, *args):
        if self.logger:
            self.logger.info(msg, *args)

    # ---- books folder (v2.46) ----
    def _choose_folder(self):
        start = self._folder() or (str(Path(self.sources[0]["source_path"]).parent) if self.sources else "")
        folder = QFileDialog.getExistingDirectory(self, "Your books folder", start)
        if not folder:
            return
        self.config["books_folder"] = folder
        try:
            from .config import save_config
            save_config(self.config)
        except Exception:
            pass
        self._fill()

    def _relink_all(self):
        folder = self._folder()
        if not folder:
            QMessageBox.information(self, "Re-link all", "Choose your books folder first (Books folder…)."); return
        _missing, relink = folder_status(self.sources, folder)
        if not relink:
            QMessageBox.information(self, "Re-link all", "Every book that has a file of the same name in your books folder already "
                                    "uses it."); return
        listing = "\n".join(f"• {s['source_name']}\n    {s['source_path']}" for s, _t in relink[:12]) + ("\n…" if len(relink) > 12 else "")
        if QMessageBox.question(self, "Re-link all", f"Use the copy in {folder} for these {len(relink)} book(s)?\n\n{listing}\n\n"
                                "Each is read from the folder; its category and names are kept. Your files are not changed.",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        done, issues = 0, []
        taken = {os.path.normcase(s["source_path"]) for s in self.sources}
        for s, target in relink:
            if os.path.normcase(target) in taken:
                issues.append(f"{s['source_name']}: that file is already another row (remove one of the two)"); continue
            try:
                content = self._read(target)
            except Exception as exc:
                issues.append(f"{s['source_name']}: {exc}"); continue
            if not content.strip():
                issues.append(f"{s['source_name']}: no readable text"); continue
            self.db.relink_reference_source(s["id"], str(Path(target).resolve()), Path(target).name, content, _hash_text(content))
            taken.add(os.path.normcase(target)); done += 1
        self._log("Re-linked %s book(s) to the books folder %s", done, folder)
        self._fill()
        QMessageBox.information(self, "Re-link all", f"Re-linked {done} book(s)." + ("\n\n" + "\n".join(issues) if issues else ""))

    # ---- the buttons ----
    def _relink(self):
        rows = self._need(one=True)
        if not rows:
            return
        s = rows[0]
        start = self._folder() or str(Path(s["source_path"]).parent)
        path, _ = QFileDialog.getOpenFileName(self, f"Re-link {s['source_name']} to…", start,
                                              "Reference files (*.pdf *.md *.markdown *.txt);;All files (*.*)")
        if not path:
            return
        new = str(Path(path).resolve())
        clash = next((n for n, o in enumerate(self.sources, 1) if o["id"] != s["id"]
                      and os.path.normcase(o["source_path"]) == os.path.normcase(new)), None)
        if clash:
            QMessageBox.information(self, "Re-link", f"That file is already in the list as row {clash}. Remove one of the two "
                                    "entries instead (or re-read row {clash} from its file).".replace("{clash}", str(clash)))
            return
        try:
            content = self._read(new)
        except Exception as exc:
            QMessageBox.warning(self, "Re-link", f"Could not read the file:\n{exc}"); return
        if not content.strip():
            QMessageBox.warning(self, "Re-link", "The file has no text the app can read (a scanned PDF needs OCR). Nothing changed."); return
        if QMessageBox.question(self, "Re-link", f"Use this file for {s['source_name']}?\n\nOld: {s['source_path']}\nNew: {new}\n\n"
                                "The app's copy of the text is replaced; its category and name choices are kept. "
                                "Your files are not changed.", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self.db.relink_reference_source(s["id"], new, Path(new).name, content, _hash_text(content))
        self._log("Reference source %s re-linked from %s to %s", s["id"], s["source_path"], new)
        self._fill()
        if QMessageBox.question(self, "Re-link", "Review the names for the new text now? (No keeps your current "
                                "choices.)", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self._terms_for(next(x for x in self.sources if x["id"] == s["id"]), content)

    def _reread(self):
        rows = self._need()
        if not rows:
            return
        done, issues = 0, []
        for s in rows:
            if not os.path.isfile(s["source_path"]):
                issues.append(f"{s['source_name']}: file not found"); continue
            try:
                content = self._read(s["source_path"])
            except Exception as exc:
                issues.append(f"{s['source_name']}: {exc}"); continue
            if not content.strip():
                issues.append(f"{s['source_name']}: no readable text"); continue
            self.db.relink_reference_source(s["id"], s["source_path"], s["source_name"], content, _hash_text(content))
            done += 1
        self._log("Reference sources re-read: %s", done)
        self._fill()
        QMessageBox.information(self, "Re-read", f"Re-read {done} book(s)." + ("\n\n" + "\n".join(issues) if issues else ""))

    def _level(self):
        """v2.49: the selected books' level in this campaign (or back to the default)."""
        rows = self._need()
        if not rows:
            return
        from .rules_edition import LEVELS, LEVEL_LABEL
        choices = ["Default (from the Game system)"] + [label for _k, label, _t in LEVELS]
        value, ok = QInputDialog.getItem(self, "Set level", f"Level for {len(rows)} book(s) in this campaign:\n\n"
                                         "Campaign book: the adventure you are running (80% close, wins over the rest)\n"
                                         "Rules: the core books of this campaign's edition (85%)\n"
                                         "Other: supplements, the other edition's core books (92%)\n"
                                         "Off: never used for names, not read for spell dice", choices, 0, False)
        if not ok:
            return
        key = "" if value.startswith("Default") else next(k for k, label, _t in LEVELS if label == value)
        for s in rows:
            self.db.set_reference_level(s["id"], key)
        self._log("Book level set to %s for %s book(s)", key or "default", len(rows))
        self._fill()

    def _category(self):
        rows = self._need()
        if not rows:
            return
        current = rows[0]["source_category"]
        idx = self.categories.index(current) if current in self.categories else 0
        value, ok = QInputDialog.getItem(self, "Change category", f"Category for {len(rows)} book(s):", self.categories, idx, False)
        if not ok:
            return
        for s in rows:
            self.db.set_reference_category(s["id"], value)
        self._fill()

    def _ddb(self):
        """v2.45: D&D Beyond details for the selected books (or all when none is selected), reviewed before use.
        v2.46: matched in the catalogue only (one request), so the window opens at once; the pages are read after."""
        rows = self._selected() or list(self.sources)
        if not rows:
            return
        from .reference_library import ddb_book_details
        from .background import run_blocking_in_thread

        def look_all():
            out = []
            for s in rows:
                try:
                    out.append((s, ddb_book_details(Path(s["source_name"]).stem.replace("_", " "), read_page=False), ""))
                except Exception as exc:
                    out.append((s, [], str(exc)))
            return out
        try:
            found = run_blocking_in_thread(look_all, parent=self, label=f"Matching {len(rows)} book(s) in D&D Beyond's catalogue…")
        except Exception as exc:
            QMessageBox.warning(self, "D&D Beyond details", f"D&D Beyond could not be reached:\n{exc}"); return
        dlg = DdbReviewDialog(found, self.categories, self)
        if dlg.exec() != QDialog.Accepted:
            return
        for source_id, meta, category in dlg.chosen():
            self.db.set_reference_metadata(source_id, meta, category)
        self._log("D&D Beyond details applied to %s book(s)", len(dlg.chosen()))
        self._fill()

    def _terms(self):
        rows = self._need(one=True)
        if rows:
            self._terms_for(rows[0], self.db.reference_content(rows[0]["id"]))

    def _terms_for(self, s, content):
        from .reference_library import term_review
        from .reference_terms_dialog import ReferenceTermReviewDialog
        from .background import run_blocking_in_thread
        terms, names = run_blocking_in_thread(term_review, content, parent=self, label="Finding the book's names…")
        if not terms:
            QMessageBox.information(self, "Book names", "No names or terms found in this book."); return
        before = self.db.term_approvals(s["id"]) if hasattr(self.db, "term_approvals") else {}
        ticked = {t for t in terms if before.get(t, t in names)} if before else names
        dlg = ReferenceTermReviewDialog(s["source_name"], terms, self, ticked=ticked, names=names)
        if dlg.exec():
            self.db.replace_reference_terms(s["id"], terms, dlg.approved_terms())
            self._fill()

    def _refind(self):
        """v2.46: the names of the selected books (or all) found again with the new finder, after confirming."""
        rows = self._selected() or list(self.sources)
        if not rows:
            return
        from .reference_library import term_review
        from .background import run_blocking_in_thread

        # the database is read here (SQLite connections belong to the thread that made them); only the finding runs off it
        texts = {s["id"]: self.db.reference_content(s["id"]) for s in rows}
        befores = {s["id"]: self.db.term_approvals(s["id"]) for s in rows}

        def work():
            out = []
            for s in rows:
                terms, names = term_review(texts[s["id"]])
                before = befores[s["id"]]
                unticked = {t for t, on in before.items() if not on}
                out.append((s, terms, names - unticked, before))
            return out
        found = run_blocking_in_thread(work, parent=self, label=f"Finding the names of {len(rows)} book(s)…")
        old = sum(sum(1 for on in b.values() if on) for _s, _t, _n, b in found)
        new = sum(len(n) for _s, _t, n, _b in found)
        total = sum(len(t) for _s, t, _n, _b in found)
        if QMessageBox.question(self, "Find names again",
                                f"{len(rows)} book(s): {old} words ticked now.\n\nThe new finder reads the whole of each book and "
                                f"finds {total} names and terms; {new} of them are written as names in the books and will be "
                                "ticked (ordinary words like “About” or “Has” will not). Words you had unticked stay unticked.\n\n"
                                "Replace the word lists of these books? (Review names… changes any book afterwards.)",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        for s, terms, names, _b in found:
            self.db.replace_reference_terms(s["id"], terms, names)
        self._log("Names found again for %s book(s): %s ticked of %s", len(rows), new, total)
        self._fill()

    def _combine(self):
        """v2.46: the parts of one book (its main file and its adventures) written into one new file, used instead."""
        rows = self._need(at_least=2)
        if not rows:
            return
        from .book_import import read_book
        texts = {s["id"]: self.db.reference_content(s["id"]) for s in rows}
        # the main file: the one with "See the *…* adventure." places, else the first selected
        main = next((s for s in rows if _PLACEHOLDER.search(texts[s["id"]])), rows[0])
        parts = [s for s in rows if s["id"] != main["id"]]
        dlg = CombineDialog(main, parts, self)
        if dlg.exec() != QDialog.Accepted:
            return
        parts = dlg.ordered_parts()
        text, placed, appended, left = combine_parts(texts[main["id"]], [(part_title(p["source_name"], texts[p["id"]]), texts[p["id"]])
                                                                       for p in parts])
        out = combined_path(main["source_path"])
        if not out.parent.is_dir():
            out = combined_path(Path(self._folder() or ".") / Path(main["source_name"]).name)
        summary = []
        if placed:
            summary.append("Put in its place: " + ", ".join(placed))
        if appended:
            summary.append("Added at the end (no place for it in the main file): " + ", ".join(appended))
        if left:
            summary.append("Places still empty (you have no file for these): " + ", ".join(left))
        if QMessageBox.question(self, "Combine into one book",
                                f"Write one new file:\n{out}\n\nfrom {main['source_name']} and {len(parts)} part(s).\n\n"
                                + "\n".join(summary) + "\n\nEach part's own Credits section is left out (the book keeps one). "
                                "Your files are not changed. The new file is then imported with the main file's category and "
                                "D&D Beyond details.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            out.write_text(text, encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Combine", f"Could not write the file:\n{exc}"); return
        import json
        from .reference_library import source_record
        _t, terms, names = read_book(str(out))
        source = source_record(str(out), main["source_category"], text)
        try:
            meta = json.loads(main.get("metadata_json") or "{}")
        except Exception:
            meta = {}
        meta.pop("part_of", None)
        meta["combined_from"] = [main["source_name"]] + [p["source_name"] for p in parts]
        source["metadata"] = meta
        self.db.save_reference_source(self.campaign_id, source, terms, names)
        self._log("Combined %s into %s", meta["combined_from"], out)
        self._fill()
        if QMessageBox.question(self, "Combine", f"Imported {out.name}.\n\nRemove the {len(rows)} separate parts from the library now? "
                                "Their files stay on disk.", QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) == QMessageBox.Yes:
            for s in rows:
                self.db.delete_reference_source(s["id"])
                self._log("Reference source removed after combining: %s", s["source_name"])
            self._fill()

    def _remove(self):
        rows = self._need()
        if not rows:
            return
        names = "\n".join(f"• {s['source_name']}  ({s['source_path']})" for s in rows[:15]) + ("\n…" if len(rows) > 15 else "")
        if QMessageBox.question(self, "Remove books", f"Remove {len(rows)} book(s) from this campaign's reference library?\n\n{names}\n\n"
                                "Their text and names are removed from the app. The files on disk are NOT deleted, and "
                                "you can import them again at any time.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        for s in rows:
            self.db.delete_reference_source(s["id"])
            self._log("Reference source removed: %s (%s)", s["source_name"], s["source_path"])
        self._fill()


class CombineDialog(QDialog):
    """v2.46: confirm the main file and put the other parts in book order."""

    def __init__(self, main, parts, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Combine into one book")
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        tip = QLabel(f"Main file: {main['source_name']}\n\nEach part goes where the main file says “See the … adventure.” "
                     "Parts without such a place go at the end in this order (drag to change it).")
        tip.setWordWrap(True); lay.addWidget(tip)
        self.list = QListWidget(); self.list.setDragDropMode(QAbstractItemView.InternalMove)
        for p in parts:
            it = QListWidgetItem(p["source_name"]); it.setData(Qt.UserRole, p); self.list.addItem(it)
        lay.addWidget(self.list, 1)
        b = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        b.button(QDialogButtonBox.Ok).setText("Next…")
        b.accepted.connect(self.accept); b.rejected.connect(self.reject); lay.addWidget(b)

    def ordered_parts(self):
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]


class DdbReviewDialog(QDialog):
    """v2.45: what D&D Beyond says for each book; tick what to use, change the match or the category first.
    v2.46: opens at once; the description and date of each chosen match are read from its page in the background."""

    def __init__(self, found, categories, parent=None, read_pages=True):
        super().__init__(parent)
        from PySide6.QtWidgets import QComboBox, QCheckBox
        self.found, self.categories = found, categories
        self.setWindowTitle("D&D Beyond details")
        self.resize(1250, 520)
        layout = QVBoxLayout(self)
        tip = QLabel("Matched by the book's file name in D&D Beyond's public catalogue. A year in the name picks the edition "
                     "(2024 or later = the 2024 book); a file that is one part of a book is matched to the whole book (“part "
                     "of”). Check the match and the category, untick any you do not want, then Apply. The date is when D&D "
                     "Beyond published the book's page: it fills in as the pages are read (you do not need to wait).")
        tip.setWordWrap(True); layout.addWidget(tip)
        heads = ["Use", "Your book", "D&D Beyond book", "Rules", "Publisher", "Date", "Category"]
        self.table = QTableWidget(len(found), len(heads)); self.table.setHorizontalHeaderLabels(heads)
        h = self.table.horizontalHeader()
        for c in range(len(heads)):
            h.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        self.rows = []
        for r, (s, matches, error) in enumerate(found):
            use = QCheckBox(); use.setChecked(bool(matches))
            use.setEnabled(bool(matches))
            self.table.setCellWidget(r, 0, use)
            self.table.setItem(r, 1, QTableWidgetItem(s["source_name"]))
            match = QComboBox()
            for m in matches:
                match.addItem(("part of " if m.get("part_of") else "") + f"{m['title']}  ({m['abbreviation']})", m)
            if not matches:
                match.addItem("no match" + (f": {error}" if error else ""))
            self.table.setCellWidget(r, 2, match)
            cat = QComboBox(); cat.addItems(categories)
            self.table.setCellWidget(r, 6, cat)
            row = {"source": s, "use": use, "match": match, "cat": cat, "cat_set": False}
            self.rows.append(row)
            match.currentIndexChanged.connect(lambda _i, rr=r: self._show(rr))
            cat.activated.connect(lambda _i, rr=r: self.rows[rr].update(cat_set=True))
            self._show(r)
        layout.addWidget(self.table, 1)
        self.status = QLabel(); layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Apply).clicked.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._pool, self._jobs = None, []
        if read_pages:
            QTimer.singleShot(0, self._read_pages)

    def _read_pages(self):
        """The best match's page of each book, one at a time in the background (the requests are spaced out)."""
        from concurrent.futures import ThreadPoolExecutor
        from .reference_library import ddb_page_details
        items = {}
        for r, row in enumerate(self.rows):
            m = row["match"].currentData()
            if m and m.get("info_link") and not m.get("published_date"):
                items.setdefault(m["info_link"], []).append((r, m))
        if not items:
            return
        self._pool = ThreadPoolExecutor(max_workers=1)
        for link, users in items.items():
            self._jobs.append((self._pool.submit(ddb_page_details, users[0][1]), users))
        self._timer = QTimer(self); self._timer.setInterval(200); self._timer.timeout.connect(self._poll); self._timer.start()
        self.status.setText(f"Reading {len(items)} D&D Beyond page(s) for dates and descriptions…")

    def _poll(self):
        left = []
        for fut, users in self._jobs:
            if not fut.done():
                left.append((fut, users)); continue
            item = fut.result()
            for r, m in users:
                m.update({k: item.get(k) for k in ("description", "published_date", "date_note", "suggested_category", "page_error")
                          if item.get(k) is not None})
                if self.rows[r]["match"].currentData() is m:
                    self._show(r)
        self._jobs = left
        if not left:
            self._timer.stop()
            self.status.setText("Dates and descriptions read.")
        else:
            self.status.setText(f"Reading D&D Beyond pages… {len(left)} left (you can Apply now; the rest is left out)")

    def done(self, result):
        if self._pool is not None:
            try:
                self._timer.stop()
                self._pool.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
        super().done(result)

    def _show(self, r):
        row = self.rows[r]
        m = row["match"].currentData()
        for c, v in ((3, (m or {}).get("rules", "")), (4, (m or {}).get("publisher", "")), (5, (m or {}).get("published_date", ""))):
            self.table.setItem(r, c, QTableWidgetItem(v))
        current = row["source"]["source_category"]
        want = (m or {}).get("suggested_category") or current
        if not row["cat_set"]:
            row["cat"].setCurrentText(want if want in self.categories else current)
        row["cat"].setToolTip(f"Now: {current}. Suggested from D&D Beyond: {(m or {}).get('suggested_category', '—')}.")

    def chosen(self):
        out = []
        for row in self.rows:
            m = row["match"].currentData()
            if row["use"].isChecked() and m:
                meta = {k: v for k, v in m.items() if k not in ("score",)}
                out.append((row["source"]["id"], meta, row["cat"].currentText()))
        return out
