const redacted = '[REDACTED]';
const standardFields = [
  'api[_-]?key', 'authorization', 'password', 'passwd', 'token',
  'access[_-]?token', 'refresh[_-]?token', 'client[_-]?secret',
  'email', 'phone', '身份证号', '手机号', '电子邮箱',
];

export function redactSensitiveJson(value: unknown, fields: readonly string[], depth = 0): unknown {
  if (depth > 32) return redacted;
  if (typeof value === 'string') return redactSensitiveText(value, fields);
  if (Array.isArray(value)) return value.map(item => redactSensitiveJson(item, fields, depth + 1));
  if (typeof value !== 'object' || value === null) return value;
  const sensitive = new RegExp(`^(?:${standardFields.join('|')})$`, 'i');
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [
    key,
    sensitive.test(key) || fields.includes(key.toLowerCase()) ? redacted : redactSensitiveJson(item, fields, depth + 1),
  ]));
}

export function redactSensitiveText(text: string, fields: readonly string[] = []): string {
  const escaped = fields.map(field => field.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const names = [...standardFields, ...escaped].join('|');
  return text
    .replace(new RegExp(`((?:^|[^\\p{L}\\p{N}_])["']?(?:${names})["']?\\s*[:=：]\\s*)("(?:[^"\\\\]|\\\\.)*"|'[^']*'|(?:Bearer|Basic)\\s+[^\\s,;]+|[^\\s,;}]+)`, 'giu'),
      (_match, label: string, value: string) => label + (value.startsWith('"') ? `"${redacted}"` : redacted))
    .replace(/\b(?:sk-(?:ant-|proj-)?|gh[pousr]_)[A-Za-z0-9_-]{16,}\b/g, redacted)
    .replace(/\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi, redacted)
    .replace(/(?<![\d.])(?:\+86[ -]?)?1[3-9]\d{9}(?![\d.])|\+\d{1,3}(?:[ -]\d{2,4}){2,4}(?!\d)/g, redacted);
}
