"""Configuration loading.

Every tunable number in the system lives in `config/*.yaml`, never inline in
code. Loading is cached, and access is by dotted path so a missing key fails
loudly at the point of use rather than silently defaulting to something
plausible.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"

# Loaded once at import so callers can rely on os.environ.
_ENV_FILE = BASE_DIR / ".env"
if _ENV_FILE.exists():
    try:
        from dotenv import load_dotenv

        load_dotenv(_ENV_FILE)
    except ImportError:  # pragma: no cover - optional dependency
        pass


class ConfigError(KeyError):
    """A requested configuration path does not exist."""


@lru_cache(maxsize=None)
def load(name: str) -> dict[str, Any]:
    """Load and cache `config/<name>.yaml`."""
    path = CONFIG_DIR / f"{name}.yaml"
    if not path.exists():
        raise ConfigError(f"no config file at {path}")
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


_MISSING = object()


def get(path: str, default: Any = _MISSING) -> Any:
    """Fetch a value by dotted path, e.g. ``get("rules.point_in_time.settle_days")``.

    Raises rather than returning None when a path is absent and no default is
    given: a silently-missing threshold is how a screening rule quietly stops
    being applied.
    """
    head, _, rest = path.partition(".")
    if not rest:
        raise ConfigError(f"path {path!r} must name a file and at least one key")

    try:
        node: Any = load(head)
    except ConfigError:
        if default is not _MISSING:
            return default
        raise

    walked = [head]
    for key in rest.split("."):
        walked.append(key)
        if not isinstance(node, dict) or key not in node:
            if default is not _MISSING:
                return default
            raise ConfigError(f"missing config path {'.'.join(walked)}")
        node = node[key]
    return node


def user_agent() -> str:
    """Contact string for SEC requests.

    SEC rejects requests without one, so this fails loudly rather than sending
    a placeholder that gets the whole project rate-limited.
    """
    value = os.environ.get("USER_AGENT", "").strip()
    if not value or "your_" in value.lower():
        raise ConfigError(
            "USER_AGENT is not set. SEC requires a real contact address "
            "(e.g. 'Name name@example.com'). Set it in .env"
        )
    return value


def clear_cache() -> None:
    """Drop cached configs. For tests that write temporary config files."""
    load.cache_clear()
