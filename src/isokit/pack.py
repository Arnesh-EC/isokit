"""ISO packing core — build a Joliet+Rock-Ridge config ISO from first-boot scripts.

Pure library logic (no argparse/print/sys.exit): the CLI/API caller collects the script
paths and handles output. Builds an ISO 9660 image carrying the scripts plus a
`firstboot.manifest` at the root, in the order received. Line endings are normalized per
type (CRLF for .ps1, LF for .sh — CRLF would break a shell shebang).
"""

import io
import json
from pathlib import Path

import pycdlib

MANIFEST_NAME = "firstboot.manifest"
MANIFEST_VERSION = 1


def _to_lf(text: str) -> str:
    """Normalize any line endings to LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _normalize_eol(text: str, suffix: str) -> str:
    """Normalize line endings per script type: CRLF for PowerShell (.ps1, safest
    read on Windows), LF for shell (.sh -- CRLF would break the shebang)."""
    lf = _to_lf(text)
    return lf.replace("\n", "\r\n") if suffix.lower() == ".ps1" else lf


def _iso9660_name(index: int, suffix: str) -> str:
    """A valid ISO 9660 (8.3, uppercase, ;1) name. The Joliet/RR name carries
    the real long filename; this is only the fallback for level-1 readers."""
    ext = suffix.lstrip(".").upper()[:3] or "DAT"
    return f"/SCRIPT{index:02d}.{ext};1"


def build_script_iso(script_paths: list[Path], output_path: Path) -> list[str]:
    """Pack the given script files into a Joliet+Rock-Ridge ISO at output_path,
    with a firstboot.manifest listing them in the order received.

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
        data = _normalize_eol(
            path.read_text(encoding="utf-8"), path.suffix
        ).encode("utf-8")
        iso.add_fp(
            io.BytesIO(data),
            len(data),
            _iso9660_name(index, path.suffix),
            joliet_path=f"/{name}",
            rr_name=name,
        )
        ordered_names.append(name)

    manifest = {"version": MANIFEST_VERSION, "scripts": ordered_names}
    manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
    iso.add_fp(
        io.BytesIO(manifest_bytes),
        len(manifest_bytes),
        "/FIRSTBT.MAN;1",
        joliet_path=f"/{MANIFEST_NAME}",
        rr_name=MANIFEST_NAME,
    )

    iso.write(str(output_path))
    iso.close()
    return ordered_names
