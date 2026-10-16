"""Resolve workspace locations without creating files or opening databases."""
from __future__ import annotations
import json
import os
from pathlib import Path


def default_data_dir() -> str:
    """CLI argument > environment > explicit JSON config > external default."""
    configured = os.environ.get("SEARCHAGENT_LAB_DATA_DIR")
    if configured:
        return str(Path(configured).expanduser().resolve())
    config_file = os.environ.get("SEARCHAGENT_LAB_CONFIG")
    if config_file:
        path = Path(config_file).expanduser().resolve()
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
            value = config["data_dir"]
            if not isinstance(value, str) or not value.strip():
                raise ValueError("data_dir must be a non-empty string")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"Invalid SearchAgent Lab config {path}: {exc}") from exc
        directory = Path(value).expanduser()
        return str((directory if directory.is_absolute() else path.parent / directory).resolve())
    project = Path(__file__).resolve().parents[1]
    if (project / "pyproject.toml").is_file():
        return str(project.parent / "eval-data" / "workspace")
    return str(Path.home() / ".local" / "share" / "searchagent-lab" / "workspace")
