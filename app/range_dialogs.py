"""Windows for re-transcribing time ranges (v2.31): choose the range(s) and retry settings, then
compare the new text with the current transcript row by row and pick what to use.

v2.31.3: several ranges at once (ticked in Suggested ranges), a row's ▶ also pauses/resumes, the compare
window can play every row one after another and split a new line between two speakers."""
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit, QComboBox,
                               QCheckBox, QDoubleSpinBox, QDialogButtonBox, QMessageBox, QTableWidget,
                               QTableWidgetItem, QHeaderView, QAbstractItemView, QPushButton, QWidget, QListWidget)

from .range_merge import build_slots_multi, can_use_new, preview_speakers, split_segment, KEEP, NEW


class RangeSetupDialog(QDialog):
    """Range (start/end) plus the retry's settings. values() -> dict with 'ranges' [(start, end), ...].
    multi: a list of (start, end) ranges (v2.31.3); the start/end boxes are then replaced by a summary."""

    def __init__(self, start, end, settings, fmt_time, parse_time, parent=None, multi=None):
        super().__init__(parent)
        self.parse_time, self.multi = parse_time, list(multi or [])
        self.setWindowTitle("Re-transcribe range" + ("s" if len(self.multi) > 1 else ""))
        layout = QVBoxLayout(self)
        intro = QLabel("Whisper runs again on just " + ("these stretches" if len(self.multi) > 1 else "this stretch") +
                       ". Nothing changes in your transcript until you pick rows in the next window and press Apply. "
                       "Reviewed segments are always kept.")
        intro.setWordWrap(True); layout.addWidget(intro)
        form = QFormLayout()
        self.start_edit, self.end_edit = QLineEdit(fmt_time(start)), QLineEdit(fmt_time(end))
        if len(self.multi) > 1:
            total = sum(b - a for a, b in self.multi)
            listing = ", ".join(fmt_time(a)[:8] for a, _ in self.multi[:8]) + (" …" if len(self.multi) > 8 else "")
            summary = QLabel(f"{len(self.multi)} ranges, {total:.0f} s in all: {listing}"); summary.setWordWrap(True)
            form.addRow("Ranges", summary)
        else:
            form.addRow("Start", self.start_edit); form.addRow("End", self.end_edit)
        self.padding = QDoubleSpinBox(); self.padding.setRange(0, 10); self.padding.setSingleStep(0.5); self.padding.setValue(2.0)
        self.padding.setSuffix(" s"); self.padding.setToolTip("Extra audio given to Whisper before and after the range so words at the edges "
                                                              "are heard in context. Text from the padding is not offered.")
        form.addRow("Padding", self.padding)
        self.vad = QDoubleSpinBox(); self.vad.setRange(0.05, 0.9); self.vad.setSingleStep(0.05); self.vad.setDecimals(2)
        self.vad.setValue(float(settings.get("vad_threshold") or 0.4))
        self.vad.setToolTip("Lower keeps quieter or overlapping speech that the full run may have treated as silence "
                            "(your Audio settings value is the default; try 0.25 for a missed stretch).")
        form.addRow("Speech detection threshold", self.vad)
        self.beam = QComboBox()
        for label, value in (("5 (same as a full run)", 5), ("8", 8), ("10 (slower, checks more alternatives)", 10)):
            self.beam.addItem(label, value)
        self.beam.setToolTip("How many alternative wordings Whisper weighs at each step. Higher is slower and sometimes more accurate.")
        form.addRow("Beam size", self.beam)
        self.style = QCheckBox("Punctuation style hint"); self.style.setChecked(bool(settings.get("style_hint", True)))
        form.addRow("", self.style)
        layout.addLayout(form)
        model = QLabel(f"Model: Whisper {settings.get('model', '?')} on {settings.get('device', '?')} ({settings.get('compute', '?')}), "
                       "from Audio settings. The model is loaded once" + (" for all the ranges." if len(self.multi) > 1 else ", which takes a little while."))
        model.setWordWrap(True); layout.addWidget(model)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Re-transcribe")
        buttons.accepted.connect(self._check); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _check(self):
        if len(self.multi) > 1:
            ranges = sorted(self.multi)
        else:
            try:
                start, end = self.parse_time(self.start_edit.text()), self.parse_time(self.end_edit.text())
            except ValueError as exc:
                QMessageBox.warning(self, "Re-transcribe range", str(exc)); return
            if end - start < 0.5:
                QMessageBox.warning(self, "Re-transcribe range", "The end must be at least half a second after the start."); return
            ranges = [(start, end)]
        if sum(b - a for a, b in ranges) > 1800:
            QMessageBox.warning(self, "Re-transcribe range", "Choose 30 minutes or less in all (for a whole session, transcribe it again instead)."); return
        self._values = {"start": ranges[0][0], "end": ranges[-1][1], "ranges": ranges, "padding": self.padding.value(),
                        "vad_threshold": self.vad.value(), "beam_size": int(self.beam.currentData()), "style_hint": self.style.isChecked()}
        self.accept()

    def values(self):
        return getattr(self, "_values", None)


