from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QComboBox, QTextEdit, QLabel, QPushButton,
                               QMessageBox, QCheckBox, QSpinBox, QWidget, QFileDialog)

from .compare_logic import load_segments, build_view, describe_run, edit_report
from .theme import tone, dim_span   # v2.46.1: shared colours

MAX_PANELS = 4
YELLOW = "background-color:#c99700; color:#000000;"   # word differs
GREEN = "background-color:#2f7d4f; color:#ffffff;"    # line only exists in this version
RED = "background-color:#a8453a; color:#ffffff;"      # reference line missing from another version
STYLE_FOR_KIND = {"changed": YELLOW, "only": GREEN, "missing": RED}


def _clock(seconds):
    s = int(max(0, seconds))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


class _Panel(QWidget):
    def __init__(self, dialog, index):
        super().__init__()
        self.dialog = dialog
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(3)
        top = QHBoxLayout()
        self.title = QLabel("Reference" if index == 0 else f"Version {chr(65 + index)}")
        self.title.setStyleSheet("font-weight:600;")
        self.choice = QComboBox()
        self.close_btn = QPushButton("✕"); self.close_btn.setMaximumWidth(28); self.close_btn.setToolTip("Remove this column from the comparison")
        self.delete_btn = QPushButton("Delete…"); self.delete_btn.setToolTip("Permanently delete this transcript version")
        top.addWidget(self.title); top.addWidget(self.choice, 1); top.addWidget(self.delete_btn); top.addWidget(self.close_btn)
        lay.addLayout(top)
        self.props = QLabel(""); self.props.setWordWrap(True); tone(self.props, "dim")
        self.props.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.props)
        self.summary = QLabel(""); lay.addWidget(self.summary)
        lay.addStretch(1)

    def run_id(self):
        return self.choice.currentData()


