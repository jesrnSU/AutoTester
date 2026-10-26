"""Constrain archive-generated files to their intended directories."""

import re
from pathlib import Path


def named_path(directory: Path, name: str, suffix: str) -> Path:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,127}", name):
        raise ValueError("Names must use letters, numbers, spaces, underscores or hyphens")
    root = directory.resolve()
    path = root / f"{name}{suffix}"
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("File must stay within its designated directory")
    return path
