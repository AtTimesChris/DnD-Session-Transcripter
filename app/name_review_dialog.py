"""Review window for the name-correction pass (v2.30). Nothing is ticked or changed until the user
ticks rows and presses Apply; reviewed segments are never offered."""
import re
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTableWidgetItem, QHeaderView,
                               QAbstractItemView, QPushButton, QCheckBox, QComboBox, QLineEdit,
                               QDoubleSpinBox, QMessageBox, QDialogButtonBox, QInputDialog)

from .corrections_dialog import ShiftCheckTable
from .name_review import find_suggestions, context_snippet, DEFAULT_THRESHOLD, BOOK_THRESHOLD

COLUMNS = ["Use", "Listen", "Time", "Speaker", "Heard", "Change to", "Why", "Context"]
C_USE, C_PLAY, C_TIME, C_SPEAKER, C_HEARD, C_CHANGE, C_WHY, C_CONTEXT = range(len(COLUMNS))


def _why(s):
    if s["kind"] == "known":
        return "Known mishearing"
    if s["kind"] == "book":
        return f"Book name ({s['score']:.0%} close) · {str(s.get('book', '')).rsplit('.', 1)[0]}"
    return (f"Split name ({s['score']:.0%} close)" if s["kind"] == "split" else f"Near miss ({s['score']:.0%} close)")


def _clock(seconds):
    s = max(0, int(seconds or 0))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


