# isokit

Pack first-boot config scripts (`.sh` / `.ps1` / `.cmd` / `.bat`) — and, since 0.2.0,
arbitrary payload files — into a config ISO: an ISO 9660 image with Joliet + Rock Ridge
(real long filenames) plus a `firstboot.manifest` at the root. On a deployed VM the
first-boot runner finds the disc by that manifest, runs the listed scripts in order, and
(v2) stages the listed payload files for those scripts to consume.

## API

- `build_script_iso(script_paths, output_path) -> list[str]` — legacy firstboot-only
  packing. Emits a **version-1** manifest (`{"version": 1, "scripts": [...]}`) and always
  will; its output is a compatibility contract with pinned consumers.
- `build_config_iso(output_path, *, scripts=(), files=()) -> IsoContents` — generalized
  packing. `scripts` are UTF-8 text, EOL-normalized per suffix (CRLF for `.ps1`/`.cmd`/
  `.bat`, LF otherwise), executed by the runner in order. `files` are copied
  byte-for-byte (binaries welcome) and never executed. Emits a **version-2** manifest.

## Version-2 manifest contract

```json
{
  "version": 2,
  "scripts": ["10-hostname.ps1", "30-install.cmd"],
  "files":   ["orchestrator.exe"]
}
```

Load-bearing rules (the runners in VM-Setup-Scripts depend on them — coordinate releases):

- Key order is exactly `version`, `scripts`, `files`, serialized `json.dumps(..., indent=2)`
  so each array entry sits on its own line. The legacy (v1) Linux runner parses `scripts`
  with a sed range over that block; this layout is what lets a v1 runner execute a v2
  disc's scripts while silently ignoring `files`. The v1 Windows runner parses real JSON
  and tolerates the extra key natively.
- Both keys are always present in v2, even when empty. A scripts-empty disc is legal but
  warned against: v1 runners reject it, v2 runners stage the files transiently and then
  discard them.
- One flat root namespace: no duplicate basenames across `scripts` + `files`; nothing may
  be named `firstboot.manifest`.

Limits: single files cap at 4 GiB − 1 (interchange level 3, no multi-extent); Joliet long
names cap at 64 characters (pycdlib raises beyond).

Depends on `pycdlib`. Tests: `uv run pytest`.

Consumed as a versioned git dependency by the `VM-Setup-Scripts` superproject and the
EC-PKI-Playground backend. Install editable for local dev: `uv pip install -e .`

> Pure library logic — no argparse/print/sys.exit. Collecting paths and reporting output is the
> caller's concern (see the superproject's `cli/`).
