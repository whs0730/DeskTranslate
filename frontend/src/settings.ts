import { native, type ApiConfig, type ApiConfigsPayload, type Appearance, type CloseAction, type NetworkMode, type Provider, type SettingsTab } from './bridge.js';
import { setLiquidGlass, trackGlassPointer } from './liquid-glass.js';

const form = document.querySelector<HTMLFormElement>('#settings-form')!;
const shortcut = document.querySelector<HTMLInputElement>('#hotkey-input')!;
const provider = document.querySelector<HTMLSelectElement>('#provider')!;
const errorLabel = document.querySelector<HTMLElement>('#settings-error')!;
const statusLabel = document.querySelector<HTMLElement>('#settings-status')!;
const configList = document.querySelector<HTMLElement>('#api-config-list')!;
const configEmpty = document.querySelector<HTMLElement>('#api-config-empty')!;
const addConfigButton = document.querySelector<HTMLButtonElement>('#api-config-add')!;
const editor = document.querySelector<HTMLElement>('#api-config-editor')!;
const editorTitle = document.querySelector<HTMLElement>('#api-config-editor-title')!;
const editorHint = document.querySelector<HTMLElement>('#api-config-editor-hint')!;
const editorName = document.querySelector<HTMLInputElement>('#api-config-name')!;
const editorModel = document.querySelector<HTMLSelectElement>('#api-config-model')!;
const keyInput = document.querySelector<HTMLInputElement>('#api-key')!;
const editorSave = document.querySelector<HTMLButtonElement>('#api-config-save')!;
const editorCancel = document.querySelector<HTMLButtonElement>('#api-config-cancel')!;
const closeAction = document.querySelector<HTMLSelectElement>('#close-action')!;
const appearanceInputs = [...document.querySelectorAll<HTMLInputElement>('input[name="appearance"]')];
const frostedRange = document.querySelector<HTMLInputElement>('#frosted-amount')!;
const frostedValue = document.querySelector<HTMLElement>('#frosted-amount-value')!;
const liquidRange = document.querySelector<HTMLInputElement>('#liquid-transparency')!;
const liquidValue = document.querySelector<HTMLElement>('#liquid-transparency-value')!;
const liquidColor = document.querySelector<HTMLInputElement>('#liquid-color')!;
const transparentRange = document.querySelector<HTMLInputElement>('#transparent-transparency')!;
const transparentValue = document.querySelector<HTMLElement>('#transparent-transparency-value')!;
const frostedTuning = document.querySelector<HTMLElement>('#appearance-tuning-frosted')!;
const liquidTuning = document.querySelector<HTMLElement>('#appearance-tuning-liquid')!;
const transparentTuning = document.querySelector<HTMLElement>('#appearance-tuning-transparent')!;
let settingsUid = '';
let savedAppearance = '';
const appearanceConfirm = document.querySelector<HTMLDialogElement>('#appearance-confirm')!;
let hotkey = '';
let hasApiKey = false;
let apiConfigs: ApiConfig[] = [];
let activeConfigId = '';
const pendingDeletes = new Set<string>();
let editingId: string | null = null;
let statusTimer = 0;

// 设置窗没有系统标题栏：标题条负责拖动，右上角负责关闭，四边四角负责缩放。
// 都交给 Windows 自己跑交互（WM_SYSCOMMAND），不逐帧改窗口几何。
const settingsTitlebar = document.querySelector<HTMLElement>('#settings-titlebar')!;
const settingsClose = document.querySelector<HTMLButtonElement>('#settings-close')!;
const settingsShell = document.querySelector<HTMLElement>('.settings-shell')!;
function focusInitialSurface(): void {
  settingsShell.focus({ preventScroll: true });
}
window.addEventListener('focus', () => {
  // WebView2 获焦时若页面暂无焦点，可能落到首个按钮；兜底转移到内容容器，避免聚焦右上角关闭键。
  if (document.activeElement === document.body || document.activeElement === settingsClose) {
    focusInitialSurface();
  }
});
settingsTitlebar.addEventListener('mousedown', (event) => {
  if (event.button !== 0 || (event.target as HTMLElement).closest('button')) return;
  void native().start_settings_drag();
});
settingsClose.addEventListener('click', () => {
  void requestClose().catch(showError);
});
for (const edge of document.querySelectorAll<HTMLElement>('[data-resize]')) {
  edge.addEventListener('mousedown', (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    void native().start_settings_resize(edge.dataset.resize ?? 'se');
  });
}

