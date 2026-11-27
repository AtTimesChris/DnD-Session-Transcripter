from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLineEdit, QTextEdit, QDialogButtonBox,
    QMessageBox, QComboBox, QLabel
)

from .rules_edition import GAME_SYSTEMS, campaign_edition


def _edition_text(system):
    ed = campaign_edition(system)
    return {"2024": "Read as the 2024 rules (5.5e): the 2024 core books are its Rules books.",
            "2014": "Read as the 2014 rules (5e): the 2014 core books are its Rules books.",
            "mixed-2024": "Read as both editions, 2024 first: both sets of core books are Rules books; the 2024 ones win ties and spell dice.",
            "mixed-2014": "Read as both editions, 2014 first: both sets of core books are Rules books; the 2014 ones win ties and spell dice.",
            }.get(ed, "No D&D edition recognised: every core book counts as a Rules book.")


class CampaignDialog(QDialog):
    def __init__(self, parent=None, campaign=None):
        super().__init__(parent)
        self.setWindowTitle("Campaign details")
        self.name = QLineEdit()
        # v2.47: a list of the usual systems that you can still type into. 5.5e, 5.5th Edition, One D&D and D&D 2024 all
        # mean the 2024 rules; it decides which core books are this campaign's Rules books (D&D books…)
        self.system = QComboBox(); self.system.setEditable(True)
        self.system.addItems(GAME_SYSTEMS)
        self.system.setCurrentText("")
        self.system.setToolTip("Pick one or type your own. The D&D edition decides which core rulebooks are this campaign's Rules "
                               "books for Correct names and spell dice (5.5e / One D&D / D&D 2024 = the 2024 rules).")
        self.edition = QLabel(); self.edition.setWordWrap(True); self.edition.setStyleSheet("color:#8b949e;")
        self.system.currentTextChanged.connect(lambda t: self.edition.setText(_edition_text(t)))
        self.description = QTextEdit()
        self.description.setMaximumHeight(110)
        if campaign:
            self.name.setText(campaign["name"])
            self.system.setCurrentText(campaign["game_system"] or "")
            self.description.setPlainText(campaign["description"] or "")
        self.edition.setText(_edition_text(self.system.currentText()))
        form = QFormLayout()
        form.addRow("Campaign name", self.name)
        form.addRow("Game system", self.system)
        form.addRow("", self.edition)
        form.addRow("Description", self.description)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        system = self.system.currentText().strip()
        return self.name.text().strip(), ("" if system == "Other" else system), self.description.toPlainText().strip()

    def accept(self):
        if not self.name.text().strip():
            QMessageBox.warning(self, "Missing name", "Enter a campaign name.")
            return
        super().accept()
