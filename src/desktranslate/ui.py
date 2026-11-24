from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, replace
from math import ceil
from pathlib import Path

import webview

from .config import MAX_API_CONFIGS, MAX_CONFIG_NAME, ApiConfig, SettingsStore, _percent
from .credentials import CredentialStore
from .deepseek import DEEPSEEK_MODELS, DeepSeekTranslator
from .geometry import intersects_visible_area, sanitize_window_geometry, work_areas
from .network import NETWORK_MODES
from .resources import app_icon_path, frontend_path, subtitle_model_path
from .subtitles import SubtitleService, list_loopback_devices
from .translator import LanguageDirection, SmartTranslator, TranslationError, TranslationNoResultError
from .windows import (
    activate_window,
    apply_window_material,
    native_glass_supported,
    show_window_no_activate,
    start_native_move,
    start_native_resize,
)


WINDOW_WIDTH = 520
WINDOW_HEIGHT = 420
MIN_WINDOW_WIDTH = 320
MIN_WINDOW_HEIGHT = 240
MIN_SUBTITLE_HEIGHT = 100
MAX_INPUT_CHARACTERS = 3_000


_HTML_OPEN = re.compile(r"<html\b[^>]*>", re.IGNORECASE)


def glass_variables(settings) -> str:
    """首帧就要正确的玻璃变量，口径与 app.ts 的 applySettings 保持一致。

    两边各写一遍是刻意的：这里管「页面还没跑 JS 时的第一帧」，
    applySettings 管「之后用户改设置」。改一处必须同步另一处。
    """
    frosted = settings.frosted_amount / 100
    alpha = 0.12 + ((100 - settings.liquid_transparency) / 100) * 0.5
    return ";".join((
        f"--frost-blur:{round(frosted * 16, 3)}px",
        f"--frost-tint:{round(0.3 + frosted * 0.35, 4)}",
        f"--liquid-alpha:{round(alpha, 4)}",
        f"--liquid-color:{settings.liquid_color}",
    ))


def appearance_attributes(settings, *, native_glass: bool) -> str:
    """渲染 <html> 上的外观属性，省掉启动时的一次 IPC 往返。"""
    classes: list[str] = []
    if native_glass:
        classes.append("native-glass")
    if settings.appearance == "liquid":
        classes.append("appearance-liquid")
    elif settings.appearance == "transparent":
        classes.append("appearance-transparent")
    attributes = f'style="{glass_variables(settings)}"'
    if classes:
        attributes = f'class="{" ".join(classes)}" ' + attributes
    return attributes


def with_appearance(markup: str, attributes: str) -> str:
    """把外观属性并进 <html> 标签，其余标记原样保留。"""
    match = _HTML_OPEN.search(markup)
    if match is None:
        return markup
    tag = match.group(0).replace("<html", f"<html {attributes}", 1)
    return markup[: match.start()] + tag + markup[match.end():]


def mask_secret(secret: str | None) -> str:
    """只把密钥的首尾几个字符交给界面，明文始终留在后端进程内。"""
    if not secret:
        return ""
    if len(secret) <= 4:
        return "…" + secret[-2:]
    if len(secret) <= 10:
        return secret[:2] + "…" + secret[-2:]
    return f"{secret[:3]}…{secret[-4:]}"


def _configs_of(host: object) -> list[ApiConfig]:
    configs = getattr(host.settings, "api_configs", None)
    return configs if isinstance(configs, list) else []


def _active_config(host: object) -> ApiConfig | None:
    configs = _configs_of(host)
    if not configs:
        return None
    active_id = getattr(host.settings, "active_api_config", "")
    for config in configs:
        if config.id == active_id:
            return config
    return configs[0]


def _read_secret(host: object, config: ApiConfig | None) -> str | None:
    if config is None:
        return None
    try:
        return host.credentials.read(config.credential_target)
    except OSError:
        return None


def _legacy_secret(host: object) -> str | None:
    try:
        return host.credentials.read()
    except OSError:
        return None


