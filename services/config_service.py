"""JSON configuration loading."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict


class ConfigService:
    CONFIG_NAMES = ("app", "devices", "process", "server")

    def __init__(self, config_dir: Path) -> None:
        self.config_dir = Path(config_dir)

    def load(self, name: str) -> Dict[str, Any]:
        if name not in self.CONFIG_NAMES:
            raise KeyError("Unknown configuration: %s" % name)
        path = self.config_dir / (name + ".json")
        with path.open("r", encoding="utf-8") as config_file:
            data = json.load(config_file)
        if not isinstance(data, dict):
            raise ValueError("Configuration must contain a JSON object: %s" % path)
        return data

    def load_all(self) -> Dict[str, Dict[str, Any]]:
        return {name: self.load(name) for name in self.CONFIG_NAMES}

    def save_section(self, name: str, section: str, values: dict) -> None:
        """Atomically replace one section, preserving unrelated live settings."""
        config = self.load(name)
        config[section] = values
        path = self.config_dir / (name + ".json")
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=str(path.parent),
                                             prefix=path.name + ".", delete=False) as handle:
                temporary_path = handle.name
                json.dump(config, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None and os.path.exists(temporary_path):
                os.unlink(temporary_path)
