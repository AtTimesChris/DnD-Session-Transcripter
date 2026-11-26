"""Alternatives window (v2.45): the selected stretch re-transcribed several ways (careful search, with your names,
looser, and v2.46: short overlapping pieces), next to what the transcript says now. Versions with the same words are
shown once. Picking one opens the usual compare window, where you choose line by line what to use; Reviewed lines are
never changed.

v2.46: the window stays open beside the editor (its playback controls and lines can be used), words that differ from
the transcript are highlighted, lines outside your selection (Whisper hears 2 s either side) are grey, every line has
its own ▶, and "5 s more" plays on past the end."""
import difflib
import html
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox, QTextBrowser, QRadioButton,
                               QPushButton, QDialogButtonBox, QButtonGroup, QScrollArea, QWidget, QPlainTextEdit)
from .theme import tone, DIM, dim_span   # v2.46.1: shared colours

_TOKEN = re.compile(r"\S+")


def version_text(segments, fmt_time):
    return "\n".join(f"{fmt_time(float(s['start']))[:8]}  {s.get('text', '').strip()}" for s in segments if s.get("text", "").strip())


def _norm(word):
    return re.sub(r"[^a-z0-9']", "", word.casefold())


def changed_words(current_text, segments):
    """For each segment, the set of word positions that differ from the current transcript (v2.46)."""
    now = [_norm(w) for w in _TOKEN.findall(current_text) if _norm(w)]
    tokens = []   # (segment index, word index in segment, normalised)
    for i, s in enumerate(segments):
        for j, w in enumerate(_TOKEN.findall(s.get("text", "") or "")):
            if _norm(w):
                tokens.append((i, j, _norm(w)))
    sm = difflib.SequenceMatcher(None, now, [t[2] for t in tokens], autojunk=False)
    out = {i: set() for i in range(len(segments))}
    for tag, _a1, _a2, b1, b2 in sm.get_opcodes():
        if tag in ("replace", "insert"):
            for k in range(b1, b2):
                out[tokens[k][0]].add(tokens[k][1])
    return out


def version_html(segments, fmt_time, start, end, current_text=""):
    """HTML lines: ▶ link, time, text; words not in the transcript highlighted; lines outside start..end grey."""
    diff = changed_words(current_text, segments) if current_text else {}
    lines = []
    for i, s in enumerate(segments):
        text = (s.get("text") or "").strip()
        if not text:
            continue
        a, b = float(s["start"]), float(s["end"])
        outside = b <= start + 0.05 or a >= end - 0.05
        words = []
        for j, w in enumerate(_TOKEN.findall(text)):
            w_html = html.escape(w)
            if j in diff.get(i, ()) and not outside:
                w_html = f"<span style='background:#6b5900;color:#fff7d1;font-weight:600'>{w_html}</span>"
            words.append(w_html)
        style = f"color:{DIM}" if outside else ""
        lines.append(f"<div style='{style}'><a href='play:{a:.2f}:{b:.2f}' style='text-decoration:none'>▶</a> "
                     f"{dim_span(fmt_time(a)[:8])}&nbsp; {' '.join(words)}"
                     + (" <i>(outside your selection)</i>" if outside else "") + "</div>")
    return "".join(lines) or "<i>(no speech found)</i>"


