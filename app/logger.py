"""Unified application logging for Local SessionKeeper."""
import logging
import os
import sys
import warnings
from pathlib import Path

_LOG_PATH = None

class _TeeStream:
    """Mirror ordinary Python stdout/stderr to the unified log and console."""
    def __init__(self, original, path):
        self.original = original
        self.path = Path(path)
    def write(self, value):
        if value:
            self.original.write(value)
            try:
                with self.path.open("a", encoding="utf-8", errors="replace") as handle:
                    handle.write(value)
            except Exception:
                pass
        return len(value)
    def flush(self):
        self.original.flush()
    def isatty(self):
        return getattr(self.original, "isatty", lambda: False)()
    @property
    def encoding(self):
        return getattr(self.original, "encoding", "utf-8")


def configure_logging(log_dir: str) -> logging.Logger:
    global _LOG_PATH
    folder = Path(log_dir)
    folder.mkdir(parents=True, exist_ok=True)
    _LOG_PATH = folder / "LocalSessionKeeper.log"
    logger = logging.getLogger("localsessionkeeper")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not getattr(logger, "_lsk_configured", False):
        formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
        # Started through RUN.bat (run_logged.py): the launcher already copies all console output
        # into this same file, so writing it here as well doubled every line.
        mirrored = os.environ.get("LSK_MIRRORED_LOG", "")
        try:
            mirrored = bool(mirrored) and Path(mirrored).resolve() == _LOG_PATH.resolve()
        except OSError:
            mirrored = False
        console = logging.StreamHandler(sys.__stderr__)
        console.setFormatter(formatter)
        if not mirrored:
            file_handler = logging.FileHandler(_LOG_PATH, encoding="utf-8")
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        logger.addHandler(console)
        logger._lsk_configured = True
        # Route Python warnings through the same timestamped logger.
        logging.captureWarnings(True)
        warning_logger = logging.getLogger("py.warnings")
        warning_logger.handlers = list(logger.handlers)
        warning_logger.setLevel(logging.WARNING)
        warning_logger.propagate = False
        warnings.simplefilter("default")
        if not mirrored:
            sys.stdout = _TeeStream(sys.__stdout__, _LOG_PATH)
            # Keep logger output on the original stderr to avoid writing it twice.
            sys.stderr = _TeeStream(sys.__stderr__, _LOG_PATH)
    return logger


def qt_message_handler(mode, context, message):
    """Capture Qt multimedia/platform diagnostics in the same application log."""
    logger = logging.getLogger("localsessionkeeper")
    try: mode_value = int(mode)
    except Exception: mode_value = 0
    level = {0: logging.INFO, 1: logging.WARNING, 2: logging.ERROR, 3: logging.CRITICAL, 4: logging.INFO}.get(mode_value, logging.INFO)
    logger.log(level, "Qt: %s", message)
