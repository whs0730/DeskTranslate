from __future__ import annotations

import queue
import re
import tkinter as tk
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

from PIL import Image, ImageTk

from .config import AppSettings, SettingsStore
from .resources import app_icon_path
from .translator import (
    LanguageDirection,
    SmartTranslator,
    TranslationError,
    TranslationNoResultError,
    TranslationResult,
)
from .windows import apply_rounded_corners


COLORS = {
    "window": "#F4F4F5",
    "surface": "#FAFAFA",
    "surface_hover": "#EFEFF1",
    "border": "#E4E4E7",
    "text": "#18181B",
    "muted": "#71717A",
    "faint": "#A1A1AA",
    "accent": "#202124",
    "accent_hover": "#34363A",
    "success": "#16A34A",
    "danger": "#DC2626",
    "white": "#FFFFFF",
}

WINDOW_WIDTH = 480
WINDOW_HEIGHT = 320
MIN_WINDOW_WIDTH = 400
MIN_WINDOW_HEIGHT = 260
RESIZE_BORDER_SIZE = 5
RESIZE_CORNER_SIZE = 12
MAX_INPUT_CHARACTERS = 3_000
SHORTCUT_DIALOG_MIN_WIDTH = 340
SHORTCUT_DIALOG_MIN_HEIGHT = 174
SHORTCUT_DIALOG_CORNER_SIZE = 18


class HoverLabel(tk.Label):
    def __init__(
        self,
        master: tk.Misc,
        *,
        text: str,
        command: Callable[[], None],
        background: str = COLORS["surface"],
        hover_background: str = COLORS["surface_hover"],
        foreground: str = COLORS["muted"],
        font: tuple[str, int] | tuple[str, int, str] = ("Microsoft YaHei UI", 9),
        padx: int = 7,
        pady: int = 4,
    ) -> None:
        super().__init__(
            master,
            text=text,
            bg=background,
            fg=foreground,
            font=font,
            padx=padx,
            pady=pady,
            cursor="hand2",
            borderwidth=0,
        )
        self._command = command
        self._background = background
        self._hover_background = hover_background
        self.bind("<Enter>", lambda _event: self.configure(bg=self._hover_background))
        self.bind("<Leave>", lambda _event: self.configure(bg=self._background))
        self.bind("<Button-1>", lambda _event: self._command())


class RoundButton(tk.Canvas):
    def __init__(
        self,
        master: tk.Misc,
        *,
        text: str,
        command: Callable[[], None],
        size: int = 38,
    ) -> None:
        super().__init__(
            master,
            width=size,
            height=size,
            bg=COLORS["surface"],
            highlightthickness=0,
            borderwidth=0,
            cursor="hand2",
        )
        self._size = size
        self._text = text
        self._command = command
        self._busy = False
        self._hovered = False
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        fill = COLORS["accent_hover"] if self._hovered else COLORS["accent"]
        if self._busy:
            fill = "#A1A1AA"
        pad = 2
        self.create_oval(pad, pad, self._size - pad, self._size - pad, fill=fill, outline="")
        self.create_text(
            self._size / 2,
            self._size / 2 - 1,
            text="…" if self._busy else self._text,
            fill=COLORS["white"],
            font=("Segoe UI Symbol", 16, "bold"),
        )

    def _on_enter(self, _event: tk.Event) -> None:
        self._hovered = True
        self._draw()

    def _on_leave(self, _event: tk.Event) -> None:
        self._hovered = False
        self._draw()

    def _on_click(self, _event: tk.Event) -> None:
        if not self._busy:
            self._command()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.configure(cursor="arrow" if busy else "hand2")
        self._draw()


@dataclass(frozen=True)
class _WorkerMessage:
    request_id: int
    future: Future[TranslationResult]


