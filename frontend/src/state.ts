export type Direction = 'auto' | 'en_to_zh' | 'zh_to_en';
export const MAX_INPUT = 3000;
export const hasChinese = (text: string): boolean => /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u{20000}-\u{2ebef}\u{30000}-\u{323af}]/u.test(text);
export const charCount = (text: string): number => Array.from(text).length;

export function reverseDirection(direction: Direction, text: string): Direction {
  if (direction === 'en_to_zh') return 'zh_to_en';
  if (direction === 'zh_to_en') return 'en_to_zh';
  return hasChinese(text) ? 'en_to_zh' : 'zh_to_en';
}

export function sourceHint(direction: Direction, text: string): string {
  if (direction === 'en_to_zh') return '英文 → 中文';
  if (direction === 'zh_to_en') return '中文 → 英文';
  if (!text.trim()) return '自动识别语言';
  return hasChinese(text) ? '中文 → 英文' : '英文 → 中文';
}
