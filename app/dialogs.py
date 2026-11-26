from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLineEdit, QTextEdit, QDialogButtonBox,
    QMessageBox
)

class CampaignDialog(QDialog):
    def __init__(self, parent=None, campaign=None):
        super().__init__(parent)
        self.setWindowTitle("Campaign details")
        self.name = QLineEdit()
        self.system = QLineEdit()
        self.description = QTextEdit()
        self.description.setMaximumHeight(110)
        if campaign:
            self.name.setText(campaign["name"])
            self.system.setText(campaign["game_system"] or "")
            self.description.setPlainText(campaign["description"] or "")
        form = QFormLayout()
        form.addRow("Campaign name", self.name)
        form.addRow("Game system", self.system)
        form.addRow("Description", self.description)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return self.name.text().strip(), self.system.text().strip(), self.description.toPlainText().strip()

    def accept(self):
        if not self.name.text().strip():
            QMessageBox.warning(self, "Missing name", "Enter a campaign name.")
            return
        super().accept()