// 外部改窗口尺寸要异步一拍才落地（当场改会把桥堵死），所以这里自己等视口到位。
// 到位后再稍等，让 WebView2 处理完 WM_SIZE；隐藏窗口不能依赖动画帧回调。
async function waitForViewport(width: number, height: number): Promise<void> {
  const deadline = Date.now() + 600;
  while (Date.now() < deadline) {
    if (window.innerWidth === width && window.innerHeight === height) break;
    await new Promise((resolve) => window.setTimeout(resolve, 16));
  }
  await new Promise((resolve) => window.setTimeout(resolve, 50));
}

// 记住用户拖出来的内容尺寸：窗口下一次打开照这个视口摆回去。
// 只在 ready 之后记：开窗过程里的中间尺寸记下来会让下次越开越小。
let sizeTimer = 0;
window.addEventListener('resize', () => {
  window.clearTimeout(sizeTimer);
  sizeTimer = window.setTimeout(() => {
    if (!window.desktranslateSettings.ready) return;
    void native().remember_settings_size(window.innerWidth, window.innerHeight);
  }, 200);
});

window.desktranslateSettings = {
  ready: false,
  showTab: (tab) => {
    // 主窗按钮打开本窗口时切到对应分类页；未识别的分类回到翻译选项页。
    const target = tabOf(tab);
    if (target) void navigateTo(target, true);
  },
  requestClose: () => { void requestClose(); },
  applyAppearance: (appearance, frostedAmount, liquidTransparency, color, transparentTransparency) => {
    setAppearance(appearance, frostedAmount, liquidTransparency, color, transparentTransparency);
  },
};


const tabs = [...document.querySelectorAll<HTMLButtonElement>('.settings-tab')];
const panels = tabs.map((tab) => document.getElementById(tab.getAttribute('aria-controls')!)!);

function tabOf(name: SettingsTab): HTMLButtonElement | undefined {
  if (name === 'hotkey') return tabs[1];
  if (name === 'window') return tabs[2];
  if (name === 'appearance') return tabs[3];
  return tabs[0];
}

function setAppearance(appearance: Appearance, frostedAmount = 60, liquidTransparency = 40, color = liquidColor.value, transparentTransparency = Number(transparentRange.value)): void {
  const liquid = appearance === 'liquid';
  document.documentElement.classList.toggle('appearance-liquid', liquid);
  document.documentElement.classList.toggle('appearance-transparent', appearance === 'transparent');
  setLiquidGlass(liquid);
  appearanceInputs.forEach((input) => { input.checked = input.value === appearance; });
  frostedRange.value = String(frostedAmount);
  frostedValue.textContent = `${frostedAmount}%`;
  liquidRange.value = String(liquidTransparency);
  liquidValue.textContent = `${liquidTransparency}%`;
  transparentRange.value = String(transparentTransparency);
  transparentValue.textContent = `${transparentTransparency}%`;
  liquidColor.value = color;
  document.documentElement.style.setProperty('--liquid-color', color);
  frostedTuning.hidden = appearance !== 'frosted';
  liquidTuning.hidden = !liquid;
  transparentTuning.hidden = appearance !== 'transparent';
  applyGlassVariables(frostedAmount, liquidTransparency);
}

// 磨砂度映射成玻璃底色浓度；透明度映射成液态玻璃层的 alpha（越透明 alpha 越低）。
function applyGlassVariables(frostedAmount: number, liquidTransparency: number): void {
  const root = document.documentElement.style;
  root.setProperty('--frost-blur', `${(frostedAmount / 100) * 16}px`);
  root.setProperty('--frost-tint', String(0.3 + (frostedAmount / 100) * 0.35));
  root.setProperty('--liquid-alpha', String(0.12 + ((100 - liquidTransparency) / 100) * 0.5));
}

function activateTab(target: HTMLButtonElement, focus = false): void {
  tabs.forEach((tab, index) => {
    const selected = tab === target;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
    const panel = panels[index]!;
    panel.hidden = !selected;
  });
  if (focus) target.focus();
}

