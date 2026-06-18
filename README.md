# isokit

Pack first-boot config scripts (`.sh` / `.ps1`) into a config ISO — an ISO 9660 image with
Joliet + Rock Ridge (real long filenames) plus a `firstboot.manifest` at the root listing the
scripts in execution order. On a deployed VM the first-boot runner finds the disc by that
manifest and runs the scripts in order.

`build_script_iso(script_paths, output_path)` is the single entry point. Depends on `pycdlib`.

Consumed as a git submodule by the `VM-Setup-Scripts` superproject. Install editable for local
dev: `uv pip install -e .`

> Pure library logic — no argparse/print/sys.exit. Collecting paths and reporting output is the
> caller's concern (see the superproject's `cli/`).
