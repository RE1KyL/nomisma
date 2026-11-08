import { expect, it, vi } from 'vitest';
import { weakMapGetOrInsert } from './grid-compat';

it('caches a window observer once, including undefined values', () => {
  const map = new WeakMap<object, unknown>();
  const windowKey = {};
  const create = vi.fn(() => undefined);
  expect(weakMapGetOrInsert.call(map, windowKey, create)).toBeUndefined();
  weakMapGetOrInsert.call(map, windowKey, create);
  expect(create).toHaveBeenCalledTimes(1);
  expect(map.has(windowKey)).toBe(true);
});
