"""System-tray integration for the Tk desktop application."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from PIL import Image, ImageDraw
import pystray

from .resources import app_icon_path


LOGGER = logging.getLogger(__name__)
Callback = Callable[[], None]
Dispatcher = Callable[[Callback], object]


def create_tray_icon(size: int = 64) -> Image.Image:
    """Load the shared application icon, with a generated fallback."""

    if size < 16:
        raise ValueError("Tray icon size must be at least 16 pixels")

    icon_path = app_icon_path()
    if icon_path is not None:
        try:
            with Image.open(icon_path) as source:
                return source.convert("RGBA").resize(
                    (size, size),
                    Image.Resampling.LANCZOS,
                )
        except OSError:
            LOGGER.warning("Could not load application icon: %s", icon_path)

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    scale = size / 64

    def point(value: float) -> int:
        return round(value * scale)

    draw.rounded_rectangle(
        (point(3), point(3), point(61), point(61)),
        radius=point(15),
        fill=(31, 35, 43, 255),
    )

    line_width = max(2, point(5))
    # Top arrow points right; the lower arrow points left.  Polygons rather
    # than a font glyph keep the icon legible on machines without CJK fonts.
    draw.line(
        (point(14), point(23), point(48), point(23)),
        fill=(255, 255, 255, 255),
        width=line_width,
    )
    draw.polygon(
        ((point(48), point(14)), (point(58), point(23)), (point(48), point(32))),
        fill=(255, 255, 255, 255),
    )
    draw.line(
        (point(50), point(41), point(16), point(41)),
        fill=(93, 214, 203, 255),
        width=line_width,
    )
    draw.polygon(
        ((point(16), point(32)), (point(6), point(41)), (point(16), point(50))),
        fill=(93, 214, 203, 255),
    )
    return image


class TrayController:
    """Run a ``pystray`` icon without touching Tk from its worker thread.

    Every user action is handed to ``dispatch``.  A Tk caller should pass
    ``lambda callback: root.after(0, callback)`` so the actual callbacks always
    execute on Tk's main thread.
    """

    def __init__(
        self,
        on_toggle: Callback,
        on_translate_clipboard: Callback,
        on_quit: Callback,
        dispatch: Dispatcher,
        *,
        title: str = "DeskTranslate 桌面翻译",
        name: str = "DeskTranslate",
    ) -> None:
        callbacks = {
            "on_toggle": on_toggle,
            "on_translate_clipboard": on_translate_clipboard,
            "on_quit": on_quit,
            "dispatch": dispatch,
        }
        for callback_name, callback in callbacks.items():
            if not callable(callback):
                raise TypeError(f"{callback_name} must be callable")

        self._on_toggle = on_toggle
        self._on_translate_clipboard = on_translate_clipboard
        self._on_quit = on_quit
        self._dispatch = dispatch
        self._title = title
        self._name = name

        self._state_lock = threading.RLock()
        self._window_visible = True
        self._icon: pystray.Icon | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        with self._state_lock:
            return self._icon is not None

    @property
    def window_visible(self) -> bool:
        with self._state_lock:
            return self._window_visible

    def start(self) -> bool:
        """Start the tray event loop on a daemon thread.

        Returns ``True`` when a new loop was started and ``False`` when it was
        already running.
        """

        with self._state_lock:
            if self._icon is not None:
                return False

            icon = pystray.Icon(
                self._name,
                create_tray_icon(),
                self._title,
                menu=self._build_menu(),
            )
            thread = threading.Thread(
                target=self._run,
                args=(icon,),
                name="DeskTranslateTray",
                daemon=True,
            )
            self._icon = icon
            self._thread = thread
            thread.start()
            return True

    def stop(self, join_timeout: float = 2.0) -> None:
        """Stop the tray loop and wait briefly for its thread to finish."""

        with self._state_lock:
            icon = self._icon
            thread = self._thread
        if icon is None:
            return

        try:
            icon.stop()
        finally:
            if (
                thread is not None
                and thread is not threading.current_thread()
                and thread.is_alive()
            ):
                thread.join(timeout=max(0.0, join_timeout))
            with self._state_lock:
                if self._icon is icon:
                    self._icon = None
                if self._thread is thread:
                    self._thread = None

    def set_window_visible(self, is_visible: bool) -> None:
        """Update the dynamic first menu item to “显示窗口” or “隐藏窗口”."""

        with self._state_lock:
            changed = self._window_visible != bool(is_visible)
            self._window_visible = bool(is_visible)
            icon = self._icon

        if changed and icon is not None:
            try:
                icon.update_menu()
            except Exception:
                # Backends can reject an update during shutdown; the next menu
                # open (or app start) will still read the correct state.
                LOGGER.debug("Could not refresh tray menu", exc_info=True)

    def update_visibility(self, is_visible: bool) -> None:
        """Backward-compatible alias for :meth:`set_window_visible`."""

        self.set_window_visible(is_visible)

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(
                self._visibility_label,
                self._handle_toggle,
                default=True,
            ),
            pystray.MenuItem("翻译剪贴板", self._handle_translate_clipboard),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._handle_quit),
        )

    def _visibility_label(self, _item: pystray.MenuItem) -> str:
        return "隐藏窗口" if self.window_visible else "显示窗口"

    def _handle_toggle(self, _icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        self._schedule(self._on_toggle)

    def _handle_translate_clipboard(
        self,
        _icon: pystray.Icon,
        _item: pystray.MenuItem,
    ) -> None:
        self._schedule(self._on_translate_clipboard)

    def _handle_quit(self, _icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        self._schedule(self._on_quit)

    def _schedule(self, callback: Callback) -> None:
        try:
            self._dispatch(callback)
        except Exception:
            # Dispatch may fail after the Tk root has already been destroyed.
            LOGGER.debug("Could not dispatch tray callback", exc_info=True)

    def _run(self, icon: pystray.Icon) -> None:
        try:
            icon.run()
        except Exception:
            LOGGER.exception("System tray loop stopped unexpectedly")
        finally:
            with self._state_lock:
                if self._icon is icon:
                    self._icon = None
                if self._thread is threading.current_thread():
                    self._thread = None


__all__ = ["TrayController", "create_tray_icon"]
