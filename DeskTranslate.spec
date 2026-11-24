# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_dynamic_libs

project_root = Path(SPEC).resolve().parent
model_root = project_root / "models" / "sherpa-onnx-x-asr-480ms-streaming-zipformer-transducer-zh-en-punct-int8-2026-06-05"
model_files = ["encoder.int8.onnx", "decoder.onnx", "joiner.int8.onnx", "tokens.txt", "README.md"]

a = Analysis(
    [str(project_root / "run.py")],
    pathex=[str(project_root / "src")],
    binaries=collect_dynamic_libs("sherpa_onnx"),
    datas=[
        (str(project_root / "DeskTranslate.ico"), "assets"),
        (str(project_root / "src" / "desktranslate" / "web" / "index.html"), "desktranslate/web"),
        (str(project_root / "src" / "desktranslate" / "web" / "settings.html"), "desktranslate/web"),
        (str(project_root / "third_party" / "APACHE-2.0.txt"), "models/" + model_root.name),
    ] + [(str(model_root / name), "models/" + model_root.name) for name in model_files],
    hiddenimports=["pystray._win32", "webview.platforms.edgechromium"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6", "PyQt6", "PyQt5", "qtpy", "tkinter"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="DeskTranslate",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(project_root / "build" / "version_info.txt"),
    icon=str(project_root / "DeskTranslate.ico"),
)