class DesktopApi:
    """仅向本地 TypeScript 界面开放桌面操作和翻译。"""

    def __init__(self, host: TranslationWindow) -> None:
        self._host = host

    def get_settings(self) -> dict:
        host = self._host
        active = _active_config(host)
        return {
            "direction": host.direction.value,
            "hotkey": host.settings.hotkey,
            "hotkey_error": host.hotkey_error,
            "initial_tab": getattr(host, "settings_tab", "translation"),
            "native_glass": host.native_glass,
            "network_mode": host.settings.network_mode,
            "translation_provider": host.settings.translation_provider,
            "deepseek_model": active.model if active is not None else host.settings.deepseek_model,
            "has_api_key": self._has_api_key(),
            "api_configs": self._config_payloads(),
            "active_api_config": active.id if active is not None else "",
            "close_action": host.settings.close_action,
            "appearance": host.settings.appearance,
            "frosted_amount": host.settings.frosted_amount,
            "liquid_transparency": host.settings.liquid_transparency,
            "transparent_transparency": host.settings.transparent_transparency,
            "liquid_color": host.settings.liquid_color,
            "settings_uid": getattr(getattr(host, "_settings_window", None), "uid", ""),
        }

    def _has_api_key(self) -> bool:
        host = self._host
        active = _active_config(host)
        if active is not None:
            return bool(_read_secret(host, active))
        return bool(_legacy_secret(host))

    def _config_payloads(self) -> list[dict]:
        host = self._host
        active = _active_config(host)
        payloads: list[dict] = []
        for config in _configs_of(host):
            secret = _read_secret(host, config)
            payloads.append(
                {
                    "id": config.id,
                    "name": config.name,
                    "model": config.model,
                    "has_key": bool(secret),
                    # 只下发掩码，完整密钥不进入界面层。
                    "key_preview": mask_secret(secret),
                    "active": active is config,
                }
            )
        return payloads

    def _configs_response(self) -> dict:
        active = _active_config(self._host)
        return {
            "configs": self._config_payloads(),
            "active_id": active.id if active is not None else "",
            "has_api_key": self._has_api_key(),
        }

    def save_api_config(self, config_id: str, name: str, model: str, api_key: str) -> dict:
        if not isinstance(config_id, str) or not isinstance(api_key, str):
            raise ValueError("配置设置格式不正确")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("请填写配置名称")
        name = name.strip()
        if len(name) > MAX_CONFIG_NAME:
            raise ValueError(f"配置名称请控制在 {MAX_CONFIG_NAME} 字以内")
        if model not in DEEPSEEK_MODELS:
            raise ValueError("请选择支持的 DeepSeek 模型")
        key = api_key.strip()
        if key and (len(key) > 512 or any(not 33 <= ord(char) <= 126 for char in key)):
            raise ValueError("API Key 应为不含空格和换行的可见英文字符")
        host = self._host
        configs = _configs_of(host)
        config = next((item for item in configs if item.id == config_id.strip()), None)
        if config is None:
            if len(configs) >= MAX_API_CONFIGS:
                raise ValueError(f"最多保存 {MAX_API_CONFIGS} 条 API 配置")
            config = ApiConfig(id=secrets.token_hex(4), name=name, model=model)
            configs.append(config)
        else:
            config.name = name
            config.model = model
        try:
            if key:
                host.credentials.save(key, config.credential_target)
        except OSError as exc:
            raise ValueError("无法保存 Windows 凭据，请稍后重试") from exc
        if not host.settings.active_api_config or host.settings.active_api_config not in {
            item.id for item in configs
        }:
            host.settings.active_api_config = config.id
        if config.id == host.settings.active_api_config:
            host.settings.deepseek_model = config.model
            host.deepseek.model = config.model
        host.save_settings()
        return self._configs_response()

    def activate_api_config(self, config_id: str) -> dict:
        if not isinstance(config_id, str):
            raise ValueError("配置设置格式不正确")
        host = self._host
        config = next((item for item in _configs_of(host) if item.id == config_id.strip()), None)
        if config is None:
            raise ValueError("配置不存在或已被删除")
        host.settings.active_api_config = config.id
        host.settings.deepseek_model = config.model
        host.deepseek.model = config.model
        host.save_settings()
        return self._configs_response()

    def delete_api_config(self, config_id: str) -> dict:
        if not isinstance(config_id, str):
            raise ValueError("配置设置格式不正确")
        host = self._host
        configs = _configs_of(host)
        config = next((item for item in configs if item.id == config_id.strip()), None)
        if config is None:
            raise ValueError("配置不存在或已被删除")
        if len(configs) <= 1:
            raise ValueError("至少保留一条配置；如需停用 DeepSeek，请先切换到 MyMemory")
        try:
            host.credentials.delete(config.credential_target)
        except OSError as exc:
            raise ValueError("无法删除 Windows 凭据，请稍后重试") from exc
        configs.remove(config)
        if host.settings.active_api_config == config.id:
            host.settings.active_api_config = configs[0].id
            host.settings.deepseek_model = configs[0].model
            host.deepseek.model = configs[0].model
        host.save_settings()
        return self._configs_response()

    def translate(self, text: str, direction: str) -> dict:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("请先输入要翻译的内容")
        text = text.strip()
        if len(text) > MAX_INPUT_CHARACTERS:
            raise ValueError(f"请将内容控制在 {MAX_INPUT_CHARACTERS} 字以内")
        selected = LanguageDirection(direction)
        # pywebview 在工作线程执行 API，网络请求不会占用 WebView2 的界面线程。
        try:
            host = self._host
            if host.settings.translation_provider == "deepseek":
                result = host.deepseek.translate(text, selected)
            else:
                result = host.translator.translate(text, selected)
        except TranslationNoResultError as exc:
            return {"ok": False, "text": f"未找到译文\n{exc}", "message": "请检查英文拼写或换一种表达"}
        except TranslationError as exc:
            return {"ok": False, "text": f"翻译失败\n{exc}", "message": "请检查网络后重试"}
        return {"ok": True, **asdict(result)}

    def set_direction(self, direction: str) -> None:
        host = self._host
        host.direction = LanguageDirection(direction)
        host.save_settings()

    def set_network_mode(self, mode: str) -> None:
        if not isinstance(mode, str) or mode not in NETWORK_MODES:
            raise ValueError("请选择自动、系统代理或直连")
        host = self._host
        host.translator.network.mode = mode
        host.settings.network_mode = mode
        host.save_settings()

    def set_translation_settings(self, provider: str, model: str, api_key: str, clear_key: bool) -> dict:
        if not isinstance(provider, str) or provider not in {"mymemory", "deepseek"}:
            raise ValueError("请选择 MyMemory 或 DeepSeek")
        if not isinstance(model, str) or model not in DEEPSEEK_MODELS:
            raise ValueError("请选择支持的 DeepSeek 模型")
        if not isinstance(api_key, str) or not isinstance(clear_key, bool):
            raise ValueError("密钥设置格式不正确")
        key = api_key.strip()
        if key and (len(key) > 512 or any(not 33 <= ord(char) <= 126 for char in key)):
            raise ValueError("API Key 应为不含空格和换行的可见英文字符")
        host = self._host
        config = _active_config(host)
        existing = _read_secret(host, config) if config is not None else _legacy_secret(host)
        if provider == "deepseek" and (clear_key or not (key or existing)):
            raise ValueError("使用 DeepSeek 前请填写 API Key")
        if provider == "deepseek" and config is None:
            # 旧版本把密钥写在固定目标里；首次启用时直接沿用它，避免复制或丢失。
            config = ApiConfig(
                id=secrets.token_hex(4),
                name="默认配置",
                model=model,
                target=CredentialStore.TARGET if existing and not key else "",
            )
            _configs_of(host).append(config)
            host.settings.active_api_config = config.id
        target = config.credential_target if config is not None else None
        try:
            if clear_key:
                host.credentials.delete(target)
            elif key:
                host.credentials.save(key, target)
        except OSError as exc:
            raise ValueError("无法保存 Windows 凭据，请稍后重试") from exc
        if config is not None:
            config.model = model
        host.settings.translation_provider = provider
        host.settings.deepseek_model = model
        host.deepseek.model = model
        host.save_settings()
        return {"has_api_key": self._has_api_key()}

    def read_clipboard(self) -> str:
        from System.Runtime.InteropServices import ExternalException
        from System.Windows.Forms import Clipboard

        try:
            return str(self._host._on_ui(Clipboard.GetText))
        except ExternalException as exc:
            raise ValueError("暂时无法读取剪贴板，请稍后重试") from exc

    def copy_result(self, text: str) -> None:
        from System.Runtime.InteropServices import ExternalException
        from System.Windows.Forms import Clipboard

        if not isinstance(text, str) or not text:
            raise ValueError("当前没有可复制的结果")
        try:
            self._host._on_ui(lambda: Clipboard.SetText(text))
        except ExternalException as exc:
            raise ValueError("暂时无法写入剪贴板，请稍后重试") from exc

    def set_hotkey(self, shortcut: str) -> dict:
        if self._host.on_hotkey_change is None:
            return {"ok": False, "message": "快捷键服务尚未启动"}
        if not isinstance(shortcut, str):
            raise ValueError("快捷键必须是文本")
        success, message = self._host.on_hotkey_change(shortcut)
        return {"ok": success, "message": message}

    def hide(self) -> None:
        self._host.hide()

    def minimize(self) -> None:
        self._host.minimize()

    def set_close_action(self, action: str) -> None:
        """记录「点击关闭按钮」的行为：tray 隐藏到托盘，exit 直接退出应用。"""
        if action not in {"tray", "exit"}:
            raise ValueError("关闭行为只能是 tray 或 exit")
        host = self._host
        host.settings.close_action = action
        host.save_settings()

    def quit(self) -> None:
        """按设置直接退出应用；界面在关闭行为为 exit 时调用。"""
        self._host.request_exit()

    def set_appearance(self, appearance: str) -> None:
        """切换外观：frosted 磨砂玻璃，liquid 液态玻璃；两处窗口都立即生效。"""
        if appearance not in {"frosted", "liquid", "transparent"}:
            raise ValueError("外观无效")
        host = self._host
        host.settings.appearance = appearance
        host.save_settings()
        self._push_appearance()
        host.refresh_glass()

    def set_appearance_options(
        self, frosted_amount: int | None = None, liquid_transparency: int | None = None,
        save: bool = True, liquid_color: str | None = None, transparent_transparency: int | None = None,
    ) -> None:
        """调整磨砂度与液态玻璃透明度（0-100），只保存提供的字段。"""
        host = self._host
        if frosted_amount is not None:
            host.settings.frosted_amount = _percent(frosted_amount, host.settings.frosted_amount)
        if liquid_transparency is not None:
            host.settings.liquid_transparency = _percent(
                liquid_transparency, host.settings.liquid_transparency
            )
        if transparent_transparency is not None:
            host.settings.transparent_transparency = _percent(
                transparent_transparency, host.settings.transparent_transparency
            )
        if liquid_color is not None:
            if not isinstance(liquid_color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", liquid_color):
                raise ValueError("玻璃颜色格式无效")
            host.settings.liquid_color = liquid_color.lower()
        self._push_appearance()
        host.refresh_glass()
        if save:
            host.save_settings()

    def preview_appearance(
        self, uid: str, appearance: str, frosted_amount: int, liquid_transparency: int,
        transparent_transparency: int, color: str,
    ) -> None:
        if appearance not in {"frosted", "liquid", "transparent"}:
            raise ValueError("外观无效")
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise ValueError("玻璃颜色格式无效")
        host = self._host
        child = host._settings_window
        if child is None or child.uid != uid:
            return
        preview = replace(
            host.settings, appearance=appearance,
            frosted_amount=_percent(frosted_amount, host.settings.frosted_amount),
            liquid_transparency=_percent(liquid_transparency, host.settings.liquid_transparency),
            transparent_transparency=_percent(transparent_transparency, host.settings.transparent_transparency),
            liquid_color=color.lower(),
        )

        def apply_preview() -> None:
            if host._settings_window is child and not child.native.IsDisposed:
                for form in (host.window.native, child.native):
                    apply_window_material(form, host.glass_tint_alpha(preview),
                                          appearance == "liquid", host.window_opacity(preview),
                                          appearance == "transparent")

        if host._settings_window is child:
            settings = self.get_settings()
            settings.update(appearance=preview.appearance, frosted_amount=preview.frosted_amount,
                            liquid_transparency=preview.liquid_transparency,
                            transparent_transparency=preview.transparent_transparency,
                            liquid_color=preview.liquid_color)
            host.window.evaluate_js(f"window.desktranslate.applySettings({json.dumps(settings)}, false)")
        # 材质改动会牵动 DWM 的客户区，等这次桥调用（含上面那次跨窗 evaluate_js）退出去再动。
        host._on_ui_later(apply_preview)

    def discard_appearance_preview(self, uid: str) -> None:
        host = self._host
        child = host._settings_window
        if child is None or child.uid != uid:
            return
        self._push_appearance()
        host.refresh_glass()

    def save_appearance(self, appearance: str, frosted: int, liquid: int, color: str, transparent: int) -> None:
        if appearance not in {"frosted", "liquid", "transparent"}:
            raise ValueError("外观无效")
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise ValueError("玻璃颜色格式无效")
        settings = self._host.settings
        settings.appearance = appearance
        settings.frosted_amount = _percent(frosted, settings.frosted_amount)
        settings.liquid_transparency = _percent(liquid, settings.liquid_transparency)
        settings.transparent_transparency = _percent(transparent, settings.transparent_transparency)
        settings.liquid_color = color.lower()
        self._host.save_settings()
        self._push_appearance()
        self._host.refresh_glass()

    def _push_appearance(self, include_settings_window: bool = False) -> None:
        """把外观同步到主窗。设置窗是这类控件的源头，默认不回推，避免覆盖拖动中的滑杆。"""
        host = self._host
        settings = host.settings
        host.window.evaluate_js(
            f"window.desktranslate.applySettings({json.dumps(self.get_settings())}, false)"
        )
        child = getattr(host, "_settings_window", None)
        if include_settings_window and child is not None:
            child.evaluate_js(
                "window.desktranslateSettings?.applyAppearance("
                f"{json.dumps(settings.appearance)}, "
                f"{settings.frosted_amount}, {settings.liquid_transparency}, {json.dumps(settings.liquid_color)}, {settings.transparent_transparency})"
            )

    def open_settings(self, tab: str = "translation") -> None:
        self._host.open_settings(tab if tab in {"translation", "hotkey", "window", "appearance"} else "translation")

    def close_settings(self) -> None:
        self._host.close_settings()

    def settings_saved(self) -> None:
        """把保存后的设置推给主窗。设置窗保持打开，由界面自己提示保存结果。"""
        host = self._host
        settings = json.dumps(self.get_settings())
        host.window.evaluate_js(f"window.desktranslate.applySettings({settings}, true)")

    def set_on_top(self, on_top: bool) -> None:
        if not isinstance(on_top, bool):
            raise ValueError("置顶状态必须是布尔值")
        self._host._on_ui(lambda: setattr(self._host.window.native, "TopMost", on_top))

    def start_resize(self, edge: str) -> None:
        start_native_resize(self._host.window.native, edge)

    def set_window_mode(self, mode: str) -> None:
        if mode not in {"translation", "subtitles"}:
            raise ValueError("窗口模式无效")
        height = MIN_SUBTITLE_HEIGHT if mode == "subtitles" else MIN_WINDOW_HEIGHT
        self._host._set_minimum_content_size(self._host.window, MIN_WINDOW_WIDTH, height)

    def subtitle_devices(self) -> list[dict]:
        try:
            return list_loopback_devices()
        except (ImportError, OSError, LookupError) as exc:
            raise ValueError("无法获取系统播放设备，请检查音频设备") from exc

    def start_subtitles(self, device_id: int) -> int:
        return self._host.subtitles.start(device_id)

    def activate_subtitles(self, session: int) -> None:
        self._host.subtitles.activate(session)

    def stop_subtitles(self, session: int | None = None) -> None:
        self._host.subtitles.stop(session)

    def clear_subtitles(self, session: int) -> int:
        return self._host.subtitles.clear(session)

    def start_drag(self) -> None:
        start_native_move(self._host.window.native)

    def start_settings_drag(self) -> None:
        """设置窗没有系统标题栏，拖动由页面上的标题条接管。"""
        child = getattr(self._host, "_settings_window", None)
        if child is not None:
            start_native_move(child.native)

    def start_settings_resize(self, edge: str) -> None:
        child = getattr(self._host, "_settings_window", None)
        if child is not None:
            start_native_resize(child.native, edge)

    def remember_settings_size(self, width: float, height: float) -> None:
        """页面自报内容视口尺寸，下次开窗照这个摆。

        页面的 innerWidth/innerHeight 才是用户看到的尺寸；WinForms 的 Size 还混着 DPI 和
        边框，换算一次就会在「记住」和「摆回去」之间漂移。
        """
        if not width or not height or width <= 0 or height <= 0:
            return
        if not self._host._settings_fitted:
            # 就绪前的 resize 是创建/摆位过程里的中间态，记下来下次开窗会越开越小。
            return
        self._host._settings_size = (max(360, round(width)), max(320, round(height)))

    def fit_settings_window(self) -> dict[str, int] | None:
        """页面就绪后按记住的内容尺寸把设置窗摆准，返回要摆到多少。

        创建窗口时 pywebview 先按带边框摆、再换成无边框，WinForms 换样式会保留客户区，
        结果比要求的小一圈；补多少和当时的边框状态有关，算不准，只能在页面起来后实测再摆。

        摆位必须晚一拍、并且不能等它摆完：此刻页面的 JS 正停在这个调用的 await 上，
        当场改窗口尺寸会把这条桥上还没取走的消息堵死，之后所有桥调用都不再返回。
        所以这里只排一个延后任务，把目标尺寸交给页面自己去等。
        """
        host = self._host
        child = host._settings_window
        if child is None:
            return None
        width, height = host._settings_size
        host._set_content_size(child, width, height, delay=0.05)
        return {"width": width, "height": height}

    def show_settings_window(self) -> None:
        host = self._host
        child = host._settings_window
        if child is None:
            return

        def show() -> None:
            if host._settings_window is child:
                child.native.Opacity = host.window_opacity()
                child.focus = True
                activate_window(child.native)
                host._settings_ready = True

        host._on_ui_later(show)


class TranslationWindow:
    def __init__(self, translator: SmartTranslator, settings_store: SettingsStore) -> None:
        self.translator = translator
        self.settings_store = settings_store
        self.settings = settings_store.load()
        self.translator.network.mode = self.settings.network_mode
        self.credentials = CredentialStore()
        self._ensure_api_configs()
        self.deepseek = DeepSeekTranslator(self.active_api_key, self.translator.network)
        self.deepseek.model = self.active_api_model()
        try:
            self.direction = LanguageDirection(self.settings.direction)
        except ValueError:
            self.direction = LanguageDirection.AUTO
        self.is_exiting = False
        self.is_visible = True
        self.is_minimized = False
        self.native_glass = False
        self.hotkey_error: str | None = None
        self.settings_tab = "translation"
        self.on_visibility_changed: Callable[[bool], None] | None = None
        self.on_hotkey_change: Callable[[str], tuple[bool, str]] | None = None
        self.stopped = threading.Event()
        self._settings_lock = threading.Lock()
        self._settings_window_lock = threading.Lock()
        self._settings_window: webview.Window | None = None
        self._allow_settings_close = False
        self._settings_size = (460, 600)
        # 设置窗「客户区像素 / 页面视口」的比值，由 ready() 现算，供关闭时兜底换算。
        self._settings_content_scale = 0.0
        # 页面就绪后会把窗口摆到记住的尺寸；在那之前页面报上来的尺寸是创建时的残值，不能记。
        self._settings_fitted = False
        self._settings_ready = False
        self.api = DesktopApi(self)
        window_x, window_y, window_width, window_height = sanitize_window_geometry(
            self.settings.window_x,
            self.settings.window_y,
            self.settings.window_width,
            self.settings.window_height,
            work_areas(),
            min_width=MIN_WINDOW_WIDTH,
            min_height=MIN_WINDOW_HEIGHT,
            default_width=WINDOW_WIDTH,
            default_height=WINDOW_HEIGHT,
        )
        # 修正后的几何立刻写回内存，后续 hide/退出保存时不会再把屏幕外坐标落盘。
        self.settings.window_x = window_x
        self.settings.window_y = window_y
        self.settings.window_width = window_width
        self.settings.window_height = window_height
        self.window = webview.create_window(
            "DeskTranslate",
            html=self._markup(),
            js_api=self.api,
            width=window_width,
            height=window_height,
            x=window_x,
            y=window_y,
            min_size=(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT),
            frameless=True,
            easy_drag=False,
            resizable=True,
            background_color="#626b6e",
            text_select=True,
            zoomable=False,
        )
        self.subtitles = SubtitleService(subtitle_model_path(), self._translate_subtitle, self._emit_subtitle)
        self.window.events.before_show += self._prepare_native_window
        self.window.events.loaded += lambda: self._set_minimum_content_size(self.window, MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self.window.events.closing += self._on_closing
        self.window.events.closed += self._on_closed
        self.window.events.resized += self._remember_size
        self.window.events.moved += self._remember_position

    def _translate_subtitle(self, text: str, direction: LanguageDirection, timeout: float):
        translator = self.deepseek if self.settings.translation_provider == "deepseek" else self.translator.primary
        return translator.translate(text, direction, timeout=timeout)

    def _emit_subtitle(self, event: dict) -> None:
        if not self.is_exiting and self.window.events.loaded.is_set():
            self.window.evaluate_js(f"window.desktranslate.updateSubtitle({json.dumps(event)})")

    def _ensure_api_configs(self) -> None:
        """把旧版本的单个凭据迁移成一条可管理的配置，不复制密钥本身。

        迁移只发生在内存里：目标名固定为旧凭据目标，因此每次启动都能幂等重建；
        真正的配置由用户在设置页操作时落盘，避免启动阶段产生隐式写盘。
        """
        configs = self.settings.api_configs
        if configs:
            if self.settings.active_api_config not in {config.id for config in configs}:
                self.settings.active_api_config = configs[0].id
                self.settings.deepseek_model = configs[0].model
            return
        try:
            legacy = self.credentials.read(CredentialStore.TARGET)
        except OSError:
            legacy = None
        if not legacy:
            return
        config = ApiConfig(
            id="default",
            name="默认配置",
            model=self.settings.deepseek_model,
            target=CredentialStore.TARGET,
        )
        self.settings.api_configs = [config]
        self.settings.active_api_config = config.id

    def active_api_config(self) -> ApiConfig | None:
        return _active_config(self)

    def active_api_key(self) -> str | None:
        return _read_secret(self, self.active_api_config())

    def active_api_model(self) -> str:
        config = self.active_api_config()
        return config.model if config is not None else self.settings.deepseek_model

    def glass_tint_alpha(self, settings=None) -> int:
        """把外观设置换算成 acrylic 底色浓度：越透明 alpha 越低。"""
        settings = settings or self.settings
        if settings.appearance == "liquid":
            return 0x18 + int((1 - settings.liquid_transparency / 100) * 0x60)
        return 0x2A + int((settings.frosted_amount / 100) * 0x66)

    def window_opacity(self, settings=None) -> float:
        settings = settings or self.settings
        if settings.appearance == "frosted":
            return 1.0
        amount = settings.transparent_transparency if settings.appearance == "transparent" else settings.liquid_transparency
        return 1 - amount * 0.0045

    def refresh_glass(self) -> None:
        """按当前设置重新应用主窗与设置窗的材质。"""
        if not self.native_glass:
            return
        alpha = self.glass_tint_alpha()
        liquid = self.settings.appearance == "liquid"
        transparent = self.settings.appearance == "transparent"
        opacity = self.window_opacity()
        self._on_ui_later(lambda: apply_window_material(self.window.native, alpha, liquid, opacity, transparent))
        child = getattr(self, "_settings_window", None)
        if child is not None:
            self._on_ui_later(lambda: apply_window_material(child.native, alpha, liquid, opacity, transparent))

    def _on_ui_later(self, callback: Callable, delay: float = 0.05) -> None:
        """把要动窗口的活排到这次桥调用退出之后。

        从页面的桥调用里同步改窗口材质/尺寸会触发 WM_SIZE，正在等回包的 WebView2 会卡住，
        之后这条桥上所有调用都不再返回（外观预览不 settle、丢弃预览卡在原地）。
        """

        def worker() -> None:
            time.sleep(delay)
            try:
                self._on_ui(callback)
            except Exception:  # noqa: BLE001 - 窗口已经关掉，没有可刷的材质
                return

        threading.Thread(target=worker, daemon=True).start()

    def _markup(self, name: str = "index.html") -> str:
        """读出前端页面并补上 nonce 与首帧外观，交给 webview 直接渲染。"""
        markup = frontend_path(name).read_text(encoding="utf-8")
        markup = markup.replace("__DESKTRANSLATE_NONCE__", secrets.token_urlsafe(24))
        return with_appearance(
            markup, appearance_attributes(self.settings, native_glass=native_glass_supported())
        )

    def _prepare_native_window(self) -> None:
        self.native_glass = apply_window_material(
            self.window.native, self.glass_tint_alpha(), self.settings.appearance == "liquid",
            self.window_opacity(),
            self.settings.appearance == "transparent",
        )

    def _set_minimum_content_size(self, window: webview.Window, width: int, height: int) -> None:
        from System.Drawing import Size

        scale = window.native.ClientSize.Width / window.evaluate_js("innerWidth")

        def set_minimum() -> None:
            # WebView2 的内容缩放可能与 WinForms 的 DeviceDpi 不同。
            form = window.native
            form.MinimumSize = Size(
                ceil(width * scale) + form.Width - form.ClientSize.Width,
                ceil(height * scale) + form.Height - form.ClientSize.Height,
            )
            if form.Height < form.MinimumSize.Height:
                form.Height = form.MinimumSize.Height

        self._on_ui(set_minimum)

    def _set_content_size(self, window: webview.Window, width: int, height: int, *, delay: float = 0.0) -> bool:
        """按记录的内容尺寸把窗口摆回去，返回是否已摆好。

        创建窗口时 pywebview 先按带边框的尺寸摆、随后才换成无边框，WinForms 换边框样式
        会保留客户区、把外框缩掉一圈，所以尺寸要在这里重写一遍 ClientSize。
        缩放系数从「客户区 / 页面视口」现算：WebView2 的内容缩放和 WinForms 的 DeviceDpi
        不是一个数，用 pywebview 的 _scale 换算会差一截。

        delay > 0 时另起线程隔一会儿再摆：从页面发起的调用里直接改窗口尺寸，会把正在跑的
        WebView2 消息处理卡住（外观预览的 await 再也不返回），必须等这一轮 JS 调用退出去。
        """
        try:
            from System.Drawing import Size
        except ImportError:  # 非 Windows 或未装 pythonnet：没有 WinForms 可摆
            return False

        inner = window.evaluate_js("innerWidth")
        if not isinstance(inner, (int, float)) or inner <= 0:
            return False
        content_scale = window.native.ClientSize.Width / inner
        self._settings_content_scale = content_scale
        self._settings_fitted = False

        def place() -> None:
            window.native.ClientSize = Size(round(width * content_scale), round(height * content_scale))
            self._settings_fitted = True

        if not delay:
            self._on_ui(place)
            return True

        def worker() -> None:
            time.sleep(delay)
            try:
                self._on_ui(place)
            except Exception:  # noqa: BLE001 - 窗口已经关掉，摆不摆都无所谓
                return

        threading.Thread(target=worker, daemon=True).start()
        # 不等它摆完：调用方（页面）此刻正停在这次桥调用的 await 上，等下去只会白等到超时。
        return True

    def _on_ui(self, callback: Callable) -> object:
        from System import Func, Object

        return self.window.native.Invoke(Func[Object](callback))

    def open_settings(self, tab: str = "translation") -> None:
        # 主窗「设置」打开翻译选项页；左下角快捷键文字打开快捷键页。
        self.settings_tab = tab if tab in {"translation", "hotkey", "window", "appearance"} else "translation"
        with self._settings_window_lock:
            if self._settings_window is not None:
                child = self._settings_window
                if self._settings_ready:
                    child.show()
                    child.evaluate_js(
                        f"window.desktranslateSettings?.showTab({json.dumps(self.settings_tab)})"
                    )
                return
            width, height = self._settings_size
            child = webview.create_window(
                "DeskTranslate 设置",
                html=self._markup("settings.html"),
                js_api=self.api,
                # 记录的尺寸是页面视口，按实测比例换成 pywebview 的创建单位才不会缩一圈。
                width=width,
                height=height,
                min_size=(360, 320),
                hidden=True,
                focus=False,
                frameless=True,
                easy_drag=False,
                resizable=True,
                background_color="#5b6669",
                text_select=True,
                zoomable=False,
            )
            self._settings_window = child
            self._settings_ready = False
            child.events.closing += lambda: self._on_settings_closing(child)
            child.events.closed += lambda: self._settings_closed(child)
            def ready() -> None:
                # 内容尺寸由页面就绪后调 fit_settings_window 摆准，这里只管最小尺寸。
                self._set_minimum_content_size(child, 360, 320)
            child.events.loaded += ready
            # 子窗口创建时消息循环已经运行，页面可能在注册回调前完成加载。
            if child.events.loaded.is_set():
                ready()
            def prepare_settings_window() -> None:
                apply_window_material(
                    child.native, self.glass_tint_alpha(), self.settings.appearance == "liquid",
                    0.0,
                    self.settings.appearance == "transparent",
                )
                # 以非激活态进入可见流程，避免零不透明度初始化阶段提前夺走主窗焦点；
                # 真正显示与激活统一交给页面就绪后的 show_settings_window()。
                show_window_no_activate(child.native)

            self._on_ui(prepare_settings_window)

    def _remember_settings_size(self, child: webview.Window) -> None:
        # 关闭路径上不能 evaluate_js：UI 线程正卡在 closing 回调里等不到页面回应。
        # 页面自己报过尺寸就用页面报的；这里只是兜底，按 fit 时量到的缩放换算。
        try:
            form = child.native
            scale = self._settings_content_scale
            if form is None or not scale:
                return
            self._settings_size = (round(form.ClientSize.Width / scale), round(form.ClientSize.Height / scale))
        except Exception:  # noqa: BLE001 - 兜底路径，量不到就沿用上一次的尺寸
            return

    def _on_settings_closing(self, child: webview.Window) -> bool:
        if self._allow_settings_close or self.is_exiting:
            self._remember_settings_size(child)
            return True
        threading.Thread(target=lambda: child.evaluate_js("window.desktranslateSettings?.requestClose()"), daemon=True).start()
        return False

    def _settings_closed(self, child: webview.Window) -> None:
        with self._settings_window_lock:
            if self._settings_window is child:
                self._settings_window = None

    def close_settings(self) -> None:
        with self._settings_window_lock:
            child = self._settings_window
            self._settings_window = None
        if child is not None:
            self._allow_settings_close = True
            try:
                child.destroy()
            finally:
                self._allow_settings_close = False

    def _remember_size(self, width: int, height: int) -> None:
        # 最小化会把窗口压到 158×26 之类的尺寸；这不是用户的缩放意图。
        if self.is_minimized or width < MIN_WINDOW_WIDTH or height < MIN_WINDOW_HEIGHT:
            return
        self.settings.window_width = width
        self.settings.window_height = height

    def _remember_position(self, x: int, y: int) -> None:
        # 最小化时 Windows 报告的是 -32000 一类的哨兵坐标，落盘会让下次启动跑到屏幕外。
        if self.is_minimized:
            return
        if not intersects_visible_area(
            x,
            y,
            max(self.settings.window_width or WINDOW_WIDTH, MIN_WINDOW_WIDTH),
            max(self.settings.window_height or WINDOW_HEIGHT, MIN_WINDOW_HEIGHT),
            work_areas(),
        ):
            return
        self.settings.window_x = x
        self.settings.window_y = y

    def save_settings(self) -> None:
        self.settings.direction = self.direction.value
        with self._settings_lock:
            self.settings_store.save(self.settings)

    def set_hotkey_status(self, shortcut: str, error: str | None = None) -> None:
        self.settings.hotkey = shortcut
        self.hotkey_error = error
        if self.window.events.loaded.is_set():
            self.window.evaluate_js(f"window.desktranslate.setHotkey({json.dumps(shortcut)}, {json.dumps(error)})")

    def dispatch(self, callback: Callable[[], None]) -> None:
        if not self.is_exiting:
            callback()

    def toggle_visibility(self) -> None:
        if self.is_minimized:
            self.show()
        elif self.is_visible:
            self.hide()
        else:
            self.show()

    def minimize(self) -> None:
        """最小化到任务栏：窗口仍然存在，不触发隐藏到托盘的可见性变化。"""
        if self.is_exiting:
            return
        self.subtitles.stop()
        self.save_settings()
        from System.Windows.Forms import FormWindowState

        self._on_ui(lambda: setattr(self.window.native, "WindowState", FormWindowState.Minimized))
        self.is_minimized = True

    def show(self) -> None:
        if self.is_exiting:
            return
        was_hidden = not self.is_visible or self.is_minimized
        if self.is_minimized:
            from System.Windows.Forms import FormWindowState

            # 从任务栏恢复被最小化的窗口时先回到正常状态，否则仍然只剩任务栏按钮。
            self._on_ui(lambda: setattr(self.window.native, "WindowState", FormWindowState.Normal))
            self.is_minimized = False
        self.window.show()
        self.is_visible = True
        if self.on_visibility_changed:
            self.on_visibility_changed(True)
        if self.window.events.loaded.is_set():
            self.window.evaluate_js("window.desktranslate.focus();" + (" window.desktranslate.subtitleStopped()" if was_hidden else ""))

    def hide(self) -> None:
        if self.is_exiting:
            return
        self.subtitles.stop()
        self.save_settings()
        self.window.hide()
        self.is_visible = False
        if self.on_visibility_changed:
            self.on_visibility_changed(False)

    def translate_clipboard(self) -> None:
        self.show()
        text = self.api.read_clipboard().strip()
        # json.dumps 保留剪贴板文本边界，不能把内容拼成可执行脚本。
        self.window.evaluate_js(f"window.desktranslate.setInput({json.dumps(text)}, true)")

    def _on_closing(self) -> bool:
        if self.is_exiting:
            return True
        if self.settings.close_action == "exit":
            # 用户在设置里选择「直接退出」：放行关闭，主循环随后结束。
            self.save_settings()
            self.is_exiting = True
            self.stopped.set()
            return True
        # FormClosing 在界面线程同步触发；先取消关闭，再由消息队列隐藏。
        from System import Action

        self.window.native.BeginInvoke(Action(self.hide))
        return False

    def _on_closed(self) -> None:
        self.subtitles.stop()
        self.is_exiting = True
        self.stopped.set()

    def request_exit(self) -> None:
        if self.is_exiting:
            return
        self.subtitles.stop()
        self.save_settings()
        self.is_exiting = True
        self.stopped.set()
        self.close_settings()
        self.window.destroy()

    def run(self, on_started: Callable[[], None] | None = None, *, debug: bool = False) -> None:
        icon = app_icon_path()
        # private_mode 默认 True：每次启动都新建临时 WebView2 profile，浏览器进程、
        # GPU 缓存和已编译的脚本缓存全部重来，窗口出现后那段灰底也就拖得更久。
        # 固定 profile 目录后二次启动直接复用，首次仍要初始化一次。
        storage: Path | None = self.settings_store.path.parent / "webview"
        try:
            storage.mkdir(parents=True, exist_ok=True)
        except OSError:
            storage = None
        webview.start(
            on_started, gui="edgechromium", debug=debug, icon=str(icon) if icon else None,
            private_mode=storage is None,
            storage_path=str(storage) if storage is not None else None,
        )
