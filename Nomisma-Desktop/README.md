# Nomisma Desktop

Electron / React research workspace with Economic, Finance and Big Data Explore
modes. See the [project README](../README.md) for source layout and requirements.

```powershell
pnpm install --frozen-lockfile
pnpm build
pnpm test
```

The current development entry and real-Python integration tests require local
runtime configuration. This source release is not a portable Windows installer.
Keep local profiles, credentials, research projects and generated runtime
configuration outside version control.

This is a Nomisma adaptation of [Harnss](https://github.com/OpenSource03/harnss),
v0.21.5, commit `d9a5c62879308c2e517b6e02f6f5db7b3122b3de`.
The original MIT [LICENSE](LICENSE), [upstream README](README.upstream.md) and
[origin record](UPSTREAM_ORIGIN.md) are retained.

GitHub source publication uses MIT and does not require a signing certificate.
Runtime integrity and package-boundary checks remain enabled. Dependencies retain
their own licenses; see [third-party notices](../THIRD_PARTY_NOTICES.md).
