import crypto from 'node:crypto';
import {spawnSync} from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {afterEach, expect, it, vi} from 'vitest';
import {verifyWindowsRuntime, writeWindowsRuntimeManifest} from '../windows-runtime.cjs';
import {configureInstalledNomisma, nomismaNode, nomismaProjectRoot, nomismaPython, nomismaPythonCwd} from '../nomisma-runtime';
import {getAgent, listAgents} from '../agent-registry';

const electron = vi.hoisted(() => ({app: {isPackaged: true, getAppPath: vi.fn(), setPath: vi.fn()}}));
vi.mock('electron', () => electron);

const required = [
  'node/node.exe', 'python/python.exe', 'agent/package.json', 'agent/package-lock.json',
  'agent/node_modules/@deepseek-ai/dsh/LICENSE',
  'agent/node_modules/@deepseek-ai/dsh/lib/profile-boot.js',
  'agent/node_modules/@deepseek-ai/dsh-launch-environment/lib/index.js',
  'agent/node_modules/@deepseek-ai/dsh-llm/lib/index.js',
  'research/launcher/start_dsh_acp.cjs', 'research/launcher/credential_dpapi.py',
  'research/launcher/installed_dsh_profile.mjs', 'research/launcher/managed_provider.mjs',
  'research/nomisma_tools/server.py', 'research/nomisma_tools/workbook_io.py',
  'research/nomisma_tools/shared_service.py', 'research/nomisma_tools/research_control.py',
];
const sha256 = (value: string) => crypto.createHash('sha256').update(value).digest('hex');
const fixtures: string[] = [];

function fixture() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'nomisma-install-'));
  fixtures.push(directory);
  const resources = path.join(directory, 'resources');
  const runtime = path.join(resources, 'nomisma');
  const local = path.join(directory, 'profile');
  fs.mkdirSync(local);
  const files = required.map(relative => {
    const target = path.join(runtime, relative);
    fs.mkdirSync(path.dirname(target), {recursive: true});
    fs.writeFileSync(target, relative);
    return {path: relative, sha256: sha256(relative)};
  });
  const manifest = path.join(runtime, 'runtime-manifest.json');
  const json = JSON.stringify({version: 1, arch: process.arch, files});
  fs.writeFileSync(manifest, json);
  return {directory, resources, runtime, local, manifest, digest: sha256(json), files};
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  for (const directory of fixtures.splice(0)) fs.rmSync(directory, {recursive: true, force: true});
});

it('detects changed, extra, missing or redirected installed files', () => {
  const {directory, runtime, digest} = fixture();
  const verify = () => verifyWindowsRuntime(runtime, digest, process.arch);
  expect(verify).not.toThrow();
  const executable = path.join(runtime, 'node', 'node.exe');
  fs.writeFileSync(executable, 'modified');
  expect(verify).toThrow(/modified/);
  fs.unlinkSync(executable);
  expect(verify).toThrow(/missing/);
  fs.writeFileSync(executable, 'node/node.exe');
  const extra = path.join(runtime, 'node', 'rogue.dll');
  fs.writeFileSync(extra, 'unreviewed');
  expect(verify).toThrow(/unreviewed/);
  fs.unlinkSync(extra);
  const outside = path.join(directory, 'outside');
  fs.mkdirSync(outside);
  fs.symlinkSync(outside, path.join(runtime, 'node', 'linked'), process.platform === 'win32' ? 'junction' : 'dir');
  expect(verify).toThrow(/unreviewed/);
});

it('rejects a replacement manifest, architecture mismatch and Windows path aliases', () => {
  const {runtime, digest, files, manifest} = fixture();
  expect(() => verifyWindowsRuntime(runtime, digest, process.arch === 'x64' ? 'arm64' : 'x64')).toThrow(/architecture/);
  const json = JSON.stringify({version: 1, arch: process.arch, files: [...files, {path: 'node/NODE.EXE', sha256: digest}]});
  fs.writeFileSync(manifest, json);
  expect(() => verifyWindowsRuntime(runtime, digest, process.arch)).toThrow(/integrity/);
  expect(() => verifyWindowsRuntime(runtime, sha256(json), process.arch)).toThrow(/duplicate/);
});

it('writes a deterministic manifest without replacing an existing approval', () => {
  const first = fixture();
  const second = fixture();
  expect(() => writeWindowsRuntimeManifest(first.runtime, process.arch)).toThrow(/EEXIST/);
  expect(sha256(fs.readFileSync(first.manifest, 'utf8'))).toBe(first.digest);
  for (const instance of [first, second]) fs.unlinkSync(instance.manifest);
  const digest = writeWindowsRuntimeManifest(first.runtime, process.arch);
  expect(writeWindowsRuntimeManifest(second.runtime, process.arch)).toBe(digest);
  expect(() => verifyWindowsRuntime(first.runtime, digest, process.arch)).not.toThrow();
});

it('refuses to include the vendor desktop executable in the CLI runtime tree', () => {
  const {runtime, manifest} = fixture();
  fs.unlinkSync(manifest);
  fs.writeFileSync(path.join(runtime, 'agent', 'DeepSeek Harness.exe'), 'vendor desktop fixture');
  expect(() => writeWindowsRuntimeManifest(runtime, process.arch)).toThrow(/unreviewed/);
});

