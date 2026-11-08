import { afterAll, beforeAll, expect, it, vi } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';

const mocked = vi.hoisted(() => ({
  data: '', handlers: new Map<string, (...args: unknown[]) => unknown>(),
  confirm: vi.fn(),
}));
vi.mock('electron', () => ({
  app: { getAppPath: () => process.cwd() }, dialog: { showMessageBox: mocked.confirm },
  ipcMain: { handle: (name: string, handler: (...args: unknown[]) => unknown) => mocked.handlers.set(name, handler) },
}));
vi.mock('../lib/data-dir', () => ({ getDataDir: () => mocked.data }));
import { registerResearch } from './research';

let base: string;
const invoke = (name: string, ...args: unknown[]) => mocked.handlers.get(name)!(null, 'test-project', ...args);
beforeAll(() => {
  base = fs.mkdtempSync(path.join(os.tmpdir(), 'nomisma-ipc-'));
  mocked.data = path.join(base, 'state');
  const root = path.join(base, 'project');
  fs.mkdirSync(mocked.data);
  for (const directory of ['datasets/raw', 'datasets/processed']) {
    fs.mkdirSync(path.join(root, directory), { recursive: true });
    fs.copyFileSync(path.resolve('../Nomisma/tests/phase1b2/fixture/datasets/raw/sample.xlsx'), path.join(root, directory, 'test.xlsx'));
  }
  fs.writeFileSync(path.join(mocked.data, 'projects.json'), JSON.stringify([{ id: 'test-project', name:'File service fixture',path: root,createdAt:1 }]));
  registerResearch();
});
afterAll(() => {
  if (base && path.dirname(base) === path.resolve(os.tmpdir())) fs.rmSync(base, { recursive: true, force: true });
});

it('opens, confirms, writes with actual Python, and reopens the real xlsx', async () => {
  const initial = await invoke('research:workbook', 'datasets/processed/test.xlsx') as {version:string};
  mocked.confirm.mockResolvedValueOnce({ response: 1 });
  await invoke('research:save', 'datasets/processed/test.xlsx', initial.version,
    [{ sheet: 'Prices', cell: 'B2', value: 609.25, kind: 'number' }]);
  const reopened = await invoke('research:workbook', 'datasets/processed/test.xlsx') as {sheets:{rows:{value:unknown}[][]}[]};
  expect(reopened.sheets[0].rows[1][1].value).toBe(609.25);
});

it('cancelled overwrite leaves bytes unchanged; raw writes never ask for permission', async () => {
  const initial = await invoke('research:workbook', 'datasets/processed/test.xlsx') as {version:string};
  const file = path.join(base, 'project/datasets/processed/test.xlsx');
  const before = fs.readFileSync(file);
  mocked.confirm.mockResolvedValueOnce({ response: 0 });
  expect(await invoke('research:save', 'datasets/processed/test.xlsx', initial.version, [])).toBeNull();
  expect(fs.readFileSync(file)).toEqual(before);
  const count = mocked.confirm.mock.calls.length;
  await expect(invoke('research:save', 'datasets/raw/test.xlsx', 'any', [])).rejects.toThrow('read-only');
  expect(mocked.confirm).toHaveBeenCalledTimes(count);
});

it('rejects traversal and unregistered project IDs at the IPC boundary', async () => {
  await expect(invoke('research:read', '../outside.txt')).rejects.toThrow();
  expect(() => mocked.handlers.get('research:list')!(null, 'unknown-project')).toThrow('Unknown project');
});
