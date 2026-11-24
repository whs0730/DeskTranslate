import { charCount, MAX_INPUT, reverseDirection, sourceHint, type Direction } from './state.js';

import { native, type CloseAction, type NetworkMode, type Provider, type Settings, type SubtitleEvent } from './bridge.js';
import { setLiquidGlass, trackGlassPointer } from './liquid-glass.js';

const input = document.querySelector<HTMLTextAreaElement>('#input')!;
const result = document.querySelector<HTMLElement>('#result')!;
const phonetic = document.querySelector<HTMLElement>('#phonetic')!;
const alternatives = document.querySelector<HTMLElement>('#alternatives')!;
const dictionary = document.querySelector<HTMLElement>('#dictionary')!;
const status = document.querySelector<HTMLElement>('#status')!;
const translateButton = document.querySelector<HTMLButtonElement>('#translate')!;
const copyButton = document.querySelector<HTMLButtonElement>('#copy')!;
const directionButtons = document.querySelectorAll<HTMLButtonElement>('[data-direction]');
let direction: Direction = 'auto';
let requestId = 0;
let resultForCopy = '';
let busy = false;
let networkMode: NetworkMode = 'auto';
let provider: Provider = 'mymemory';
let closeAction: CloseAction = 'tray';
let copyTimer: ReturnType<typeof setTimeout>;
let currentMode: 'translation' | 'subtitles' = 'translation';
let modeChange: Promise<void> = Promise.resolve();
let subtitleSession: number | null = null;
let subtitleStarting = false;
let subtitleRevision = 0;
let subtitleEpoch = 0;
let subtitleClearing = false;
let bufferedSubtitleEvents: SubtitleEvent[] = [];
const subtitleDevice = document.querySelector<HTMLSelectElement>('#subtitle-device')!;
const subtitleToggle = document.querySelector<HTMLButtonElement>('#subtitle-toggle')!;
const subtitleClear = document.querySelector<HTMLButtonElement>('#subtitle-clear')!;
const subtitleLines = document.querySelector<HTMLElement>('#subtitle-lines')!;
const subtitleEmpty = document.querySelector<HTMLElement>('#subtitle-empty')!;
const subtitleMessage = document.querySelector<HTMLElement>('#subtitle-message')!;
const subtitleSegments = new Map<number, HTMLElement>();

// 逐字上屏的节奏。字符多时自动压缩单字时长，避免整行打完比下一帧还慢。
const CHAR_MS = 26;
const REVEAL_BUDGET = 240;

