"""v2.46: the mouse wheel never changes a dropdown list (or a number box) by itself.

Qt changes a list's choice when the wheel turns over it, so scrolling down the roll finder changed Pass / fail, Who or
Used on whatever row passed under the pointer. Now the wheel over a closed list scrolls the table or window it is in
instead. Click a list to open it: the wheel and the arrow keys then move the marked choice (v2.50: the wheel used to scroll the
list without moving it), and Enter or a click picks it."""
from PySide6.QtCore import QObject, QEvent, Qt
from PySide6.QtWidgets import QComboBox, QAbstractSpinBox, QAbstractScrollArea, QApplication, QAbstractItemView

# v2.50: the choice under the pointer or the arrow keys in an open list is clearly marked
POPUP_STYLE = ("QComboBox QAbstractItemView { selection-background-color: #2f81f7; selection-color: #ffffff; "
               "outline: 0; }")


def _open_list(obj):
    """The list of an open dropdown, when obj is that list or its viewport, else None."""
    view = obj if isinstance(obj, QAbstractItemView) else (obj.parent() if isinstance(obj.parent(), QAbstractItemView) else None)
    if view is None:
        return None
    win = view.window()
    return view if win is not None and win.inherits("QComboBoxPrivateContainer") else None


class WheelGuard(QObject):
    def eventFilter(self, obj, event):
        if event.type() != QEvent.Wheel:
            return False
        view = _open_list(obj)
        if view is not None:   # v2.50: in an open list the wheel moves the marked choice, one step per notch
            model = view.model()
            step = -1 if event.angleDelta().y() > 0 else 1
            row = view.currentIndex().row() + step
            while 0 <= row < model.rowCount() and not (model.flags(model.index(row, 0)) & Qt.ItemIsEnabled):
                row += step
            if 0 <= row < model.rowCount():
                view.setCurrentIndex(model.index(row, 0))
                view.scrollTo(model.index(row, 0))
            return True
        if isinstance(obj, QComboBox):
            view = obj.view()
            if view is not None and view.isVisible():
                return False   # the list is open: scroll through it
        elif not isinstance(obj, QAbstractSpinBox) or obj.hasFocus():
            return False       # a number box you clicked into still takes the wheel
        target = obj.parentWidget()
        while target is not None and not isinstance(target, QAbstractScrollArea):
            target = target.parentWidget()
        if target is not None:
            QApplication.sendEvent(target.viewport(), event)
        return True


def install(app):
    guard = WheelGuard(app)
    app.installEventFilter(guard)
    try:
        app.setStyleSheet((app.styleSheet() or "") + "\n" + POPUP_STYLE)
    except Exception:
        pass
    app._wheel_guard = guard   # keep it alive
    return guard
