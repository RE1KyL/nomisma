const path = require("path");
const fs = require("fs");
const { createRequire } = require("module");
const { verifyWindowsRuntime } = require("./electron/src/lib/windows-runtime.cjs");
const runtimeStaging = process.env.NOMISMA_WINDOWS_RUNTIME_STAGING;
const runtimeDigest = process.env.NOMISMA_WINDOWS_RUNTIME_SHA256;
if (Boolean(runtimeStaging) !== Boolean(runtimeDigest)) {
  throw new Error("Set both the reviewed Nomisma runtime staging path and its manifest SHA-256");
}

const applicationFiles = [
  "package.json", "dist/**/*", "electron/dist/**/*", "LICENSE", "UPSTREAM_ORIGIN.md",
  "!**/{test,tests,__tests__,__mocks__,spec,specs}/**",
  "!**/*.d.ts",
  "!**/*.d.cts",
  "!**/*.d.mts",
  "!**/*.map",
];
const runtimeEntry = /^(?:package\.json|LICENSE|UPSTREAM_ORIGIN\.md|dist(?:\/.*)?|electron(?:\/dist(?:\/.*)?)?|node_modules(?:\/.*)?)$/;

async function afterPackHook(context) {
  const resourcesDir = ["darwin", "mas"].includes(context.electronPlatformName)
    ? path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`, "Contents", "Resources")
    : path.join(context.appOutDir, "resources");

  const asarPath = path.join(resourcesDir, "app.asar");
  if (!fs.existsSync(asarPath)) throw new Error("Release is missing app.asar");

  // @electron/asar is a transitive dep of electron-builder, always available
  // pnpm does not hoist transitive dependencies into the project root.
  const builderRequire = createRequire(require.resolve("electron-builder"));
  const appBuilderRequire = createRequire(builderRequire.resolve("app-builder-lib"));
  const asar = appBuilderRequire("@electron/asar");
  // Inspect only. Repacking here would invalidate the executable's ASAR hash
  // and could move unpacked native modules back into the archive.
  asar.uncache(asarPath);
  const entries = new Set(asar.listPackage(asarPath).map(entry => entry.replace(/^[\\/]/, "").replace(/\\/g, "/")));
  if ([...entries].some(entry => !runtimeEntry.test(entry))) {
    throw new Error("ASAR contains an undeclared application entry");
  }
  for (const file of ["package.json", "dist/index.html", "electron/dist/main.js", "electron/dist/preload.js"]) {
    if (!entries.has(file)) throw new Error(`ASAR is missing ${file}`);
  }
  if (context.electronPlatformName === "win32" && runtimeStaging) {
    const metadata = JSON.parse(asar.extractFile(asarPath, "package.json").toString("utf8"));
    if (metadata.nomismaRuntimeManifestSha256 !== runtimeDigest) throw new Error("Runtime digest is missing from app.asar");
    const destination = path.join(resourcesDir, "nomisma");
    fs.mkdirSync(destination);
    // The staged vendor binaries already carry their reviewed hashes and any
    // vendor signatures. The builder's extraResources transformer can re-sign EXEs.
    fs.cpSync(runtimeStaging, destination, {recursive: true, force: false, errorOnExist: true});
    verifyWindowsRuntime(destination, runtimeDigest, builderRequire("builder-util").Arch[context.arch]);
  }
}

/** @type {import('electron-builder').Configuration} */
module.exports = {
  appId: "org.nomisma.research",
  productName: "NOMISMA",
  ...(runtimeStaging ? {extraMetadata: {nomismaRuntimeManifestSha256: runtimeDigest}} : {}),

  directories: {
    output: "release/${version}",
    buildResources: "build",
  },

  files: applicationFiles,

  // --- ASAR packing ---
  asar: true,
  electronFuses: {
    runAsNode: false,
    enableNodeOptionsEnvironmentVariable: false,
    enableNodeCliInspectArguments: false,
    onlyLoadAppFromAsar: true,
    enableEmbeddedAsarIntegrityValidation: process.platform === "win32" || process.platform === "darwin",
  },
  asarUnpack: [
    "node_modules/node-pty/**",
    "node_modules/electron-liquid-glass/**",
    "node_modules/@anthropic-ai/claude-agent-sdk/cli.js",
    "node_modules/@anthropic-ai/claude-agent-sdk/*.wasm",
    "node_modules/@anthropic-ai/claude-agent-sdk/vendor/**",
    "node_modules/@anthropic-ai/claude-agent-sdk/manifest*.json",
  ],

  npmRebuild: true,
  nodeGypRebuild: false,
  includePdb: false,

  beforePack: ({packager, electronPlatformName, arch, targets}) => {
    for (const name of [".pnpm-store", ".corepack"]) {
      if (fs.existsSync(path.join(packager.info.appDir, name))) {
        throw new Error(`Keep the package-manager cache outside the app staging directory: ${name}`);
      }
    }
    if (electronPlatformName === "win32") {
      if (!runtimeStaging && targets?.some(target => target.name !== "dir")) {
        throw new Error("Windows installer requires a reviewed Nomisma runtime staging directory");
      }
      if (runtimeStaging) {
        const builderRequire = createRequire(require.resolve("electron-builder"));
        verifyWindowsRuntime(runtimeStaging, runtimeDigest, builderRequire("builder-util").Arch[arch]);
        for (const name of ["package.json", "package-lock.json"]) {
          const reviewed = fs.readFileSync(path.join(__dirname, "scripts/windows-agent", name));
          const staged = fs.readFileSync(path.join(runtimeStaging, "agent", name));
          if (!reviewed.equals(staged)) throw new Error("Windows ACP staging must use the reviewed npm recipe");
        }
      }
    }
  },
  afterPack: afterPackHook,

  // --- macOS ---
  mac: {
    target: ["dmg", "zip"],
    category: "public.app-category.developer-tools",
    icon: "build/icon.icon",
    darkModeSupport: true,
    hardenedRuntime: true,
    gatekeeperAssess: false,
    entitlements: "build/entitlements.mac.plist",
    entitlementsInherit: "build/entitlements.mac.plist",
    extendInfo: {
      NSMicrophoneUsageDescription: "Harnss uses the microphone for voice dictation to transcribe speech into text.",
    },
  },

  dmg: {
    icon: "build/icon.icns",
    contents: [
      { x: 130, y: 220 },
      { x: 410, y: 220, type: "link", path: "/Applications" },
    ],
    window: { width: 540, height: 380 },
  },

  // --- Windows ---
  win: {
    // Each reviewed staging tree and digest cover one architecture. Honor
    // the CLI architecture instead of scheduling both in the same build.
    target: ["nsis"],
    icon: "build/icon.ico",
    // GitHub/MIT publication does not require a publisher certificate.
    // Runtime integrity, provenance and package-boundary checks remain enabled.
    forceCodeSigning: false,
    files: [
      // The builder may use platform filters as its first file matcher.
      ...applicationFiles,
      "!node_modules/electron-liquid-glass/**",
      "!node_modules/@anthropic-ai/claude-agent-sdk/vendor/ripgrep/arm64-darwin/**",
      "!node_modules/@anthropic-ai/claude-agent-sdk/vendor/ripgrep/x64-darwin/**",
      "!node_modules/@anthropic-ai/claude-agent-sdk/vendor/ripgrep/arm64-linux/**",
      "!node_modules/@anthropic-ai/claude-agent-sdk/vendor/ripgrep/x64-linux/**",
      "!node_modules/node-pty/prebuilds/darwin-*/**",
      "!node_modules/node-pty/prebuilds/linux-*/**",
    ],
  },

  nsis: {
    oneClick: false,
    allowToChangeInstallationDirectory: true,
    perMachine: false,
    deleteAppDataOnUninstall: false,
    // Include arch in filename so x64 and arm64 installers don't collide
    artifactName: "${productName}-Setup-${version}-${arch}.${ext}",
  },

  // --- Linux ---
  linux: {
    target: [
      { target: "AppImage" },
      { target: "deb" },
    ],
    category: "Development",
    icon: "build/icon.png",
    files: [
      ...applicationFiles,
      "!node_modules/electron-liquid-glass/**",
      "!node_modules/@anthropic-ai/claude-agent-sdk/vendor/ripgrep/arm64-darwin/**",
      "!node_modules/@anthropic-ai/claude-agent-sdk/vendor/ripgrep/x64-darwin/**",
      "!node_modules/@anthropic-ai/claude-agent-sdk/vendor/ripgrep/arm64-win32/**",
      "!node_modules/@anthropic-ai/claude-agent-sdk/vendor/ripgrep/x64-win32/**",
      "!node_modules/node-pty/prebuilds/darwin-*/**",
      "!node_modules/node-pty/prebuilds/win32-*/**",
    ],
  },

  deb: {
    depends: ["libnotify4", "libsecret-1-0"],
  },

  // --- Auto-update ---
  // Nomisma has no reviewed release channel yet. Never install upstream Harnss updates.
  publish: null,

  afterSign: "scripts/notarize.js",
};
