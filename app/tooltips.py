"""Word-wrapped, slightly delayed tooltips.

Qt only wraps tooltips that are rich text, so plain-text tooltips become one very long line.
This filter turns them into wrapped ones, makes table-cell tooltips wrap too, shows them a little
later than Qt does, and shows the full text only for editable segment text boxes whose text is cut off
(capped in length). Read-only text views (such as the Compare window) never get a text tooltip.
"""
import re
from html import escape

from PySide6.QtCore import Qt, QObject, QEvent, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QToolTip, QWidget, QPlainTextEdit, QAbstractItemView

EXTRA_DELAY_MS = 600     # added to Qt's own delay (about 0.7 s)
MAX_TOOLTIP_CHARS = 700  # never build a giant tooltip

_RICH = re.compile(r"<[a-zA-Z/!][^>]*>")


def might_be_rich(text: str) -> bool:
    return bool(_RICH.search(text))


def wrapped(text: str) -> str:
    if len(text) > MAX_TOOLTIP_CHARS:
        text = text[:MAX_TOOLTIP_CHARS].rstrip() + " …"
    return "<div style='white-space:pre-wrap;'>" + escape(text) + "</div>"


class WrappedToolTips(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self); self._timer.setSingleShot(True); self._timer.setInterval(EXTRA_DELAY_MS)
        self._timer.timeout.connect(self._show_pending)
        self._pending = None

    def eventFilter(self, obj, event):
        # Never let a tooltip problem raise an error: fall back to Qt's normal tooltip.
        try:
            return self._filter(obj, event)
        except Exception:
            return False

    def _filter(self, obj, event):
        kind = event.type()
        if kind in (QEvent.Leave, QEvent.MouseButtonPress, QEvent.Wheel, QEvent.KeyPress):
            self._timer.stop(); self._pending = None
            return False
        if kind != QEvent.ToolTip or not isinstance(obj, QWidget):
            return False
        text = obj.toolTip()
        parent = obj.parent()
        if not text and isinstance(parent, QAbstractItemView) and parent.viewport() is obj:
            index = parent.indexAt(event.pos())
            if index.isValid():
                text = index.data(Qt.ToolTipRole) or ""
        if not text and isinstance(parent, QPlainTextEdit) and parent.viewport() is obj and not parent.isReadOnly():
            full = parent.toPlainText()
            if full.strip() and parent.document().size().height() > obj.height() + 2:
                text = full  # the segment text is cut off: show (the start of) all of it
        if not text or might_be_rich(text):
            return False
        pos = event.globalPos() if hasattr(event, "globalPos") else event.globalPosition().toPoint()
        self._pending = (obj, pos, text)
        self._timer.start()
        return True

    def _show_pending(self):
        if not self._pending:
            return
        obj, pos, text = self._pending
        self._pending = None
        under = QApplication.widgetAt(QCursor.pos())
        if under is None or not (under is obj or obj.isAncestorOf(under) or under.isAncestorOf(obj)):
            return  # the pointer moved away while we waited
        QToolTip.showText(pos, wrapped(text), obj)

