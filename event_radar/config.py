"""Load config.yaml + .env. Secrets come only from the environment (spec 1)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


@dataclass
class Config:
    """Thin wrapper over the parsed config.yaml plus a handle on env secrets."""

    raw: dict[str, Any]
    project_root: Path

    def section(self, name: str) -> dict[str, Any]:
        return self.raw.get(name, {})

    @property
    def db_path(self) -> str:
        configured = self.raw.get("database", "event_radar.db")
        # Resolve relative to the project root so any working dir behaves the same.
        return str((self.project_root / configured).resolve())

    def env(self, key: str, default: str | None = None) -> str | None:
        return os.environ.get(key, default)

    def require_env(self, key: str) -> str:
        value = os.environ.get(key)
        if not value:
            raise RuntimeError(
                f"Missing required environment variable {key!r}. "
                f"Copy .env.example to .env and fill it in."
            )
        return value


def load_config(config_path: str | None = None) -> Config:
    """Read .env then config.yaml. Defaults to files next to the project root."""
    project_root = Path(__file__).resolve().parent.parent
    load_dotenv(project_root / ".env")

    path = Path(config_path) if config_path else project_root / "config.yaml"
    with open(path, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return Config(raw=raw, project_root=project_root)
