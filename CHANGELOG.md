# Changelog

## 0.2.0

- New `build_config_iso(output_path, *, scripts=(), files=())` entry point: packs
  firstboot scripts plus arbitrary payload files (raw bytes — binaries welcome, never
  executed) and writes a **version-2 manifest** `{"version": 2, "scripts": [...],
  "files": [...]}`. Returns an `IsoContents` dataclass. Exported alongside
  `CONFIG_MANIFEST_VERSION = 2`.
- Payload files get `/FILENN.EXT;1` ISO 9660 fallback names (extensionless → `.DAT`)
  and are streamed from disk at write time (large binaries are not loaded into memory).
- Manifest v2 layout is a documented contract: key order `version, scripts, files`,
  `indent=2`, one array entry per line — a v1 runner's `scripts` parsing (including the
  legacy Linux sed pipeline) keeps working against v2 manifests, ignoring `files`.
- `build_script_iso` is **unchanged** and stays on the version-1 manifest permanently;
  its output is locked byte-for-byte by the new test suite.
- EOL normalization fix (applies to both entry points): `.cmd`/`.bat` scripts are now
  normalized to CRLF like `.ps1` (LF batch files misbehave in cmd.exe). No existing
  caller packed `.cmd`/`.bat`, so this is a bug-fix, not a break.
- First test suite (`tests/test_pack.py`, pytest): v1 lockdown, v2 round-trips, binary
  integrity, legacy-sed-parser regression.

## 0.1.0

- Initial release: `build_script_iso` — firstboot-only packing, version-1
  `firstboot.manifest`, Joliet + Rock Ridge, per-type EOL normalization.
