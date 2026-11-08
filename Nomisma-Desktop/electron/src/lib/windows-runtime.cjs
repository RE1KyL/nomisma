const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const {Worker} = require('node:worker_threads');

const manifestName = 'runtime-manifest.json';
const digestPattern = /^[a-f0-9]{64}$/;
const runtimeRoots = new Set(['node', 'python', 'agent', 'research']);
const requiredFiles = [
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

function samePath(left, right) {
  const a = path.resolve(left);
  const b = path.resolve(right);
  return process.platform === 'win32' ? a.toLowerCase() === b.toLowerCase() : a === b;
}

function isPathRedirected(file, entry = fs.lstatSync(file)) {
  // Some Windows junctions report as directories to lstat. The native
  // resolver still exposes their final target, including linked ancestors.
  return entry.isSymbolicLink() ||
    (process.platform === 'win32' && !samePath(file, fs.realpathSync.native(file)));
}

function checkedDirectory(directory) {
  if (!fs.lstatSync(directory).isDirectory() || !samePath(fs.realpathSync.native(directory), directory)) {
    throw new Error('Nomisma runtime or state directory is redirected');
  }
}

function validEntry(relative) {
  if (typeof relative !== 'string' || relative.length > 768) return false;
  const segments = relative.split('/');
  if (segments[0] === 'agent' && !['package.json', 'package-lock.json', 'node_modules'].includes(segments[1])) return false;
  return segments.length > 1 && runtimeRoots.has(segments[0]) && segments.every(segment =>
    segment && !/[<>:"\\|?*\x00-\x1f]|[ .]$/.test(segment) &&
    !/^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(segment));
}

function hashFile(file, buffer) {
  // Node's native whole-file path avoids repeated JS readSync calls for the
  // many small package files. Keep streaming large binaries rather than
  // allocating their full size. Both paths hash all bytes on every check.
  if (fs.statSync(file).size <= buffer.length) {
    return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  }
  const hash = crypto.createHash('sha256');
  const fd = fs.openSync(file, 'r');
  try {
    let bytes;
    while ((bytes = fs.readSync(fd, buffer, 0, buffer.length, null))) hash.update(buffer.subarray(0, bytes));
  } finally { fs.closeSync(fd); }
  return hash.digest('hex');
}

function* runtimeFiles(directory, prefix = '') {
  for (const name of fs.readdirSync(directory)) {
    if (!prefix && name === manifestName) continue;
    const absolute = path.join(directory, name);
    const relative = prefix ? `${prefix}/${name}` : name;
    const entry = fs.lstatSync(absolute);
    if (isPathRedirected(absolute, entry)) throw new Error('Windows runtime contains an unreviewed or redirected entry');
    if (entry.isDirectory() && (prefix ? validEntry(relative) : runtimeRoots.has(name))) {
      yield* runtimeFiles(absolute, relative);
    } else if (entry.isFile() && validEntry(relative)) {
      yield {relative, absolute};
    } else throw new Error('Windows runtime contains an unreviewed or redirected entry');
  }
}

// Keep this source self-contained: production bundlers rename module locals.
// Inputs are structured worker data, never interpolated into executable code.
const hashWorkerSource = `
const {workerData} = require('node:worker_threads');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const state = new Int32Array(workerData.state);
let outcome = -1;
try {
  const buffer = Buffer.allocUnsafe(1024 * 1024);
  for (const item of workerData.files) {
    const stat = fs.lstatSync(item.absolute);
    const actual = fs.realpathSync.native(item.absolute);
    const normalize = value => process.platform === 'win32' ? path.resolve(value).toLowerCase() : path.resolve(value);
    if (!stat.isFile() || stat.isSymbolicLink() || normalize(actual) !== normalize(item.absolute)) throw new Error('Redirected file');
    const hash = crypto.createHash('sha256');
    if (stat.size <= buffer.length) hash.update(fs.readFileSync(item.absolute));
    else {
      const fd = fs.openSync(item.absolute, 'r');
      try {
        let count;
        while ((count = fs.readSync(fd, buffer, 0, buffer.length, null))) hash.update(buffer.subarray(0, count));
      } finally {fs.closeSync(fd);}
    }
    if (hash.digest('hex') !== item.sha256) throw new Error('Modified file');
  }
  outcome = 1;
} finally {
  Atomics.store(state, workerData.index + 1, outcome);
  Atomics.add(state, 0, 1);
  Atomics.notify(state, 0);
}
`;

function verifyFileDigests(files) {
  if (files.length < 256) {
    const buffer = Buffer.allocUnsafe(1024 * 1024);
    for (const item of files) {
      if (isPathRedirected(item.absolute) || hashFile(item.absolute, buffer) !== item.sha256) {
        throw new Error('Windows runtime contains an unreviewed or modified file');
      }
    }
    return;
  }
  // Bound parallel opens and memory. Every file is still read in full, with a
  // fresh final-path check in its worker immediately before opening it.
  const count = 8;
  const state = new Int32Array(new SharedArrayBuffer((count + 1) * Int32Array.BYTES_PER_ELEMENT));
  const batches = Array.from({length: count}, () => []);
  files.forEach((file, index) => batches[index % count].push(file));
  const workers = [];
  const deadline = Date.now() + 300_000;
  try {
    for (let index = 0; index < count; index++) {
      const worker = new Worker(hashWorkerSource, {eval: true, env: {}, execArgv: [],
        resourceLimits: {maxOldGenerationSizeMb: 64}, workerData: {files: batches[index], index, state: state.buffer}});
      // A native worker failure may not reach its finally block. The deadline
      // then rejects the runtime; it must never turn a missing reply into PASS.
      worker.on('error', () => {});
      workers.push(worker);
    }
    while (Atomics.load(state, 0) !== count) {
      if (Array.from(state.subarray(1)).includes(-1)) throw new Error('Windows runtime contains an unreviewed or modified file');
      const remaining = deadline - Date.now();
      if (remaining <= 0) throw new Error('Windows runtime integrity workers timed out');
      const completed = Atomics.load(state, 0);
      Atomics.wait(state, 0, completed, Math.min(remaining, 1000));
    }
    if (Array.from(state.subarray(1)).some(value => value !== 1)) {
      throw new Error('Windows runtime contains an unreviewed or modified file');
    }
  } finally {
    for (const worker of workers) void worker.terminate();
  }
}

/** The expected manifest digest comes from the approved build or the application's ASAR. */
function verifyWindowsRuntime(root, expectedDigest, arch) {
  if (!path.isAbsolute(root) || !digestPattern.test(expectedDigest ?? '') || !['x64', 'arm64'].includes(arch)) {
    throw new Error('Reviewed Windows runtime manifest is required');
  }
  checkedDirectory(root);
  const manifestFile = path.join(root, manifestName);
  const stat = fs.lstatSync(manifestFile);
  if (!stat.isFile() || isPathRedirected(manifestFile, stat) || stat.size > 32 * 1024 * 1024) {
    throw new Error('Invalid Windows runtime manifest file');
  }
  const bytes = fs.readFileSync(manifestFile);
  if (crypto.createHash('sha256').update(bytes).digest('hex') !== expectedDigest) {
    throw new Error('Windows runtime manifest integrity check failed');
  }
  const manifest = JSON.parse(bytes.toString('utf8'));
  if (manifest?.version !== 1 || manifest.arch !== arch || !Array.isArray(manifest.files)) {
    throw new Error('Windows runtime manifest architecture or format is invalid');
  }
  const remaining = new Map();
  const identities = new Set();
  for (const entry of manifest.files) {
    if (!validEntry(entry?.path) || typeof entry.sha256 !== 'string' || !digestPattern.test(entry.sha256) ||
        identities.has(entry.path.toLowerCase())) throw new Error('Invalid or duplicate Windows runtime manifest entry');
    identities.add(entry.path.toLowerCase());
    remaining.set(entry.path, entry.sha256);
  }
  if (requiredFiles.some(file => !remaining.has(file))) throw new Error('Windows runtime manifest is missing a required file');

  const files = [];
  for (const {relative, absolute} of runtimeFiles(root)) {
    if (!remaining.has(relative)) {
      throw new Error('Windows runtime contains an unreviewed or modified file');
    }
    files.push({absolute, sha256: remaining.get(relative)});
    remaining.delete(relative);
  }
  if (remaining.size) throw new Error('Windows runtime is missing reviewed files');
  verifyFileDigests(files);
}

function writeWindowsRuntimeManifest(root, arch) {
  if (!path.isAbsolute(root) || !['x64', 'arm64'].includes(arch)) throw new Error('Absolute staging path and architecture required');
  checkedDirectory(root);
  const buffer = Buffer.allocUnsafe(1024 * 1024);
  const files = Array.from(runtimeFiles(root), ({relative, absolute}) => ({path: relative, sha256: hashFile(absolute, buffer)}));
  files.sort((left, right) => left.path < right.path ? -1 : left.path > right.path ? 1 : 0);
  const names = new Set(files.map(entry => entry.path));
  if (new Set(files.map(entry => entry.path.toLowerCase())).size !== files.length || requiredFiles.some(file => !names.has(file))) {
    throw new Error('Staging contains duplicate paths or lacks a required Windows runtime file');
  }
  const bytes = JSON.stringify({version: 1, arch, files}) + '\n';
  if (Buffer.byteLength(bytes) > 32 * 1024 * 1024) throw new Error('Windows runtime manifest is too large');
  fs.writeFileSync(path.join(root, manifestName), bytes, {flag: 'wx'});
  return crypto.createHash('sha256').update(bytes).digest('hex');
}

module.exports = {checkedDirectory, isPathRedirected, verifyWindowsRuntime, writeWindowsRuntimeManifest};
