import sys
from PySide6.QtWidgets import QApplication, QMessageBox
from .config import load_config, ensure_directories, APP_NAME, APP_VERSION
from .logger import configure_logging, qt_message_handler
from PySide6.QtCore import qInstallMessageHandler
from .database import Database
from .main_window import MainWindow

def main():
    config = load_config()
    ensure_directories(config)
    logger = configure_logging(config["logs_dir"])
    logger.info("Starting %s v%s", APP_NAME, APP_VERSION)
    def log_uncaught(exc_type, exc_value, exc_traceback):
        logger.critical("Uncaught application exception", exc_info=(exc_type, exc_value, exc_traceback))
    sys.excepthook = log_uncaught
    qInstallMessageHandler(qt_message_handler)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    from .wheel_guard import install as install_wheel_guard
    install_wheel_guard(app)   # v2.46: the wheel never changes a closed dropdown list
    from . import theme
    theme.configure(config); theme.apply_app_theme(app, config.get("app_theme", "system"))   # v2.47: View → Colours…
    from .window_state import configure as configure_window_state
    configure_window_state(f"{config['data_dir']}/window_state.ini")   # v2.46.1: windows open where you left them
    db = Database(f"{config['data_dir']}/localsessionkeeper.sqlite3")
    window = MainWindow(db, config, logger)
    window.show()

    def shutdown():
        try:
            db.checkpoint()
            db.close()
            logger.info("Application shutdown complete")
        except Exception:
            logger.exception("Shutdown error")

    app.aboutToQuit.connect(shutdown)
    code = app.exec()
    sys.exit(code)

if __name__ == "__main__":
    main()