it('hashes empty, boundary-sized and streamed files completely', () => {
  const {runtime, manifest} = fixture();
  fs.unlinkSync(manifest);
  const sizes = [0, 1024 * 1024, 1024 * 1024 + 17];
  for (const size of sizes) fs.writeFileSync(path.join(runtime, 'node', `sample-${size}`), Buffer.alloc(size, 0x5a));
  const digest = writeWindowsRuntimeManifest(runtime, process.arch);
  expect(() => verifyWindowsRuntime(runtime, digest, process.arch)).not.toThrow();
  for (const size of sizes) {
    const file = path.join(runtime, 'node', `sample-${size}`);
    const original = fs.readFileSync(file);
    // Alter the last byte, including the final partial streaming chunk.
    const changed = original.length ? Buffer.from(original) : Buffer.from([1]);
    changed[changed.length - 1] ^= 0xff;
    fs.writeFileSync(file, changed);
    expect(() => verifyWindowsRuntime(runtime, digest, process.arch)).toThrow(/modified/);
    fs.writeFileSync(file, original);
  }
});

it('verifies every digest in the bounded worker pool and rejects a changed final chunk', () => {
  const {runtime, manifest} = fixture();
  fs.unlinkSync(manifest);
  for (let index = 0; index < 256; index++) {
    fs.writeFileSync(path.join(runtime, 'node', `pool-${index}`), `reviewed bytes ${index}`);
  }
  const large = path.join(runtime, 'node', 'large-pool-file');
  const bytes = Buffer.alloc(1024 * 1024 + 17, 0x5a);
  fs.writeFileSync(large, bytes);
  const digest = writeWindowsRuntimeManifest(runtime, process.arch);
  expect(() => verifyWindowsRuntime(runtime, digest, process.arch)).not.toThrow();
  bytes[bytes.length - 1] ^= 0xff;
  fs.writeFileSync(large, bytes);
  expect(() => verifyWindowsRuntime(runtime, digest, process.arch)).toThrow(/modified/);
  bytes[bytes.length - 1] ^= 0xff;
  fs.writeFileSync(large, bytes);
  fs.writeFileSync(path.join(runtime, 'node', 'pool-255'), 'changed last small file');
  expect(() => verifyWindowsRuntime(runtime, digest, process.arch)).toThrow(/modified/);

  // A worker that disappears without writing shared completion state must not
  // authorize the runtime. Exercise the real verifier in an isolated process;
  // only its worker transport and clock are replaced to avoid a five-minute test.
  const child = spawnSync(process.execPath, ['-e', `
    let created = 0, terminated = 0;
    require('node:worker_threads').Worker = class {
      constructor() {created++;}
      on() {return this;}
      terminate() {terminated++; return Promise.resolve(0);}
    };
    const {verifyWindowsRuntime} = require(process.argv[1]);
    let ticks = 0;
    Date.now = () => ticks++ === 0 ? 0 : 300001;
    let failure = '';
    try {verifyWindowsRuntime(process.argv[2], process.argv[3], process.arch);}
    catch (error) {failure = error.message;}
    console.log(JSON.stringify({created, terminated, failure}));
  `, path.resolve('electron/src/lib/windows-runtime.cjs'), runtime, digest], {encoding: 'utf8', env: {}, timeout: 10000});
  expect(child.status).toBe(0);
  const stalled = JSON.parse(child.stdout);
  expect(stalled.created).toBeGreaterThan(0);
  expect(stalled.created).toBeLessThanOrEqual(8);
  expect(stalled.terminated).toBe(stalled.created);
  expect(stalled.failure).toMatch(/workers timed out/);
}, 30000);

it.skipIf(process.platform !== 'win32')('bootstraps only verified bundled executables and separate local state', () => {
  const {directory, resources, local, runtime, digest} = fixture();
  vi.stubGlobal('process', Object.create(process, {resourcesPath: {value: resources}}));
  for (const name of ['NOMISMA_STATE_ROOT', 'NOMISMA_DESKTOP_MODE', 'NOMISMA_FILE_TEST', 'PATH']) {
    vi.stubEnv(name, process.env[name]);
  }
  vi.stubEnv('NOMISMA_PROJECT_ROOT', path.join(directory, 'untrusted-project-root'));
  vi.stubEnv('LOCALAPPDATA', local);
  electron.app.getAppPath.mockReturnValue(directory);
  const metadata = path.join(directory, 'package.json');
  fs.writeFileSync(metadata, '{}');
  expect(configureInstalledNomisma).toThrow(/manifest is required/);
  expect(fs.readdirSync(local)).toEqual([]);
  fs.writeFileSync(metadata, JSON.stringify({nomismaRuntimeManifestSha256: digest}));
  configureInstalledNomisma();
  const state = path.join(local, 'NomismaRelease', 'Nomisma');
  expect(electron.app.setPath.mock.calls).toEqual([
    ['userData', path.join(state, 'desktop-dev')], ['sessionData', path.join(state, 'desktop-dev')],
  ]);
  expect(process.env.NOMISMA_STATE_ROOT).toBe(state);
  expect(nomismaProjectRoot()).toBe(path.join(runtime, 'research'));
  expect(nomismaPython()).toBe(path.join(runtime, 'python', 'python.exe'));
  expect(nomismaPythonCwd()).toBe(path.join(runtime, 'python'));
  expect(listAgents().map(agent => agent.id)).toEqual(['nomisma-dsh-acp']);
  expect(getAgent('nomisma-dsh-acp')?.binary).toBe(nomismaNode());
  expect(getAgent('claude-code')).toBeUndefined();
});
