from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QPushButton, QHBoxLayout, QDialogButtonBox, QHeaderView, QComboBox, QCheckBox,
    QInputDialog, QMessageBox)
from PySide6.QtCore import Qt

STATUSES = ["Unknown", "Encountered", "Partially discovered", "Revealed", "DM override"]

class SpoilerFactsDialog(QDialog):
    def __init__(self, db, campaign_id, parent=None):
        super().__init__(parent)
        self.db = db
        self.campaign_id = campaign_id
        self.setWindowTitle("Spoiler-aware campaign facts")
        self.resize(1000, 600)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Facts are tracked individually. Encountering an NPC does not reveal their hidden identity or backstory. "
            "Only facts explicitly marked Revealed or DM override can be approved for future Obsidian export. "
            "Published adventure text is never automatically added here."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Entity", "Fact", "Discovery status", "Approved for export", "Source / DM note"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setWordWrap(True)
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        add = QPushButton("Add fact"); delete = QPushButton("Delete selected")
        add.clicked.connect(self.add_fact); delete.clicked.connect(self.delete_fact)
        row.addWidget(add); row.addWidget(delete); row.addStretch()
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.load_rows()

    def load_rows(self):
        self.table.setRowCount(0)
        for fact in self.db.campaign_facts(self.campaign_id):
            r = self.table.rowCount(); self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(fact["entity_name"]))
            self.table.setItem(r, 1, QTableWidgetItem(fact["fact_text"]))
            status = QComboBox(); status.addItems(STATUSES); status.setCurrentText(fact["discovery_status"])
            self.table.setCellWidget(r, 2, status)
            approved = QCheckBox(); approved.setChecked(bool(fact["approved_for_export"]))
            approved.setEnabled(status.currentText() in {"Revealed", "DM override"})
            status.currentTextChanged.connect(lambda value, cb=approved: cb.setEnabled(value in {"Revealed", "DM override"}))
            self.table.setCellWidget(r, 3, approved)
            self.table.setItem(r, 4, QTableWidgetItem(fact["source_note"]))
            self.table.item(r, 0).setData(Qt.UserRole, fact["id"])

    def add_fact(self):
        entity, ok = QInputDialog.getText(self, "Add campaign fact", "Entity / subject:")
        if not ok or not entity.strip(): return
        fact, ok = QInputDialog.getMultiLineText(self, "Add campaign fact", "Fact (only what the party has actually discovered):")
        if not ok or not fact.strip(): return
        status, ok = QInputDialog.getItem(self, "Discovery status", "How much has been discovered?", STATUSES, 0, False)
        if not ok: return
        source, _ = QInputDialog.getText(self, "Source note", "Session / DM confirmation / source (optional):")
        self.db.save_campaign_fact(self.campaign_id, entity.strip(), fact.strip(), status, False, source.strip())
        self.load_rows()

    def delete_fact(self):
        r = self.table.currentRow()
        if r < 0: return
        item = self.table.item(r, 0)
        if item:
            fact_id = item.data(Qt.UserRole)
            if QMessageBox.question(self, "Delete fact", "Delete this fact?", QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
                self.db.delete_campaign_fact(fact_id)
                self.load_rows()

    def save(self):
        for r in range(self.table.rowCount()):
            entity = self.table.item(r, 0).text().strip() if self.table.item(r, 0) else ""
            fact_text = self.table.item(r, 1).text().strip() if self.table.item(r, 1) else ""
            source = self.table.item(r, 4).text().strip() if self.table.item(r, 4) else ""
            status = self.table.cellWidget(r, 2).currentText()
            approved = self.table.cellWidget(r, 3).isChecked() and status in {"Revealed", "DM override"}
            fact_id = self.table.item(r, 0).data(Qt.UserRole)
            self.db.update_campaign_fact(fact_id, entity, fact_text, status, approved, source)
        self.accept()
