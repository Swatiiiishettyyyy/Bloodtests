"""
Login utilities package.

This file exists to make `from Login_module.Utils import security` work reliably.
"""

from __future__ import annotations

import importlib
from typing import Any


def __getattr__(name: str) -> Any:
    # Lazy import to avoid circular imports during app startup.
    if name == "security":
        # Canonical implementation lives in `Security.py` (capital S).
        return importlib.import_module(".Security", __name__)
    raise AttributeError(name)


__all__ = ["security"]

