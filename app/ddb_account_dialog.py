"""Connect / test / disconnect the optional D&D Beyond account (v2.33)."""
import datetime

from PySide6.QtCore import Qt

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QDialogButtonBox,
                               QMessageBox)

from . import ddb_auth
from .theme import tone   # v2.46.1: shared colours

INSTRUCTIONS = (
    "<b>How to get the value</b> (it is not your password):<ol>"
    "<li>In your normal browser, sign in to <b>dndbeyond.com</b>.</li>"
    "<li>Press <b>F12</b> to open the developer tools.</li>"
    "<li><b>Firefox:</b> the <b>Storage</b> tab → <b>Cookies</b> → <i>https://www.dndbeyond.com</i>.<br>"
    "<b>Chrome / Edge:</b> the <b>Application</b> tab → <b>Cookies</b> → <i>https://www.dndbeyond.com</i>.</li>"
    "<li>Find the row named <b>CobaltSession</b>, double-click its <b>Value</b>, copy it (Ctrl+C) and paste it below.</li></ol>"
    "Treat it like a password: anyone with it can use your D&amp;D Beyond account until you log out there.")

SAFETY = ("It is saved only on this PC, in your Windows profile (not in the program folder, config.json, the database or "
          "the logs), encrypted so only your Windows user can read it. A copy of the program, for example from Git, never "
          "includes it. Disconnect deletes it; logging out of D&D Beyond in your browser also ends it. This uses an "
          "unofficial route (D&D Beyond has no public API), so it may stop working if they change their site.")


class DdbAccountDialog(QDialog):
    def __init__(self, parent=None, run_in_thread=None):
        super().__init__(parent)
        self.run_in_thread = run_in_thread or (lambda fn, *a, **k: fn(*a))
        self.setWindowTitle("D&D Beyond account")
        self.resize(640, 520)
        layout = QVBoxLayout(self)
        self.status = QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)
        intro = QLabel("Connecting your account lets the program read characters your account can see on D&D Beyond, "
                       "including other players' campaign-only characters in your campaigns. No password is asked for.")
        intro.setWordWrap(True); layout.addWidget(intro)
        how = QLabel(INSTRUCTIONS); how.setWordWrap(True); how.setTextFormat(Qt.RichText); how.setOpenExternalLinks(False); layout.addWidget(how)
        row = QHBoxLayout()
        self.value = QLineEdit(); self.value.setEchoMode(QLineEdit.Password)
        self.value.setPlaceholderText("Paste the CobaltSession value here")
        self.connect_btn = QPushButton("Connect and test"); self.connect_btn.clicked.connect(self._connect)
        row.addWidget(self.value, 1); row.addWidget(self.connect_btn); layout.addLayout(row)
        safety = QLabel(SAFETY); safety.setWordWrap(True); tone(safety,"dim"); layout.addWidget(safety)
        actions = QHBoxLayout()
        self.test_btn = QPushButton("Test connection"); self.test_btn.clicked.connect(self._test)
        self.disconnect_btn = QPushButton("Disconnect (delete saved session)"); self.disconnect_btn.clicked.connect(self._disconnect)
        actions.addWidget(self.test_btn); actions.addWidget(self.disconnect_btn); actions.addStretch(); layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.Close); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self._refresh()

    def _refresh(self):
        connected = ddb_auth.has_session()
        if connected:
            stamp = datetime.datetime.fromtimestamp(ddb_auth.session_path().stat().st_mtime).strftime("%d %b %Y %H:%M")
            self.status.setText(f"<b>Connected</b> · saved {stamp}")
        else:
            self.status.setText("<b>Not connected</b> · only characters set to Public can be read")
        self.test_btn.setEnabled(connected); self.disconnect_btn.setEnabled(connected)

    def _connect(self):
        try:
            value = ddb_auth.clean_cookie(self.value.text())
        except ValueError as exc:
            QMessageBox.warning(self, "D&D Beyond account", str(exc)); return
        try:
            self.run_in_thread(ddb_auth.token_from_cookie, value, parent=self, label="Checking with D&D Beyond…")
        except Exception as exc:
            QMessageBox.warning(self, "D&D Beyond account", f"D&D Beyond did not accept it, so nothing was saved.\n\n{exc}"); return
        try:
            ddb_auth.save_session(value)
        except Exception as exc:
            QMessageBox.warning(self, "D&D Beyond account", f"It works, but it could not be saved:\n{exc}"); return
        finally:
            self.value.clear()
        self._refresh()
        QMessageBox.information(self, "D&D Beyond account", "Connected. D&D Beyond accepted the session and it is saved, encrypted, "
                                "for your Windows user only.")

    def _test(self):
        try:
            self.run_in_thread(ddb_auth.account_token, parent=self, label="Checking with D&D Beyond…")
        except Exception as exc:
            QMessageBox.warning(self, "D&D Beyond account", f"The connection does not work any more:\n\n{exc}"); return
        QMessageBox.information(self, "D&D Beyond account", "The connection works.")

    def _disconnect(self):
        if QMessageBox.question(self, "D&D Beyond account", "Delete the saved D&D Beyond session from this PC?",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        ddb_auth.clear_session()
        self._refresh()
        QMessageBox.information(self, "D&D Beyond account", "Disconnected: the saved session was deleted. "
                                "To end it everywhere, also log out of D&D Beyond in your browser.")
