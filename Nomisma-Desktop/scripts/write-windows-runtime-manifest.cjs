// Inventory an already reviewed staging tree. This does not attest provenance.
const {writeWindowsRuntimeManifest} = require('../electron/src/lib/windows-runtime.cjs');

try {
  const [root, arch] = process.argv.slice(2);
  if (!root || !arch) throw new Error('Usage: node write-windows-runtime-manifest.cjs <absolute-runtime-dir> <x64|arm64>');
  console.log(writeWindowsRuntimeManifest(root, arch));
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