class TranscriptComparisonDialog(QDialog):
    def __init__(self, runs, parent=None, database=None):
        super().__init__(parent)
        self.runs = list(runs)
        self.database = database
        self._segments = {}
        self.setWindowTitle("Compare Whisper transcript versions")
        self.resize(1500, 860)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        layout = QVBoxLayout(self)

        options = QHBoxLayout()
        self.add_btn = QPushButton("＋ Add another version")
        self.add_btn.clicked.connect(self.add_panel)
        self.highlight = QCheckBox("Highlight differences"); self.highlight.setChecked(True)
        self.highlight.setToolTip("Words that differ from the reference (the first column) are highlighted. In the reference column, words that differ in any other column are highlighted. Capitalisation and punctuation are ignored.")
        self.only_diff = QCheckBox("Only lines that differ")
        self.min_changed = QSpinBox(); self.min_changed.setRange(1, 20); self.min_changed.setValue(1)
        self.min_changed.setToolTip("A line counts as different when at least this many words changed.")
        self.report_btn = QPushButton("Export edit report…")
        self.report_btn.setToolTip("Write a text report for the reference (first) column: what you changed in your reviewed segments compared with the original Whisper output of that same version (common word fixes, merges, splits, stretches Whisper missed).")
        self.report_btn.clicked.connect(self.export_report)
        for w in (self.add_btn, self.report_btn, self.highlight, self.only_diff, QLabel("changed words ≥"), self.min_changed):
            options.addWidget(w)
        options.addStretch()
        layout.addLayout(options)
        self.legend = QLabel(
            f"<span style='{YELLOW}'>&nbsp;yellow&nbsp;</span> a word that differs from the reference (in the reference column: a word that differs in another version) &nbsp; "
            f"<span style='{GREEN}'>&nbsp;green&nbsp;</span> a line that only this version has; the reference has nothing at that time &nbsp; "
            f"<span style='{RED}'>&nbsp;red&nbsp;</span> (reference column) a line another version does not have &nbsp; "
            "<b>—</b> this version has nothing at that time. Rows are lined up by time, so the same row is the same moment in every column.")
        self.legend.setWordWrap(True); layout.addWidget(self.legend)

        self.header_row = QHBoxLayout(); self.header_row.setSpacing(8)
        layout.addLayout(self.header_row)
        # One text view holding one table: every moment is one row, so a tall cell in one version
        # makes the same row tall in all versions and the columns can never drift apart.
        self.text = QTextEdit(); self.text.setReadOnly(True)
        layout.addWidget(self.text, 1)
        self.panels = []
        for _ in range(min(2, max(1, len(self.runs)))):
            self._new_panel()
        if len(self.runs) > 1:
            self.panels[1].choice.setCurrentIndex(1)
        for w in (self.highlight, self.only_diff):
            w.toggled.connect(self.refresh)
        self.min_changed.valueChanged.connect(self.refresh)
        self.refresh()
        from .window_state import remember; remember(self)   # v2.46.1: opens at the size and place you left it
        from .notices import attach; attach(self)   # v2.47: yellow note bar at the bottom

    # ---- panels
    def _label(self, r):
        return f"{r['model_name']} · {r['device']} · {r['compute_type']} · {r['created_at'][:16].replace('T', ' ')} · {r['id'][:8]}"

    def _new_panel(self):
        panel = _Panel(self, len(self.panels))
        for run in self.runs:
            panel.choice.addItem(self._label(run), run["id"])
        panel.choice.currentIndexChanged.connect(self.refresh)
        panel.delete_btn.clicked.connect(lambda _=False, p=panel: self.delete_version(p))
        panel.close_btn.clicked.connect(lambda _=False, p=panel: self.remove_panel(p))
        self.panels.append(panel); self.header_row.addWidget(panel, 1)
        self._update_buttons()
        return panel

    def add_panel(self):
        if len(self.panels) >= MAX_PANELS:
            return
        panel = self._new_panel()
        panel.choice.setCurrentIndex(min(len(self.panels) - 1, len(self.runs) - 1))
        self.refresh()

    def remove_panel(self, panel):
        if len(self.panels) <= 2:
            return
        self.panels.remove(panel); panel.setParent(None); panel.deleteLater()
        for i, p in enumerate(self.panels):
            p.title.setText("Reference" if i == 0 else f"Version {chr(65 + i)}")
        self._update_buttons(); self.refresh()

    def _update_buttons(self):
        self.add_btn.setEnabled(len(self.panels) < MAX_PANELS)
        for p in self.panels:
            p.close_btn.setVisible(len(self.panels) > 2)
            p.delete_btn.setEnabled(bool(self.database and len(self.runs) > 1))

    # ---- content
    def _run(self, run_id):
        return next((r for r in self.runs if r["id"] == run_id), self.runs[0])

    def _segments_for(self, run):
        if run["id"] not in self._segments:
            self._segments[run["id"]] = load_segments(run)
        return self._segments[run["id"]]

    @staticmethod
    def _html_line(row):
        label = dim_span(f"[{_clock(row['start'])}]") + " "
        if row["kind"] == "gap":
            return f"{label}{dim_span('—')}"
        style = STYLE_FOR_KIND.get(row["kind"], "")
        words = []
        for word, flagged in row["words"]:
            w = escape(word)
            words.append(f"<span style='{style}'>{w}</span>" if flagged and style else w)
        return f"{label}{' '.join(words)}"

    def refresh(self, *_):
        if not self.runs or not self.panels:
            return
        runs = [self._run(p.run_id()) for p in self.panels]
        segs = [self._segments_for(r) for r in runs]
        highlight, only, threshold = self.highlight.isChecked(), self.only_diff.isChecked(), self.min_changed.value()
        rows, differs = build_view(segs, threshold)
        keep_rows = [i for i, d in enumerate(differs) if d] if (only and highlight) else list(range(len(differs)))
        shown_rows = [[(rows[index][i] if highlight else dict(rows[index][i], kind="same")) for i in keep_rows]
                      for index in range(len(self.panels))]
        width = 100 // len(self.panels)
        bar = self.text.verticalScrollBar(); keep = bar.value()
        parts = ["<table width='100%' cellspacing='0' cellpadding='4' border='0'>"]
        for n in range(len(keep_rows)):
            parts.append("<tr>" + "".join(
                f"<td width='{width}%' valign='top'>{self._html_line(shown_rows[index][n])}</td>"
                for index in range(len(self.panels))) + "</tr>")
        parts.append("</table>")
        self.text.setHtml("".join(parts))
        bar.setValue(keep)
        count = sum(1 for d in differs if d)
        for index, panel in enumerate(self.panels):
            panel.props.setText("\n".join(describe_run(runs[index], segs[index])))
            panel.summary.setText(f"{len(segs[index]):,} segments" + (f" · {count:,} row(s) differ in this comparison" if highlight else ""))
        self._update_buttons()

    def export_report(self):
        run = self._run(self.panels[0].run_id())
        text, stats = edit_report(run)
        path, _ = QFileDialog.getSaveFileName(self, "Save edit report", "edit_report.md", "Markdown (*.md);;Text (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError as exc:
            QMessageBox.warning(self, "Could not save", str(exc)); return
        QMessageBox.information(self, "Edit report saved", f"Saved. {stats['reviewed']:,} reviewed segments analysed.")

    # ---- deleting
    def delete_version(self, panel):
        if len(self.runs) <= 1:
            QMessageBox.information(self, "Keep one transcript", "At least one transcript version must remain."); return
        run_id = panel.run_id()
        if not run_id: return
        newest_id = self.runs[0]["id"]
        if run_id == newest_id:
            QMessageBox.warning(self, "Current version protected", "The newest transcript version is protected here. Choose an older version to delete it."); return
        run = self._run(run_id)
        answer = QMessageBox.question(self, "Delete transcript version",
            f"Permanently delete this transcript version?\n\n{self._label(run)}\n\nThis does not delete the original recording or other transcript versions.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes: return
        self.database.delete_transcript_run(run_id)
        self.runs = [r for r in self.runs if r["id"] != run_id]
        self._segments.pop(run_id, None)
        for p in self.panels:
            keep = p.run_id()
            p.choice.blockSignals(True); p.choice.clear()
            for r in self.runs:
                p.choice.addItem(self._label(r), r["id"])
            idx = next((i for i, r in enumerate(self.runs) if r["id"] == keep), 0)
            p.choice.setCurrentIndex(idx); p.choice.blockSignals(False)
        self.refresh()
