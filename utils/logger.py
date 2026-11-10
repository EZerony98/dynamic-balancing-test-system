"""Application logging configuration."""

import logging
from pathlib import Path


def configure_logging(log_directory: Path) -> None:
    log_directory.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(log_directory / "app.log"),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