/** 比较用的「正文」：流式识别两帧之间常常只是标点漂移，正文没变才算没变。 */
function captionKey(text: string): string {
  return text.replace(/[\s.,!?;:'"…、，。！？；：]/g, '').toLowerCase();
}

/**
 * 把文本拆成「每字一个 span」的片段，空格留作纯文本节点。
 * 用 span 而不是逐字 substring：`textContent` 立刻就是完整句子，
 * 动画只改 opacity，读屏与验收断言不会读到半句话。
 */
function charSpans(text: string, start: number): { fragment: DocumentFragment; count: number } {
  const fragment = document.createDocumentFragment();
  let index = start;
  for (const char of text) {
    if (/\s/.test(char)) { fragment.append(char); continue; }
    const span = document.createElement('span');
    span.textContent = char;
    span.style.setProperty('--i', String(index++));
    fragment.append(span);
  }
  return { fragment, count: index - start };
}

/** 重放入场动效：先摘掉原子类，强制回流，再按需要挂回去。 */
function replay(el: HTMLElement, className: string): void {
  el.classList.remove('instant', 'swap', 'reveal');
  void el.offsetWidth;
  if (className) el.classList.add(className);
}

/**
 * 原文上屏。三种情况分开处理，绝不「删尾重打」——
 * 那会让文本先变短再变长，看上去就是字幕抖了一下。
 */
function paintSource(el: HTMLElement, next: string): void {
  const shown = el.dataset.shown ?? '';
  if (next === shown) return;
  el.dataset.shown = next;
  if (!shown) {
    const { fragment, count } = charSpans(next, 0);
    el.style.setProperty('--char-ms', `${Math.min(CHAR_MS, Math.round(REVEAL_BUDGET / Math.max(1, count)))}ms`);
    el.replaceChildren(fragment);
    replay(el, '');
    return;
  }
  if (next.startsWith(shown)) {
    // 纯追加：只给新后缀挂动效，已经显示的前缀一个像素都不动。
    el.classList.remove('instant', 'swap');
    const count = el.querySelectorAll('span').length;
    el.append(charSpans(next.slice(shown.length), count).fragment);
    return;
  }
  const key = captionKey(shown);
  if (key && captionKey(next).startsWith(key)) {
    // 只是补标点或改大小写：直接重排，不做逐字动画。
    const { fragment } = charSpans(next, 0);
    el.style.setProperty('--char-ms', '0ms');
    el.replaceChildren(fragment);
    replay(el, 'instant');
    return;
  }
  const { fragment } = charSpans(next, 0);
  el.style.setProperty('--char-ms', '0ms');
  el.replaceChildren(fragment);
  replay(el, 'swap');
}

/** 译文上屏：定稿淡入 / 预览句短淡入，空行首现时从透明浮出。 */
function paintTranslation(el: HTMLElement, next: string, final: boolean): void {
  el.classList.toggle('preview', !final);
  const shown = el.dataset.shown ?? '';
  if (next === shown) return;
  el.dataset.shown = next;
  el.textContent = next;
  replay(el, shown ? 'swap' : 'reveal');
}

function subtitleStatus(text: string, error = false): void {
  document.querySelector('#subtitle-status')!.textContent = text;
  document.querySelector('#subtitle-service-text')!.textContent = text;
  subtitleMessage.textContent = error ? text : '';
  subtitleMessage.hidden = !error;
}

function subtitleStopped(): void {
  subtitleRevision++;
  subtitleSession = null;
  subtitleStarting = false;
  subtitleClearing = false;
  bufferedSubtitleEvents = [];
  subtitleToggle.textContent = '开始字幕';
  subtitleDevice.disabled = false;
  subtitleToggle.disabled = !subtitleDevice.value;
  if (!subtitleSegments.size) subtitleEmpty.querySelector('p')!.textContent = '点击开始显示字幕';
  subtitleStatus('已停止');
}

async function stopSubtitles(): Promise<void> {
  if (subtitleSession === null && !subtitleStarting) return;
  const session = subtitleSession;
  subtitleStopped();
  await native().stop_subtitles(session);
}

async function loadSubtitleDevices(): Promise<void> {
  subtitleDevice.replaceChildren();
  subtitleToggle.disabled = true;
  try {
    const devices = await native().subtitle_devices();
    for (const device of devices) {
      const option = document.createElement('option');
      option.value = String(device.id);
      option.textContent = device.name;
      option.selected = device.default;
      subtitleDevice.append(option);
    }
    if (!devices.length) {
      subtitleStatus('未找到系统播放设备', true);
      return;
    }
    subtitleToggle.disabled = false;
    subtitleStatus('已停止');
  } catch (error) {
    subtitleStatus(error instanceof Error ? error.message : String(error), true);
  }
}

subtitleToggle.addEventListener('click', () => {
  if (subtitleSession !== null) { void stopSubtitles().catch(reportError); return; }
  if (!subtitleDevice.value || subtitleStarting) return;
  const revision = ++subtitleRevision;
  subtitleStarting = true;
  subtitleToggle.disabled = true;
  subtitleDevice.disabled = true;
  subtitleLines.replaceChildren(subtitleEmpty);
  subtitleEmpty.hidden = false;
  subtitleEmpty.querySelector('p')!.textContent = '等待新的字幕';
  subtitleSegments.clear();
  subtitleStatus('正在启动…');
  void native().start_subtitles(Number(subtitleDevice.value)).then((session) => {
    if (revision !== subtitleRevision) { void native().stop_subtitles(session); return; }
    subtitleSession = session;
    subtitleEpoch = 0;
    subtitleStarting = false;
    subtitleToggle.textContent = '停止字幕';
    subtitleToggle.disabled = false;
    void native().activate_subtitles(session).catch((error) => {
      if (subtitleSession !== session) return;
      void stopSubtitles();
      subtitleStatus(error instanceof Error ? error.message : String(error), true);
    });
  }).catch((error) => {
    if (revision !== subtitleRevision) return;
    subtitleStopped();
    subtitleStatus(error instanceof Error ? error.message : String(error), true);
  });
});

subtitleClear.addEventListener('click', () => {
  subtitleLines.replaceChildren(subtitleEmpty);
  subtitleEmpty.hidden = false;
  subtitleSegments.clear();
  const session = subtitleSession;
  subtitleEmpty.querySelector('p')!.textContent = session === null ? '点击开始显示字幕' : '等待新的字幕';
  if (session === null) return;
  subtitleClearing = true;
  bufferedSubtitleEvents = [];
  subtitleClear.disabled = true;
  void native().clear_subtitles(session).then((epoch) => {
    if (subtitleSession !== session) return;
    subtitleEpoch = epoch;
    subtitleClearing = false;
    const buffered = bufferedSubtitleEvents;
    bufferedSubtitleEvents = [];
    buffered.forEach(updateSubtitle);
  }).catch((error) => {
    subtitleClearing = false;
    subtitleStatus(error instanceof Error ? error.message : String(error), true);
  }).finally(() => { subtitleClear.disabled = false; });
});

function updateSubtitle(event: SubtitleEvent): void {
  if (event.session !== subtitleSession) return;
  if (event.kind === 'status') { subtitleStatus(event.text); return; }
  if (event.kind === 'error') { subtitleStopped(); subtitleStatus(event.text, true); return; }
  if (event.segment === undefined) return;
  if (subtitleClearing) { bufferedSubtitleEvents.push(event); return; }
  if ((event.epoch ?? 0) !== subtitleEpoch) return;
  let segment = subtitleSegments.get(event.segment);
  // 预览译文与定稿译文都不能凭空建行：行必须由原文先立起来。
  if (!segment && event.kind !== 'translation' && event.kind !== 'partial_translation') {
    segment = document.createElement('div');
    segment.className = 'subtitle-segment';
    const english = document.createElement('p');
    english.className = 'subtitle-en';
    const chinese = document.createElement('p');
    chinese.className = 'subtitle-zh';
    segment.append(english, chinese);
    subtitleEmpty.hidden = true;
    subtitleLines.append(segment);
    subtitleSegments.set(event.segment, segment);
    if (subtitleSegments.size > 8) {
      const oldest = subtitleSegments.keys().next().value!;
      subtitleSegments.get(oldest)!.remove();
      subtitleSegments.delete(oldest);
    }
  }
  if (!segment) return;
  const english = segment.querySelector<HTMLElement>('.subtitle-en')!;
  const chinese = segment.querySelector<HTMLElement>('.subtitle-zh')!;
  if (event.kind === 'translation') paintTranslation(chinese, event.text, true);
  else if (event.kind === 'partial_translation') {
    // 预览译文只是占位：句末定稿一到就整体换掉，所以定稿之后不再接受预览。
    if (chinese.dataset.final !== '1') paintTranslation(chinese, event.text, false);
  } else {
    paintSource(english, event.text);
    if (event.kind === 'final') chinese.dataset.final = '1';
  }
  subtitleLines.scrollTop = subtitleLines.scrollHeight;
}

function setMode(mode: 'translation' | 'subtitles'): void {
  if (mode === currentMode) return;
  currentMode = mode;
  const subtitles = mode === 'subtitles';
  document.querySelector<HTMLElement>('.app')!.dataset.mode = mode;
  document.querySelector<HTMLElement>('#translation-workspace')!.hidden = subtitles;
  document.querySelector<HTMLElement>('#subtitle-workspace')!.hidden = !subtitles;
  document.querySelector<HTMLElement>('#service')!.hidden = subtitles;
  document.querySelector<HTMLElement>('#subtitle-service')!.hidden = !subtitles;
  document.querySelector<HTMLElement>('#language-pair')!.hidden = subtitles;
  document.querySelector<HTMLElement>('#shortcut')!.hidden = subtitles;
  document.querySelector<HTMLElement>('#subtitle-actions')!.hidden = !subtitles;
  for (const button of document.querySelectorAll<HTMLButtonElement>('[data-mode]')) {
    button.setAttribute('aria-pressed', String(button.dataset.mode === mode));
  }
  modeChange = modeChange.then(() => native().set_window_mode(mode)).catch(reportError);
  if (subtitles) void loadSubtitleDevices();
  else void stopSubtitles().catch(reportError);
}

for (const button of document.querySelectorAll<HTMLButtonElement>('[data-mode]')) {
  button.addEventListener('click', () => setMode(button.dataset.mode as 'translation' | 'subtitles'));
}

function setStatus(text: string, tone: '' | 'error' | 'success' = ''): void {
  status.textContent = text;
  status.title = text;
  status.className = `status ${tone}`;
}
function reportError(error: unknown): void {
  setStatus(error instanceof Error ? error.message : String(error), 'error');
}
function act(action: () => Promise<unknown>): void { void action().catch(reportError); }
function updateInput(): void {
  const count = charCount(input.value);
  const counter = document.querySelector<HTMLElement>('#counter')!;
  counter.textContent = `${count} / ${MAX_INPUT}`;
  counter.classList.toggle('error', count > MAX_INPUT);
  document.querySelector('#source-label span')!.textContent = sourceHint(direction, input.value);
}
function renderDirection(): void {
  for (const button of directionButtons) {
    const active = button.dataset.direction === direction;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  }
  updateInput();
}
async function setDirection(value: Direction): Promise<void> {
  direction = value;
  renderDirection();
  await native().set_direction(value);
}
function setBusy(value: boolean): void {
  busy = value;
  translateButton.disabled = value;
  translateButton.querySelector('span')!.textContent = value ? '翻译中…' : '翻译';
  document.querySelector('#service')!.classList.toggle('busy', value);
  document.querySelector('#service-text')!.textContent = value ? '翻译中' : provider === 'deepseek' ? 'DeepSeek' : '就绪';
  document.querySelector('#result-pane')!.setAttribute('aria-busy', String(value));
}
function renderDictionary(entries?: { pos: string; meanings: string[] }[], alts?: string[]): void {
  const cleanAlts = (alts ?? []).filter((item) => item.trim().length > 0);
  alternatives.textContent = cleanAlts.length ? `其他译法：${cleanAlts.join(' · ')}` : '';
  alternatives.hidden = !cleanAlts.length;

  dictionary.replaceChildren();
  const cleanEntries = (entries ?? []).filter((entry) => entry.pos && entry.meanings?.length);
  for (const entry of cleanEntries) {
    const row = document.createElement('div');
    row.className = 'dict-entry';
    const pos = document.createElement('span');
    pos.className = 'dict-pos';
    pos.textContent = entry.pos;
    const meanings = document.createElement('span');
    meanings.className = 'dict-meanings';
    meanings.textContent = entry.meanings.join('；');
    row.append(pos, meanings);
    dictionary.append(row);
  }
  dictionary.hidden = !cleanEntries.length;
}
function clearResult(): void {
  result.textContent = '翻译结果会显示在这里';
  result.className = 'result empty';
  phonetic.hidden = true;
  phonetic.textContent = '';
  renderDictionary();
  document.querySelector('#result-direction')!.textContent = '';
  resultForCopy = '';
  copyButton.disabled = true;
}
function invalidate(keepResult = false): void {
  // 输入、方向变化或清空后，迟到的网络结果不能覆盖当前内容。
  requestId++;
  setBusy(false);
  // 清空原文时保留已有译文（仍可复制）；改写原文时译文立即失效，避免误读旧结果。
  if (!keepResult) clearResult();
}
async function translate(): Promise<void> {
  const text = input.value.trim();
  if (!text) { setStatus('请先输入要翻译的内容', 'error'); input.focus(); return; }
  if (charCount(text) > MAX_INPUT) { setStatus(`请将内容控制在 ${MAX_INPUT} 字以内`, 'error'); return; }
  const id = ++requestId;
  setBusy(true);
  copyButton.disabled = true;
  setStatus('正在翻译…');
  try {
    const response = await native().translate(text, direction);
    if (id !== requestId) return;
    result.textContent = response.text;
    result.className = response.ok ? 'result' : 'result error';
    phonetic.textContent = response.phonetic ?? '';
    phonetic.hidden = !response.phonetic;
    renderDictionary(response.ok ? response.entries : undefined, response.ok ? response.alternatives : undefined);
    resultForCopy = response.ok ? response.text : '';
    copyButton.disabled = !response.ok;
    document.querySelector('#result-direction')!.textContent = response.ok ? `${response.source_language?.startsWith('zh') ? '中文' : '英文'} → ${response.target_language?.startsWith('zh') ? '中文' : '英文'}` : '';
    if (!response.ok) setStatus(response.message ?? '请稍后重试', 'error');
    else if (response.corrected_source) setStatus(`已纠正拼写：${text} → ${response.corrected_source}`, 'success');
    else if (response.phonetic_unavailable) setStatus('翻译完成 · 音标暂不可用，可稍后重试');
    else setStatus('翻译完成 · 可选中文字或复制全文');
    document.querySelector('.result-scroll')!.scrollTop = 0;
  } catch (error) { if (id === requestId) reportError(error); }
  finally { if (id === requestId) setBusy(false); }
}

input.addEventListener('input', () => { invalidate(input.value.trim() === ''); updateInput(); });
input.addEventListener('keydown', (event) => {
  if (event.isComposing || event.key !== 'Enter') return;
  if (event.ctrlKey) {
    event.preventDefault();
    input.setRangeText('\n', input.selectionStart, input.selectionEnd, 'end');
    input.dispatchEvent(new Event('input'));
  } else if (!event.shiftKey && !event.altKey) {
    event.preventDefault();
    if (!busy) act(translate);
  }
});
translateButton.addEventListener('click', () => act(translate));
for (const button of directionButtons) button.addEventListener('click', () => {
  invalidate(); act(() => setDirection(button.dataset.direction as Direction));
});
document.querySelector('#swap')!.addEventListener('click', () => {
  invalidate(); act(() => setDirection(reverseDirection(direction, input.value)));
});
document.querySelector('#clear')!.addEventListener('click', () => {
  // 「清空」按钮把原文和译文一起清掉；只在输入框里用退格删空时才保留译文。
  invalidate();
  input.value = ''; updateInput();
  setStatus('Enter 翻译，Ctrl + Enter 换行');
  input.focus();
});
document.querySelector('#paste')!.addEventListener('click', () => act(async () => {
  const text = await native().read_clipboard();
  if (!text.trim()) { setStatus('剪贴板中没有文本', 'error'); return; }
  window.desktranslate.setInput(text.trim());
}));
copyButton.addEventListener('click', () => act(async () => {
  await native().copy_result(resultForCopy);
  document.querySelector('#copy-label')!.textContent = '已复制';
  setStatus('已复制译文到剪贴板', 'success');
  clearTimeout(copyTimer);
  copyTimer = setTimeout(() => { document.querySelector('#copy-label')!.textContent = '复制'; }, 1600);
}));
document.querySelector('#minimize')!.addEventListener('click', () => act(() => native().minimize()));
document.querySelector('#close')!.addEventListener('click', () => act(() => (closeAction === 'exit' ? native().quit() : native().hide())));
document.querySelector('#pin')!.addEventListener('click', () => act(async () => {
  const pin = document.querySelector('#pin')!;
  const value = pin.getAttribute('aria-pressed') !== 'true';
  await native().set_on_top(value);
  pin.setAttribute('aria-pressed', String(value));
  pin.setAttribute('title', value ? '取消置顶' : '置顶窗口');
}));
document.querySelector('#titlebar')!.addEventListener('pointerdown', (event) => {
  const pointer = event as PointerEvent;
  if (pointer.button === 0 && !(event.target as HTMLElement).closest('button')) act(() => native().start_drag());
});
for (const edge of document.querySelectorAll<HTMLElement>('[data-resize]')) edge.addEventListener('pointerdown', (event) => {
  if (event.button !== 0) return;
  event.preventDefault();
  act(() => native().start_resize(edge.dataset.resize!));
});

document.querySelector('#settings')!.addEventListener('click', () => act(() => native().open_settings('translation')));
document.querySelector('#shortcut')!.addEventListener('click', () => act(() => native().open_settings('hotkey')));

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') act(() => native().hide());
});

