"""Locate bundled application resources in source and PyInstaller builds."""

from __future__ import annotations

import sys
from pathlib import Path


def app_icon_path() -> Path | None:
    candidates: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(Path(bundle_root) / "assets" / "DeskTranslate.ico")

    project_root = Path(__file__).resolve().parents[2]
    candidates.extend(
        (
            project_root / "DeskTranslate.ico",
            Path(__file__).resolve().parent / "assets" / "DeskTranslate.ico",
        )
    )
    return next((path for path in candidates if path.is_file()), None)


__all__ = ["app_icon_path"]