class ShortcutDialog(tk.Toplevel):
    def __init__(
        self,
        master: "TranslationWindow",
        current: str,
        on_save: Callable[[str], tuple[bool, str]],
    ) -> None:
        super().__init__(master)
        self.on_save = on_save
        self._corner_resize_pointer_x = 0
        self._corner_resize_pointer_y = 0
        self._corner_resize_origin_x = 0
        self._corner_resize_origin_y = 0
        self._corner_resize_width = SHORTCUT_DIALOG_MIN_WIDTH
        self._corner_resize_height = SHORTCUT_DIALOG_MIN_HEIGHT
        self._corner_resize_edges = "se"
        self._corner_resize_handles: list[tk.Frame] = []
        self.title("设置召唤快捷键")
        self.geometry(
            f"{SHORTCUT_DIALOG_MIN_WIDTH}x{SHORTCUT_DIALOG_MIN_HEIGHT}"
        )
        self.resizable(True, True)
        self.configure(bg=COLORS["surface"])
        self.transient(master)

        tk.Label(
            self,
            text="全局召唤快捷键",
            bg=COLORS["surface"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(anchor="w", padx=22, pady=(18, 4))
        tk.Label(
            self,
            text="点击输入框后按组合键，或直接输入（例如 Ctrl+Alt+T）",
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="w", padx=22)

        self.value = tk.StringVar(value=current)
        entry = tk.Entry(
            self,
            textvariable=self.value,
            bg=COLORS["white"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            relief="solid",
            borderwidth=1,
            font=("Segoe UI", 11),
        )
        entry.pack(fill="x", padx=22, pady=(10, 4), ipady=5)
        entry.bind("<KeyPress>", self._capture)
        entry.select_range(0, "end")
        entry.focus_set()

        self.error_label = tk.Label(
            self,
            text="",
            bg=COLORS["surface"],
            fg=COLORS["danger"],
            font=("Microsoft YaHei UI", 8),
        )
        self.error_label.pack(anchor="w", padx=22)

        buttons = tk.Frame(self, bg=COLORS["surface"])
        buttons.pack(side="bottom", fill="x", padx=18, pady=(4, 12))
        HoverLabel(buttons, text="取消", command=self.destroy).pack(side="right")
        HoverLabel(
            buttons,
            text="保存",
            command=self._save,
            background=COLORS["accent"],
            hover_background=COLORS["accent_hover"],
            foreground=COLORS["white"],
            padx=13,
        ).pack(side="right", padx=6)

        self.update_idletasks()
        dialog_width = max(SHORTCUT_DIALOG_MIN_WIDTH, self.winfo_reqwidth())
        dialog_height = max(SHORTCUT_DIALOG_MIN_HEIGHT, self.winfo_reqheight())
        self.geometry(f"{dialog_width}x{dialog_height}")
        self.minsize(dialog_width, dialog_height)
        self._create_corner_resize_handles()

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()

    def _create_corner_resize_handles(self) -> None:
        specs = (
            ("nw", "size_nw_se", {"x": 0, "y": 0}),
            ("ne", "size_ne_sw", {"relx": 1, "x": -SHORTCUT_DIALOG_CORNER_SIZE, "y": 0}),
            ("sw", "size_ne_sw", {"x": 0, "rely": 1, "y": -SHORTCUT_DIALOG_CORNER_SIZE}),
            (
                "se",
                "size_nw_se",
                {
                    "relx": 1,
                    "rely": 1,
                    "x": -SHORTCUT_DIALOG_CORNER_SIZE,
                    "y": -SHORTCUT_DIALOG_CORNER_SIZE,
                },
            ),
        )
        for edges, cursor, placement in specs:
            handle = tk.Frame(
                self,
                bg=COLORS["surface"],
                cursor=cursor,
                width=SHORTCUT_DIALOG_CORNER_SIZE,
                height=SHORTCUT_DIALOG_CORNER_SIZE,
            )
            handle.place(
                width=SHORTCUT_DIALOG_CORNER_SIZE,
                height=SHORTCUT_DIALOG_CORNER_SIZE,
                **placement,
            )
            handle.bind(
                "<ButtonPress-1>",
                lambda event, active_edges=edges: self._start_corner_resize(
                    event, active_edges
                ),
            )
            handle.bind("<B1-Motion>", self._resize_from_corner)
            handle.lift()
            self._corner_resize_handles.append(handle)

    def _start_corner_resize(self, event: tk.Event, edges: str) -> None:
        self._corner_resize_pointer_x = event.x_root
        self._corner_resize_pointer_y = event.y_root
        self._corner_resize_origin_x = self.winfo_x()
        self._corner_resize_origin_y = self.winfo_y()
        self._corner_resize_width = self.winfo_width()
        self._corner_resize_height = self.winfo_height()
        self._corner_resize_edges = edges

    def _resize_from_corner(self, event: tk.Event) -> None:
        delta_x = event.x_root - self._corner_resize_pointer_x
        delta_y = event.y_root - self._corner_resize_pointer_y
        min_width, min_height = self.minsize()
        x = self._corner_resize_origin_x
        y = self._corner_resize_origin_y
        width = self._corner_resize_width
        height = self._corner_resize_height

        if "e" in self._corner_resize_edges:
            width = max(min_width, self._corner_resize_width + delta_x)
        if "s" in self._corner_resize_edges:
            height = max(min_height, self._corner_resize_height + delta_y)
        if "w" in self._corner_resize_edges:
            width = max(min_width, self._corner_resize_width - delta_x)
            x = self._corner_resize_origin_x + self._corner_resize_width - width
        if "n" in self._corner_resize_edges:
            height = max(min_height, self._corner_resize_height - delta_y)
            y = self._corner_resize_origin_y + self._corner_resize_height - height

        self.geometry(f"{width}x{height}{x:+d}{y:+d}")

    def _capture(self, event: tk.Event) -> str:
        if event.keysym in {
            "Control_L", "Control_R", "Alt_L", "Alt_R",
            "Shift_L", "Shift_R",
        }:
            return "break"
        key = event.keysym.upper()
        if not (
            (len(key) == 1 and key in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
            or (key.startswith("F") and key[1:].isdigit())
        ):
            return "break"

        modifiers: list[str] = []
        if event.state & 0x0004:
            modifiers.append("Ctrl")
        if event.state & 0x0008 or event.state & 0x20000:
            modifiers.append("Alt")
        if event.state & 0x0001:
            modifiers.append("Shift")
        if modifiers:
            self.value.set("+".join([*modifiers, key]))
            self.error_label.configure(text="")
        return "break"

    def _save(self) -> None:
        success, message = self.on_save(self.value.get())
        if success:
            self.destroy()
        else:
            self.error_label.configure(text=message)


class TranslationWindow(tk.Tk):
    def __init__(
        self,
        translator: SmartTranslator,
        settings_store: SettingsStore,
    ) -> None:
        super().__init__(className="DeskTranslate")
        self.translator = translator
        self.settings_store = settings_store
        self.settings = settings_store.load()
        self.direction = self._load_direction(self.settings.direction)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="translate")
        self.worker_messages: queue.Queue[_WorkerMessage] = queue.Queue()
        self.request_id = 0
        self.is_exiting = False
        self.is_visible = True
        self.on_visibility_changed: Callable[[bool], None] | None = None
        self.on_hotkey_change: Callable[[str], tuple[bool, str]] | None = None
        self._shortcut_dialog: ShortcutDialog | None = None
        self._drag_x = 0
        self._drag_y = 0
        self._resize_pointer_x = 0
        self._resize_pointer_y = 0
        self._resize_origin_x = 0
        self._resize_origin_y = 0
        self._resize_width = WINDOW_WIDTH
        self._resize_height = WINDOW_HEIGHT
        self._resize_edges = "se"
        self._resize_handles: list[tk.Frame] = []
        self._last_rounded_size: tuple[int, int] | None = None

        self.title("DeskTranslate")
        self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.minsize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self.configure(bg=COLORS["window"])
        self.overrideredirect(True)
        self.protocol("WM_DELETE_WINDOW", self.hide)
        self._apply_window_icon(self)

        self._build_ui()
        self._restore_position()
        self.update_idletasks()
        apply_rounded_corners(self)
        self.bind("<Configure>", self._on_configure)
        self.bind("<Escape>", lambda _event: self.hide())
        self.after(80, self._poll_worker_messages)
        self.after(80, self._focus_input)

    @staticmethod
    def _load_direction(value: str) -> LanguageDirection:
        lookup = {
            "auto": LanguageDirection.AUTO,
            "en_to_zh": LanguageDirection.EN_TO_ZH,
            "zh_to_en": LanguageDirection.ZH_TO_EN,
        }
        return lookup.get(value, LanguageDirection.AUTO)

    def _build_ui(self) -> None:
        outer = tk.Frame(
            self,
            bg=COLORS["surface"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        outer.pack(fill="both", expand=True)
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(6, weight=1, minsize=24)

        self.header = tk.Frame(outer, bg=COLORS["surface"], height=40)
        self.header.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 0))
        self.header.pack_propagate(False)
        for widget in (self.header,):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._drag_window)

        self._header_icon: ImageTk.PhotoImage | None = None
        icon_path = app_icon_path()
        if icon_path is not None:
            try:
                with Image.open(icon_path) as source:
                    image = source.convert("RGBA").resize(
                        (24, 24),
                        Image.Resampling.LANCZOS,
                    )
                self._header_icon = ImageTk.PhotoImage(image)
            except OSError:
                self._header_icon = None

        logo_options: dict[str, object] = {
            "bg": COLORS["surface"],
            "font": ("Microsoft YaHei UI", 11, "bold"),
        }
        if self._header_icon is not None:
            logo_options["image"] = self._header_icon
        else:
            logo_options.update(
                text="译",
                bg=COLORS["accent"],
                fg=COLORS["white"],
                width=2,
                height=1,
            )
        logo = tk.Label(self.header, **logo_options)
        logo.pack(side="left", padx=(2, 8), pady=4)

        title = tk.Label(
            self.header,
            text="DeskTranslate",
            bg=COLORS["surface"],
            fg=COLORS["text"],
            font=("Segoe UI", 10, "bold"),
        )
        title.pack(side="left")
        title.bind("<ButtonPress-1>", self._start_drag)
        title.bind("<B1-Motion>", self._drag_window)

        self.provider_label = tk.Label(
            self.header,
            text="●  在线",
            bg=COLORS["surface"],
            fg=COLORS["success"],
            font=("Microsoft YaHei UI", 8),
        )
        self.provider_label.pack(side="left", padx=8)

        close = HoverLabel(
            self.header,
            text="×",
            command=self.hide,
            font=("Segoe UI", 14),
            padx=9,
            pady=2,
        )
        close.pack(side="right", padx=(2, 0), pady=3)
        minimize = HoverLabel(
            self.header,
            text="—",
            command=self.hide,
            font=("Segoe UI", 11),
            padx=9,
            pady=3,
        )
        minimize.pack(side="right", pady=3)
        shortcut = HoverLabel(
            self.header,
            text="快捷键",
            command=self.open_shortcut_settings,
            font=("Microsoft YaHei UI", 8),
            padx=7,
            pady=3,
        )
        shortcut.pack(side="right", padx=2, pady=3)

        direction_row = tk.Frame(outer, bg=COLORS["surface"])
        direction_row.grid(row=1, column=0, sticky="ew", padx=20, pady=(1, 0))
        self.direction_button = HoverLabel(
            direction_row,
            text="自动检测",
            command=self._show_direction_menu,
            background=COLORS["surface_hover"],
            hover_background="#E4E4E7",
            foreground=COLORS["text"],
            font=("Microsoft YaHei UI", 9),
            padx=9,
            pady=3,
        )
        self.direction_button.pack(side="left")
        swap = HoverLabel(
            direction_row,
            text="⇄",
            command=self._swap_direction,
            foreground=COLORS["muted"],
            font=("Segoe UI Symbol", 13),
            padx=8,
            pady=2,
        )
        swap.pack(side="left", padx=3)
        self.direction_menu = tk.Menu(
            self,
            tearoff=False,
            font=("Microsoft YaHei UI", 9),
            bg=COLORS["white"],
            fg=COLORS["text"],
            activebackground=COLORS["surface_hover"],
            activeforeground=COLORS["text"],
            relief="flat",
            borderwidth=1,
        )
        self.direction_menu.add_command(
            label="自动检测",
            command=lambda: self._set_direction(LanguageDirection.AUTO),
        )
        self.direction_menu.add_command(
            label="英文 → 中文",
            command=lambda: self._set_direction(LanguageDirection.EN_TO_ZH),
        )
        self.direction_menu.add_command(
            label="中文 → 英文",
            command=lambda: self._set_direction(LanguageDirection.ZH_TO_EN),
        )

        input_frame = tk.Frame(outer, bg=COLORS["surface"])
        input_frame.grid(row=2, column=0, sticky="ew", padx=20)
        self.input_text = tk.Text(
            input_frame,
            height=3,
            wrap="word",
            bg=COLORS["surface"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            selectbackground="#D4D4D8",
            selectforeground=COLORS["text"],
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            font=("Microsoft YaHei UI", 11),
            undo=True,
            padx=1,
            pady=4,
        )
        self.input_text.pack(fill="x")
        self.input_text.bind("<<Modified>>", self._on_input_modified)
        self.input_text.bind("<Return>", self._translate_from_enter)
        self.input_text.bind("<KP_Enter>", self._translate_from_enter)
        self.input_text.bind("<Control-Return>", self._insert_newline)
        self.input_text.bind("<Control-KP_Enter>", self._insert_newline)

        action_row = tk.Frame(outer, bg=COLORS["surface"])
        action_row.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 2))
        paste = HoverLabel(action_row, text="粘贴", command=self.paste_from_clipboard)
        paste.pack(side="left")
        clear = HoverLabel(action_row, text="清空", command=self.clear)
        clear.pack(side="left", padx=2)
        self.counter_label = tk.Label(
            action_row,
            text="0 / 3000",
            bg=COLORS["surface"],
            fg=COLORS["faint"],
            font=("Segoe UI", 8),
        )
        self.counter_label.pack(side="left", padx=8)
        self.translate_button = RoundButton(
            action_row,
            text="↑",
            command=self.translate,
            size=34,
        )
        self.translate_button.pack(side="right")

        separator = tk.Frame(outer, bg=COLORS["border"], height=1)
        separator.grid(row=4, column=0, sticky="ew", padx=20)

        self.result_header = tk.Frame(outer, bg=COLORS["surface"])
        self.result_header.grid(row=5, column=0, sticky="ew", padx=20, pady=(5, 0))
        self.result_direction_label = tk.Label(
            self.result_header,
            text="翻译结果",
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 8, "bold"),
        )
        self.result_direction_label.pack(side="left")
        copy_button = HoverLabel(
            self.result_header,
            text="复制",
            command=self.copy_result,
            padx=7,
            pady=2,
        )
        copy_button.pack(side="right")

        self.result_text = tk.Text(
            outer,
            height=3,
            wrap="word",
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            selectbackground="#D4D4D8",
            selectforeground=COLORS["text"],
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            font=("Microsoft YaHei UI", 10),
            padx=20,
            pady=4,
            cursor="arrow",
        )
        self.result_text.grid(row=6, column=0, sticky="nsew")
        self.result_text.insert("1.0", "翻译结果会显示在这里")
        self.result_text.configure(state="disabled")

        self.footer = tk.Frame(outer, bg=COLORS["surface"])
        self.footer.grid(row=7, column=0, sticky="ew", padx=20, pady=(0, 6))
        self.status_label = tk.Label(
            self.footer,
            text="在线翻译",
            bg=COLORS["surface"],
            fg=COLORS["faint"],
            font=("Microsoft YaHei UI", 8),
        )
        self.status_label.pack(side="left")
        self.hotkey_hint = tk.Label(
            self.footer,
            text=f"{self.settings.hotkey} 召唤",
            bg=COLORS["surface"],
            fg=COLORS["faint"],
            font=("Microsoft YaHei UI", 8),
        )
        self.hotkey_hint.pack(side="right")

        self._create_resize_handles()
        self._update_direction_label()

    def _create_resize_handles(self) -> None:
        """Create always-available resize hit areas around the border."""
        specs = (
            ("n", "size_ns", {"x": 0, "y": 0, "relwidth": 1, "height": RESIZE_BORDER_SIZE}),
            ("s", "size_ns", {"x": 0, "rely": 1, "y": -RESIZE_BORDER_SIZE, "relwidth": 1, "height": RESIZE_BORDER_SIZE}),
            ("w", "size_we", {"x": 0, "y": 0, "width": RESIZE_BORDER_SIZE, "relheight": 1}),
            ("e", "size_we", {"relx": 1, "x": -RESIZE_BORDER_SIZE, "y": 0, "width": RESIZE_BORDER_SIZE, "relheight": 1}),
            ("nw", "size_nw_se", {"x": 0, "y": 0, "width": RESIZE_CORNER_SIZE, "height": RESIZE_CORNER_SIZE}),
            ("ne", "size_ne_sw", {"relx": 1, "x": -RESIZE_CORNER_SIZE, "y": 0, "width": RESIZE_CORNER_SIZE, "height": RESIZE_CORNER_SIZE}),
            ("sw", "size_ne_sw", {"x": 0, "rely": 1, "y": -RESIZE_CORNER_SIZE, "width": RESIZE_CORNER_SIZE, "height": RESIZE_CORNER_SIZE}),
            ("se", "size_nw_se", {"relx": 1, "rely": 1, "x": -RESIZE_CORNER_SIZE, "y": -RESIZE_CORNER_SIZE, "width": RESIZE_CORNER_SIZE, "height": RESIZE_CORNER_SIZE}),
        )
        for edges, cursor, placement in specs:
            handle = tk.Frame(
                self,
                bg=COLORS["surface"],
                cursor=cursor,
                borderwidth=0,
            )
            handle.place(**placement)
            handle.bind(
                "<ButtonPress-1>",
                lambda event, active_edges=edges: self._start_resize(event, active_edges),
            )
            handle.bind("<B1-Motion>", self._resize_window)
            self._resize_handles.append(handle)
        self._raise_resize_handles()

    def _raise_resize_handles(self) -> None:
        for handle in self._resize_handles:
            if handle.winfo_exists():
                handle.lift()

    def open_shortcut_settings(self) -> None:
        if self.on_hotkey_change is None:
            self.status_label.configure(text="快捷键服务尚未启动", fg=COLORS["danger"])
            return
        if self._shortcut_dialog is not None and self._shortcut_dialog.winfo_exists():
            self._shortcut_dialog.lift()
            return
        self._shortcut_dialog = ShortcutDialog(
            self,
            self.settings.hotkey,
            self.on_hotkey_change,
        )
        self._apply_window_icon(self._shortcut_dialog)

    @staticmethod
    def _apply_window_icon(window: tk.Misc) -> None:
        icon_path = app_icon_path()
        if icon_path is None:
            return
        try:
            window.iconbitmap(default=str(icon_path))
        except tk.TclError:
            return

    def set_hotkey_status(self, shortcut: str, error: str | None = None) -> None:
        self.hotkey_hint.configure(text=f"{shortcut} 召唤")
        if error:
            self.status_label.configure(text=error, fg=COLORS["danger"])

    def save_settings(self) -> None:
        self._save_settings()

    def _restore_position(self) -> None:
        self.update_idletasks()
        if self.settings.window_x is not None and self.settings.window_y is not None:
            x = self.settings.window_x
            y = self.settings.window_y
        else:
            screen_width = self.winfo_screenwidth()
            screen_height = self.winfo_screenheight()
            x = max((screen_width - WINDOW_WIDTH) // 2, 0)
            y = max((screen_height - WINDOW_HEIGHT) // 3, 0)
        width = max(self.settings.window_width or WINDOW_WIDTH, MIN_WINDOW_WIDTH)
        height = max(self.settings.window_height or WINDOW_HEIGHT, MIN_WINDOW_HEIGHT)
        self.geometry(TranslationWindow._format_geometry(width, height, x, y))

    def _save_settings(self) -> None:
        if self.winfo_exists():
            self.settings.window_x = self.winfo_x()
            self.settings.window_y = self.winfo_y()
            self.settings.window_width = self.winfo_width()
            self.settings.window_height = self.winfo_height()
        direction_values = {
            LanguageDirection.AUTO: "auto",
            LanguageDirection.EN_TO_ZH: "en_to_zh",
            LanguageDirection.ZH_TO_EN: "zh_to_en",
        }
        self.settings.direction = direction_values[self.direction]
        self.settings_store.save(self.settings)

    def _on_configure(self, _event: tk.Event) -> None:
        size = (self.winfo_width(), self.winfo_height())
        if size != self._last_rounded_size:
            self._last_rounded_size = size
            self.after_idle(lambda: apply_rounded_corners(self))
        self.after_idle(self._raise_resize_handles)

    def _start_drag(self, event: tk.Event) -> None:
        self._drag_x = event.x_root - self.winfo_x()
        self._drag_y = event.y_root - self.winfo_y()

    def _drag_window(self, event: tk.Event) -> None:
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self.geometry(f"{x:+d}{y:+d}")

    @staticmethod
    def _format_geometry(width: int, height: int, x: int, y: int) -> str:
        return f"{width}x{height}{x:+d}{y:+d}"

    def _start_resize(self, event: tk.Event, edges: str = "se") -> None:
        self._resize_pointer_x = event.x_root
        self._resize_pointer_y = event.y_root
        self._resize_origin_x = self.winfo_x()
        self._resize_origin_y = self.winfo_y()
        self._resize_width = self.winfo_width()
        self._resize_height = self.winfo_height()
        self._resize_edges = edges

    def _resize_window(self, event: tk.Event) -> None:
        delta_x = event.x_root - self._resize_pointer_x
        delta_y = event.y_root - self._resize_pointer_y
        x = self._resize_origin_x
        y = self._resize_origin_y
        width = self._resize_width
        height = self._resize_height

        if "e" in self._resize_edges:
            width = max(MIN_WINDOW_WIDTH, self._resize_width + delta_x)
        if "s" in self._resize_edges:
            height = max(MIN_WINDOW_HEIGHT, self._resize_height + delta_y)
        if "w" in self._resize_edges:
            width = max(MIN_WINDOW_WIDTH, self._resize_width - delta_x)
            x = self._resize_origin_x + self._resize_width - width
        if "n" in self._resize_edges:
            height = max(MIN_WINDOW_HEIGHT, self._resize_height - delta_y)
            y = self._resize_origin_y + self._resize_height - height

        self.geometry(TranslationWindow._format_geometry(width, height, x, y))

    def _translate_from_enter(self, _event: tk.Event) -> str:
        self.translate()
        return "break"

    def _insert_newline(self, _event: tk.Event) -> str:
        self.input_text.insert("insert", "\n")
        return "break"

    def _focus_input(self) -> None:
        if self.is_visible and self.winfo_exists():
            self.input_text.focus_set()

    def _show_direction_menu(self) -> None:
        x = self.direction_button.winfo_rootx()
        y = self.direction_button.winfo_rooty() + self.direction_button.winfo_height() + 3
        try:
            self.direction_menu.tk_popup(x, y)
        finally:
            self.direction_menu.grab_release()

    def _set_direction(self, direction: LanguageDirection) -> None:
        self.direction = direction
        self._update_direction_label()

    def _swap_direction(self) -> None:
        if self.direction == LanguageDirection.EN_TO_ZH:
            self.direction = LanguageDirection.ZH_TO_EN
        elif self.direction == LanguageDirection.ZH_TO_EN:
            self.direction = LanguageDirection.EN_TO_ZH
        else:
            text = self._get_input()
            self.direction = (
                LanguageDirection.EN_TO_ZH
                if self._looks_chinese(text)
                else LanguageDirection.ZH_TO_EN
            )
        self._update_direction_label()

    @staticmethod
    def _looks_chinese(text: str) -> bool:
        return bool(re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", text))

    def _effective_direction_text(self) -> str:
        if self.direction == LanguageDirection.EN_TO_ZH:
            return "英文 → 中文"
        if self.direction == LanguageDirection.ZH_TO_EN:
            return "中文 → 英文"
        if not self._get_input().strip():
            return "自动检测"
        return "自动 · 中文 → 英文" if self._looks_chinese(self._get_input()) else "自动 · 英文 → 中文"

    def _update_direction_label(self) -> None:
        self.direction_button.configure(text=self._effective_direction_text())

    def _on_input_modified(self, _event: tk.Event) -> None:
        if not self.input_text.edit_modified():
            return
        self.input_text.edit_modified(False)
        length = len(self._get_input())
        self.counter_label.configure(
            text=f"{length} / {MAX_INPUT_CHARACTERS}",
            fg=COLORS["danger"] if length > MAX_INPUT_CHARACTERS else COLORS["faint"],
        )
        if self.direction == LanguageDirection.AUTO:
            self._update_direction_label()

    def _get_input(self) -> str:
        return self.input_text.get("1.0", "end-1c")

    def _set_input(self, text: str) -> None:
        self.input_text.delete("1.0", "end")
        self.input_text.insert("1.0", text)
        self.input_text.edit_modified(True)

    def _set_result(self, text: str, *, color: str = COLORS["text"]) -> None:
        self.result_text.configure(state="normal", fg=color)
        self.result_text.delete("1.0", "end")
        self.result_text.insert("1.0", text)
        self.result_text.configure(state="disabled")

    def translate(self) -> None:
        text = self._get_input().strip()
        if not text:
            self.status_label.configure(text="请先输入要翻译的内容", fg=COLORS["danger"])
            self.input_text.focus_set()
            return
        if len(text) > MAX_INPUT_CHARACTERS:
            self.status_label.configure(
                text=f"内容过长，请控制在 {MAX_INPUT_CHARACTERS} 字以内",
                fg=COLORS["danger"],
            )
            return

        self.request_id += 1
        request_id = self.request_id
        self.translate_button.set_busy(True)
        self.status_label.configure(text="正在翻译…", fg=COLORS["muted"])
        self.provider_label.configure(text="●  翻译中", fg="#D97706")
        future = self.executor.submit(self.translator.translate, text, self.direction)
        future.add_done_callback(
            lambda completed: self.worker_messages.put(_WorkerMessage(request_id, completed))
        )

    def _poll_worker_messages(self) -> None:
        if self.is_exiting:
            return
        try:
            while True:
                message = self.worker_messages.get_nowait()
                if message.request_id == self.request_id:
                    self._finish_translation(message.future)
        except queue.Empty:
            pass
        self.after(80, self._poll_worker_messages)

    def _finish_translation(self, future: Future[TranslationResult]) -> None:
        self.translate_button.set_busy(False)
        self.provider_label.configure(text="●  在线", fg=COLORS["success"])
        try:
            result = future.result()
        except TranslationNoResultError as exc:
            self._set_result(f"未找到译文\n{exc}", color=COLORS["danger"])
            self.status_label.configure(text="请检查英文拼写或换一种表达", fg=COLORS["danger"])
            return
        except TranslationError as exc:
            self._set_result(f"翻译失败\n{exc}", color=COLORS["danger"])
            self.status_label.configure(text="请检查网络后重试", fg=COLORS["danger"])
            return
        except Exception:
            self._set_result("翻译失败\n发生了未预期的错误", color=COLORS["danger"])
            self.status_label.configure(text="请稍后重试", fg=COLORS["danger"])
            return

        self._set_result(result.text)
        source = "中文" if result.source_language.lower().startswith("zh") else "英文"
        target = "中文" if result.target_language.lower().startswith("zh") else "英文"
        self.result_direction_label.configure(text=f"{source} → {target}")
        if result.corrected_source:
            original = self._get_input().strip()
            self.status_label.configure(
                text=f"已纠正拼写：{original} → {result.corrected_source}",
                fg=COLORS["success"],
            )
        else:
            self.status_label.configure(text="翻译完成 · 结果可直接复制", fg=COLORS["faint"])

    def paste_from_clipboard(self) -> None:
        try:
            text = self.clipboard_get()
        except tk.TclError:
            self.status_label.configure(text="剪贴板中没有文本", fg=COLORS["danger"])
            return
        self._set_input(str(text).strip())
        self.input_text.focus_set()

    def translate_clipboard(self) -> None:
        self.show()
        self.paste_from_clipboard()
        if self._get_input().strip():
            self.translate()

    def copy_result(self) -> None:
        result = self.result_text.get("1.0", "end-1c").strip()
        if not result or result == "翻译结果会显示在这里" or result.startswith("翻译失败"):
            self.status_label.configure(text="当前没有可复制的结果", fg=COLORS["danger"])
            return
        self.clipboard_clear()
        self.clipboard_append(result)
        self.update_idletasks()
        self.status_label.configure(text="已复制到剪贴板", fg=COLORS["success"])

    def clear(self) -> None:
        self._set_input("")
        self._set_result("翻译结果会显示在这里", color=COLORS["muted"])
        self.result_direction_label.configure(text="翻译结果")
        self.status_label.configure(text="在线翻译", fg=COLORS["faint"])
        self.input_text.focus_set()

    def toggle_visibility(self) -> None:
        if self.is_visible:
            self.hide()
        else:
            self.show()

    def show(self) -> None:
        if self.is_exiting:
            return
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(120, lambda: self.attributes("-topmost", False))
        self.is_visible = True
        apply_rounded_corners(self)
        if self.on_visibility_changed:
            self.on_visibility_changed(True)
        self.after(140, self._focus_input)

    def hide(self) -> None:
        if self.is_exiting:
            return
        self._save_settings()
        self.withdraw()
        self.is_visible = False
        if self.on_visibility_changed:
            self.on_visibility_changed(False)

    def request_exit(self) -> None:
        if self.is_exiting:
            return
        self.is_exiting = True
        self._save_settings()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()