window.desktranslate = {
  updateSubtitle,
  subtitleStopped,
  setInput(text, shouldTranslate = false) {
    setMode('translation');
    invalidate(); input.value = text; updateInput(); input.focus();
    if (shouldTranslate && text.trim()) act(translate);
  },
  focus() { if (!document.querySelector<HTMLElement>('#translation-workspace')!.hidden) input.focus(); },
  applySettings(settings: Settings, saved = false) {
    const changed = provider !== settings.translation_provider || networkMode !== settings.network_mode;
    provider = settings.translation_provider;
    networkMode = settings.network_mode;
    closeAction = settings.close_action;
    if (changed) invalidate();
    else setBusy(busy);
    window.desktranslate.setHotkey(settings.hotkey, settings.hotkey_error ?? undefined);
    document.documentElement.classList.toggle('native-glass', settings.native_glass);
    document.documentElement.classList.toggle('appearance-liquid', settings.appearance === 'liquid');
    document.documentElement.classList.toggle('appearance-transparent', settings.appearance === 'transparent');
    setLiquidGlass(settings.appearance === 'liquid');
    const glassRoot = document.documentElement.style;
    glassRoot.setProperty('--frost-blur', `${(settings.frosted_amount / 100) * 16}px`);
    glassRoot.setProperty('--frost-tint', String(0.3 + (settings.frosted_amount / 100) * 0.35));
    glassRoot.setProperty('--liquid-alpha', String(0.12 + ((100 - settings.liquid_transparency) / 100) * 0.5));
    glassRoot.setProperty('--liquid-color', settings.liquid_color);
    if (saved && !busy) setStatus('设置已保存', 'success');
  },
  setHotkey(shortcut, error) {
    document.querySelector('#hotkey')!.textContent = shortcut.replaceAll('+', ' + ');
    if (error) setStatus(error, 'error');
  },
};
window.addEventListener('pywebviewready', () => act(async () => {
  const settings = await native().get_settings();
  direction = settings.direction; renderDirection();
  window.desktranslate.applySettings(settings);
  input.focus();
}));
// 玻璃表面的指针高光（液态玻璃主题下才真正生效）。
trackGlassPointer();
