<p align="center">
  <img src="assets/DeskTranslate-icon.png" alt="DeskTranslate 图标" width="112">
</p>

<h1 align="center">DeskTranslate</h1>

<p align="center"><strong>免费 · 轻量 · 无广告</strong></p>
<p align="center">随手查词，快速翻译。支持 Windows 和 Android。</p>

<p align="center">
  <a href="https://github.com/whs0730/DeskTranslate/releases">
    <img src="https://img.shields.io/badge/Windows-v0.3.0-0078d4" alt="Windows v0.3.0">
  </a>
  <a href="https://github.com/whs0730/DeskTranslate/releases">
    <img src="https://img.shields.io/badge/Android-v0.1-3ddc84" alt="Android v0.1">
  </a>
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20Android-0078d4" alt="支持 Windows 和 Android">
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-22a06b" alt="MIT License">
  </a>
</p>

<p align="center">
  <a href="https://github.com/whs0730/DeskTranslate/releases"><strong>下载 Windows / Android 版</strong></a>
</p>

DeskTranslate 是一款免费的中英翻译与查词工具，界面简洁，没有广告。输入单词可以查看音标、词性与多种释义，输入句子可以快速得到译文。Windows 版支持快捷键唤出和实时字幕，Android 版打开就能用。

默认使用免费的在线翻译服务，免填 API Key。也支持选用自己的 DeepSeek API，调用费用由个人 DeepSeek 账户承担。翻译与查词需要联网。

## 下载与安装

在 [GitHub Releases](https://github.com/whs0730/DeskTranslate/releases) 中展开 **Assets**，选择对应平台的文件。

| 平台    | 版本   | 下载与安装                                                    |
| ------- | ------ | ------------------------------------------------------------- |
| Windows | v0.3.0 | 下载 `.exe` 文件，双击运行                                    |
| Android | v0.1   | 下载 `.apk` 文件，传到手机后打开安装，支持 Android 8.0 及以上 |

Windows 版可直接运行。若提示缺少 WebView2，请安装微软提供的 [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)。Windows 11 通常已预装。

Android 安装时，按系统提示允许浏览器或文件管理器安装此来源的应用。以后更新可下载新版 APK 覆盖安装。

## 可以做什么

- **中英双向翻译** — 自动判断方向，也可以手动选择中译英或英译中。
- **查词看多义** — 单词可显示音标、词性和多条释义，中文短词可查看不同英文表达。实际内容取决于翻译服务及词典收录情况。
- **一键复制** — 复制主译文，方便粘贴到聊天、笔记或文档。
- **玻璃质感界面** — Windows 提供磨砂、液态玻璃与透明外观；Android 支持浅色和深色液态玻璃界面，效果随系统版本而有所不同。
- **免费翻译开箱即用** — 默认使用 MyMemory，随时可以在设置中切换为自己的 DeepSeek API。

### Windows 上随时唤出

按 **Ctrl + Alt + T** 显示或隐藏翻译窗口，快捷键可以在设置中修改。窗口支持置顶、拖动和自由缩放，宽窗口会将原文与译文并排显示。

输入后按 **Enter** 翻译，按 **Ctrl + Enter** 换行。默认点击关闭后继续驻留托盘，右键托盘图标可以翻译剪贴板内容或退出程序，也可以在设置中调整关闭行为。

### Android 上随手翻译

输入或长按粘贴原文，选择方向后点击翻译。原文区的“清空”按钮可以重新开始；编辑原文时会清除旧译文，方便继续查询。

点击右上角齿轮打开设置，可切换翻译服务、管理自己的 DeepSeek API 配置。

## Windows 实时字幕

播放英文视频、课程或会议音频时，可以用实时字幕查看英文识别内容和中文翻译。

1. 切换到“字幕”页。
2. 选择当前使用的扬声器或耳机。
3. 点击“开始字幕”，播放音频。
4. 使用结束后点击“停止字幕”。

英文语音在本机识别，识别出的文字发送给当前翻译服务生成中文。更换耳机或播放设备后，请重新选择设备。所选设备播放的其他声音也可能进入识别。

## 选择翻译服务

**MyMemory** 是默认的免费服务，适合单词和短句。英文查词还会结合有道英汉词典补充中文多义与音标，必要时通过 Free Dictionary API 补充音标。公共服务有使用额度限制，词典查询失败时仍会保留已有主译文。

**DeepSeek** 适合希望使用自己 API 账户的用户。在设置中新增 API 配置，填写 API Key，选择 Flash 或 V4 Pro，激活配置并保存。支持保存多套配置；编辑时密钥留空表示保留原密钥。

## 常见问题

**计费问题**

DeskTranslate 软件免费、开源，无广告。默认翻译使用免费在线服务。选用 DeepSeek 时，API 调用按你自己的账户计费。

**为什么第一次运行 Windows 版会出现安全提示？**

当前 Windows EXE 尚未进行代码签名，可能触发 SmartScreen 提示。请核对下载来源为本仓库的 Releases 页面。

**翻译失败怎么办？**

先检查网络，再尝试翻译较短的文本。使用 DeepSeek 时，检查 API Key、账户余额和当前配置。Windows 用户还可以在设置中检查网络连接方式。

**Android 更新时提示签名不一致怎么办？**

如果此前安装的是开发测试包，切换到正式签名包时可能需要先卸载旧版。卸载会清除本地设置和已保存的 API Key，请提前保留自己的密钥。

## 隐私

原文会通过 HTTPS 发送给你选择的 MyMemory 或 DeepSeek 服务。查词时，英文单词还可能发送给有道词典和 Free Dictionary API；Windows 免费翻译在尝试纠正拼写时可能调用 LanguageTool。相关数据处理遵循各服务的条款。

API Key 在 Windows 上保存到系统凭据管理器，在 Android 上加密保存在应用私有存储中。Windows 字幕在本机识别音频，不保存音频文件，识别文字会发送给所选翻译服务。

## 反馈与开源

遇到问题或有功能建议，欢迎到 [Issues](https://github.com/whs0730/DeskTranslate/issues) 反馈，并附上使用的平台、软件版本和复现步骤。

DeskTranslate 原创代码采用 [MIT License](LICENSE)。第三方依赖、语音模型和在线服务遵循各自的许可证或服务条款。
