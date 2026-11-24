from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .deepseek import DEEPSEEK_MODELS
from .network import NETWORK_MODES

# 每条 API 配置都写入独立的 Windows 凭据目标，避免互相覆盖。
CREDENTIAL_TARGET_PREFIX = "DeskTranslate/DeepSeek"
MAX_API_CONFIGS = 20
MAX_CONFIG_NAME = 40
_CONFIG_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

LIQUID_COLOR = "#4c5057"


@dataclass
class ApiConfig:
    """一条可切换的 DeepSeek 配置；密钥本体只存在于 Windows 凭据管理器。"""

    id: str
    name: str
    model: str = "deepseek-flash"
    # 旧配置沿用手改的凭据目标；留空时按 id 派生，避免把密钥复制到新目标。
    target: str = ""

    @property
    def credential_target(self) -> str:
        return self.target or f"{CREDENTIAL_TARGET_PREFIX}/{self.id}"


@dataclass
class AppSettings:
    window_x: int | None = None
    window_y: int | None = None
    window_width: int | None = None
    window_height: int | None = None
    direction: str = "auto"
    hotkey: str = "Ctrl+Alt+T"
    network_mode: str = "auto"
    translation_provider: str = "mymemory"
    deepseek_model: str = "deepseek-flash"
    api_configs: list[ApiConfig] = field(default_factory=list)
    active_api_config: str = ""
    # close_action: "tray" 点击关闭按钮隐藏到托盘，"exit" 直接退出应用。
    # appearance: "frosted" 磨砂玻璃（默认），"liquid" 液态玻璃。
    close_action: str = "tray"
    appearance: str = "frosted"
    # frosted_amount: 磨砂度 0-100；liquid_transparency: 液态玻璃透明度 0-100。
    frosted_amount: int = 60
    liquid_transparency: int = 40
    transparent_transparency: int = 40
    # liquid_color: 液态玻璃的底色。默认是中性冷灰，绿色只作为界面强调色，不再给整窗染色。
    liquid_color: str = LIQUID_COLOR


def _percent(value: Any, fallback: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        return fallback
    return max(0, min(100, value))


def _is_credential_target(value: Any) -> bool:
    return isinstance(value, str) and all(32 <= ord(char) <= 126 for char in value) and len(value) <= 256


def _parse_api_configs(value: Any) -> list[ApiConfig]:
    if not isinstance(value, list):
        return []
    configs: list[ApiConfig] = []
    seen: set[str] = set()
    for item in value[:MAX_API_CONFIGS]:
        if not isinstance(item, dict):
            continue
        identifier = item.get("id")
        if not isinstance(identifier, str) or not _CONFIG_ID.match(identifier) or identifier in seen:
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            name = "DeepSeek"
        model = item.get("model")
        if model not in DEEPSEEK_MODELS:
            model = "deepseek-flash"
        target = item.get("target")
        if not _is_credential_target(target):
            target = ""
        seen.add(identifier)
        configs.append(
            ApiConfig(id=identifier, name=name.strip()[:MAX_CONFIG_NAME], model=model, target=target)
        )
    return configs


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
            network_mode = payload.get("network_mode", "auto")
            provider = payload.get("translation_provider", "mymemory")
            model = payload.get("deepseek_model", "deepseek-flash")
            close_action = payload.get("close_action", "tray")
            appearance = payload.get("appearance", "frosted")
            frosted_amount = payload.get("frosted_amount", 60)
            liquid_transparency = payload.get("liquid_transparency", 40)
            transparent_transparency = payload.get("transparent_transparency", 40)
            liquid_color = payload.get("liquid_color", LIQUID_COLOR)
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
            if not isinstance(network_mode, str) or network_mode not in NETWORK_MODES:
                network_mode = "auto"
            if not isinstance(provider, str) or provider not in {"mymemory", "deepseek"}:
                provider = "mymemory"
            if not isinstance(model, str) or model not in DEEPSEEK_MODELS:
                model = "deepseek-flash"
            if not isinstance(close_action, str) or close_action not in {"tray", "exit"}:
                close_action = "tray"
            if not isinstance(appearance, str) or appearance not in {"frosted", "liquid", "transparent"}:
                appearance = "frosted"
            frosted_amount = _percent(frosted_amount, 60)
            liquid_transparency = _percent(liquid_transparency, 40)
            transparent_transparency = _percent(transparent_transparency, 40)
            if not isinstance(liquid_color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", liquid_color):
                liquid_color = LIQUID_COLOR
            api_configs = _parse_api_configs(payload.get("api_configs"))
            active = payload.get("active_api_config")
            identifiers = {config.id for config in api_configs}
            if not isinstance(active, str) or active not in identifiers:
                active = ""
            active_config = next((config for config in api_configs if config.id == active), None)
            # 顶层模型字段只作为旧版兼容项保留，实际生效的模型跟随当前配置。
            if active_config is not None:
                model = active_config.model
            return AppSettings(
                window_x=x,
                window_y=y,
                window_width=width,
                window_height=height,
                direction=direction,
                hotkey=hotkey,
                network_mode=network_mode,
                translation_provider=provider,
                deepseek_model=model,
                api_configs=api_configs,
                active_api_config=active,
                close_action=close_action,
                appearance=appearance,
                frosted_amount=frosted_amount,
                liquid_transparency=liquid_transparency,
                transparent_transparency=transparent_transparency,
                liquid_color=liquid_color.lower(),
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
