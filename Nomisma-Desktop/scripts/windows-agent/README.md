# Windows ACP runtime recipe

These package files pin the official MIT-licensed DSH CLI and its reviewed
dependency overrides. Place them in a new installed runtime `agent/` directory.
The production builder compares both files byte for byte before packaging.

With a reviewed standalone Node and npm toolchain, install using an isolated npm
configuration and cache:

```powershell
npm ci --omit=dev --ignore-scripts --no-bin-links --no-audit --no-fund
```

Append `--offline` for repeats after all required locked archives are cached and
hash-verified. Do not regenerate the lock during release builds. Do not copy npm
caches or this README into `agent/`. Keep applicable third-party notices.

Record absent optional packages and check that intended functionality works with
install scripts disabled. Validate required native packages through actual
research scenarios on the target architecture. Review native library advisories
and license obligations separately from npm advisory queries.

`research/launcher/installed_dsh_profile.mjs` invokes the official profile API
with the installed module root supplied by the managed launcher. The installed
branch does not require the branded DSH desktop app.

Generate and verify the complete runtime manifest, run security tests, review
third-party notices, sign the application and installer, then validate native
windows, installation, upgrade, rollback and uninstall under an independent
Windows standard user with synthetic data. This recipe alone is not release
approval.
