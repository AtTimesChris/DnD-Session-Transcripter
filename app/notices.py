"""v2.47: short notes ("Select a recording first", "Nothing found") in a yellow bar at the bottom of every open window.

Each working window gets a bar with attach(window). notify(text, origin) shows the note in every open window's bar,
so you see it in whichever window you are working in. In the other windows it starts with the name of the window it
came from ("Editor: Select a segment first."). It stays for 12 seconds; click a bar to hide it sooner.
Nothing runs between notes: no timers or checks while you work.
"""
import weakref

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QMainWindow

from .theme import tone

SHOW_MS = 12000
_windows = weakref.WeakSet()


def _name(window):
    name = getattr(window, "notice_name", "")
    if name: return name
    title = window.windowTitle()
    return title.split(" · ")[0] if title else type(window).__name__


def attach(window):
    """Add a hidden notice bar at the bottom of this window. Safe to call twice."""
    if getattr(window, "_notice_bar", None) is not None: return window._notice_bar
    layout = window.centralWidget().layout() if isinstance(window, QMainWindow) and window.centralWidget() else window.layout()
    if layout is None: return None
    bar = tone(QLabel(""), "notice"); bar.setWordWrap(True); bar.hide()
    bar.setToolTip("Click to hide"); bar.mousePressEvent = lambda _e: bar.hide()
    timer = QTimer(bar); timer.setSingleShot(True); timer.timeout.connect(bar.hide)
    bar._timer = timer
    layout.addWidget(bar)
    window._notice_bar = bar
    _windows.add(window)
    return bar


def bar_of(window):
    return getattr(window, "_notice_bar", None)


def notify(text, origin=None, ms=SHOW_MS):
    """Show text in every open window's bar (prefixed with the origin's name in the others)."""
    if origin is not None and bar_of(origin) is None: attach(origin)
    for window in list(_windows):
        bar = bar_of(window)
        if bar is None: continue
        try:
            if window is not origin and not window.isVisible(): continue
            bar.setText(text if window is origin or origin is None else f"{_name(origin)}: {text}")
            bar.show(); bar._timer.start(ms)
        except RuntimeError:   # the window was closed and Qt already deleted it
            _windows.discard(window)
