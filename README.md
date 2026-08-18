<p align="center">
  <img src="assets/DeskTranslate-icon.png" alt="DeskTranslate 图标" width="128">
</p>

<h1 align="center">DeskTranslate</h1>

<p align="center">
  一个面向 Windows 的轻量英汉桌面翻译工具
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-0.2.4-5b3df5" alt="Version 0.2.4">
  <img src="https://img.shields.io/badge/platform-Windows-0078d4" alt="Windows">
  <img src="https://img.shields.io/badge/license-MIT-22a06b" alt="MIT License">
</p>

DeskTranslate 启动后显示紧凑的翻译窗口。关闭窗口不会结束进程，程序会继续驻留系统托盘，适合随时唤出进行短句和单词查询。

## 下载

前往 [Releases](https://github.com/whs0730/DeskTranslate/releases/latest) 下载最新的 `DeskTranslate.exe`。该文件可以直接运行，无需安装 Python。

> 当前发布文件尚未进行代码签名。Windows 首次运行时可能显示 SmartScreen 提示，请确认文件来自本仓库后再运行。

## 当前功能

- 自动判断中文或英文，并选择对应翻译方向
- 英文原样返回时自动检查拼写并重新翻译
- 可手动固定“英文 → 中文”或“中文 → 英文”
- `Enter` 快速翻译，`Ctrl + Enter` 插入换行
- 默认使用 `Ctrl + Alt + T` 全局召唤，可在窗口内自定义
- 从剪贴板粘贴、复制翻译结果
- 托盘显示/隐藏窗口、翻译剪贴板、退出
- 后台网络请求，不阻塞界面
- 单实例运行；重复启动会唤回已有窗口
- 窗口四条边和四个角均可自由拖拽缩放，并记住尺寸和位置
- 记住上次翻译方向和快捷键
- 可打包为独立的 `DeskTranslate.exe`

## 使用方式

1. 输入中文或英文。
2. 按 `Enter` 或点击翻译箭头；需要换行时按 `Ctrl + Enter`。
3. 点击“复制”取得翻译结果。
4. 点击窗口右上角关闭按钮后，程序会继续驻留托盘。
5. 右键托盘图标可以显示或隐藏窗口、翻译剪贴板内容或真正退出程序。
6. 点击标题栏“快捷键”可以录入新的全局召唤组合键。

## 从源码运行

需要 Windows 和 Python 3.10 或更高版本。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
$env:PYTHONPATH = "$PWD\src"
.\.venv\Scripts\python.exe run.py
```

也可以在依赖已经安装后运行：

```powershell
.\scripts\run.ps1
```

## 构建 Windows EXE

```powershell
.\scripts\build.ps1
```

构建脚本会先运行测试，再生成：

```text
dist\DeskTranslate.exe
```

构建脚本会自动安装开发依赖、运行测试并调用 PyInstaller。构建成功后，目标电脑无需预装 Python。

## 翻译服务与隐私

默认使用 [MyMemory REST API](https://mymemory.translated.net/doc/spec.php)，无需 API Key，但需要联网。若英文被原样返回，会调用 [LanguageTool 公共检查接口](https://dev.languagetool.org/public-http-api.html) 尝试纠正拼写后再翻译。输入内容可能通过 HTTPS 发送给这两个服务，因此不要翻译密码、密钥或其他敏感内容。公共接口有长度和每日额度限制，本工具更适合短句和快速查询。

## 项目结构

```text
src/desktranslate/
  app.py              程序入口和生命周期
  ui.py               小窗口界面及异步翻译交互
  translator.py       语言判断、文本分段和翻译服务
  hotkey.py           可自定义的 Windows 全局快捷键
  tray.py             Windows 托盘菜单
  single_instance.py  单实例与重复启动唤回
  config.py           本地窗口设置
  windows.py          DPI 和圆角窗口适配
tests/                 无网络单元测试
scripts/build.ps1      测试并打包 EXE
```

## 开源许可证

本项目原创代码采用 [MIT License](LICENSE)，版权所有 © 2026 whs0730。

项目使用的第三方依赖和在线翻译服务可能采用各自的许可证或服务条款，请分别遵守对应规定。
