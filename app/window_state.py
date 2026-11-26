"""v2.46.1: windows open at the size and place you left them, with their dividers where you dragged them.

Each working window (main window, editor, roll finder, Correct names and the others) calls remember(self) at the end
of building itself: its saved size, position and divider positions are put back before it is shown, and saved again
when it is hidden or closed. Only those windows are watched (no filter on the whole program), so nothing else slows
down. Message boxes and file pickers are left alone.

Stored in data\\window_state.ini (not config.json). Delete that file to go back to the default sizes.
A saved place that is no longer on any screen (a monitor was unplugged) is ignored by Qt, so a window never opens
off screen. Without configure() (tests, scripts) remember() does nothing.
"""
from PySide6.QtCore import QObject, QEvent, QSettings
from PySide6.QtWidgets import QSplitter

_settings = None


def configure(path):
    global _settings
    _settings = QSettings(str(path), QSettings.IniFormat)


def _key(window, key):
    return key or type(window).__name__


def _splitters(window):
    """Splitters inside this window (not inside another window opened from it), in a stable order."""
    return [s for s in window.findChildren(QSplitter) if s.window() is window]


def restore(window, key=None):
    if _settings is None: return
    key = _key(window, key)
    try:
        geometry = _settings.value(f"{key}/geometry")
        if geometry is not None:
            window.restoreGeometry(geometry)
        splitters = _splitters(window)
        if int(_settings.value(f"{key}/splitter_count", -1)) == len(splitters):   # layout unchanged since it was saved
            for i, splitter in enumerate(splitters):
                state = _settings.value(f"{key}/splitter{i}")
                if state is not None: splitter.restoreState(state)
    except Exception:
        pass   # a size that cannot be put back just means the default size


def save(window, key=None):
    if _settings is None: return
    key = _key(window, key)
    try:
        _settings.setValue(f"{key}/geometry", window.saveGeometry())
        splitters = _splitters(window)
        _settings.setValue(f"{key}/splitter_count", len(splitters))
        for i, splitter in enumerate(splitters):
            _settings.setValue(f"{key}/splitter{i}", splitter.saveState())
        _settings.sync()
    except Exception:
        pass


def reset():
    """v2.47 (View → Reset window sizes): forget every saved size, and save none until the program is started again,
    so the windows that are open now do not write their sizes back when they close."""
    global _settings
    if _settings is None: return
    _settings.clear(); _settings.sync()
    _settings = None


class _Keeper(QObject):
    def __init__(self, window, key):
        super().__init__(window)
        self.key = key

    def eventFilter(self, obj, event):
        # Minimising sends a "spontaneous" hide: not saved, so the normal size is kept.
        if event.type() == QEvent.Hide and not event.spontaneous():
            save(obj, self.key)
        return False


def remember(window, key=None):
    """Put back this window's saved size and dividers now, and save them whenever it is hidden or closed."""
    if _settings is None: return
    restore(window, key)
    window.installEventFilter(_Keeper(window, key))
