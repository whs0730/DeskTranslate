"""Verify that the standalone EXE carries everything subtitles need at runtime.

PyInstaller does not fail the build when a data file or DLL is missing; the
problem only shows up when the user starts subtitles.  Run this after packaging:

    .venv\\Scripts\\python.exe scripts\\check-package.py [path-to-exe]

It inspects the appended PyInstaller archive (CArchive + PYZ) instead of
launching the application, so it also works on a machine without audio devices.
"""

from __future__ import annotations

import sys
from pathlib import Path

MODEL_NAME = "sherpa-onnx-x-asr-480ms-streaming-zipformer-transducer-zh-en-punct-int8-2026-06-05"
MODEL_FILES = [
    "encoder.int8.onnx",
    "decoder.onnx",
    "joiner.int8.onnx",
    "tokens.txt",
    "README.md",
]
BUNDLE_FILES = [
    f"models/{MODEL_NAME}/{name}" for name in MODEL_FILES
] + [
    f"models/{MODEL_NAME}/APACHE-2.0.txt",
    "sherpa_onnx/lib/onnxruntime.dll",
    "sherpa_onnx/lib/sherpa-onnx-c-api.dll",
    "sherpa_onnx/lib/sherpa-onnx-cxx-api.dll",
    "desktranslate/web/index.html",
    "desktranslate/web/settings.html",
    "assets/DeskTranslate.ico",
]
PYZ_MODULES = [
    "desktranslate.subtitles",
    "desktranslate.selftest",
    "desktranslate.resources",
    "sherpa_onnx",
    "pyaudiowpatch",
]


def _normalize(name: str) -> str:
    return name.replace("\\", "/")


def main() -> int:
    executable = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dist") / "DeskTranslate.exe"
    if not executable.is_file():
        print(f"找不到交付 EXE：{executable}（先运行 scripts/build.ps1）")
        return 1

    from PyInstaller.archive.readers import CArchiveReader

    reader = CArchiveReader(str(executable))
    entries = {_normalize(name): meta for name, meta in reader.toc.items()}
    failures: list[str] = []

    print(f"检查 {executable}（{executable.stat().st_size:,} 字节）")

    def require_file(name: str) -> None:
        if name not in entries:
            failures.append(f"缺少文件：{name}")
            return
        size = entries[name][1]
        if size <= 0:
            failures.append(f"文件为空：{name}")
            return
        print(f"  OK  {name}  {size:,} 字节")

    def require_pyd(prefix: str) -> None:
        hits = [key for key in entries if key.startswith(prefix) and key.endswith(".pyd")]
        if not hits:
            failures.append(f"缺少扩展模块：{prefix}*.pyd")
            return
        for hit in hits:
            print(f"  OK  {hit}  {entries[hit][1]:,} 字节")

    for name in BUNDLE_FILES:
        require_file(name)
    require_pyd("sherpa_onnx/lib/_sherpa_onnx.")
    require_pyd("_portaudiowpatch.")

    pyz_names = [name for name, meta in entries.items() if meta[4] == "z"]
    if not pyz_names:
        failures.append("缺少 PYZ 模块归档")
    else:
        modules = set(reader.open_embedded_archive(pyz_names[0]).toc)
        for module in PYZ_MODULES:
            if module in modules:
                print(f"  OK  PYZ  {module}")
            else:
                failures.append(f"缺少模块：{module}")

    if failures:
        print("\n独立 EXE 检查未通过：")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("\n独立 EXE 所需文件齐全。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
