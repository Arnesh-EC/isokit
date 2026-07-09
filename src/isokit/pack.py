"""ISO packing core — build a Joliet+Rock-Ridge config ISO from first-boot scripts.

Pure library logic (no argparse/print/sys.exit): the CLI/API caller collects the script
paths and handles output. Builds an ISO 9660 image carrying the scripts plus a
`firstboot.manifest` at the root, in the order received. Line endings are normalized per
type (CRLF for .ps1/.cmd/.bat, LF for .sh — CRLF would break a shell shebang).

Two entry points:

- `build_script_iso` — legacy firstboot-only packing. Emits a version-1 manifest
  (`{"version": 1, "scripts": [...]}`) and MUST keep doing so byte-for-byte: consumers
  pinned to older tags re-pin expecting identical output.
- `build_config_iso` — generalized packing. Scripts (text, EOL-normalized, executed by
  the runner in manifest order) plus arbitrary payload files (raw bytes, never executed,
  staged by the runner for scripts to consume). Emits a version-2 manifest.

Version-2 manifest contract (load-bearing for the first-boot runners — do not change
without coordinating a VM-Setup-Scripts release):

    {
      "version": 2,
      "scripts": ["10-hostname.ps1", ...],
      "files":   ["payload.bin", ...]
    }

- Key order is exactly `version`, `scripts`, `files`, serialized with
  `json.dumps(..., indent=2)` so each array element sits on its own line. The legacy
  Linux runner extracts the scripts with a sed range over the `"scripts": [...]` block;
  this layout is what keeps a v1 runner working against a v2 manifest.
- Both keys are always present in v2, even when empty.
- All names share one flat root namespace: no duplicates across scripts + files, and no
  entry may be named `firstboot.manifest`.

Limits: single files are capped at 4 GiB - 1 (ISO 9660 interchange level 3 without
multi-extent records); Joliet caps long names at 64 characters (pycdlib raises beyond).
"""

import io
import json
import warnings
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

import pycdlib

MANIFEST_NAME = "firstboot.manifest"
MANIFEST_VERSION = 1
CONFIG_MANIFEST_VERSION = 2

# Two-digit 8.3 index (SCRIPTNN.EXT / FILENN.EXT) caps each list.
_MAX_ENTRIES = 99


@dataclass(frozen=True)
class IsoContents:
    """What `build_config_iso` wrote: manifest version plus the ordered name lists
    exactly as they appear in the manifest (order == execution/staging order)."""

    manifest_version: int
    scripts: list[str]
    files: list[str]