class NameReviewDialog(QDialog):
    """segments: editor rows (dicts). index/lower_vocab: from name_review. speaker_label: sid -> text.
    play(edit_id): plays that segment in the editor. record(heard, intended, accept): stores a decision.
    skip(pairs): stores (heard, intended) pairs skipped for this transcript only (v2.30.2).
    pause(): pauses or resumes the playing clip; False when there is nothing to resume (v2.31.2)."""

    def __init__(self, segments, index, lower_vocab, speaker_label, play=None, record=None, parent=None, skip=None, pause=None,
                 reload=None, add_name=None, keep=None):
        """v2.46: reload() gives the editor's lines as they are now (Search again uses it: the window stays open beside the
        editor); add_name(name, category) adds a name to Campaign names; keep(words) remembers the words you chose."""
        super().__init__(parent)
        self.reload, self.add_name, self.keep = reload, add_name, keep
        self.pause, self._playing_row = pause, -1
        self.segments, self.index, self.lower_vocab = segments, index, lower_vocab
        self.speaker_label, self.play, self.record, self.skip = speaker_label, play, record, skip
        self.suggestions, self.skipped = [], 0
        self.chosen = []
        self.line_edits = {}  # edit_id -> the whole line as the user rewrote it (v2.30.1)
        self.setWindowTitle("Correct names")
        self.resize(1200, 680)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        layout = QVBoxLayout(self)
        intro = QLabel("Possible misspelled campaign names. Nothing is ticked and nothing changes until you tick rows and press "
                       "“Apply ticked”. Known mishearings are exact matches of your aliases and accepted corrections; near misses "
                       "are capitalised words that are close to a name and never appear in lower case in your transcripts. "
                       "Segments marked Reviewed are never offered. If more than the name is wrong (“Grinding Bull” for "
                       "“Guiding Bolt”), use “Edit line…” or double-click the Context to rewrite the whole line.")
        intro.setWordWrap(True); layout.addWidget(intro)

        top = QHBoxLayout()
        top.addWidget(QLabel("Show:"))
        self.filter = QLineEdit(); self.filter.setPlaceholderText("filter by heard word or name")
        self.filter.textChanged.connect(self._apply_filter); top.addWidget(self.filter, 1)
        top.addWidget(QLabel("Near-miss closeness:"))
        self.threshold = QDoubleSpinBox(); self.threshold.setRange(0.60, 0.95); self.threshold.setSingleStep(0.05)
        self.threshold.setDecimals(2); self.threshold.setValue(DEFAULT_THRESHOLD)
        self.threshold.setToolTip("How close a word must be to a name to be listed as a near miss. Lower finds more, with more wrong suggestions. 0.75 suited Witchlight 07.")
        top.addWidget(self.threshold)
        rescan = QPushButton("Search again"); rescan.clicked.connect(lambda: self._scan(fresh=True)); top.addWidget(rescan)
        rescan.setToolTip("Look again, using the lines as they are now in the editor (this window can stay open while you "
                          "play or edit lines there).")
        layout.addLayout(top)
        opts = QHBoxLayout()
        self.hide_known = QCheckBox("Hide words that are already a name")
        self.hide_known.setChecked(True)
        self.hide_known.setToolTip("A word spelled exactly like a name in your books or your Obsidian notes, or a word you chose "
                                   "here before (for example “Garry”), is not offered as a misspelling of another name.")
        self.use_books = QCheckBox(f"Also suggest names from your D&&D books (at least {int(BOOK_THRESHOLD * 100)}% close)")
        self.use_books.setChecked(bool(getattr(index, "book", None)))
        self.use_books.setEnabled(bool(getattr(index, "book", None)))
        self.use_books.setToolTip("When no campaign name is close, a name ticked in D&D books… (a spell, monster, place or person "
                                  "from the books) is offered, with the book it comes from. Stricter than campaign names, "
                                  "because there are many more of them.")
        for w in (self.hide_known, self.use_books):
            w.toggled.connect(lambda *_: self._scan()); opts.addWidget(w)
        opts.addStretch(); layout.addLayout(opts)

        self.table = ShiftCheckTable(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        header = self.table.horizontalHeader()
        for col in range(len(COLUMNS) - 1):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(len(COLUMNS) - 1, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.cellDoubleClicked.connect(lambda r, c: self._edit_line(r) if c == C_CONTEXT else self._play_row(r))
        layout.addWidget(self.table, 1)

        controls = QHBoxLayout()
        play = QPushButton("▶ Play selected"); play.clicked.connect(lambda: self._play_row(self.table.currentRow()))
        play.setToolTip("Plays the selected line so you can hear it before ticking (or double-click a row, or use its ▶ button).")
        pause_btn = QPushButton("⏯ Pause / resume"); pause_btn.clicked.connect(self._pause_or_play); pause_btn.setEnabled(pause is not None)
        pause_btn.setToolTip("Pause the line that is playing, or carry on from where it stopped. Space does the same; "
                             "on a different row, Space plays that row.")
        QShortcut(QKeySequence(Qt.Key_Space), self.table, activated=self._pause_or_play)
        edit = QPushButton("Edit line…"); edit.clicked.connect(lambda: self._edit_line(self.table.currentRow()))
        edit.setToolTip("Rewrite the whole line of the selected row (or double-click its Context). Applied with the others when you press Apply; "
                        "other suggestions in that line are then not used.")
        add = QPushButton("It's a name: add it…"); add.clicked.connect(self._add_name); add.setEnabled(add_name is not None)
        add.setToolTip("The heard word is right (a name you have not added yet, like “Garry”): add it to Campaign names so it "
                       "is never offered as another name again.")
        group = QPushButton("Tick all like selected"); group.clicked.connect(self._tick_group)
        group.setToolTip("Ticks every shown row with the same heard word and the same change as the selected row.")
        tick_all, untick_all = QPushButton("Tick all shown"), QPushButton("Untick all")
        tick_all.clicked.connect(lambda: self._set_all(Qt.Checked, shown_only=True))
        untick_all.clicked.connect(lambda: self._set_all(Qt.Unchecked))
        for w in (play, pause_btn, edit, add, group): controls.addWidget(w)
        controls.addStretch()
        for w in (tick_all, untick_all): controls.addWidget(w)
        layout.addLayout(controls)
        layout.addWidget(QLabel("Tip: tick one box, then hold Shift and click another to tick everything between them."))
        self.remember = QCheckBox("Remember applied near-miss spellings as known mishearings (found exactly next time)")
        self.remember.setToolTip("Stores e.g. “Agdon → Agden” in this campaign's known mishearings. Your campaign names list is not changed.")
        layout.addWidget(self.remember)
        self.status = QLabel(""); layout.addWidget(self.status)

        buttons = QDialogButtonBox()
        self.apply_btn = buttons.addButton("Apply ticked…", QDialogButtonBox.AcceptRole)
        self.skip_btn = buttons.addButton("Skip in this transcript", QDialogButtonBox.ActionRole)
        self.skip_btn.setToolTip("Stop suggesting the selected heard word -> name pairs in this transcript only. "
                                 "Other sessions still get them. No text is changed.")
        self.skip_btn.setEnabled(skip is not None)
        self.never_btn = buttons.addButton("Never suggest selected rows", QDialogButtonBox.ActionRole)
        self.never_btn.setToolTip("Never offer the selected heard word → name pairs again, in every line of every transcript of "
                                  "this campaign (for example “Alec” → Alex: every “Alec”, not just this row). The same "
                                  "word can still be offered as a different name. No text is changed.")
        buttons.addButton(QDialogButtonBox.Close)
        self.apply_btn.clicked.connect(self._apply)
        self.never_btn.clicked.connect(self._never)
        self.skip_btn.clicked.connect(self._skip_here)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._scan()
        from .window_state import remember; remember(self)   # v2.46.1: opens at the size and place you left it
        from .notices import attach; attach(self)   # v2.47: yellow note bar at the bottom

    def keyPressEvent(self, event):
        """v2.46: Enter in the filter box does not press Apply."""
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            event.accept(); return
        super().keyPressEvent(event)

    # ---- building the table ----
    def _scan(self, fresh=False):
        # Keep the user's ticks across a re-search (keyed by segment and position).
        kept = {(s["edit_id"], s["offset"]) for r, s in enumerate(self.suggestions)
                if self.table.item(r, 0) is not None and self.table.item(r, 0).checkState() == Qt.Checked}
        if fresh and self.reload is not None:
            try:
                self.segments = self.reload()
            except Exception:
                pass
        hide = getattr(self, "hide_known", None)
        books = getattr(self, "use_books", None)
        self.suggestions, self.skipped = find_suggestions(self.segments, self.index, self.lower_vocab, self.threshold.value(),
                                                          skip_known=bool(hide and hide.isChecked()),
                                                          use_books=bool(books and books.isChecked()))
        self.table.setRowCount(0)
        self.table._anchor = None
        self.table.setRowCount(len(self.suggestions))
        for r, s in enumerate(self.suggestions):
            use = QTableWidgetItem(); use.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            use.setCheckState(Qt.Checked if (s["edit_id"], s["offset"]) in kept else Qt.Unchecked); use.setData(Qt.UserRole, r)
            self.table.setItem(r, C_USE, use)
            listen = QPushButton("▶"); listen.setToolTip("Play this line before deciding; press again to pause or carry on")
            listen.setStyleSheet("QPushButton{padding:1px 8px;}")
            listen.clicked.connect(lambda _checked=False, row=r: self._toggle_row(row))
            self.table.setCellWidget(r, C_PLAY, listen)
            self.table.setItem(r, C_TIME, QTableWidgetItem(_clock(s["start"])))
            self.table.setItem(r, C_SPEAKER, QTableWidgetItem(self.speaker_label(s["speaker_id"]) if s["speaker_id"] else ""))
            self.table.setItem(r, C_HEARD, QTableWidgetItem(s["heard"]))
            combo = QComboBox(); combo.setEditable(True); combo.addItems(s["options"])
            combo.setToolTip("The spelling to use. Pick another suggestion or type your own.")
            self.table.setCellWidget(r, C_CHANGE, combo)
            self.table.setItem(r, C_WHY, QTableWidgetItem(_why(s)))
            ctx = QTableWidgetItem(context_snippet(s["text"], s["offset"], s["length"]))
            ctx.setToolTip(s["text"][:600])
            self.table.setItem(r, C_CONTEXT, ctx)
        for edit_id in list(self.line_edits):
            self._show_line_edit(edit_id)
        self._apply_filter()

    # ---- whole-line edits ----
    def _rows_for(self, edit_id):
        return [r for r, s in enumerate(self.suggestions) if s["edit_id"] == edit_id]

    def _edit_line(self, r):
        if not 0 <= r < len(self.suggestions):
            return
        s = self.suggestions[r]
        current = self.line_edits.get(s["edit_id"], s["text"])
        text, ok = QInputDialog.getMultiLineText(
            self, "Edit line", "Rewrite the whole line as it was said. Nothing changes until you press Apply. "
            "Leave it as it was to cancel the edit.", current)
        if not ok:
            return
        text = text.strip()
        if not text or text == s["text"].strip():
            self.line_edits.pop(s["edit_id"], None)
        else:
            self.line_edits[s["edit_id"]] = text
            self.table.item(r, C_USE).setCheckState(Qt.Checked)
        self._show_line_edit(s["edit_id"])

    def _show_line_edit(self, edit_id):
        edited = self.line_edits.get(edit_id)
        for r in self._rows_for(edit_id):
            s = self.suggestions[r]
            # v2.43: the name list stays usable on an edited line; a ticked name is also put into your edited text
            if edited is None:
                self.table.item(r, C_WHY).setText(_why(s))
                ctx = self.table.item(r, C_CONTEXT)
                ctx.setText(context_snippet(s["text"], s["offset"], s["length"])); ctx.setToolTip(s["text"][:600])
            else:
                self.table.item(r, C_WHY).setText("Line edited by you")
                ctx = self.table.item(r, C_CONTEXT)
                ctx.setText("✎ " + edited)
                ctx.setToolTip("Original: " + s["text"][:600] + "\n\nIf this row is ticked, its name choice is also applied "
                               "inside your edited line wherever the misheard word is still there.")

    def _apply_filter(self):
        needle = self.filter.text().strip().casefold()
        shown = 0
        for r, s in enumerate(self.suggestions):
            hide = bool(needle) and needle not in s["heard"].casefold() and not any(needle in o.casefold() for o in s["options"])
            self.table.setRowHidden(r, hide)
            shown += 0 if hide else 1
        skipped = f" · {self.skipped} more in Reviewed segments, not offered" if self.skipped else ""
        self.status.setText(f"{len(self.suggestions)} suggestion(s), {shown} shown{skipped}.")

    # ---- ticking ----
    def _set_all(self, state, shown_only=False):
        for r in range(self.table.rowCount()):
            if shown_only and self.table.isRowHidden(r):
                continue
            self.table.item(r, 0).setCheckState(state)

    def _choice(self, r):
        combo = self.table.cellWidget(r, C_CHANGE)
        return combo.currentText().strip() if combo else ""

    def _tick_group(self):
        r0 = self.table.currentRow()
        if r0 < 0:
            return
        heard, choice = self.suggestions[r0]["heard"].casefold(), self._choice(r0)
        for r, s in enumerate(self.suggestions):
            if not self.table.isRowHidden(r) and s["heard"].casefold() == heard and self._choice(r) == choice:
                self.table.item(r, 0).setCheckState(Qt.Checked)

    def _play_row(self, r):
        if self.play and 0 <= r < len(self.suggestions):
            self._playing_row = r
            self.play(self.suggestions[r]["edit_id"])

    def _pause_or_play(self):
        self._toggle_row(self.table.currentRow())

    def _toggle_row(self, r):
        """A row's ▶ (and Space): play it, or pause/resume it if it is the row playing (v2.31.3)."""
        if r == self._playing_row and self.pause and self.pause():
            return
        self._play_row(r)

    # ---- decisions ----
    def ticked(self):
        out, lines_done = [], set()
        for r, s in enumerate(self.suggestions):
            if self.table.item(r, 0).checkState() == Qt.Checked:
                if s["edit_id"] in self.line_edits:
                    # One whole-line change per segment; its word suggestions are not used.
                    if s["edit_id"] not in lines_done:
                        lines_done.add(s["edit_id"])
                        out.append(dict(s, kind="line", heard=s["text"], offset=0, length=len(s["text"]),
                                        replacement=self._edited_with_names(s["edit_id"])))
                    continue
                choice = self._choice(r)
                if choice and choice != s["heard"]:
                    out.append(dict(s, replacement=choice))
        return out

    def _edited_with_names(self, edit_id):
        """v2.43: your edited line with the ticked name choices of that line put in, wherever the misheard word
        is still in your text ("Where is Mirna now, Sam?" + Mirna -> Myrna)."""
        text = self.line_edits[edit_id]
        for r in self._rows_for(edit_id):
            s, choice = self.suggestions[r], self._choice(r)
            if self.table.item(r, 0).checkState() != Qt.Checked or not choice or choice == s["heard"]:
                continue
            text = re.sub(r"(?<![\w'’])" + re.escape(s["heard"]) + r"(?!\w)", lambda _m, c=choice: c, text)
        return text

    def _apply(self):
        chosen = self.ticked()
        if not chosen:
            QMessageBox.information(self, "Correct names", "Tick the rows to change first (Shift+click ticks a range)."); return
        segs = len({c["edit_id"] for c in chosen})
        lines = sum(1 for c in chosen if c["kind"] == "line")
        what = f"{len(chosen) - lines} word(s)" + (f" and {lines} edited line(s)" if lines else "")
        if QMessageBox.question(self, "Apply name corrections",
                                f"Change {what} in {segs} segment(s)?\n\nOnly the ticked words and your edited lines change. "
                                "Reviewed segments are never changed. You can undo this in the editor (Ctrl+Z).",
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        if self.remember.isChecked() and self.record:
            for pair in sorted({(c["heard"], c["replacement"]) for c in chosen if c["kind"] in ("near", "book")}):
                self.record(pair[0], pair[1], True)
        if self.keep:   # v2.46: the names you chose are right: never offered as another name again
            self.keep(sorted({c["replacement"] for c in chosen if c["kind"] != "line"}))
        self.chosen = chosen
        self.accept()

    def _add_name(self):
        r = self.table.currentRow()
        if not (0 <= r < len(self.suggestions)) or self.add_name is None:
            QMessageBox.information(self, "Add a name", "Select the row whose heard word is a real name first."); return
        heard = self.suggestions[r]["heard"]
        name, ok = QInputDialog.getText(self, "Add to Campaign names", "Name to add (as it should be spelled):", text=heard)
        if not ok or not name.strip():
            return
        cats = ["NPC", "Character / campaign name", "Place", "Creature", "Item", "Faction", "Other"]
        cat, ok = QInputDialog.getItem(self, "Add to Campaign names", f"Category for {name.strip()}:", cats, 0, False)
        if not ok:
            return
        added = self.add_name(name.strip(), cat)
        self.index.correct_words.add(name.strip().casefold())
        self.index.correct_words.update(p.casefold() for p in name.split())
        self.index.known_words.add(name.strip().casefold())
        self._scan()
        self.status.setText(self.status.text() + (f" Added {name.strip()} to Campaign names." if added else
                                                  f" {name.strip()} is already in Campaign names: it is no longer offered here."))

    def _selected_pairs(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return sorted({(self.suggestions[r]["heard"], self._choice(r)) for r in rows
                       if self._choice(r) and self.suggestions[r]["edit_id"] not in self.line_edits})

    def _skip_here(self):
        pairs = self._selected_pairs()
        if not pairs:
            QMessageBox.information(self, "Skip in this transcript", "Select the rows (click their text, Ctrl/Shift for several) to skip."); return
        if self.skip:
            self.skip(pairs)
        self.index.rejected.update((h.casefold(), c.casefold()) for h, c in pairs)
        self._scan()
        self.status.setText(self.status.text() + f" Skipped in this transcript: {', '.join(f'{h} → {c}' for h, c in pairs[:6])}.")

    def _never(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            QMessageBox.information(self, "Never suggest", "Select the rows (click their text, Ctrl/Shift for several) that are wrong suggestions."); return
        pairs = sorted({(self.suggestions[r]["heard"], self._choice(r)) for r in rows if self._choice(r)})
        listing = "\n".join(f"• {h} → {c}" for h, c in pairs[:15]) + ("\n…" if len(pairs) > 15 else "")
        if QMessageBox.question(self, "Never suggest",
                                f"Never suggest these again in this campaign?\n\n{listing}\n\nNo transcript text is changed.",
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        if self.record:
            for heard, choice in pairs:
                self.record(heard, choice, False)
        self.index.rejected.update((h.casefold(), c.casefold()) for h, c in pairs)
        self._scan()
