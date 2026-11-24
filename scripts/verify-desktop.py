"""在真实 WebView2 窗口中验证界面，不调用在线翻译服务。"""

from __future__ import annotations

import json
from pathlib import Path
import time
import traceback
import threading
import ctypes
from ctypes import wintypes
from unittest.mock import patch

from desktranslate.config import SettingsStore
from desktranslate.credentials import CredentialStore
from desktranslate.hotkey import normalize_hotkey
from desktranslate.network import NetworkClient
from desktranslate.translator import TranslationResult
from desktranslate.ui import TranslationWindow
from desktranslate.windows import apply_window_material, enable_dpi_awareness


class TestTranslator:
    def __init__(self):
        self.network = NetworkClient()

    def translate(self, text, direction):
        if text == "slow":
            time.sleep(0.4)
        return TranslationResult(
            "Apple" if text == "苹果" else text,
            "zh-CN" if text == "苹果" else "en",
            "en" if text == "苹果" else "zh-CN",
            "UI test",
            phonetic="/ˈæpəl/" if text == "苹果" else None,
        )


class FakeCredentialStore:
    """内存凭据库，替代真实的 Windows 凭据管理器。

    验收脚本会填入并删除一个测试密钥；若直接使用真实凭据库，就会覆盖甚至
    删除用户真实的 DeepSeek Key，因此这里用按 target 隔离的内存字典顶替，
    并通过 patch("desktranslate.ui.CredentialStore", ...) 注入到窗口。
    """

    TARGET = CredentialStore.TARGET

    def __init__(self):
        self.secrets: dict[str, str] = {}
        self.saved: list[str] = []
        self.deleted: list[str] = []

    def read(self, target=None):
        return self.secrets.get(target or self.TARGET)

    def save(self, value, target=None):
        key = target or self.TARGET
        self.secrets[key] = value
        self.saved.append(key)

    def delete(self, target=None):
        key = target or self.TARGET
        self.secrets.pop(key, None)
        self.deleted.append(key)


CHECKS = r"""
(async () => {
  const checks = [];
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const assert = (condition, message) => { if (!condition) throw Error(message); checks.push(message); };
  const input = document.querySelector('#input');
  const result = document.querySelector('#result');
  const clear = () => document.querySelector('#clear').click();
  const waitResult = async expected => {
    for (let n = 0; n < 100; n++) {
      if (result.textContent === expected && !document.querySelector('#translate').disabled) return;
      await sleep(25);
    }
    throw Error('translation did not finish: ' + expected + ', actual: ' + result.textContent);
  };
  await sleep(100);
  window.desktranslate.setInput('苹果');
  input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  await waitResult('Apple');
  assert(!document.querySelector('#phonetic').hidden && document.querySelector('#phonetic').textContent === '/ˈæpəl/', 'phonetics displayed separately');
  document.querySelector('#copy').click();
  await sleep(100);
  assert(await window.pywebview.api.read_clipboard() === 'Apple', 'clipboard contains translation without phonetics');
  clear();
  assert(result.textContent === '翻译结果会显示在这里' && result.classList.contains('empty') && document.querySelector('#copy').disabled, 'clear button also resets the translation');
  window.desktranslate.setInput('苹果');
  input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  await waitResult('Apple');
  input.value = '';
  input.dispatchEvent(new Event('input'));
  assert(result.textContent === 'Apple' && !document.querySelector('#copy').disabled, 'deleting the source keeps the translation');
  window.desktranslate.setInput('apple');
  assert(result.textContent === '翻译结果会显示在这里' && result.classList.contains('empty') && document.querySelector('#copy').disabled, 'editing the source clears the translation');
  input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  await waitResult('apple');
  assert(!document.querySelector('#copy').disabled, 'retranslation restores the result');
  clear();
  window.desktranslate.setInput('slow', true);
  await sleep(80);
  clear();
  await sleep(600);
  assert(result.textContent === '翻译结果会显示在这里' && document.querySelector('#copy').disabled, 'clearing input rejects late network results');
  window.desktranslate.setInput('<img src=x onerror=alert(1)>', true);
  await waitResult('<img src=x onerror=alert(1)>');
  assert(!result.querySelector('img'), 'translation is rendered as text, never HTML');
  clear();
  window.desktranslate.setInput('你好');
  input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true }));
  await sleep(80);
  assert(!document.querySelector('#translate').disabled && result.classList.contains('empty'), 'IME confirmation does not submit translation');
  input.setSelectionRange(2, 2);
  input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true, bubbles: true, cancelable: true }));
  assert(input.value === '你好\n', 'Ctrl+Enter inserts a newline');
  input.value = '🍎'.repeat(3001);
  input.dispatchEvent(new Event('input'));
  document.querySelector('#translate').click();
  assert(document.querySelector('#status').textContent.includes('3000') && !document.querySelector('#translate').disabled, 'Unicode input limit checked before submitting');
  clear();
  window.desktranslate.setInput('保持专注，让表达更轻松。');
  return checks;
})().then(checks => ({ok:true,checks})).catch(error => ({ok:false,error:String(error)}))
"""