def _to_lf(text: str) -> str:
    """Normalize any line endings to LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _normalize_eol(text: str, suffix: str) -> str:
    """Normalize line endings per script type: CRLF for Windows script types (.ps1,
    .cmd, .bat — cmd.exe label/goto scanning misbehaves on LF batch files), LF for
    shell (.sh -- CRLF would break the shebang)."""
    lf = _to_lf(text)
    return lf.replace("\n", "\r\n") if suffix.lower() in {".ps1", ".cmd", ".bat"} else lf


def _iso9660_name(index: int, suffix: str, prefix: str = "SCRIPT") -> str:
    """A valid ISO 9660 (8.3, uppercase, ;1) name. The Joliet/RR name carries
    the real long filename; this is only the fallback for level-1 readers."""
    ext = suffix.lstrip(".").upper()[:3] or "DAT"
    return f"/{prefix}{index:02d}.{ext};1"


def _add_script(iso: pycdlib.PyCdlib, path: Path, index: int) -> None:
    """Add one script: UTF-8 text, EOL-normalized per suffix."""
    data = _normalize_eol(path.read_text(encoding="utf-8"), path.suffix).encode("utf-8")
    iso.add_fp(
        io.BytesIO(data),
        len(data),
        _iso9660_name(index, path.suffix),
        joliet_path=f"/{path.name}",
        rr_name=path.name,
    )


def _add_manifest(iso: pycdlib.PyCdlib, manifest: dict) -> None:
    """Write the manifest at the ISO root. `indent=2` layout is part of the contract
    (see module docstring); dict insertion order becomes key order."""
    manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
    iso.add_fp(
        io.BytesIO(manifest_bytes),
        len(manifest_bytes),
        "/FIRSTBT.MAN;1",
        joliet_path=f"/{MANIFEST_NAME}",
        rr_name=MANIFEST_NAME,
    )


def build_script_iso(script_paths: list[Path], output_path: Path) -> list[str]:
    """Pack the given script files into a Joliet+Rock-Ridge ISO at output_path,
    with a firstboot.manifest listing them in the order received.

    Legacy entry point: emits a version-1 manifest and always will (consumers expect
    byte-identical output across upgrades). Use `build_config_iso` for payload files.

    Returns the ordered list of script filenames written to the manifest.
    """
    if not script_paths:
        raise ValueError("No scripts to package.")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, joliet=3, rock_ridge="1.09")

    ordered_names: list[str] = []
    for index, path in enumerate(script_paths, start=1):
        name = path.name
        if name in ordered_names:
            raise ValueError(f"Duplicate script name '{name}' in bundle.")
        _add_script(iso, path, index)
        ordered_names.append(name)

    _add_manifest(iso, {"version": MANIFEST_VERSION, "scripts": ordered_names})

    iso.write(str(output_path))
    iso.close()
    return ordered_names


def build_config_iso(
    output_path: Path,
    *,
    scripts: Sequence[Path] = (),
    files: Sequence[Path] = (),
) -> IsoContents:
    """Pack firstboot scripts plus arbitrary payload files into a Joliet+Rock-Ridge
    ISO at output_path, with a version-2 firstboot.manifest.

    `scripts` are UTF-8 text, EOL-normalized per suffix, and executed by the first-boot
    runner in the order received. `files` are copied byte-for-byte (binaries welcome)
    and never executed — the runner stages them for scripts to consume.

    Returns the manifest contents as written.
    """
    if not scripts and not files:
        raise ValueError("No scripts or files to package.")
    if len(scripts) > _MAX_ENTRIES:
        raise ValueError(f"Too many scripts ({len(scripts)}); the limit is {_MAX_ENTRIES}.")
    if len(files) > _MAX_ENTRIES:
        raise ValueError(f"Too many files ({len(files)}); the limit is {_MAX_ENTRIES}.")
    if not scripts:
        warnings.warn(
            "Packing a config ISO with no firstboot scripts: version-1 runners reject "
            "such a disc, and version-2 runners stage the files transiently and then "
            "discard them.",
            stacklevel=2,
        )

    seen: set[str] = set()
    for path in [*scripts, *files]:
        name = path.name
        if name == MANIFEST_NAME:
            raise ValueError(f"'{MANIFEST_NAME}' is reserved for the manifest itself.")
        if name in seen:
            raise ValueError(f"Duplicate name '{name}' in bundle.")
        seen.add(name)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, joliet=3, rock_ridge="1.09")

    script_names: list[str] = []
    file_names: list[str] = []
    # pycdlib reads file contents lazily at write() time, so payload handles must stay
    # open until the image is written — hence the ExitStack around write().
    with ExitStack() as stack:
        for index, path in enumerate(scripts, start=1):
            _add_script(iso, path, index)
            script_names.append(path.name)

        for index, path in enumerate(files, start=1):
            fp = stack.enter_context(path.open("rb"))
            iso.add_fp(
                fp,
                path.stat().st_size,
                _iso9660_name(index, path.suffix, prefix="FILE"),
                joliet_path=f"/{path.name}",
                rr_name=path.name,
            )
            file_names.append(path.name)

        _add_manifest(
            iso,
            {
                "version": CONFIG_MANIFEST_VERSION,
                "scripts": script_names,
                "files": file_names,
            },
        )

        iso.write(str(output_path))
    iso.close()
    return IsoContents(
        manifest_version=CONFIG_MANIFEST_VERSION,
        scripts=script_names,
        files=file_names,
    )
