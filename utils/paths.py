"""Project path constants."""

from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_DIR / "config"
DATA_DIR = PROJECT_DIR / "data"
LOG_DIR = PROJECT_DIR / "logs"
EXPORT_DIR = PROJECT_DIR / "exports"

