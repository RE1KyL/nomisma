// RDG beta.63 uses WeakMap upsert for its per-window ResizeObserver cache.
// Electron 40's Chromium does not yet implement it. Keep this narrow fallback
// until the fixed desktop runtime supports the native method; do not upgrade
// Electron or alter the installed grid bundle for this prototype.
export function weakMapGetOrInsert(
  this: WeakMap<object, unknown>, key: object, compute: (key: object) => unknown,
): unknown {
  if (typeof compute !== 'function') throw new TypeError('Expected a callback');
  if ((typeof key !== 'object' || key === null) && typeof key !== 'function') throw new TypeError('Expected an object key');
  if (this.has(key)) return this.get(key);
  const value = compute(key);
  this.set(key, value);
  return value;
}

const prototype = WeakMap.prototype as WeakMap<object, unknown> & { getOrInsertComputed?: typeof weakMapGetOrInsert };
if (!prototype.getOrInsertComputed) {
  Object.defineProperty(prototype, 'getOrInsertComputed', {
    value: weakMapGetOrInsert, configurable: true, writable: true,
  });
}