class AlternativesDialog(QDialog):
    def __init__(self, current, versions, start, end, fmt_time, play=None, pause=None, settings_text="", parent=None, padding=2.0):
        super().__init__(parent)
        self.versions, self.start, self.end, self.play, self.pause = versions, start, end, play, pause
        self.padding = padding
        self._play_end = end
        self.setWindowTitle(f"Alternatives · {fmt_time(start)[:8]}–{fmt_time(end)[:8]}")
        self.resize(1150, 800)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        layout = QVBoxLayout(self)
        intro = QLabel("The same stretch transcribed several ways. Highlighted words differ from the transcript; grey lines are "
                       f"outside your selection (Whisper hears {padding:.0f} s either side). ▶ plays one line. Choose the version "
                       "that heard it best: the compare window opens next so you can pick line by line what to use (Reviewed "
                       "lines are never changed). This window can stay open while you use the editor. " + settings_text)
        intro.setWordWrap(True); layout.addWidget(intro)
        row = QHBoxLayout()
        play_btn = QPushButton("▶ Play the stretch"); play_btn.clicked.connect(self._play_all)
        play_btn.setToolTip(f"Plays your selection with {padding:.0f} s either side (what Whisper heard).")
        more_btn = QPushButton("▶ 5 s more"); more_btn.clicked.connect(self._play_more)
        more_btn.setToolTip("Plays the 5 seconds after the last thing played (press again for 5 more).")
        pause_btn = QPushButton("⏯ Pause / resume"); pause_btn.clicked.connect(lambda: self.pause and self.pause())
        for w in (play_btn, more_btn, pause_btn):
            row.addWidget(w)
        row.addStretch(); layout.addLayout(row)
        inner = QWidget(); il = QVBoxLayout(inner)
        current_text = " ".join(s.get("text", "") for s in current if float(s["end"]) > start and float(s["start"]) < end)
        now = QGroupBox("Now in the transcript")
        nl = QVBoxLayout(now)
        box = self._browser(version_html(current, fmt_time, start, end))
        box.setMaximumHeight(150); nl.addWidget(box); il.addWidget(now)
        self.group = QButtonGroup(self)
        for i, v in enumerate(versions):
            g = QGroupBox(v["label"] + (f" — same words as {v['same_as']}" if v.get("same_as") else ""))
            gl = QVBoxLayout(g)
            about = QLabel(v.get("about", "")); about.setWordWrap(True); tone(about,"dim"); gl.addWidget(about)
            if v.get("copy_only"):
                # short pieces overlap, so they cannot replace lines: shown as text to copy into a line
                text = QPlainTextEdit(version_text(v["segments"], fmt_time) or "(no speech found)"); text.setReadOnly(True)
                text.setMaximumHeight(170); gl.addWidget(text)
            elif not v.get("same_as"):
                b = self._browser(version_html(v["segments"], fmt_time, start, end, current_text))
                b.setMaximumHeight(190); gl.addWidget(b)
                pick = QRadioButton("Use this version"); self.group.addButton(pick, i); gl.addWidget(pick)
                pick.setEnabled(bool(v["segments"]))
            il.addWidget(g)
        il.addStretch()
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(inner); layout.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = buttons.button(QDialogButtonBox.Ok); self.ok.setText("Compare with the transcript…"); self.ok.setEnabled(False)
        buttons.button(QDialogButtonBox.Cancel).setText("Close")
        self.group.idToggled.connect(lambda *_: self.ok.setEnabled(self.group.checkedId() >= 0))
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        from .window_state import remember; remember(self)   # v2.46.1: opens at the size and place you left it
        from .notices import attach; attach(self)   # v2.47: yellow note bar at the bottom

    def _browser(self, html_text):
        b = QTextBrowser(); b.setOpenLinks(False); b.setHtml(html_text)
        b.anchorClicked.connect(self._link)
        return b

    def _link(self, url):
        parts = url.toString().split(":")
        if len(parts) == 3 and parts[0] == "play" and self.play:
            a, b = float(parts[1]), float(parts[2])
            self._play_end = b
            self.play(a, b)

    def _play_all(self):
        if self.play:
            a, b = max(0.0, self.start - self.padding), self.end + self.padding
            self._play_end = b
            self.play(a, b)

    def _play_more(self):
        if self.play:
            a = self._play_end
            self._play_end = a + 5
            self.play(a, a + 5)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            event.accept(); return
        super().keyPressEvent(event)

    def chosen(self):
        i = self.group.checkedId()
        return self.versions[i] if 0 <= i < len(self.versions) else None
