"""Round-trip tests for isokit's ISO packing.

The v1 tests are a lockdown: `build_script_iso` output is a compatibility contract with
consumers pinned to old tags, so its manifest shape and payload bytes must never drift.
The v2 tests pin the `build_config_iso` contract, including the manifest text layout the
legacy Linux runner's sed parser depends on.
"""

import hashlib
import io
import json
import os
import subprocess
from pathlib import Path

import pycdlib
import pytest

from isokit import (
    CONFIG_MANIFEST_VERSION,
    MANIFEST_NAME,
    MANIFEST_VERSION,
    build_config_iso,
    build_script_iso,
)

# The exact manifest-parsing pipeline from VM-Setup-Scripts
# base-vm-setup/linux-server/firstboot-setup.sh (the v1 Linux runner). A v2 manifest
# must keep satisfying it.
LEGACY_SED_PIPELINE = (
    "sed -n '/\"scripts\"[[:space:]]*:[[:space:]]*\\[/,/\\]/{/\\[/d;/\\]/d;p}' \"$1\""
    " | grep -oE '\"[^\"]+\"' | tr -d '\"'"
)


def _joliet_names(iso: pycdlib.PyCdlib) -> list[str]:
    names = []
    for child in iso.list_children(joliet_path="/"):
        if child is None or child.is_dot() or child.is_dotdot():
            continue
        names.append(child.file_identifier().decode("utf-16-be"))
    return names


def read_back(iso_path: Path) -> dict[str, bytes]:
    """Read every root file out of the ISO by its Joliet (long) name."""
    iso = pycdlib.PyCdlib()
    iso.open(str(iso_path))
    try:
        out = {}
        for name in _joliet_names(iso):
            buf = io.BytesIO()
            iso.get_file_from_iso_fp(buf, joliet_path=f"/{name}")
            out[name] = buf.getvalue()
        return out
    finally:
        iso.close()


def iso9660_names(iso_path: Path) -> set[str]:
    """The plain ISO 9660 (8.3;1) root names — the fallback namespace."""
    iso = pycdlib.PyCdlib()
    iso.open(str(iso_path))
    try:
        names = set()
        for child in iso.list_children(iso_path="/"):
            if child is None or child.is_dot() or child.is_dotdot():
                continue
            names.add(child.file_identifier().decode("ascii"))
        return names
    finally:
        iso.close()


def write(tmp_path: Path, name: str, content: str | bytes) -> Path:
    p = tmp_path / name
    if isinstance(content, bytes):
        p.write_bytes(content)
    else:
        p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# v1 lockdown — build_script_iso must never drift
# ---------------------------------------------------------------------------


def test_v1_manifest_shape_and_order(tmp_path):
    a = write(tmp_path, "20-b.ps1", "Write-Host hi\n")
    b = write(tmp_path, "10-a.sh", "#!/bin/sh\necho hi\n")
    out = tmp_path / "out.iso"

    returned = build_script_iso([a, b], out)

    contents = read_back(out)
    manifest = json.loads(contents[MANIFEST_NAME])
    assert set(manifest) == {"version", "scripts"}, "v1 manifest gained/lost keys"
    assert manifest["version"] == MANIFEST_VERSION == 1
    assert manifest["scripts"] == ["20-b.ps1", "10-a.sh"] == returned  # received order


def test_v1_eol_normalization(tmp_path):
    ps1 = write(tmp_path, "s.ps1", "line1\nline2\r\nline3\rline4\n")
    sh = write(tmp_path, "s.sh", "#!/bin/sh\r\necho ok\rdone\n")
    out = tmp_path / "out.iso"

    build_script_iso([ps1, sh], out)

    contents = read_back(out)
    assert contents["s.ps1"] == b"line1\r\nline2\r\nline3\r\nline4\r\n"
    assert contents["s.sh"] == b"#!/bin/sh\necho ok\ndone\n"


