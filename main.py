"""Dynamic Balancing Test application entry point."""

import sys
from typing import Optional, Sequence

from PySide6.QtWidgets import QApplication

from services.config_service import ConfigService
from ui.main_window import MainWindow
from utils.logger import configure_logging
from utils.paths import CONFIG_DIR, LOG_DIR, PROJECT_DIR


CONFIG = ConfigService(CONFIG_DIR)


def create_application(argv: Optional[Sequence[str]] = None) -> QApplication:
    application = QApplication(list(argv) if argv is not None else sys.argv)
    app_config = CONFIG.load("app")["application"]
    application.setApplicationName(app_config["name"])
    application.setOrganizationName("Boreasa")
    return application


def main(argv: Optional[Sequence[str]] = None) -> int:
    configure_logging(LOG_DIR)
    application = create_application(argv)
    window = MainWindow(CONFIG.load_all(), PROJECT_DIR)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
