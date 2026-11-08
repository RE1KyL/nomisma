const fs = require('node:fs');
const path = require('node:path');
const {isRedirected} = require('./project_context.cjs');
const bundles = ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-acp-app'];

const disabledTools = [
  'credentials', 'deepseek-account', 'authorization', 'llm-deepseek', 'llm-deepseek-account',
  'llm-pi-ai', 'session-title', 'session-title-llm', 'session-log-deepseek',
  'plugin-package-inventory-deepseek', 'tool-plugin-manager', 'tool-working-directory',
  'tool-bash', 'tool-pwsh', 'tool-jobs', 'tool-fs', 'tool-fs-search', 'tool-skill',
  'tool-subagent-control', 'tool-subagent-list-agents', 'tool-subagent', 'tool-subagent-fork',
  'tool-workflow', 'tool-todo', 'tool-goal', 'tool-web', 'workflow-ptc', 'ptc-runtime',
  'subagent-spawn-in-process', 'subagent-fork-in-process', 'session-telemetry-otel',
  'skill-filesystem', 'agent-instructions',
];

function installedPatch() {
  return [{id: 'acp', config: {provider: 'nomisma-managed', model: 'configured'}},
    ...disabledTools.map(id => ({id, disabled: true})),
    {insert: [{id: 'nomisma-health-policy', name: path.join(__dirname, 'health_policy.mjs')}]}];
}

function readProfileFile(file) {
  const info = fs.lstatSync(file);
  if (!info.isFile() || isRedirected(file, info) || info.size > 64_000) throw new Error('Unsafe managed ACP profile file');
  return fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '');
}

function exactKeys(value, names) {
  return value && typeof value === 'object' && !Array.isArray(value) &&
    Object.keys(value).sort().join(',') === [...names].sort().join(',');
}

function validateManagedProfile(home, installed = false) {
  const profiles = path.join(home, 'profiles');
  const profile = path.join(profiles, 'acp');
  for (const directory of [home, profiles, profile]) {
    const entry = fs.lstatSync(directory);
    if (!entry.isDirectory() || isRedirected(directory, entry)) throw new Error('Unsafe managed ACP profile directory');
  }
  // Harness loads the home overlay after the validated profile layer.
  try {
    fs.lstatSync(path.join(home, 'cordis.patch.yml'));
    throw new Error('Managed ACP cannot load a home-level overlay');
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  const expected = ['cordis.patch.yml', 'cordis.yml', 'package.json'];
  if (fs.readdirSync(profile).sort().join(',') !== expected.join(',')) {
    throw new Error('Managed ACP profile has unreviewed entries');
  }
  const config = JSON.parse(readProfileFile(path.join(profile, 'package.json')));
  const dsh = config?.dsh;
  if (!exactKeys(config, ['name', 'private', 'dependencies', 'dsh']) || config.private !== true ||
      typeof config.name !== 'string' || !exactKeys(config.dependencies, []) ||
      !exactKeys(dsh, ['profile']) || !exactKeys(dsh.profile, ['bundles']) ||
      JSON.stringify(dsh.profile.bundles) !== JSON.stringify(bundles)) {
    throw new Error('Managed ACP profile must use the two reviewed bundles');
  }
  // DSH rewrites its empty profile root with a comment header on startup.
  const base = readProfileFile(path.join(profile, 'cordis.yml'))
    .split(/\r?\n/).filter(line => !/^[ \t]*#/.test(line)).join('\n').trim();
  if (base !== '[]') throw new Error('Managed ACP profile cannot load extra plugins');
  const patch = JSON.parse(readProfileFile(path.join(profile, 'cordis.patch.yml')));
  if (!Array.isArray(patch) || patch.length > 128) throw new Error('Invalid managed ACP patch');
  if (installed && JSON.stringify(patch) !== JSON.stringify(installedPatch())) {
    throw new Error('Installed ACP profile differs from its reviewed tool policy');
  }
  let healthCount = 0;
  for (const row of patch) {
    if (exactKeys(row, ['id', 'disabled']) && typeof row.id === 'string' && row.disabled === true) continue;
    if (exactKeys(row, ['id', 'config']) && row.id === 'acp' && exactKeys(row.config, ['provider', 'model']) &&
        ['deepseek-account', 'nomisma-managed'].includes(row.config.provider) &&
        typeof row.config.model === 'string' && row.config.model.length < 128) continue;
    if (exactKeys(row, ['insert']) && Array.isArray(row.insert)) {
      for (const plugin of row.insert) {
        if (exactKeys(plugin, ['id', 'name']) && plugin.id === 'nomisma-health-policy' &&
            path.resolve(String(plugin.name)) === path.join(__dirname, 'health_policy.mjs')) {
          healthCount++;
          continue;
        }
        // The bundled managed patch disables this legacy plugin before the
        // runtime accepts any prompt. Allow only the original fixed module.
        if (exactKeys(plugin, ['id', 'name', 'config']) && plugin.id === 'nomisma-readonly-credentials' &&
            path.resolve(String(plugin.name)) === path.join(__dirname, 'readonly_credentials.mjs') &&
            exactKeys(plugin.config, ['path', 'watch']) && plugin.config.watch === false &&
            typeof plugin.config.path === 'string') continue;
        throw new Error('Unapproved managed ACP plugin');
      }
      continue;
    }
    throw new Error('Unapproved managed ACP configuration');
  }
  if (healthCount !== 1) throw new Error('Managed ACP profile requires its fixed tool policy');
}

function ensureInstalledProfile(home) {
  const profiles = path.join(home, 'profiles');
  for (const directory of [home, profiles]) {
    try { fs.mkdirSync(directory); }
    catch (error) { if (error.code !== 'EEXIST') throw error; }
    const entry = fs.lstatSync(directory);
    if (!entry.isDirectory() || isRedirected(directory, entry)) throw new Error('Unsafe installed ACP profile directory');
  }
  const profile = path.join(profiles, 'acp');
  if (!fs.existsSync(profile)) {
    const temporary = fs.mkdtempSync(path.join(profiles, 'acp-initial-'));
    try {
      const packageInfo = {name: 'nomisma-acp-profile', private: true, dependencies: {}, dsh: {profile: {bundles}}};
      fs.writeFileSync(path.join(temporary, 'package.json'), JSON.stringify(packageInfo), {flag: 'wx'});
      fs.writeFileSync(path.join(temporary, 'cordis.yml'), '[]', {flag: 'wx'});
      fs.writeFileSync(path.join(temporary, 'cordis.patch.yml'), JSON.stringify(installedPatch()), {flag: 'wx'});
      try { fs.renameSync(temporary, profile); }
      catch (error) { if (!fs.existsSync(profile)) throw error; }
    } finally { fs.rmSync(temporary, {recursive: true, force: true}); }
  }
  validateManagedProfile(home, true);
}

module.exports = {ensureInstalledProfile, validateManagedProfile};