def main() -> int:
    enable_dpi_awareness()
    root = Path(__file__).resolve().parents[1]
    output = root / "build" / "ui-migration"
    output.mkdir(parents=True, exist_ok=True)
    (output / "desktop-error.log").unlink(missing_ok=True)
    settings_path = output / "acceptance-settings.json"
    settings_path.unlink(missing_ok=True)
    real_credentials = CredentialStore()
    real_secret_before = real_credentials.read(CredentialStore.TARGET)
    with patch("desktranslate.ui.CredentialStore", FakeCredentialStore):
        window = TranslationWindow(TestTranslator(), SettingsStore(settings_path))
    if not isinstance(window.credentials, FakeCredentialStore):
        raise RuntimeError("验收脚本没替换凭据存储，拒绝在真实凭据上运行")
    window.deepseek = TestTranslator()

    class FakeSubtitleService:
        def __init__(self):
            self.session = 0
            self.starts = 0

        def start(self, device_id):
            self.starts += 1
            self.session += 1
            return self.session

        def activate(self, session):
            pass

        def stop(self, session=None):
            pass

        def clear(self, session):
            return 1

    window.subtitles = FakeSubtitleService()

    opacities = []

    def conceal(form, accent_alpha=None, liquid=False, opacity=1.0, transparent=False):
        result = apply_window_material(form, accent_alpha, liquid, opacity, transparent)
        opacities.append((liquid, float(form.Opacity), transparent))
        form.Opacity = 0
        return result
    failures = []

    def save_hotkey(value):
        normalized, _, _ = normalize_hotkey(value)
        window.settings.hotkey = normalized
        window.save_settings()
        return True, normalized

    window.on_hotkey_change = save_hotkey

    def verify() -> None:
        clipboard = None
        clipboard_captured = False
        try:
            if not window.window.events.loaded.wait(20):
                raise TimeoutError("WebView2 failed to load")
            window.window.title = "DeskTranslate · 界面验收"
            from System.Windows.Forms import Clipboard

            clipboard = window._on_ui(Clipboard.GetDataObject)
            clipboard_captured = True
            completed = threading.Event()
            results = []

            def receive(result):
                results.append(result)
                completed.set()

            window.window.evaluate_js(CHECKS, callback=receive)
            if not completed.wait(20):
                raise TimeoutError("Desktop interaction checks did not finish")
            if not results[0]["ok"]:
                raise AssertionError(results[0]["error"])
            checks = results[0]["checks"]
            mode_check = window.window.evaluate_js("""(() => {
              const input = document.querySelector('#input');
              const result = document.querySelector('#result');
              const before = {input: input.value, result: result.textContent};
              document.querySelector('[data-mode=subtitles]').click();
              const subtitle = {
                translationHidden: document.querySelector('#translation-workspace').hidden,
                subtitleVisible: !document.querySelector('#subtitle-workspace').hidden,
                selected: document.querySelector('[data-mode=subtitles]').getAttribute('aria-pressed'),
                frameCount: document.querySelectorAll('#subtitle-workspace .subtitle-frame').length,
                unavailable: document.querySelector('#subtitle-workspace').textContent.includes('点击开始显示字幕'),
              };
              document.querySelector('[data-mode=translation]').click();
              return {subtitle, restored: !document.querySelector('#translation-workspace').hidden &&
                document.querySelector('#subtitle-workspace').hidden && input.value === before.input && result.textContent === before.result};
            })()""")
            if not all(mode_check["subtitle"].values()) or mode_check["subtitle"]["selected"] != "true" or mode_check["subtitle"]["frameCount"] != 1 or not mode_check["restored"]:
                raise AssertionError(f"Mode switching did not preserve the translation: {mode_check}")
            if window.subtitles.starts:
                raise AssertionError("Changing to subtitle mode started audio capture")
            checks.append("subtitle mode shows one placeholder and preserves translation")
            window.window.evaluate_js("document.querySelector('[data-mode=subtitles]').click(); true")
            for _ in range(60):
                if window.window.evaluate_js("!document.querySelector('#subtitle-toggle').disabled"):
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("Subtitle device list was not loaded")
            window.window.evaluate_js("document.querySelector('#subtitle-toggle').click(); true")
            for _ in range(60):
                if window.window.evaluate_js("document.querySelector('#subtitle-toggle').textContent") == '停止字幕':
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("Subtitle start did not update the controls")
            session = window.subtitles.session
            window._emit_subtitle({"session": session, "kind": "partial", "segment": 0, "text": "Hello"})
            window._emit_subtitle({"session": session, "kind": "final", "segment": 0, "text": "Hello world"})
            window._emit_subtitle({"session": session, "kind": "partial", "segment": 1, "text": "Next line"})
            window._emit_subtitle({"session": session, "kind": "translation", "segment": 0, "text": "你好，世界"})
            lines = window.window.evaluate_js("[...document.querySelectorAll('.subtitle-segment')].map(x => x.textContent)")
            if len(lines) != 2 or '你好，世界' not in lines[0] or 'Next line' not in lines[1]:
                raise AssertionError(f"Subtitles were not matched to the right segment: {lines}")
            window.window.evaluate_js("document.querySelector('#subtitle-clear').click(); true")
            for _ in range(60):
                if window.window.evaluate_js("!document.querySelector('#subtitle-clear').disabled"):
                    break
                time.sleep(0.05)
            window._emit_subtitle({"session": session, "epoch": 0, "kind": "translation", "segment": 0, "text": "old caption"})
            window._emit_subtitle({"session": session, "epoch": 1, "kind": "partial", "segment": 0, "text": "Fresh caption"})
            text = window.window.evaluate_js("document.querySelector('#subtitle-lines').textContent")
            if 'old caption' in text or 'Fresh caption' not in text:
                raise AssertionError(f"Clear did not isolate old subtitle events: {text}")
            checks.append("clearing subtitles rejects pre-clear results without stopping")
            window.window.evaluate_js("document.querySelector('#subtitle-toggle').click(); true")
            window._emit_subtitle({"session": session, "kind": "translation", "segment": 0, "text": "late result"})
            if 'late result' in window.window.evaluate_js("document.querySelector('#subtitle-lines').textContent"):
                raise AssertionError("Stopped subtitle session accepted a late result")
            window.window.evaluate_js("document.querySelector('[data-mode=translation]').click(); true")
            checks.append("subtitle start, stop and late translation are isolated")
            layouts = []
            from System.Drawing import Size

            scale = window.window.native.ClientSize.Width / window.window.evaluate_js("innerWidth")

            def resize(width: int, height: int) -> None:
                def set_size():
                    form = window.window.native
                    form.ClientSize = Size(round(width * scale), round(height * scale))

                window._on_ui(set_size)

            for width, height in ((320, 240), (340, 260), (400, 300), (560, 520), (960, 540), (820, 240)):
                resize(width, height)
                for _ in range(20):
                    time.sleep(0.05)
                    metrics = window.window.evaluate_js("""(() => {
                  const editor=document.querySelector('.editor-pane').getBoundingClientRect();
                  const result=document.querySelector('.result-pane').getBoundingClientRect();
                  return {width:innerWidth,height:innerHeight,overflow:document.documentElement.scrollWidth>innerWidth,
                    input:document.querySelector('#input').clientHeight,result:document.querySelector('.result-scroll').clientHeight,
                    fontSize:parseFloat(getComputedStyle(document.querySelector('#input')).fontSize),
                    controlsFit:[...document.querySelectorAll('main button')].every(button=>{
                      const box=button.getBoundingClientRect();
                      return box.left>=0 && box.top>=0 && box.right<=innerWidth && box.bottom<=innerHeight;
                    }),
                    sideBySide:result.left>editor.right};
                    })()""")
                    if abs(metrics["width"] - width) <= 1 and abs(metrics["height"] - height) <= 1:
                        break
                if abs(metrics["width"] - width) > 1 or abs(metrics["height"] - height) > 1:
                    raise AssertionError(f"Resize did not reach requested content size: {width}x{height}, {metrics}")
                if metrics["overflow"] or not metrics["controlsFit"] or min(metrics["input"], metrics["result"]) < 20:
                    raise AssertionError(f"Layout is clipped: {metrics}")
                if (metrics["width"] >= 800) != metrics["sideBySide"]:
                    raise AssertionError(f"Unexpected layout: {metrics}")
                layouts.append(metrics)
                if width in (320, 960):
                    subtitle_layout = window.window.evaluate_js("""(() => {
                      document.querySelector('[data-mode=subtitles]').click();
                      const frame = document.querySelector('.subtitle-frame').getBoundingClientRect();
                      const controls = [...document.querySelectorAll('.mode-option, .window-controls button')];
                      const fits = controls.every(button => {const r=button.getBoundingClientRect(); return r.left>=0 && r.top>=0 && r.right<=innerWidth && r.bottom<=innerHeight;});
                      const valid = frame.width>0 && frame.height>0 && frame.left>=0 && frame.right<=innerWidth && frame.bottom<=innerHeight && fits;
                      document.querySelector('[data-mode=translation]').click();
                      return valid;
                    })()""")
                    if not subtitle_layout:
                        raise AssertionError(f"Subtitle layout is clipped at {width}x{height}")
            if layouts[3]["fontSize"] <= layouts[0]["fontSize"]:
                raise AssertionError("Font size did not adapt to the smaller window")
            window.window.evaluate_js("document.querySelector('[data-mode=subtitles]').click(); true")
            for _ in range(80):
                minimum = window._on_ui(lambda: window.window.native.MinimumSize.Height)
                if minimum / scale < 150:
                    break
                time.sleep(0.05)
            else:
                raise AssertionError(f"Subtitle minimum height was not reduced: {minimum}")
            resize(320, 100)
            for _ in range(60):
                compact = window.window.evaluate_js("""(() => {
                  const frame = document.querySelector('.subtitle-frame').getBoundingClientRect();
                  const controls = [...document.querySelectorAll('.mode-option, .window-controls button, #settings, #subtitle-device, #subtitle-toggle, #subtitle-clear')];
                  return {width:innerWidth,height:innerHeight, frameHeight:frame.height,
                    fits:controls.map(x=>x.getBoundingClientRect()).every(r => r.width>0 && r.height>0 && r.left>=0 && r.top>=0 && r.right<=innerWidth && r.bottom<=innerHeight),
                    scroll:document.documentElement.scrollWidth>innerWidth};
                })()""")
                if compact["height"] <= 102:
                    break
                time.sleep(0.05)
            if compact["height"] > 102 or compact["frameHeight"] < 18 or not compact["fits"] or compact["scroll"]:
                raise AssertionError(f"Compact subtitle layout is clipped: {compact}")
            window.window.evaluate_js("document.querySelector('[data-mode=translation]').click(); true")
            for _ in range(80):
                if window.window.evaluate_js("innerHeight") >= 240:
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("Translation mode did not restore its minimum height")
            checks.append("subtitle mode shrinks to 100px and translation restores its minimum")
            # 最小化按钮把窗口收进任务栏；关闭按钮仍然隐藏到托盘。
            window.api.minimize()
            for _ in range(40):
                time.sleep(0.05)
                if window.is_minimized:
                    break
            else:
                raise AssertionError("Minimize button did not minimize the window")
            if not window.is_visible:
                raise AssertionError("Minimized window should stay visible in the taskbar")
            state = str(window._on_ui(lambda: window.window.native.WindowState))
            if "Minimized" not in state:
                raise AssertionError(f"Window state is {state}, expected Minimized")
            checks.append("minimize keeps the window in the taskbar")
            window.show()
            for _ in range(40):
                time.sleep(0.05)
                if not window.is_minimized:
                    break
            state = str(window._on_ui(lambda: window.window.native.WindowState))
            if "Normal" not in state:
                raise AssertionError(f"Window did not restore from the taskbar: {state}")
            checks.append("restoring from the taskbar brings the window back")

            def open_settings():
                window.window.evaluate_js("document.querySelector('#settings').click()")
                for _ in range(100):
                    child = window._settings_window
                    if child is not None:
                        break
                    time.sleep(0.05)
                else:
                    raise TimeoutError("Settings window did not open")
                if not child.events.loaded.wait(15):
                    raise TimeoutError("Settings page did not load")
                for _ in range(100):
                    if child.evaluate_js("Boolean(window.desktranslateSettings?.ready)") and window._settings_ready:
                        return child
                    time.sleep(0.05)
                raise TimeoutError("Settings did not initialize")

            def wait_status(child, expected, timeout=8.0):
                for _ in range(int(timeout / 0.05)):
                    if child.events.closed.is_set():
                        raise AssertionError("Settings closed after saving")
                    if expected in child.evaluate_js("document.querySelector('#settings-status').textContent"):
                        return
                    time.sleep(0.05)
                raise AssertionError(f"Settings did not report {expected!r}")

            def submit_settings(child):
                child.evaluate_js("document.querySelector('#settings-status').textContent=''; true")
                child.evaluate_js("document.querySelector('#settings-form').requestSubmit(); true")
                wait_status(child, "设置已保存")

            started = time.monotonic()
            child = open_settings()
            print(json.dumps({"settings_open_seconds": round(time.monotonic() - started, 3)}), flush=True)
            if child.evaluate_js("document.querySelector('#appearance-confirm') === null"):
                raise AssertionError("Appearance confirmation dialog is missing")
            if child.evaluate_js("document.querySelector('#settings-tab-translation').getAttribute('aria-selected') !== 'true'"):
                raise AssertionError("Settings did not open on the translation tab")
            if child.evaluate_js("document.querySelector('#settings-close').tabIndex !== -1 || document.activeElement?.id === 'settings-close'"):
                raise AssertionError("Settings close button unexpectedly holds initial or tab focus")
            checks.append("settings button opens the translation tab")
            checks.append("settings window does not focus the close button on open")
            if window.window.evaluate_js("document.querySelector('.shortcut-caption') !== null"):
                raise AssertionError("Summon button is still rendered")
            if not window.window.evaluate_js("document.querySelector('#shortcut').textContent.trim().length > 0"):
                raise AssertionError("Shortcut hint is missing")
            window.window.evaluate_js("document.querySelector('#shortcut').click()")
            for _ in range(100):
                if child.evaluate_js("document.querySelector('#settings-tab-hotkey').getAttribute('aria-selected') === 'true'"):
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("Shortcut text did not open the hotkey tab")
            checks.append("shortcut text opens the hotkey tab")
            child.evaluate_js("document.querySelector('#hotkey-input').dispatchEvent(new KeyboardEvent('keydown', {key:'J',ctrlKey:true,altKey:true,bubbles:true,cancelable:true}))")
            if child.evaluate_js("document.querySelector('#hotkey-input').value") != "Ctrl+Alt+J":
                raise AssertionError("Shortcut capture failed")
            submit_settings(child)
            if not window.window.evaluate_js("document.querySelector('#hotkey').textContent.includes('J')"):
                raise AssertionError("Main window did not receive the saved shortcut")
            if child.events.closed.is_set():
                raise AssertionError("Saving settings closed the settings window")
            checks.append("independent settings captures and saves shortcut")
            checks.append("saving settings keeps the settings window open")

            child.evaluate_js("document.querySelector('#settings-tab-window').click(); true")
            if child.evaluate_js("document.querySelector('#settings-panel-window').hidden"):
                raise AssertionError("Window tab did not reveal its panel")
            if child.evaluate_js("document.querySelector('#close-action').value") != "tray":
                raise AssertionError("Close action did not default to hiding in the tray")
            child.evaluate_js("const action=document.querySelector('#close-action'); action.value='exit'; action.dispatchEvent(new Event('change')); true")
            if window.api.get_settings()["close_action"] != "tray":
                raise AssertionError("Close action changed before saving")
            submit_settings(child)
            if window.api.get_settings()["close_action"] != "exit":
                raise AssertionError("Close action was not stored in the settings")
            child.evaluate_js("const action=document.querySelector('#close-action'); action.value='tray'; action.dispatchEvent(new Event('change')); true")
            submit_settings(child)
            if window.api.get_settings()["close_action"] != "tray":
                raise AssertionError("Close action did not switch back to the tray")
            checks.append("window tab stores the close button behaviour")

            child.evaluate_js("document.querySelector('#settings-tab-appearance').click(); true")
            if child.evaluate_js("document.querySelector('#settings-panel-appearance').hidden"):
                raise AssertionError("Appearance tab did not reveal its panel")
            if child.evaluate_js("document.querySelector('input[name=\"appearance\"]:checked').value") != "frosted":
                raise AssertionError("Frosted glass is not the default appearance")
            child.evaluate_js("const glass=document.querySelector('input[name=\"appearance\"][value=\"liquid\"]'); glass.checked=true; glass.dispatchEvent(new Event('change')); true")
            for _ in range(60):
                if window.window.evaluate_js("document.documentElement.classList.contains('appearance-liquid')"):
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("Appearance preview did not reach the main window")
            if window.api.get_settings()["appearance"] != "frosted":
                raise AssertionError("Appearance preview changed committed settings")
            if window.settings_store.load().appearance != "frosted":
                raise AssertionError("Preview was persisted before saving")
            submit_settings(child)
            if window.api.get_settings()["appearance"] != "liquid":
                raise AssertionError("Liquid glass appearance was not stored")
            if not child.evaluate_js("document.documentElement.classList.contains('appearance-liquid')"):
                raise AssertionError("Settings window did not switch to the liquid appearance")
            if not window.window.evaluate_js("document.documentElement.classList.contains('appearance-liquid')"):
                raise AssertionError("Main window did not switch to the liquid appearance")
            original_color = window.api.get_settings()["liquid_color"]
            dialog_color = child.evaluate_js("getComputedStyle(document.querySelector('#appearance-confirm')).backgroundColor")
            child.evaluate_js("const color=document.querySelector('#liquid-color'); color.value='#af4255'; color.dispatchEvent(new Event('input')); document.querySelector('#settings-tab-window').click(); true")
            for _ in range(60):
                if child.evaluate_js("document.querySelector('#appearance-confirm').open"):
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("Leaving appearance did not request confirmation")
            if child.evaluate_js("getComputedStyle(document.querySelector('#appearance-confirm')).backgroundColor") == dialog_color:
                raise AssertionError("Appearance dialog did not follow preview color")
            child.evaluate_js("document.querySelector('#appearance-continue').click(); true")
            if child.evaluate_js("document.querySelector('#settings-tab-appearance').getAttribute('aria-selected')") != 'true':
                raise AssertionError("Continue editing left the appearance tab")
            child.evaluate_js("document.querySelector('#settings-tab-window').click(); true")
            for _ in range(60):
                if child.evaluate_js("document.querySelector('#appearance-confirm').open"):
                    break
                time.sleep(0.05)
            child.evaluate_js("document.querySelector('#appearance-discard').click(); true")
            for _ in range(60):
                if child.evaluate_js("document.querySelector('#settings-tab-window').getAttribute('aria-selected')") == 'true':
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("Discard did not continue to the selected tab")
            if window.api.get_settings()["liquid_color"] != original_color or window.window.evaluate_js("getComputedStyle(document.documentElement).getPropertyValue('--liquid-color').trim()") != original_color:
                raise AssertionError("Discard did not restore the saved color")
            child.evaluate_js("document.querySelector('#close-action').value='exit'; document.querySelector('#settings-tab-appearance').click(); const slider=document.querySelector('#liquid-transparency'); slider.value='45'; slider.dispatchEvent(new Event('input')); document.querySelector('#settings-tab-window').click(); true")
            for _ in range(60):
                if child.evaluate_js("document.querySelector('#appearance-confirm').open"):
                    break
                time.sleep(0.05)
            child.evaluate_js("document.querySelector('#appearance-save').click(); true")
            for _ in range(60):
                if window.settings_store.load().liquid_transparency == 45 and child.evaluate_js("document.querySelector('#settings-tab-window').getAttribute('aria-selected')") == 'true':
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("Dialog did not save appearance before navigation")
            if window.api.get_settings()["close_action"] != "tray":
                raise AssertionError("Appearance dialog committed unrelated settings")
            child.evaluate_js("document.querySelector('#close-action').value='tray'; document.querySelector('#settings-tab-appearance').click(); const slider=document.querySelector('#liquid-transparency'); slider.value='40'; slider.dispatchEvent(new Event('input')); true")
            submit_settings(child)
            checks.append("appearance navigation prompts and saves or restores the preview")
            for _ in range(60):
                effect = window.window.evaluate_js("document.querySelector('.direction-tabs').style.getPropertyValue('--hyalite')")
                if effect.startswith('url('):
                    break
                time.sleep(0.05)
            else:
                raise AssertionError(f"Liquid glass filter was not attached: {effect!r}")
            checks.append("liquid glass filter attaches to direction controls")
            filter_info = window.window.evaluate_js("""(() => {
              const element = document.querySelector('.direction-tabs');
              const value = element.style.getPropertyValue('--hyalite');
              const match = value.match(/url\\([\"']?#([^\"')]+)/);
              const filter = match && document.getElementById(match[1]);
              return {value, computed: getComputedStyle(element).backdropFilter,
                filters: filter?.querySelectorAll('feDisplacementMap').length ?? 0,
                images: [...(filter?.querySelectorAll('feImage') ?? [])].map(x => x.getAttribute('href')?.slice(0, 30)),
                allFilters: document.querySelectorAll('filter').length};
            })()""")
            print(json.dumps({"glass_filter_diagnostic": filter_info}), flush=True)
            if not any(liquid and abs(opacity - 0.82) < 0.01 for liquid, opacity, _ in opacities):
                raise AssertionError(f"Liquid window transparency was not applied: {opacities}")
            glass = window.window.evaluate_js("""(() => {
              const app = document.querySelector('.app');
              const style = getComputedStyle(app);
              const noise = getComputedStyle(app, '::before');
              const base = style.backgroundImage.match(/rgba\\(\\s*38,\\s*42,\\s*48,\\s*([0-9.]+)\\s*\\)/);
              return {
                layers: (style.backgroundImage.match(/gradient\\(/g) ?? []).length,
                inset: style.boxShadow.includes('inset'),
                noise: noise.backgroundImage.includes('data:image/svg+xml'),
                panes: ['.editor-pane', '.result-pane'].map(selector => {
                  const pane = getComputedStyle(document.querySelector(selector), '::before');
                  return {background: pane.backgroundImage, blur: pane.backdropFilter};
                }),
                surfaces: [...document.querySelectorAll('[data-glass-surface]')].map((element) =>
                  element.style.getPropertyValue('--hyalite')),
                alpha: parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--liquid-alpha')),
                baseAlpha: base ? parseFloat(base[1]) : 1,
                dpr: window.devicePixelRatio,
              };
            })()""")
            print(json.dumps({"glass_material": glass}), flush=True)
            if glass["layers"] < 4 or not glass["inset"]:
                raise AssertionError(f"Liquid background layers are incomplete: {glass}")
            if not glass["noise"]:
                raise AssertionError("Liquid background has no grain layer for the SVG refraction")
            if glass["panes"][0] != glass["panes"][1] or glass["panes"][0]["blur"] != "none":
                raise AssertionError(f"Editor and result panels have different glass layers: {glass['panes']!r}")
            if glass["alpha"] < 0.11 or glass["baseAlpha"] >= 1:
                raise AssertionError(f"Liquid base is not a translucent layer: {glass}")
            if glass["surfaces"] and any(not value.startswith("url(") for value in glass["surfaces"]):
                raise AssertionError(f"Glass surfaces missed the refraction filter: {glass['surfaces']}")
            if glass["dpr"] <= 0:
                raise AssertionError(f"Device pixel ratio was not reported: {glass['dpr']}")
            checks.append("liquid glass applies native window transparency and layered surfaces")

            window.window.evaluate_js("""(() => {
              const button = document.querySelector('#translate');
              const bounds = button.getBoundingClientRect();
              button.dispatchEvent(new PointerEvent('pointermove', {
                bubbles: true,
                clientX: bounds.left + 8,
                clientY: bounds.top + 6,
              }));
              return true;
            })()""")
            for _ in range(30):
                if window.window.evaluate_js("document.querySelector('#translate').style.getPropertyValue('--gx')"):
                    break
                time.sleep(0.02)
            else:
                raise AssertionError("Pointer glow did not follow the cursor")
            checks.append("glass highlight follows the pointer")

            window.window.evaluate_js("""(() => {
              window.__glassFrames = [];
              let last = performance.now();
              let count = 0;
              const step = (now) => {
                if (count > 0) window.__glassFrames.push(now - last);
                last = now;
                if (++count < 26) requestAnimationFrame(step);
              };
              requestAnimationFrame(step);
              return true;
            })()""")
            time.sleep(0.9)
            perf = window.window.evaluate_js("""(() => {
              const frames = window.__glassFrames ?? [];
              const sorted = [...frames].sort((a, b) => a - b);
              return {
                frames: frames.length,
                avg: frames.length ? frames.reduce((a, b) => a + b, 0) / frames.length : 0,
                p95: sorted.length ? sorted[Math.floor(sorted.length * 0.95)] : 0,
              };
            })()""")
            print(json.dumps({"glass_perf": perf}), flush=True)
            if perf["frames"] < 8 or perf["avg"] > 40:
                raise AssertionError(f"Liquid glass frame budget is too slow: {perf}")
            checks.append("liquid glass keeps a smooth frame budget")
            if child.evaluate_js("document.querySelector('#appearance-tuning-liquid').hidden"):
                raise AssertionError("Liquid glass did not reveal the transparency slider")
            if not child.evaluate_js("document.querySelector('#appearance-tuning-frosted').hidden"):
                raise AssertionError("Frosted tuning stayed visible while liquid is selected")
            child.evaluate_js("const glassSlider=document.querySelector('#liquid-transparency'); glassSlider.value='85'; glassSlider.dispatchEvent(new Event('input')); true")
            if window.api.get_settings()["liquid_transparency"] != 40:
                raise AssertionError("Transparency changed before saving")
            submit_settings(child)
            for _ in range(60):
                if window.api.get_settings()["liquid_transparency"] == 85:
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("Liquid transparency was not stored")
            alpha = child.evaluate_js(
                "getComputedStyle(document.documentElement).getPropertyValue('--liquid-alpha').trim()"
            )
            if alpha in {"", "0.6"}:
                raise AssertionError(f"Liquid transparency did not reach the CSS variable: {alpha!r}")
            if child.evaluate_js("document.querySelector('#liquid-transparency-value').textContent") != "85%":
                raise AssertionError("Transparency slider label did not follow the value")
            if child.evaluate_js("getComputedStyle(document.querySelector('.settings-nav')).backdropFilter") != 'none':
                raise AssertionError("Settings navigation unexpectedly uses a backdrop filter")
            for view, selector, pseudo in (
                (window.window, '.editor-pane', '::before'),
                (window.window, '.result-pane', '::before'),
                (child, '.settings-nav', None),
                (child, '.appearance-card:not(:has(input:checked)):not(:hover)', None),
            ):
                layers = view.evaluate_js(fr"""(() => {{
                  const root = document.documentElement;
                  const previous = root.style.getPropertyValue('--liquid-alpha');
                  const element = document.querySelector({json.dumps(selector)});
                  if (!element) return null;
                  const read = value => {{
                    root.style.setProperty('--liquid-alpha', value);
                    return getComputedStyle(element, {json.dumps(pseudo)}).backgroundImage +
                      getComputedStyle(element, {json.dumps(pseudo)}).backgroundColor;
                  }};
                  const opaque = read('0.62');
                  const transparent = read('0.26');
                  root.style.setProperty('--liquid-alpha', previous);
                  const alpha = text => Number(text.match(/rgba\([^)]*,\s*([\d.]+)\)/)?.[1]);
                  return [alpha(opaque), alpha(transparent)];
                }})()""")
                if not layers or not (0 < layers[1] < layers[0] < 0.4):
                    raise AssertionError(f"Glass panel did not become lighter with transparency: {selector}: {layers}")
            checks.append("glass panels follow transparency in both windows")
            before_color = window.window.evaluate_js("getComputedStyle(document.querySelector('.app')).backgroundImage")
            child.evaluate_js("const color=document.querySelector('#liquid-color'); color.value='#315b78'; color.dispatchEvent(new Event('input')); true")
            for _ in range(60):
                if window.window.evaluate_js("getComputedStyle(document.querySelector('.app')).backgroundImage") != before_color:
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("Color preview did not reach the main window")
            submit_settings(child)
            for _ in range(60):
                color = window.window.evaluate_js("getComputedStyle(document.documentElement).getPropertyValue('--liquid-color').trim()")
                if color == '#315b78':
                    break
                time.sleep(0.05)
            else:
                raise AssertionError(f"Glass color did not reach the main window: {color!r}")
            after_color = window.window.evaluate_js("getComputedStyle(document.querySelector('.app')).backgroundImage")
            if before_color == after_color:
                raise AssertionError("Changing glass color did not change the rendered background")
            checks.append("glass color synchronizes between windows")
            child.evaluate_js("const frosted=document.querySelector('input[name=\"appearance\"][value=\"frosted\"]'); frosted.checked=true; frosted.dispatchEvent(new Event('change')); true")
            submit_settings(child)
            if window.api.get_settings()["appearance"] != "frosted":
                raise AssertionError("Frosted glass appearance was not restored")
            for _ in range(60):
                if not window.window.evaluate_js("document.querySelector('.direction-tabs').style.getPropertyValue('--hyalite')"):
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("Liquid glass filter was not removed after switching to frosted")
            if child.evaluate_js("document.querySelector('#appearance-tuning-liquid').hidden") is not True:
                raise AssertionError("Liquid tuning stayed visible after switching to frosted")
            if child.evaluate_js("document.documentElement.classList.contains('appearance-liquid')"):
                raise AssertionError("Liquid class stayed on after switching to frosted")
            child.evaluate_js("const frostSlider=document.querySelector('#frosted-amount'); frostSlider.value='100'; frostSlider.dispatchEvent(new Event('input')); true")
            submit_settings(child)
            for _ in range(60):
                if window.api.get_settings()["frosted_amount"] == 100:
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("Frosted amount was not stored")
            blur = child.evaluate_js(
                "getComputedStyle(document.documentElement).getPropertyValue('--frost-blur').trim()"
            )
            if blur != "16px":
                raise AssertionError(f"Frosted amount did not reach the CSS variable: {blur!r}")
            child.evaluate_js("const frostSlider=document.querySelector('#frosted-amount'); frostSlider.value='60'; frostSlider.dispatchEvent(new Event('input')); true")
            child.evaluate_js("const glassSlider=document.querySelector('#liquid-transparency'); glassSlider.value='40'; glassSlider.dispatchEvent(new Event('input')); true")
            submit_settings(child)
            tuning = window.api.get_settings()
            if (tuning["frosted_amount"], tuning["liquid_transparency"]) != (60, 40):
                raise AssertionError(f"Appearance tuning was not restored: {tuning}")
            checks.append("liquid glass appearance switches both windows")
            checks.append("appearance sliders tune blur and transparency")

            child.evaluate_js("const option=document.querySelector('input[name=appearance][value=transparent]'); option.checked=true; option.dispatchEvent(new Event('change')); true")
            submit_settings(child)
            for _ in range(60):
                if window.api.get_settings()["appearance"] == "transparent" and window.window.evaluate_js("document.documentElement.classList.contains('appearance-transparent')"):
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("Transparent appearance did not reach the main window")
            if child.evaluate_js("document.querySelector('#appearance-tuning-transparent').hidden"):
                raise AssertionError("Transparent mode slider is hidden")
            child.evaluate_js("const slider=document.querySelector('#transparent-transparency'); slider.value='75'; slider.dispatchEvent(new Event('input')); true")
            if window.api.get_settings()["transparent_transparency"] != 40:
                raise AssertionError("Transparent slider changed before saving")
            submit_settings(child)
            for _ in range(60):
                if window.api.get_settings()["transparent_transparency"] == 75:
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("Transparent mode slider was not applied")
            if window.api.get_settings()["liquid_transparency"] != 40:
                raise AssertionError("Transparent mode changed the liquid opacity")
            for _ in range(60):
                if any(transparent and abs(opacity - 0.6625) < 0.01 for _, opacity, transparent in opacities):
                    break
                time.sleep(0.05)
            else:
                raise AssertionError(f"Transparent window opacity was not applied: {opacities}")
            if window.window.evaluate_js("document.querySelector('.direction-tabs').style.getPropertyValue('--hyalite')"):
                raise AssertionError("Transparent mode retained a liquid filter")
            checks.append("transparent mode adjusts opacity independently without liquid filters")

            child = open_settings()
            child.evaluate_js("document.querySelector('#settings-tab-translation').click(); true")
            child.evaluate_js("const pick=document.querySelector('#provider'); pick.value='deepseek'; pick.dispatchEvent(new Event('change')); true")
            child.evaluate_js("document.querySelector('#settings-form button[type=submit]').click(); true")
            message = ""
            for _ in range(60):
                message = child.evaluate_js("document.querySelector('#settings-error').textContent")
                if "API Key" in message:
                    break
                time.sleep(0.05)
            else:
                raise AssertionError(f"Missing DeepSeek key was not explained: {message!r}")
            checks.append("missing DeepSeek key is explained instead of closing")
            child.evaluate_js("document.querySelector('#api-config-add').click(); true")
            if child.evaluate_js("document.querySelector('#api-config-editor').hidden"):
                raise AssertionError("New API configuration form did not open")
            if not child.evaluate_js("document.querySelector('#api-config-list').lastElementChild?.id === 'api-config-editor'"):
                raise AssertionError("New API configuration form is not appended to the list")
            if not child.evaluate_js("document.querySelector('#api-config-name').value"):
                raise AssertionError("New API configuration form has no default name")
            child.evaluate_js("document.querySelector('#api-config-name').value='验收配置'; document.querySelector('#api-key').value='test-key-not-a-real-secret'; document.querySelector('#api-config-save').click(); true")
            for _ in range(100):
                if child.evaluate_js("document.querySelector('#api-config-editor').hidden"):
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("API configuration form did not close after saving")
            if child.evaluate_js("document.querySelectorAll('.api-config-row').length") != 1:
                raise AssertionError("New API configuration did not appear in the list")
            if "test-key-not-a-real-secret" in child.evaluate_js("document.querySelector('#api-config-list').textContent"):
                raise AssertionError("Configuration list leaked the full key")
            checks.append("API configuration is created in its own form")
            submit_settings(child)
            settings = window.api.get_settings()
            if not settings["has_api_key"] or "test-key-not-a-real-secret" in json.dumps(settings):
                raise AssertionError("Settings API exposed or failed to store the key")
            if window.window.evaluate_js("document.querySelector('#service-text').textContent") != "DeepSeek":
                raise AssertionError("Main window did not receive the provider change")
            checks.append("DeepSeek configuration persists without returning its key")

            child = open_settings()
            child.evaluate_js("document.querySelector('.api-config-edit').click(); true")
            if child.evaluate_js("document.querySelector('#api-key').value"):
                raise AssertionError("Stored key was inserted into the edit form")
            if "已保存" not in child.evaluate_js("document.querySelector('#api-key').placeholder"):
                raise AssertionError("Edit form does not explain that the stored key is kept")
            if not child.evaluate_js("document.querySelector('.api-config-row').nextElementSibling?.id === 'api-config-editor'"):
                raise AssertionError("Edit form did not expand below its configuration row")
            child.evaluate_js("document.querySelector('#api-config-cancel').click(); true")
            if not child.evaluate_js("document.querySelector('#api-config-editor').hidden"):
                raise AssertionError("Cancel did not close the API configuration form")
            checks.append("editing keeps the stored key and never inserts it")
            checks.append("edit form expands below its own configuration row")
            if child.evaluate_js("document.querySelector('#remove-key') !== null"):
                raise AssertionError("Obsolete remove-key option is still rendered")
            checks.append("obsolete remove-key option is gone")
            # 新增第二条配置，再删除持有测试密钥的第一条：删除配置时凭据必须一并清理。
            child.evaluate_js("document.querySelector('#api-config-add').click(); true")
            if child.evaluate_js("document.querySelector('#api-config-editor').hidden"):
                raise AssertionError("Second API configuration form did not open")
            child.evaluate_js("document.querySelector('#api-config-name').value='备用配置'; document.querySelector('#api-key').value='second-key-not-a-real-secret'; document.querySelector('#api-config-save').click(); true")
            for _ in range(100):
                if child.evaluate_js("document.querySelectorAll('.api-config-row').length") == 2:
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("Second API configuration was not added")
            deleted_before = len(window.credentials.deleted)
            child.evaluate_js("document.querySelectorAll('.api-config-delete')[0].click(); document.querySelector('#cancel-settings').click(); true")
            if not child.events.closed.wait(5):
                raise AssertionError("Settings did not close after cancel")
            child = open_settings()
            if child.evaluate_js("document.querySelectorAll('.api-config-row').length") != 2 or len(window.credentials.deleted) != deleted_before:
                raise AssertionError("Cancel did not restore the staged configuration deletion")
            checks.append("cancel restores staged API configuration deletion")
            deleted_before = len(window.credentials.deleted)
            child.evaluate_js("document.querySelectorAll('.api-config-delete')[0].click(); true")
            for _ in range(100):
                if child.evaluate_js("document.querySelectorAll('.api-config-row').length") == 1:
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("Deleting an API configuration failed")
            if len(window.credentials.deleted) != deleted_before:
                raise AssertionError("Deleting a configuration committed before saving")
            submit_settings(child)
            if len(window.credentials.deleted) != deleted_before + 1:
                raise AssertionError("Deleting a configuration did not remove its credential")
            if "second-key-not-a-real-secret" in json.dumps(window.api.get_settings()):
                raise AssertionError("Settings API exposed the new key")
            checks.append("deleting a configuration removes its credential")

            child = open_settings()
            window.open_settings()
            if window._settings_window is not child:
                raise AssertionError("Repeated open created a duplicate settings window")
            child_before = child.evaluate_js("({width:innerWidth,height:innerHeight})")
            resize(320, 240)
            time.sleep(0.1)
            if child.evaluate_js("({width:innerWidth,height:innerHeight})") != child_before:
                raise AssertionError("Settings followed the main window size")
            root_before = window.window.evaluate_js("({width:innerWidth,height:innerHeight})")
            child_scale = child.native.ClientSize.Width / child_before["width"]
            settings_layouts = []
            for width, height in ((360, 320), (520, 640)):
                window._on_ui(lambda: setattr(child.native, "ClientSize", Size(round(width * child_scale), round(height * child_scale))))
                for _ in range(40):
                    time.sleep(0.05)
                    metrics = child.evaluate_js("""(() => {
                      const buttons=[...document.querySelectorAll('.settings-actions button')];
                      return {width:innerWidth,height:innerHeight, fields:document.querySelector('.settings-fields').clientHeight,
                        overflow:document.querySelector('.settings-shell').scrollWidth>innerWidth,
                        actionsVisible:buttons.every(button=>{const box=button.getBoundingClientRect();return box.top>=0&&box.bottom<=innerHeight&&box.left>=0&&box.right<=innerWidth;})};
                    })()""")
                    if abs(metrics["width"]-width)<=1 and abs(metrics["height"]-height)<=1:
                        break
                if abs(metrics["width"]-width)>1 or abs(metrics["height"]-height)>1 or metrics["overflow"] or not metrics["actionsVisible"] or metrics["fields"]<40:
                    raise AssertionError(f"Settings is clipped: {metrics}")
                settings_layouts.append(metrics)
            if window.window.evaluate_js("({width:innerWidth,height:innerHeight})") != root_before:
                raise AssertionError("Resizing settings changed the main window")
            saved_appearance = window.api.get_settings()["appearance"]
            saved_close_action = window.api.get_settings()["close_action"]
            child.evaluate_js("const option=document.querySelector('input[name=appearance][value=liquid]'); option.checked=true; option.dispatchEvent(new Event('change')); const action=document.querySelector('#close-action'); action.value='exit'; action.dispatchEvent(new Event('change')); true")
            window.save_settings()
            if window.settings_store.load().appearance != saved_appearance:
                raise AssertionError("Automatic persistence saved an appearance preview")
            child.evaluate_js("document.querySelector('#cancel-settings').click(); true")
            if not child.events.closed.wait(5) or window.is_exiting:
                raise AssertionError("Closing settings affected the translator")
            if window.settings_store.load().appearance != saved_appearance or window.api.get_settings()["close_action"] != saved_close_action:
                raise AssertionError("Cancel committed the settings preview")
            checks.append("settings and translation resize independently")
            checks.append("settings save and cancel remain visible at the minimum size")
            checks.append("closing settings keeps translation alive")
            child = open_settings()
            reopened = child.evaluate_js("({width:innerWidth,height:innerHeight})")
            if abs(reopened["width"]-settings_layouts[-1]["width"])>2 or abs(reopened["height"]-settings_layouts[-1]["height"])>2:
                raise AssertionError("Settings did not retain its own window size")
            child.evaluate_js("const option=document.querySelector('input[name=appearance][value=liquid]'); option.checked=true; option.dispatchEvent(new Event('change')); true")
            window._on_ui(child.native.Close)
            for _ in range(60):
                if child.evaluate_js("document.querySelector('#appearance-confirm').open"):
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("Native close did not request confirmation")
            child.evaluate_js("document.querySelector('#appearance-discard').click(); true")
            if not child.events.closed.wait(5) or window.is_exiting:
                raise AssertionError("Native settings close affected translation")
            if window.settings_store.load().appearance != saved_appearance:
                raise AssertionError("Native close committed the settings preview")
            checks.append("cancel and native close discard unsaved settings")
            checks.append("settings retains its size and native close leaves translation running")
            resizing = threading.Event()
            window.window.native.ResizeBegin += lambda sender, event: resizing.set()
            hwnd = wintypes.HWND(window.window.native.Handle.ToInt64())
            cancel = threading.Timer(0.25, lambda: ctypes.windll.user32.PostMessageW(hwnd, 0x0100, 0x1B, 0))
            cancel.start()
            window.api.start_resize("se")
            native_resize_started = resizing.wait(1)
            cancel.join()
            if not native_resize_started:
                raise AssertionError("Windows did not enter the native resize loop")
            payload = {"checks": checks, "layouts": layouts, "native_glass": window.native_glass, "native_resize_started": native_resize_started, "settings_layouts": settings_layouts}
            (output / "desktop-checks.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(payload, ensure_ascii=False), flush=True)
            resize(560, 520)
            window.window.evaluate_js("document.querySelector('#clear').click()")
        except Exception:
            failures.append(traceback.format_exc())
            (output / "desktop-error.log").write_text(traceback.format_exc(), encoding="utf-8")
            traceback.print_exc()
        finally:
            try:
                if clipboard_captured:
                    if clipboard is not None:
                        window._on_ui(lambda: Clipboard.SetDataObject(clipboard, True))
                    else:
                        window._on_ui(Clipboard.Clear)
            finally:
                window.request_exit()

    with patch("desktranslate.ui.apply_window_material", side_effect=conceal), patch("desktranslate.ui.list_loopback_devices", return_value=[{"id": 1, "name": "测试播放设备", "default": True}]):
        window.run(verify)

    real_secret_after = real_credentials.read(CredentialStore.TARGET)
    if real_secret_after != real_secret_before:
        failures.append("真实 Windows 凭据被修改：验收脚本必须只在内存凭据库上运行")
    elif not failures:
        if not window.credentials.saved:
            failures.append("凭据隔离未被验证：测试密钥没有写入内存凭据库")
        else:
            print(
                json.dumps(
                    {
                        "credentials": "isolated",
                        "real_credential_untouched": True,
                        "fake_targets_saved": window.credentials.saved,
                        "fake_targets_deleted": window.credentials.deleted,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
