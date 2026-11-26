"""v2.47: View → Colours…: light / dark / Windows theme, the speaker colour scheme (shown side by side, to pick by eye)
and the symbol beside each speaker."""
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QRadioButton, QButtonGroup, QCheckBox,
                               QDialogButtonBox, QGroupBox, QApplication)

from . import theme


def swatches_html(scheme, symbols=True):
    cells = []
    for i, colour in enumerate(theme.speaker_palette(scheme)):
        mark = theme.SPEAKER_SYMBOLS[i] + " " if symbols else ""
        cells.append(f"<td style='background:{colour};color:{theme.contrast_text(colour)};padding:6px 10px;'>"
                     f"<b>{mark}Speaker {i:02d}</b></td>")
    return "<table cellspacing='3'><tr>" + "".join(cells[:4]) + "</tr><tr>" + "".join(cells[4:]) + "</tr></table>"


class ColoursDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Colours")
        layout = QVBoxLayout(self)

        row = QHBoxLayout(); row.addWidget(QLabel("Theme:"))
        self.theme_combo = QComboBox()
        for key, label in theme.APP_THEMES.items(): self.theme_combo.addItem(label, key)
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(config.get("app_theme", "system"))))
        self.theme_combo.setToolTip("Light, dark, or the same as your Windows setting. Changes as soon as you press OK.")
        row.addWidget(self.theme_combo); row.addStretch(); layout.addLayout(row)

        self.symbols = QCheckBox("Show a symbol beside each speaker (● ■ ▲ ◆ …), so colour is never the only clue")
        self.symbols.setChecked(bool(config.get("speaker_symbols", True)))
        layout.addWidget(self.symbols)

        box = QGroupBox("Speaker colours (pick the set where every speaker looks different to you)")
        box_layout = QVBoxLayout(box)
        self.group = QButtonGroup(self); self._previews = {}
        current = config.get("speaker_colours", "okabe_ito")
        for i, (key, (name, about, _colours)) in enumerate(theme.SPEAKER_SCHEMES.items()):
            radio = QRadioButton(name); radio.setChecked(key == current); radio.setProperty("scheme", key)
            self.group.addButton(radio, i); box_layout.addWidget(radio)
            note = theme.tone(QLabel(about), "dim"); note.setWordWrap(True); box_layout.addWidget(note)
            preview = QLabel(); self._previews[key] = preview; box_layout.addWidget(preview)
        layout.addWidget(box)
        self.symbols.toggled.connect(self._draw)
        self._draw()

        layout.addWidget(theme.tone(QLabel("Transcript editors already open show the new speaker colours after you close and open them again."), "dim"))
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _draw(self, *_):
        for key, preview in self._previews.items():
            preview.setText(swatches_html(key, self.symbols.isChecked()))

    def choices(self):
        checked = self.group.checkedButton()
        return {"app_theme": self.theme_combo.currentData(),
                "speaker_colours": checked.property("scheme") if checked else "okabe_ito",
                "speaker_symbols": self.symbols.isChecked()}

    def accept(self):
        self.config.update(self.choices())
        theme.configure(self.config)
        theme.apply_app_theme(QApplication.instance(), self.config["app_theme"])
        try:
            from .config import save_config
            save_config(self.config)
        except Exception:
            pass   # the choice still applies until the program closes
        super().accept()
