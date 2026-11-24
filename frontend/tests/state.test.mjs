import { test } from 'node:test';
import assert from 'node:assert/strict';
import { charCount, reverseDirection, sourceHint } from '../../src/desktranslate/web/state.js';

test('counts Unicode code points consistently with the Python input limit', () => {
  assert.equal(charCount('苹果🍎'), 3);
  assert.equal(charCount('a'.repeat(3000)), 3000);
});
test('reverses the effective direction in auto and explicit modes', () => {
  assert.equal(reverseDirection('auto', '苹果'), 'en_to_zh');
  assert.equal(reverseDirection('auto', 'apple'), 'zh_to_en');
  assert.equal(reverseDirection('en_to_zh', ''), 'zh_to_en');
  assert.equal(reverseDirection('zh_to_en', ''), 'en_to_zh');
});
test('auto direction follows input while explicit selection stays fixed', () => {
  assert.equal(sourceHint('auto', ' '), '自动识别语言');
  assert.equal(sourceHint('auto', '你好'), '中文 → 英文');
  assert.equal(sourceHint('auto', '𠀀'), '中文 → 英文');
  assert.equal(sourceHint('auto', 'hello'), '英文 → 中文');
  assert.equal(sourceHint('zh_to_en', 'hello'), '中文 → 英文');
});
