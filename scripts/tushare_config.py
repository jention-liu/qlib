"""Shared Tushare configuration."""

import os
from pathlib import Path


def _load_project_env() -> None:
    """Load simple KEY=VALUE entries from the ignored project .env file."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            os.environ.setdefault(key, value)


def get_tushare_token() -> str:
    """Return the Tushare token from the environment."""
    _load_project_env()
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "Missing TUSHARE_TOKEN. Set it before running Tushare scripts, "
            "for example: export TUSHARE_TOKEN='your-token'"
        )
    return token
