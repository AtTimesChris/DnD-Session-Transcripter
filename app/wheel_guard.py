"""v2.46: the mouse wheel never changes a dropdown list (or a number box) by itself.

Qt changes a list's choice when the wheel turns over it, so scrolling down the roll finder changed Pass / fail, Who or
Used on whatever row passed under the pointer. Now the wheel over a closed list scrolls the table or window it is in
instead. Click a list to open it: the wheel and the arrow keys then move through its choices as usual."""
from PySide6.QtCore import QObject, QEvent
from PySide6.QtWidgets import QComboBox, QAbstractSpinBox, QAbstractScrollArea, QApplication


class WheelGuard(QObject):
    def eventFilter(self, obj, event):
        if event.type() != QEvent.Wheel:
            return False
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
    app._wheel_guard = guard   # keep it alive
    return guard