function appearanceValues() {
  return {
    appearance: document.querySelector<HTMLInputElement>('input[name="appearance"]:checked')!.value as Appearance,
    frosted: Number(frostedRange.value), liquid: Number(liquidRange.value),
    color: liquidColor.value, transparent: Number(transparentRange.value),
  };
}

async function saveAppearance(): Promise<void> {
  await previewTask;
  const values = appearanceValues();
  await native().save_appearance(values.appearance, values.frosted, values.liquid, values.color, values.transparent);
  savedAppearance = JSON.stringify(values);
}

async function discardAppearance(): Promise<void> {
  await previewTask;
  await native().discard_appearance_preview(settingsUid);
  const values = JSON.parse(savedAppearance) as ReturnType<typeof appearanceValues>;
  setAppearance(values.appearance, values.frosted, values.liquid, values.color, values.transparent);
}

async function confirmAppearance(): Promise<boolean> {
  if (!savedAppearance || JSON.stringify(appearanceValues()) === savedAppearance) return true;
  if (appearanceConfirm.open || pendingConfirm) return false;
  appearanceConfirm.returnValue = '';
  try {
    appearanceConfirm.showModal();
  } catch {
    return false;
  }
  const choice = await new Promise<string>((resolve) => { pendingConfirm = resolve; });
  try {
    if (choice === 'save') await saveAppearance();
    else if (choice === 'discard') await discardAppearance();
    else return false;
    return true;
  } catch (error) {
    showError(error);
    return false;
  }
}

async function navigateTo(target: HTMLButtonElement, focus = false): Promise<void> {
  if (target !== tabs[3] && !(await confirmAppearance())) return;
  activateTab(target, focus);
}

async function requestClose(): Promise<void> {
  if (!(await confirmAppearance())) return;
  await previewTask;
  await native().discard_appearance_preview(settingsUid);
  await native().close_settings();
}

document.querySelector('#appearance-save')!.addEventListener('click', () => settleConfirm('save'));
document.querySelector('#appearance-discard')!.addEventListener('click', () => settleConfirm('discard'));
document.querySelector('#appearance-continue')!.addEventListener('click', () => settleConfirm('continue'));
// Esc 关掉 dialog：close 事件带着空的 returnValue 到达，按「不离开」处理。
// 弹窗已经重新打开时收到的 close 是上一轮 close() 排队到现在的迟到事件，必须忽略，
// 否则它会把新一次等待用空值结掉（页面随后卡在旧 tab、按钮再也点不动）。
appearanceConfirm.addEventListener('close', () => {
  if (appearanceConfirm.open) return;
  settleConfirm(appearanceConfirm.returnValue);
});

tabs.forEach((tab, index) => {
  tab.addEventListener('click', () => { void navigateTo(tab); });
  tab.addEventListener('keydown', (event) => {
    const step = event.key === 'ArrowDown' || event.key === 'ArrowRight' ? 1
      : event.key === 'ArrowUp' || event.key === 'ArrowLeft' ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    void navigateTo(tabs[(index + step + tabs.length) % tabs.length]!, true);
  });
});

function showError(error: unknown): void {
  statusLabel.textContent = '';
  errorLabel.textContent = error instanceof Error ? error.message : String(error);
}

function flashStatus(text: string): void {
  errorLabel.textContent = '';
  statusLabel.textContent = text;
  window.clearTimeout(statusTimer);
  statusTimer = window.setTimeout(() => { statusLabel.textContent = ''; }, 2600);
}

function activeConfig(): ApiConfig | undefined {
  return apiConfigs.find((config) => config.id === activeConfigId);
}

function activeModel(): string {
  return activeConfig()?.model ?? 'deepseek-flash';
}

