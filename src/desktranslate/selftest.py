"""Self-checks for the packaged subtitle stack.

PyInstaller only reports missing files at runtime, so the build script runs the
frozen executable with ``--self-test-subtitles``.  The check resolves the
bundled model, loads the sherpa-onnx native libraries and decodes a short silent
buffer; any missing file or DLL makes it fail with a non-zero exit code.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPORT_NAME = "DeskTranslate-selftest.json"


def _report_path(output: Path | None) -> Path:
    if output is not None:
        return Path(output)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / REPORT_NAME
    return Path.cwd() / REPORT_NAME


def subtitle_self_test(output: Path | None = None) -> int:
    from .resources import subtitle_model_path
    from .subtitles import MODEL_FILES, create_recognizer

    report: dict[str, object] = {
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "python": sys.version.split()[0],
    }
    ok = True
    try:
        model_dir = subtitle_model_path()
        report["model_dir"] = str(model_dir)
        sizes: dict[str, int | None] = {}
        for name in list(MODEL_FILES.values()) + ["README.md"]:
            path = model_dir / name
            sizes[name] = path.stat().st_size if path.is_file() else None
        report["files"] = sizes
        missing = [name for name in MODEL_FILES.values() if not sizes.get(name)]
        if missing:
            raise FileNotFoundError(f"缺少模型文件：{', '.join(missing)}")

        # 创建识别器会真正加载 sherpa-onnx 的 pyd 与 onnxruntime.dll。
        recognizer = create_recognizer(model_dir)
        report["recognizer"] = True

        # 无模型的纯解码路径同样依赖 native 库，用静音确认解码器可用。
        import numpy as np

        stream = recognizer.create_stream()
        stream.accept_waveform(16000, np.zeros(16000, dtype=np.float32))
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        report["decode"] = True
        report["text"] = recognizer.get_result(stream)
    except Exception as exc:  # 自检要把失败原因写进报告而不是弹窗。
        ok = False
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["ok"] = ok

    path = _report_path(output)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    if sys.stdout is not None:
        print(json.dumps(report, ensure_ascii=False))
    return 0 if ok else 1
