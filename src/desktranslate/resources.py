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


def frontend_path(name: str = "index.html") -> Path:
    path = Path(__file__).resolve().parent / "web" / name
    if not path.is_file():
        raise FileNotFoundError("界面尚未构建，请先运行 npm ci 和 npm run build")
    return path


def subtitle_model_path() -> Path:
    from .subtitles import MODEL_NAME

    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root) / "models" / MODEL_NAME
    return Path(__file__).resolve().parents[2] / "models" / MODEL_NAME


__all__ = ["app_icon_path", "frontend_path"]