function buildConfigRow(config: ApiConfig): HTMLElement {
  const row = document.createElement('div');
  row.className = 'api-config-row';
  row.dataset.id = config.id;
  row.dataset.active = String(config.active);
  row.dataset.editing = String(config.id === editingId);

  const radio = document.createElement('input');
  radio.type = 'radio';
  radio.name = 'api-config';
  radio.value = config.id;
  radio.checked = config.active;
  radio.setAttribute('aria-label', `使用 ${config.name}`);
  radio.addEventListener('change', () => { void selectConfig(config.id); });

  const name = document.createElement('span');
  name.className = 'api-config-name';
  name.textContent = config.name;
  name.title = config.name;

  const meta = document.createElement('span');
  meta.className = 'api-config-meta';
  meta.textContent = config.has_key
    ? `${config.model} · ${config.key_preview}`
    : `${config.model} · 未保存密钥`;

  const actions = document.createElement('div');
  actions.className = 'api-config-actions';

  const edit = document.createElement('button');
  edit.type = 'button';
  edit.className = 'api-config-edit';
  edit.textContent = '编辑';
  edit.title = `编辑 ${config.name}`;
  edit.addEventListener('click', () => { openEditor(config.id); });

  const remove = document.createElement('button');
  remove.type = 'button';
  remove.className = 'api-config-delete';
  remove.textContent = '删除';
  remove.title = `删除 ${config.name}`;
  remove.disabled = apiConfigs.length <= 1;
  remove.addEventListener('click', () => { void deleteConfig(config.id); });

  actions.append(edit, remove);
  row.append(radio, name, meta, actions);
  return row;
}

function positionEditor(): void {
  // 编辑表单要贴在对应配置行下面；新增时放在列表末尾。
  const row = editingId === null
    ? null
    : configList.querySelector<HTMLElement>(`.api-config-row[data-id="${editingId}"]`);
  if (row) row.after(editor);
  else configList.append(editor);
}

function renderConfigs(): void {
  configList.replaceChildren(...apiConfigs.map(buildConfigRow));
  positionEditor();
  configEmpty.hidden = apiConfigs.length > 0;
}

function applyConfigs(payload: ApiConfigsPayload): void {
  apiConfigs = payload.configs.filter((config) => !pendingDeletes.has(config.id));
  activeConfigId = apiConfigs.some((config) => config.id === payload.active_id) ? payload.active_id : (apiConfigs[0]?.id ?? '');
  apiConfigs = apiConfigs.map((config) => ({ ...config, active: config.id === activeConfigId }));
  hasApiKey = activeConfig()?.has_key ?? false;
  if (editingId !== null && !apiConfigs.some((config) => config.id === editingId)) closeEditor();
  renderConfigs();
}

async function refreshConfigs(): Promise<void> {
  const settings = await native().get_settings();
  applyConfigs({
    configs: settings.api_configs,
    active_id: settings.active_api_config,
    has_api_key: settings.has_api_key,
  });
}

function nextConfigName(): string {
  const used = new Set(apiConfigs.map((config) => config.name));
  let index = apiConfigs.length + 1;
  while (used.has(`配置 ${index}`)) index += 1;
  return `配置 ${index}`;
}

function openEditor(configId: string | null): void {
  const config = configId === null ? undefined : apiConfigs.find((item) => item.id === configId);
  editingId = config ? config.id : null;
  errorLabel.textContent = '';
  editorTitle.textContent = config ? '编辑配置' : '新增配置';
  editorHint.textContent = config ? (config.active ? '当前使用中' : '') : '保存后加入列表';
  editorName.value = config ? config.name : nextConfigName();
  editorModel.value = config ? config.model : activeModel();
  keyInput.value = '';
  keyInput.placeholder = config?.has_key ? '已保存密钥，留空保留' : '填写你的 DeepSeek API Key';
  renderConfigs();
  editor.hidden = false;
  editorName.focus();
  editorName.select();
}

function closeEditor(): void {
  editor.hidden = true;
  keyInput.value = '';
  editorHint.textContent = '';
  editingId = null;
  renderConfigs();
}

async function saveEditor(): Promise<void> {
  if (editorSave.disabled) return;
  const name = editorName.value.trim();
  if (!name) {
    showError('请填写配置名称');
    editorName.focus();
    return;
  }
  const creating = editingId === null;
  editorSave.disabled = true;
  try {
    const key = keyInput.value;
    let payload = await native().save_api_config(editingId ?? '', name, editorModel.value, key);
    if (creating && key.trim()) {
      const newest = payload.configs[payload.configs.length - 1];
      if (newest) payload = await native().activate_api_config(newest.id);
    }
    applyConfigs(payload);
    closeEditor();
    flashStatus(creating ? '已新增 API 配置' : '已保存 API 配置');
  } catch (error) {
    showError(error);
  } finally {
    editorSave.disabled = false;
  }
}

