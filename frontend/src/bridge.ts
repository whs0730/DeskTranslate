import type { Direction } from './state.js';

export type NetworkMode = 'auto' | 'system' | 'direct';
export type Provider = 'mymemory' | 'deepseek';
export type SettingsTab = 'translation' | 'hotkey' | 'window' | 'appearance';
export type CloseAction = 'tray' | 'exit';
export type Appearance = 'frosted' | 'liquid' | 'transparent';
export interface ApiConfig { id: string; name: string; model: string; has_key: boolean; key_preview: string; active: boolean }
export interface ApiConfigsPayload { configs: ApiConfig[]; active_id: string; has_api_key: boolean }
export interface Settings { direction: Direction; hotkey: string; hotkey_error: string | null; initial_tab: SettingsTab; native_glass: boolean; network_mode: NetworkMode; translation_provider: Provider; deepseek_model: string; has_api_key: boolean; api_configs: ApiConfig[]; active_api_config: string; close_action: CloseAction; appearance: Appearance; frosted_amount: number; liquid_transparency: number; transparent_transparency: number; liquid_color: string; settings_uid: string }
export interface DictionaryEntry { pos: string; meanings: string[] }
interface Translation { ok: boolean; text: string; source_language?: string; target_language?: string; phonetic?: string | null; phonetic_unavailable?: boolean; corrected_source?: string | null; entries?: DictionaryEntry[]; alternatives?: string[]; message?: string }
export interface SubtitleEvent { session: number; kind: 'status' | 'error' | 'partial' | 'final' | 'translation' | 'partial_translation'; text: string; segment?: number; epoch?: number; seq?: number }
interface NativeApi {
  get_settings(): Promise<Settings>;
  translate(text: string, direction: Direction): Promise<Translation>;
  set_direction(direction: Direction): Promise<void>;
  set_network_mode(mode: NetworkMode): Promise<void>;
  set_translation_settings(provider: Provider, model: string, apiKey: string, clearKey: boolean): Promise<{has_api_key: boolean}>;
  save_api_config(configId: string, name: string, model: string, apiKey: string): Promise<ApiConfigsPayload>;
  activate_api_config(configId: string): Promise<ApiConfigsPayload>;
  delete_api_config(configId: string): Promise<ApiConfigsPayload>;
  read_clipboard(): Promise<string>;
  copy_result(text: string): Promise<void>;
  set_hotkey(shortcut: string): Promise<{ok: boolean; message: string}>;
  open_settings(tab?: SettingsTab): Promise<void>;
  close_settings(): Promise<void>;
  settings_saved(): Promise<void>;
  hide(): Promise<void>;
  minimize(): Promise<void>;
  set_close_action(action: CloseAction): Promise<void>;
  set_appearance(appearance: Appearance): Promise<void>;
  set_appearance_options(frostedAmount: number | null, liquidTransparency: number | null, save?: boolean, liquidColor?: string, transparentTransparency?: number): Promise<void>;
  preview_appearance(uid: string, appearance: Appearance, frostedAmount: number, liquidTransparency: number, transparentTransparency: number, color: string): Promise<void>;
  discard_appearance_preview(uid: string): Promise<void>;
  save_appearance(appearance: Appearance, frosted: number, liquid: number, color: string, transparent: number): Promise<void>;
  quit(): Promise<void>;
  set_on_top(onTop: boolean): Promise<void>;
  start_resize(edge: string): Promise<void>;
  set_window_mode(mode: 'translation' | 'subtitles'): Promise<void>;
  subtitle_devices(): Promise<{id: number; name: string; default: boolean}[]>;
  start_subtitles(device: number): Promise<number>;
  activate_subtitles(session: number): Promise<void>;
  stop_subtitles(session?: number | null): Promise<void>;
  clear_subtitles(session: number): Promise<number>;
  start_drag(): Promise<void>;
  start_settings_drag(): Promise<void>;
  start_settings_resize(edge: string): Promise<void>;
  remember_settings_size(width: number, height: number): Promise<void>;
  fit_settings_window(): Promise<{ width: number; height: number } | null>;
  show_settings_window(): Promise<void>;
}
declare global {
  interface Window {
    pywebview?: {api: NativeApi};
    desktranslate: { setInput(text: string, translate?: boolean): void; focus(): void; setHotkey(shortcut: string, error?: string): void; applySettings(settings: Settings, saved?: boolean): void; updateSubtitle(event: SubtitleEvent): void; subtitleStopped(): void };
    desktranslateSettings: {ready: boolean; showTab(tab: SettingsTab): void; requestClose(): void; applyAppearance(appearance: Appearance, frostedAmount: number, liquidTransparency: number, liquidColor: string, transparentTransparency: number): void};
  }
}

export function native(): NativeApi {
  if (!window.pywebview) throw new Error('桌面服务未连接，请从 DeskTranslate 启动应用。');
  return window.pywebview.api;
}
