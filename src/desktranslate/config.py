from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class AppSettings:
    window_x: int | None = None
    window_y: int | None = None
    window_width: int | None = None
    window_height: int | None = None
    direction: str = "auto"
    hotkey: str = "Ctrl+Alt+T"


class SettingsStore:
    """Small, failure-tolerant JSON settings store under %APPDATA%."""

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            base = Path(os.environ.get("APPDATA", Path.home()))
            path = base / "DeskTranslate" / "settings.json"
        self.path = path

    def load(self) -> AppSettings:
        try:
            payload: Any = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return AppSettings()
            x = payload.get("window_x")
            y = payload.get("window_y")
            width = payload.get("window_width")
            height = payload.get("window_height")
            direction = payload.get("direction", "auto")
            hotkey = payload.get("hotkey", "Ctrl+Alt+T")
            if not isinstance(x, int):
                x = None
            if not isinstance(y, int):
                y = None
            if not isinstance(width, int) or width <= 0:
                width = None
            if not isinstance(height, int) or height <= 0:
                height = None
            if direction not in {"auto", "en_to_zh", "zh_to_en"}:
                direction = "auto"
            if not isinstance(hotkey, str) or not hotkey.strip():
                hotkey = "Ctrl+Alt+T"
            return AppSettings(
                window_x=x,
                window_y=y,
                window_width=width,
                window_height=height,
                direction=direction,
                hotkey=hotkey,
            )
        except (OSError, ValueError, TypeError):
            return AppSettings()

    def save(self, settings: AppSettings) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(asdict(settings), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError:
            # Settings must never prevent the translator from closing.
            return