class _RowPlayback:
    """Shared by the review windows: a row's ▶ (and Space) plays it, or pauses/resumes it if it is the row
    playing. Needs self.table, self._play_row_audio(r) and self.pause (returns False if nothing to resume)."""

    def _init_playback(self, pause):
        self.pause, self._playing_row = pause, -1

    def _toggle_row(self, r):
        if r < 0:
            return
        if r == self._playing_row and self.pause and self.pause():
            return
        self._playing_row = r
        self._play_row_audio(r)

    def _toggle_current(self):
        self._toggle_row(self.table.currentRow())

    def _listen_button(self, r):
        listen = QPushButton("▶"); listen.setToolTip("Play this row; press again to pause or carry on")
        listen.setStyleSheet("QPushButton{padding:1px 8px;}")
        listen.clicked.connect(lambda _c=False, row=r: self._toggle_row(row))
        return listen


# v2.32.1: Listen is last, next to the speaker boxes, so choosing a speaker and listening sit side by side.
COLUMNS = ["Time", "Current transcript", "New (re-transcribed)", "Use", "Speaker for new lines", "Listen"]
C_TIME, C_CUR, C_NEW, C_USE, C_SPK, C_PLAY = range(len(COLUMNS))
AUTO = ""


class RangeMergeDialog(QDialog, _RowPlayback):
    """Rows lined up by time. Every row starts on "Keep current". choices(), speakers(), check_names().

    ranges: [(start, end, new_segments)] for several re-transcribed ranges (v2.31.3); otherwise new/start/end.
    speaker_ids: the transcript's speaker ids for the Speaker column; turns: saved speaker turns, used for the
    automatic guess. pause(): pause/resume the playing clip. is_playing(): True while audio plays (for Play all)."""

    def __init__(self, current, new, start, end, speaker_label, fmt_time, play=None, settings_text="", parent=None,
                 speaker_ids=(), turns=None, pause=None, ranges=None, is_playing=None):
        super().__init__(parent)
        from PySide6.QtGui import QKeySequence, QShortcut
        self.current, self.turns, self.speaker_ids = current, turns, list(speaker_ids)
        self.ranges = ranges or [(start, end, new)]
        self.slots = build_slots_multi(current, self.ranges)
        self.play, self.fmt_time, self.speaker_label, self.is_playing = play, fmt_time, speaker_label, is_playing
        self._init_playback(pause)
        self.setWindowTitle("Compare and merge re-transcribed range" + ("s" if len(self.ranges) > 1 else ""))
        self.resize(1300, 700)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        layout = QVBoxLayout(self)
        where = (f"{fmt_time(start)} – {fmt_time(end)}" if len(self.ranges) == 1 else f"{len(self.ranges)} ranges")
        info = QLabel(f"{where} · {settings_text}\nEvery row starts on “Keep current”. Pick “Use new” where the new text is better, "
                      "then press Apply. Rows with a Reviewed segment are locked and always kept. New segments are unreviewed; "
                      "check their speakers. If two people speak in one new line, select the row and press “Split new line…”.")
        info.setWordWrap(True); layout.addWidget(info)
        self.table = QTableWidget(len(self.slots), len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        header = self.table.horizontalHeader()
        for col in (C_TIME, C_PLAY, C_USE):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(C_CUR, QHeaderView.Stretch); header.setSectionResizeMode(C_NEW, QHeaderView.Stretch)
        header.setSectionResizeMode(C_SPK, QHeaderView.Interactive); self.table.setColumnWidth(C_SPK, 250)
        self.table.setWordWrap(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        for r in range(len(self.slots)):
            self._fill_row(r)
        self.table.resizeRowsToContents()
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        holes = QPushButton("Use new where current is empty"); holes.clicked.connect(lambda: self._set(lambda s: not s["current"]))
        holes.setToolTip("Fills only the gaps: rows where the current transcript has nothing.")
        all_new = QPushButton("Use new for all unlocked rows"); all_new.clicked.connect(lambda: self._set(lambda s: True))
        keep = QPushButton("Keep all current"); keep.clicked.connect(self._keep_all)
        split = QPushButton("Split new line…"); split.clicked.connect(self._split_selected)
        split.setToolTip("Split the selected row's new text into two lines (for example “Yes.” from one person and the rest from "
                         "another), so each part can get its own speaker.")
        for b in (holes, all_new, keep, split):
            row.addWidget(b)
        row.addStretch()
        self.play_all_btn = QPushButton("▶ Play all from selected"); self.play_all_btn.clicked.connect(self._play_all)
        self.play_all_btn.setToolTip("Play every row one after another, starting at the selected row. ⏯ pauses and carries on; "
                                     "pressing a row's ▶ stops playing all.")
        self.play_all_btn.setEnabled(is_playing is not None and play is not None)
        pause_btn = QPushButton("⏯ Pause / resume"); pause_btn.clicked.connect(self._pause_button); pause_btn.setEnabled(pause is not None)
        pause_btn.setToolTip("Pause what is playing, or carry on from where it stopped. Space does the same; on a different row, "
                             "Space plays that row.")
        QShortcut(QKeySequence(Qt.Key_Space), self.table, activated=self._toggle_current)
        row.addWidget(self.play_all_btn); row.addWidget(pause_btn)
        layout.addLayout(row)
        self.names_box = QCheckBox("After applying, check names in the new lines (opens Correct names on just those lines)")
        self.names_box.setChecked(True)
        layout.addWidget(self.names_box)
        self.review_box = QCheckBox("Mark a new line Reviewed when I pick its speaker (lines left on Automatic stay unreviewed)")
        self.review_box.setChecked(True)
        self.review_box.setToolTip("Picking a speaker here means you listened and chose who said it, the same as ticking Reviewed in the "
                                   "editor. Reviewed lines are locked: speaker identification never changes them and uses them as "
                                   "examples. The names check above runs first, so names can still be corrected.")
        layout.addWidget(self.review_box)
        self.status = QLabel(); layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.apply_btn = buttons.addButton("Apply…", QDialogButtonBox.AcceptRole)
        self.apply_btn.clicked.connect(self._apply); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._all_timer = QTimer(self); self._all_timer.setInterval(150); self._all_timer.timeout.connect(self._play_all_tick)
        self._playing_all, self._user_paused = False, False
        self._update_status()
        from .window_state import remember; remember(self)   # v2.46.1: opens at the size and place you left it
        from .notices import attach; attach(self)   # v2.47: yellow note bar at the bottom

    # ---- rows ----
    def _fill_row(self, r, choice=None):
        slot = self.slots[r]
        self.table.setItem(r, C_TIME, QTableWidgetItem(self.fmt_time(slot["start"])[:8]))
        self.table.setCellWidget(r, C_PLAY, self._listen_button(r))
        cur_text = "\n".join(f"[{self._who(s)}{' · Reviewed' if s.get('speaker_confirmed') else ''}] {s['text']}"
                             for s in slot["current"]) or "— nothing here —"
        many = len(slot["new"]) > 1
        new_text = "\n".join((f"{k + 1}. " if many else "") + s["text"] for k, s in enumerate(slot["new"])) or "— nothing —"
        cur, nw = QTableWidgetItem(cur_text), QTableWidgetItem(new_text)
        if not slot["current"]:
            cur.setForeground(Qt.gray)
        if not slot["new"]:
            nw.setForeground(Qt.gray)
        self.table.setItem(r, C_CUR, cur); self.table.setItem(r, C_NEW, nw)
        combo = QComboBox(); combo.addItem("Keep current", KEEP)
        if can_use_new(slot):
            combo.addItem("Use new", NEW)
        elif slot["locked"]:
            combo.setToolTip("Contains a Reviewed segment: always kept.")
            combo.setItemText(0, "Keep (Reviewed)")
        combo.setEnabled(combo.count() > 1)
        if choice is not None and combo.findData(choice) >= 0:
            combo.setCurrentIndex(combo.findData(choice))
        combo.currentIndexChanged.connect(self._update_status)
        self.table.setCellWidget(r, C_USE, combo)
        # one speaker box per new line, so a split line can go to two people
        box = QWidget(); lay = QVBoxLayout(box); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(2)
        guesses = preview_speakers(slot, self.current, self.turns) if slot["new"] else []
        box.combos = []
        for k in range(max(1, len(slot["new"]))):
            spk = QComboBox()
            guess = guesses[k] if k < len(guesses) else ""
            spk.addItem((f"{k + 1}. " if many else "") + "Automatic: " + (self.speaker_label(guess) if guess else "none found"), AUTO)
            for sid in self.speaker_ids:
                spk.addItem((f"{k + 1}. " if many else "") + f"{sid} · {self.speaker_label(sid)}", sid)
            spk.setToolTip("Automatic uses the saved speaker turns if there are any, otherwise the speaker of the line it "
                           "replaces (or the nearest line). Pick a speaker to give this new line to them. New lines are unreviewed either way.")
            spk.setEnabled(can_use_new(slot))
            lay.addWidget(spk); box.combos.append(spk)
        self.table.setCellWidget(r, C_SPK, box)

    def _who(self, seg):
        sid = seg.get("speaker_id") or ""
        return self.speaker_label(sid) if sid else "no speaker"

    def _combo(self, r):
        return self.table.cellWidget(r, C_USE)

    def _set(self, wanted):
        for r, slot in enumerate(self.slots):
            combo = self._combo(r)
            if can_use_new(slot) and wanted(slot):
                combo.setCurrentIndex(combo.findData(NEW))

    def _keep_all(self):
        for r in range(len(self.slots)):
            self._combo(r).setCurrentIndex(0)

    def choices(self):
        return [self._combo(r).currentData() for r in range(len(self.slots))]

    def speakers(self):
        """Per row: a list with one entry per new line (a speaker id, or None for automatic)."""
        return [[c.currentData() or None for c in self.table.cellWidget(r, C_SPK).combos] for r in range(len(self.slots))]

    def check_names(self):
        return self.names_box.isChecked()

    def review_picked(self):
        return self.review_box.isChecked()

    # ---- split a new line between two speakers ----
    def _split_selected(self):
        r = self.table.currentRow()
        if r < 0 or not self.slots[r]["new"]:
            QMessageBox.information(self, "Split new line", "Select a row that has new text first."); return
        if not can_use_new(self.slots[r]):
            QMessageBox.information(self, "Split new line", "This row contains a Reviewed segment, so its new text is not used."); return
        dlg = SplitLineDialog(self.slots[r]["new"], self)
        if dlg.exec() != QDialog.Accepted or dlg.result_split is None:
            return
        k, (first, second) = dlg.result_split
        keep_choice = self._combo(r).currentData()
        old_speakers = [c.currentData() for c in self.table.cellWidget(r, C_SPK).combos]
        self.slots[r]["new"][k:k + 1] = [first, second]
        self._fill_row(r, NEW if keep_choice == NEW else None)
        # keep the speakers already picked for the other lines; both halves of the split line start on Automatic
        combos = self.table.cellWidget(r, C_SPK).combos
        mapping = old_speakers[:k] + [AUTO, AUTO] + old_speakers[k + 1:]
        for c, sid in zip(combos, mapping):
            if sid and c.findData(sid) >= 0:
                c.setCurrentIndex(c.findData(sid))
        self.table.resizeRowToContents(r)
        self._update_status()

    # ---- playback ----
    def _play_row_audio(self, r):
        if self.play and 0 <= r < len(self.slots):
            self.table.selectRow(r)
            self.play(self.slots[r]["start"], self.slots[r]["end"])

    def _toggle_row(self, r):
        if self._playing_all:
            if r == self._playing_row:
                self._pause_button(); return    # pausing the row that is playing keeps Play all going
            self._stop_all()
        _RowPlayback._toggle_row(self, r)

    def _pause_button(self):
        if self._playing_all:
            if self.pause and self.pause():
                self._user_paused = not self._user_paused
            return
        self._toggle_current()

    def _play_all(self):
        if not self.slots:
            return
        r = max(0, self.table.currentRow())
        self._playing_all, self._user_paused = True, False
        self.play_all_btn.setText("■ Stop playing all")
        self.play_all_btn.clicked.disconnect(); self.play_all_btn.clicked.connect(self._stop_all)
        self._start_all_row(r)
        self._all_timer.start()

    def _start_all_row(self, r):
        self._playing_row, self._seen_playing, self._row_started = r, False, time.monotonic()
        self._play_row_audio(r)

    def _play_all_tick(self):
        if not self._playing_all or self._user_paused:
            return
        playing = bool(self.is_playing and self.is_playing())
        if playing:
            self._seen_playing = True; return
        if not self._seen_playing and time.monotonic() - self._row_started < 2.0:
            return   # still starting
        nxt = self._playing_row + 1
        if nxt >= len(self.slots):
            self._stop_all(); return
        self._start_all_row(nxt)

    def _stop_all(self):
        self._playing_all, self._user_paused = False, False
        self._all_timer.stop()
        self.play_all_btn.setText("▶ Play all from selected")
        self.play_all_btn.clicked.disconnect(); self.play_all_btn.clicked.connect(self._play_all)

    def done(self, result):
        self._all_timer.stop()
        super().done(result)

    # ---- status / apply ----
    def _update_status(self, *_):
        picks = self.choices()
        used = sum(1 for c in picks if c == NEW)
        locked = sum(1 for s in self.slots if s["locked"])
        holes = sum(1 for s in self.slots if s["new"] and not s["current"])
        self.status.setText(f"{len(self.slots)} row(s) · {used} set to “Use new” · {holes} where the current transcript has nothing"
                            + (f" · {locked} locked (Reviewed)" if locked else ""))

    def _apply(self):
        picks = self.choices()
        used = sum(1 for c in picks if c == NEW)
        if not used:
            QMessageBox.information(self, "Re-transcribe range", "No row is set to “Use new”. Pick the rows to replace first."); return
        old = sum(len([s for s in slot["current"] if not s.get("speaker_confirmed")]) for slot, c in zip(self.slots, picks) if c == NEW)
        new = sum(len(slot["new"]) for slot, c in zip(self.slots, picks) if c == NEW)
        if QMessageBox.question(self, "Apply re-transcribed rows",
                                f"Replace {old} current segment(s) with {new} new segment(s) in {used} row(s)?\n\n"
                                "Reviewed segments are never changed. "
                                + ("New lines whose speaker you picked will be marked Reviewed; the rest are unreviewed. "
                                   if self.review_box.isChecked() else "New segments are unreviewed. ")
                                + "Ctrl+Z in the editor undoes this.",
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self._all_timer.stop()
        self.accept()


class SplitLineDialog(QDialog):
    """Pick one of a row's new lines and where to split it. result_split -> (index, (first, second))."""

    def __init__(self, new_lines, parent=None):
        super().__init__(parent)
        self.new_lines, self.result_split = new_lines, None
        self.setWindowTitle("Split new line")
        self.resize(720, 260)
        layout = QVBoxLayout(self)
        tip = QLabel("Click in the text where the second person starts (or use the arrow keys), then press Split. "
                     "The time is taken from Whisper's word timings, so each part plays its own words.")
        tip.setWordWrap(True); layout.addWidget(tip)
        self.which = QListWidget()
        for k, s in enumerate(new_lines):
            self.which.addItem(f"{k + 1}. {s['text']}")
        self.which.setVisible(len(new_lines) > 1)
        self.which.currentRowChanged.connect(self._show)
        layout.addWidget(self.which)
        self.edit = QLineEdit(); self.edit.setReadOnly(True)
        self.edit.setStyleSheet("QLineEdit{font-size:14px; padding:4px;}")
        layout.addWidget(self.edit)
        self.preview = QLabel(); self.preview.setWordWrap(True); layout.addWidget(self.preview)
        self.edit.cursorPositionChanged.connect(self._preview)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.ok = buttons.addButton("Split", QDialogButtonBox.AcceptRole)
        self.ok.clicked.connect(self._split); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.which.setCurrentRow(0); self._show(0)

    def _show(self, k):
        if 0 <= k < len(self.new_lines):
            text = self.new_lines[k]["text"]
            self.edit.setText(text)
            # start at the end of the first sentence, the most common split (“Yes. Oh, it's me…”)
            cut = next((i + 1 for i, ch in enumerate(text) if ch in ".?!" and i + 1 < len(text)), len(text) // 2)
            self.edit.setCursorPosition(cut); self.edit.setFocus(); self._preview()

    def _preview(self, *_):
        k = max(0, self.which.currentRow())
        parts = split_segment(self.new_lines[k], self.edit.cursorPosition())
        if parts is None:
            self.preview.setText("Move the cursor between two words."); self.ok.setEnabled(False); return
        a, b = parts
        self.preview.setText(f"1: “{a['text']}”  ({a['start']:.2f}–{a['end']:.2f} s)\n2: “{b['text']}”  ({b['start']:.2f}–{b['end']:.2f} s)")
        self.ok.setEnabled(True)

    def _split(self):
        k = max(0, self.which.currentRow())
        parts = split_segment(self.new_lines[k], self.edit.cursorPosition())
        if parts:
            self.result_split = (k, parts); self.accept()


SUGGEST_COLUMNS = ["Do", "Time", "Length", "Why", "Current transcript", "Listen", "Tried"]
S_TICK, S_TIME, S_LEN, S_WHY, S_TEXT, S_PLAY, S_DONE = range(len(SUGGEST_COLUMNS))


class SuggestedRangesDialog(QDialog, _RowPlayback):
    """Likely problem spots (v2.31.1). Listen, then pick ranges to re-transcribe.
    chosen -> [(start, end), ...] (the ticked rows, or the selected row if none is ticked) or None.

    compute(loose) -> ranges, so "More suggestions" can recompute. tried: set of (start, end) already re-run."""

    def __init__(self, compute, fmt_time, play=None, pause=None, tried=(), audio_checked=True, loose=False, parent=None):
        super().__init__(parent)
        from PySide6.QtGui import QKeySequence, QShortcut
        self.compute, self.fmt_time, self.play = compute, fmt_time, play
        self._init_playback(pause)
        self.tried = {(round(a, 1), round(b, 1)) for a, b in tried}
        self.chosen, self.ranges, self.ticked = None, [], set()
        self.setWindowTitle("Suggested ranges to re-transcribe")
        self.resize(1180, 640)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        layout = QVBoxLayout(self)
        intro = QLabel("Places where Whisper may have missed or garbled speech. Listen first; if it sounds wrong, tick it (or several) "
                       "and press Re-transcribe. Nothing changes in your transcript from this list. Reviewed segments are not flagged."
                       + ("" if audio_checked else "\nThe prepared audio file was not found, so gaps were not checked for sound: "
                          "only gaps of 10 seconds or more are listed."))
        intro.setWordWrap(True); layout.addWidget(intro)
        top = QHBoxLayout()
        self.reason = QComboBox(); self.reason.addItem("All reasons", "")
        from .range_suggest import REASONS
        for r in REASONS:
            self.reason.addItem(r, r)
        self.reason.currentIndexChanged.connect(self._fill)
        self.sort = QComboBox(); self.sort.addItem("In time order", "time"); self.sort.addItem("Longest first", "length")
        self.sort.currentIndexChanged.connect(self._fill)
        self.loose = QCheckBox("More suggestions (also quieter gaps)"); self.loose.setChecked(loose)
        self.loose.setToolTip("Also lists gaps with only a little speech-level sound. Finds more, with more false alarms "
                              "(dice, laughter, music).")
        self.loose.toggled.connect(self._recompute)
        self.hide_tried = QCheckBox("Hide ones I have tried"); self.hide_tried.toggled.connect(self._fill)
        for w in (QLabel("Show"), self.reason, self.sort, self.loose, self.hide_tried):
            top.addWidget(w)
        top.addStretch(); layout.addLayout(top)
        self.table = QTableWidget(0, len(SUGGEST_COLUMNS))
        self.table.setHorizontalHeaderLabels(SUGGEST_COLUMNS)
        self.table.horizontalHeaderItem(S_TICK).setToolTip("Tick the ranges to re-transcribe together (the model is loaded once).")
        header = self.table.horizontalHeader()
        for col in (S_TICK, S_TIME, S_LEN, S_WHY, S_PLAY, S_DONE):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(S_TEXT, QHeaderView.Stretch)
        self.table.setWordWrap(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.cellDoubleClicked.connect(lambda r, c: self._toggle_row(r))
        self.table.itemChanged.connect(self._tick_changed)
        layout.addWidget(self.table, 1)
        controls = QHBoxLayout()
        play = QPushButton("▶ Play selected"); play.clicked.connect(lambda: self._toggle_row(self.table.currentRow()))
        pause_btn = QPushButton("⏯ Pause / resume"); pause_btn.clicked.connect(self._toggle_current); pause_btn.setEnabled(pause is not None)
        pause_btn.setToolTip("Pause the audio, or carry on from where it stopped (Space and each row's ▶ do the same for the row that is playing).")
        tick_all = QPushButton("Tick all shown"); tick_all.clicked.connect(lambda: self._tick_all(True))
        untick = QPushButton("Untick all"); untick.clicked.connect(lambda: self._tick_all(False))
        QShortcut(QKeySequence(Qt.Key_Space), self.table, activated=self._toggle_current)
        self.status = QLabel()
        for w in (play, pause_btn, tick_all, untick, self.status):
            controls.addWidget(w)
        controls.addStretch(); layout.addLayout(controls)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.go = buttons.addButton("Re-transcribe selected…", QDialogButtonBox.AcceptRole)
        self.go.clicked.connect(self._choose); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._recompute()
        from .window_state import remember; remember(self)   # v2.46.1: opens at the size and place you left it
        from .notices import attach; attach(self)   # v2.47: yellow note bar at the bottom

    def _recompute(self, *_):
        self.ranges = self.compute(self.loose.isChecked())
        self._fill()

    def _key(self, rng):
        return (round(rng["start"], 1), round(rng["end"], 1))

    def _visible(self):
        want = self.reason.currentData()
        rows = [r for r in self.ranges if (not want or want in r["reasons"])
                and not (self.hide_tried.isChecked() and self._key(r) in self.tried)]
        if self.sort.currentData() == "length":
            rows.sort(key=lambda r: r["start"] - r["end"])
        return rows

    def _fill(self, *_):
        self.shown = self._visible()
        self._playing_row = -1
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.shown))
        for i, rng in enumerate(self.shown):
            tick = QTableWidgetItem(); tick.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            tick.setCheckState(Qt.Checked if self._key(rng) in self.ticked else Qt.Unchecked)
            self.table.setItem(i, S_TICK, tick)
            self.table.setItem(i, S_TIME, QTableWidgetItem(self.fmt_time(rng["start"])[:8]))
            self.table.setItem(i, S_LEN, QTableWidgetItem(f"{rng['end'] - rng['start']:.1f} s"))
            why = QTableWidgetItem("\n".join(rng["reasons"])); why.setToolTip("\n".join(rng["details"]))
            self.table.setItem(i, S_WHY, why)
            text = QTableWidgetItem(rng["text"]); text.setToolTip("\n".join(rng["details"]))
            if rng["text"].startswith("—"):
                text.setForeground(Qt.gray)
            self.table.setItem(i, S_TEXT, text)
            self.table.setCellWidget(i, S_PLAY, self._listen_button(i))
            self.table.setItem(i, S_DONE, QTableWidgetItem("✓" if self._key(rng) in self.tried else ""))
        self.table.blockSignals(False)
        self.table.resizeRowsToContents()
        if self.shown:
            self.table.selectRow(0)
        self._update_status()

    def _tick_changed(self, item):
        if item.column() != S_TICK or not (0 <= item.row() < len(self.shown)):
            return
        key = self._key(self.shown[item.row()])
        (self.ticked.add if item.checkState() == Qt.Checked else self.ticked.discard)(key)
        self._update_status()

    def _tick_all(self, on):
        if on:
            self.ticked |= {self._key(r) for r in self.shown}
        else:
            self.ticked.clear()
        self._fill()

    def _update_status(self):
        counts = {}
        for r in self.ranges:
            for reason in r["reasons"]:
                counts[reason] = counts.get(reason, 0) + 1
        live = {self._key(r) for r in self.ranges}
        self.ticked &= live
        self.status.setText(f"{len(self.shown)} of {len(self.ranges)} shown · {len(self.ticked)} ticked · "
                            + (" · ".join(f"{n} {k.lower()}" for k, n in counts.items()) or "nothing found"))
        self.go.setText(f"Re-transcribe {len(self.ticked)} ticked…" if self.ticked else "Re-transcribe selected…")
        self.go.setEnabled(bool(self.shown))

    def _play_row_audio(self, r):
        if self.play and 0 <= r < len(self.shown):
            self.play(self.shown[r]["start"], self.shown[r]["end"])

    def _choose(self):
        if self.ticked:
            self.chosen = sorted((r["start"], r["end"]) for r in self.ranges if self._key(r) in self.ticked)
        else:
            r = self.table.currentRow()
            if not (0 <= r < len(self.shown)):
                return
            self.chosen = [(self.shown[r]["start"], self.shown[r]["end"])]
        self.accept()