async function selectConfig(configId: string): Promise<void> {
  activeConfigId = configId;
  apiConfigs = apiConfigs.map((config) => ({ ...config, active: config.id === configId }));
  hasApiKey = activeConfig()?.has_key ?? false;
  renderConfigs();
}

async function deleteConfig(configId: string): Promise<void> {
  if (apiConfigs.length <= 1) return;
  pendingDeletes.add(configId);
  apiConfigs = apiConfigs.filter((config) => config.id !== configId);
  if (activeConfigId === configId) activeConfigId = apiConfigs[0]!.id;
  apiConfigs = apiConfigs.map((config) => ({ ...config, active: config.id === activeConfigId }));
  hasApiKey = activeConfig()?.has_key ?? false;
  if (editingId === configId) closeEditor();
  else renderConfigs();
  flashStatus('删除将在保存设置后生效');
}

function updateProviderFields(): void {
  document.querySelector<HTMLFieldSetElement>('#deepseek-fields')!.hidden = provider.value !== 'deepseek';
}

addConfigButton.addEventListener('click', () => { openEditor(null); });
editorCancel.addEventListener('click', () => { closeEditor(); });
editorSave.addEventListener('click', () => { void saveEditor(); });
editor.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    void saveEditor();
  } else if (event.key === 'Escape') {
    event.preventDefault();
    closeEditor();
    addConfigButton.focus();
  }
});

provider.addEventListener('change', updateProviderFields);
appearanceInputs.forEach((input) => {
  input.addEventListener('change', () => {
    if (!input.checked) return;
    const appearance = input.value as Appearance;
    setAppearance(appearance, Number(frostedRange.value), Number(liquidRange.value));
    schedulePreview();
  });
});

// 外观确认弹窗只允许一个等待者：按钮和 Esc 都走 settleConfirm，避免上一轮 close() 排队的
// 事件在下一轮 showModal() 之后才派发、把新一次确认用空值提前结掉（那样页面会卡在旧 tab）。
let pendingConfirm: ((choice: string) => void) | null = null;

function settleConfirm(choice: string): void {
  const resolve = pendingConfirm;
  if (!resolve) return;
  pendingConfirm = null;
  appearanceConfirm.returnValue = choice;
  if (appearanceConfirm.open) appearanceConfirm.close(choice);
  resolve(choice);
}

let previewPending = false;
let previewRunning = false;
let previewTask: Promise<void> = Promise.resolve();
async function syncPreview(): Promise<void> {
  previewRunning = true;
  try {
    while (previewPending) {
      previewPending = false;
      const appearance = document.querySelector<HTMLInputElement>('input[name="appearance"]:checked')!.value as Appearance;
      await native().preview_appearance(settingsUid, appearance, Number(frostedRange.value), Number(liquidRange.value), Number(transparentRange.value), liquidColor.value);
    }
  } catch (error) {
    showError(error);
  } finally {
    previewRunning = false;
  }
}