def test_v1_cmd_and_bat_get_crlf(tmp_path):
    cmd = write(tmp_path, "s.cmd", "@echo off\necho hi\n")
    bat = write(tmp_path, "s.bat", "@echo off\necho hi\n")
    out = tmp_path / "out.iso"

    build_script_iso([cmd, bat], out)

    contents = read_back(out)
    assert contents["s.cmd"] == b"@echo off\r\necho hi\r\n"
    assert contents["s.bat"] == b"@echo off\r\necho hi\r\n"


def test_v1_iso9660_names(tmp_path):
    a = write(tmp_path, "10-hostname.ps1", "x\n")
    b = write(tmp_path, "20-network.sh", "y\n")
    out = tmp_path / "out.iso"

    build_script_iso([a, b], out)

    assert iso9660_names(out) == {"SCRIPT01.PS1;1", "SCRIPT02.SH;1", "FIRSTBT.MAN;1"}


def test_v1_duplicate_name_rejected(tmp_path):
    d = tmp_path / "other"
    d.mkdir()
    a = write(tmp_path, "same.ps1", "a\n")
    b = write(d, "same.ps1", "b\n")

    with pytest.raises(ValueError, match="Duplicate script name"):
        build_script_iso([a, b], tmp_path / "out.iso")


def test_v1_empty_rejected(tmp_path):
    with pytest.raises(ValueError, match="No scripts"):
        build_script_iso([], tmp_path / "out.iso")


# ---------------------------------------------------------------------------
# v2 — build_config_iso
# ---------------------------------------------------------------------------


def test_v2_manifest_shape_and_layout(tmp_path):
    s1 = write(tmp_path, "10-a.ps1", "a\n")
    s2 = write(tmp_path, "30-c.cmd", "c\n")
    f1 = write(tmp_path, "payload.json", "{}\n")
    out = tmp_path / "out.iso"

    result = build_config_iso(out, scripts=[s1, s2], files=[f1])

    assert result.manifest_version == CONFIG_MANIFEST_VERSION == 2
    assert result.scripts == ["10-a.ps1", "30-c.cmd"]
    assert result.files == ["payload.json"]

    text = read_back(out)[MANIFEST_NAME].decode("utf-8")
    manifest = json.loads(text)
    assert list(manifest) == ["version", "scripts", "files"]  # key order is contract
    assert manifest == {
        "version": 2,
        "scripts": ["10-a.ps1", "30-c.cmd"],
        "files": ["payload.json"],
    }
    # Layout contract for the legacy sed parser: scripts block before files, each
    # entry alone on its own line.
    assert text.index('"scripts"') < text.index('"files"')
    stripped_lines = [line.strip().rstrip(",") for line in text.splitlines()]
    for entry in ("10-a.ps1", "30-c.cmd", "payload.json"):
        assert f'"{entry}"' in stripped_lines


