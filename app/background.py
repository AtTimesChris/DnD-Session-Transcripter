from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtWidgets import QProgressDialog


def run_blocking_in_thread(fn, *args, parent=None, label="Working…"):
    """Run a slow call (network lookup, PDF parse) off the GUI thread.

    The window keeps repainting while a modal busy dialog is shown. Returns the
    function's result or re-raises its exception.
    shortcut: this is a nested event loop; the modal dialog blocks other clicks,
    so re-entrancy is not a concern. Move to a QThread worker if it needs a
    cancel button or progress percentages.
    """
    dialog = QProgressDialog(label, "", 0, 0, parent)
    dialog.setWindowModality(Qt.WindowModal)
    dialog.setCancelButton(None)
    dialog.setMinimumDuration(300)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn, *args)
        loop = QEventLoop()
        timer = QTimer()
        timer.setInterval(50)
        timer.timeout.connect(lambda: loop.quit() if future.done() else None)
        timer.start()
        if not future.done():
            loop.exec()
        timer.stop()
        dialog.close()
        return future.result()