function schedulePreview(): void {
  previewPending = true;
  if (!previewRunning) previewTask = syncPreview();
}
frostedRange.addEventListener('input', () => {
  frostedValue.textContent = `${frostedRange.value}%`;
  applyGlassVariables(Number(frostedRange.value), Number(liquidRange.value));
  schedulePreview();
});
liquidRange.addEventListener('input', () => {
  liquidValue.textContent = `${liquidRange.value}%`;
  applyGlassVariables(Number(frostedRange.value), Number(liquidRange.value));
  schedulePreview();
});
liquidColor.addEventListener('input', () => {
  document.documentElement.style.setProperty('--liquid-color', liquidColor.value);
  schedulePreview();
});
transparentRange.addEventListener('input', () => {
  transparentValue.textContent = `${transparentRange.value}%`;
  schedulePreview();
});
document.querySelector('#cancel-settings')!.addEventListener('click', () => {
  keyInput.value = '';
  void discardAppearance().then(() => native().close_settings()).catch(showError);
});
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  if (appearanceConfirm.open) return;
  if (!editor.hidden) {
    closeEditor();
    addConfigButton.focus();
    return;
  }
  keyInput.value = '';
  void requestClose().catch(showError);
});
shortcut.addEventListener('keydown', (event) => {
  if (event.key === 'Tab' || event.key === 'Escape') return;
  event.preventDefault();
  const key = event.key.toUpperCase();
  if (!/^(?:[A-Z0-9]|F[1-9]|F1[0-2])$/.test(key)) return;
  const modifiers = [event.ctrlKey ? 'Ctrl' : '', event.altKey ? 'Alt' : '', event.shiftKey ? 'Shift' : ''].filter(Boolean);
  if (modifiers.length) shortcut.value = [...modifiers, key].join('+');
});
form.addEventListener('submit', (event) => {
  event.preventDefault();
  const save = form.querySelector<HTMLButtonElement>('button[type="submit"]')!;
  if (save.disabled) return;
  save.disabled = true;
  errorLabel.textContent = '';
  void (async () => {
    const selectedProvider = provider.value as Provider;
    if (selectedProvider === 'deepseek' && !hasApiKey) {
      if (tabs[3]!.getAttribute('aria-selected') !== 'true' || JSON.stringify(appearanceValues()) === savedAppearance) {
        activateTab(tabs[0]!);
      }
      errorLabel.textContent = '请先在「API 配置」中新增并保存 DeepSeek API Key';
      return;
    }
    if (shortcut.value !== hotkey) {
      const response = await native().set_hotkey(shortcut.value);
      if (!response.ok) { errorLabel.textContent = response.message; return; }
      hotkey = response.message;
    }
    await previewTask;
    if (activeConfigId && activeConfigId !== (await native().get_settings()).active_api_config) {
      applyConfigs(await native().activate_api_config(activeConfigId));
    }
    for (const id of pendingDeletes) {
      await native().delete_api_config(id);
      pendingDeletes.delete(id);
    }
    const saved = await native().set_translation_settings(selectedProvider, activeModel(), '', false);
    hasApiKey = saved.has_api_key;
    await refreshConfigs();
    await native().set_network_mode(document.querySelector<HTMLSelectElement>('#network-mode')!.value as NetworkMode);
    await native().set_close_action(closeAction.value as CloseAction);
    await saveAppearance();
    await native().settings_saved();
    flashStatus('设置已保存');
  })().catch(showError).finally(() => { save.disabled = false; });
});
window.addEventListener('pywebviewready', () => {
  void (async () => {
    // 先把窗口摆到记住的内容尺寸。创建时 pywebview 先按带边框摆、再换成无边框，
    // WinForms 换样式会保留客户区，窗口会小一圈；这件事必须在 init 里最早做：
    // 后面 setAppearance 会立刻发预览调用，预览还在桥上来回时改尺寸会把桥堵死。
    const target = await native().fit_settings_window();
    if (target) await waitForViewport(target.width, target.height);
    const settings = await native().get_settings();
    settingsUid = settings.settings_uid;
    hotkey = settings.hotkey;
    shortcut.value = hotkey;
    provider.value = settings.translation_provider;
    applyConfigs({
      configs: settings.api_configs,
      active_id: settings.active_api_config,
      has_api_key: settings.has_api_key,
    });
    document.querySelector<HTMLSelectElement>('#network-mode')!.value = settings.network_mode;
    closeAction.value = settings.close_action;
    document.documentElement.classList.toggle('native-glass', settings.native_glass);
    setAppearance(settings.appearance, settings.frosted_amount, settings.liquid_transparency, settings.liquid_color, settings.transparent_transparency);
    savedAppearance = JSON.stringify(appearanceValues());
    updateProviderFields();
    if (settings.hotkey_error) {
      errorLabel.textContent = settings.hotkey_error;
      activateTab(tabs[1]!);
    } else if (settings.initial_tab !== 'translation') {
      const target = tabOf(settings.initial_tab);
      if (target) activateTab(target);
    }
    // 窗口尺寸在 init 开头就摆好了，这里把初始焦点落在内容容器（避开关闭键），并定下就绪状态。
    focusInitialSurface();
    window.desktranslateSettings.ready = true;
  })().catch(showError).finally(() => { void native().show_settings_window(); });
});

// 设置页同样有玻璃表面（导航栏、保存按钮），指针高光与主窗保持一致。
trackGlassPointer();