def test_v2_survives_legacy_linux_sed_parser(tmp_path):
    s1 = write(tmp_path, "10-a.ps1", "a\n")
    s2 = write(tmp_path, "20-b.sh", "b\n")
    f1 = write(tmp_path, "blob.bin", b"\x00\x01")
    out = tmp_path / "out.iso"

    build_config_iso(out, scripts=[s1, s2], files=[f1])

    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_bytes(read_back(out)[MANIFEST_NAME])
    parsed = subprocess.run(
        ["bash", "-c", LEGACY_SED_PIPELINE, "_", str(manifest_file)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert parsed.stdout.split() == ["10-a.ps1", "20-b.sh"]  # files never leak in


def test_v2_binary_file_roundtrip(tmp_path):
    blob = b"\x00\r\n\r\x1a" + os.urandom(1024 * 1024)
    f = write(tmp_path, "orchestrator.bin", blob)
    s = write(tmp_path, "10-a.ps1", "a\n")
    out = tmp_path / "out.iso"

    build_config_iso(out, scripts=[s], files=[f])

    got = read_back(out)["orchestrator.bin"]
    assert hashlib.sha256(got).hexdigest() == hashlib.sha256(blob).hexdigest()


def test_v2_scripts_normalized_files_untouched(tmp_path):
    s = write(tmp_path, "s.ps1", "a\nb\n")
    f = write(tmp_path, "notes.txt", "a\nb\n")
    out = tmp_path / "out.iso"

    build_config_iso(out, scripts=[s], files=[f])

    contents = read_back(out)
    assert contents["s.ps1"] == b"a\r\nb\r\n"
    assert contents["notes.txt"] == b"a\nb\n"  # files bypass EOL normalization


def test_v2_iso9660_file_names(tmp_path):
    s = write(tmp_path, "10-a.ps1", "a\n")
    f1 = write(tmp_path, "tool.exe", b"MZ")
    f2 = write(tmp_path, "noext", b"x")
    out = tmp_path / "out.iso"

    build_config_iso(out, scripts=[s], files=[f1, f2])

    assert iso9660_names(out) == {
        "SCRIPT01.PS1;1",
        "FILE01.EXE;1",
        "FILE02.DAT;1",
        "FIRSTBT.MAN;1",
    }


def test_v2_duplicate_across_namespaces_rejected(tmp_path):
    d = tmp_path / "other"
    d.mkdir()
    s = write(tmp_path, "same.ps1", "a\n")
    f = write(d, "same.ps1", "b\n")

    with pytest.raises(ValueError, match="Duplicate name"):
        build_config_iso(tmp_path / "out.iso", scripts=[s], files=[f])


def test_v2_duplicate_within_files_rejected(tmp_path):
    d = tmp_path / "other"
    d.mkdir()
    s = write(tmp_path, "10-a.ps1", "a\n")
    f1 = write(tmp_path, "same.bin", b"a")
    f2 = write(d, "same.bin", b"b")

    with pytest.raises(ValueError, match="Duplicate name"):
        build_config_iso(tmp_path / "out.iso", scripts=[s], files=[f1, f2])


def test_v2_manifest_name_reserved(tmp_path):
    s = write(tmp_path, "10-a.ps1", "a\n")
    f = write(tmp_path, MANIFEST_NAME, "{}")

    with pytest.raises(ValueError, match="reserved"):
        build_config_iso(tmp_path / "out.iso", scripts=[s], files=[f])


def test_v2_empty_scripts_warns(tmp_path):
    f = write(tmp_path, "payload.bin", b"x")
    out = tmp_path / "out.iso"

    with pytest.warns(UserWarning, match="no firstboot scripts"):
        result = build_config_iso(out, files=[f])

    assert result.scripts == []
    assert result.files == ["payload.bin"]
    manifest = json.loads(read_back(out)[MANIFEST_NAME])
    assert manifest["scripts"] == []  # key present even when empty


def test_v2_both_empty_rejected(tmp_path):
    with pytest.raises(ValueError, match="No scripts or files"):
        build_config_iso(tmp_path / "out.iso")


def test_v2_entry_count_capped(tmp_path):
    fake = [tmp_path / f"s{i}.ps1" for i in range(100)]  # validated before reading

    with pytest.raises(ValueError, match="Too many scripts"):
        build_config_iso(tmp_path / "out.iso", scripts=fake)
    with pytest.raises(ValueError, match="Too many files"):
        build_config_iso(tmp_path / "out.iso", files=fake)


def test_v1_v2_script_payloads_identical(tmp_path):
    a = write(tmp_path, "10-a.ps1", "one\ntwo\r\n")
    b = write(tmp_path, "20-b.sh", "#!/bin/sh\r\necho ok\n")
    v1 = tmp_path / "v1.iso"
    v2 = tmp_path / "v2.iso"

    build_script_iso([a, b], v1)
    build_config_iso(v2, scripts=[a, b])

    c1, c2 = read_back(v1), read_back(v2)
    for name in ("10-a.ps1", "20-b.sh"):
        assert c1[name] == c2[name]
    assert json.loads(c1[MANIFEST_NAME])["scripts"] == json.loads(c2[MANIFEST_NAME])["scripts"]
