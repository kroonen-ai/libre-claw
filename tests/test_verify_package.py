# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest


_SPEC = importlib.util.spec_from_file_location(
    "verify_package", Path(__file__).resolve().parents[1] / "scripts/verify_package.py",
)
assert _SPEC is not None and _SPEC.loader is not None
verify_package = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(verify_package)

STEM = "libre_claw-2.3.4"
METADATA = b"Metadata-Version: 2.4\nName: libre-claw\nVersion: 2.3.4\n\n"
BUNDLE = "libre_claw/cordis_runtime/vendor/cordis.mjs"


def write_wheel(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)


def write_sdist(path: Path, files: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, content in files.items():
            member = tarfile.TarInfo(f"{STEM}/{name}")
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))


@pytest.fixture
def package_project(tmp_path):
    """A small build-shaped archive pair with independent, readable source files."""
    root = tmp_path / "project"
    root.mkdir()
    package = root / "src/libre_claw"
    for relative in verify_package.REQUIRED_PACKAGE_FILES:
        path = package / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"Fixture source: {relative}\n")
    upstream = package / "cordis_runtime/compat/upstream/schema.ts"
    upstream.write_text("export const schema = 'pinned';\n")
    (package / "cordis_runtime/compat/upstream.json").write_text(json.dumps({"files": [
        {"file": "upstream/schema.ts", "sha256": hashlib.sha256(upstream.read_bytes()).hexdigest()},
    ]}))
    (root / "pyproject.toml").write_text(
        '[project]\nname = "libre-claw"\nversion = "2.3.4"\n'
        '[project.scripts]\nlibre-claw = "libre_claw.cli:main"\nlc = "libre_claw.cli:main"\n'
    )
    (root / "LICENSE").write_text("Fixture license\n")
    (root / "package.json").write_text('{"name":"libre-claw-build","private":true}\n')
    (root / "pnpm-workspace.yaml").write_text("packages:\n  - src/libre_claw/cordis_runtime\n")
    (root / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
    (root / "scripts").mkdir()
    for name in ("verify_package.py", "smoke_package.py"):
        (root / "scripts" / name).write_text("# Packaging entry point fixture\n")
    source_files = {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    wheel_files = {name.removeprefix("src/"): data for name, data in source_files.items() if name.startswith("src/")}
    wheel_files.update({
        f"{STEM}.dist-info/METADATA": METADATA,
        f"{STEM}.dist-info/entry_points.txt": b"[console_scripts]\nlc = libre_claw.cli:main\nlibre-claw = libre_claw.cli:main\n",
        f"{STEM}.dist-info/licenses/LICENSE": (root / "LICENSE").read_bytes(),
    })
    sdist_files = {**source_files, "PKG-INFO": METADATA}
    dist = root / "dist"
    dist.mkdir()
    wheel = dist / f"{STEM}-py3-none-any.whl"
    sdist = dist / f"{STEM}.tar.gz"
    write_wheel(wheel, wheel_files)
    write_sdist(sdist, sdist_files)
    return root, wheel, sdist, wheel_files, sdist_files


def test_matching_packages_emit_checksums_for_the_actual_archive_bytes(package_project):
    root, wheel, sdist, _, _ = package_project
    manifest = verify_package.verify_packages(root)
    recorded = json.loads((root / "dist/package-manifest.json").read_text())
    assert recorded == manifest
    assert manifest["name"] == "libre-claw" and manifest["version"] == "2.3.4"
    assert {item["kind"] for item in manifest["artifacts"]} == {"wheel", "sdist"}
    lines = (root / "dist/SHA256SUMS").read_text().splitlines()
    for path in (wheel, sdist):
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        assert f"{checksum}  {path.name}" in lines
        assert next(item for item in manifest["artifacts"] if item["filename"] == path.name)["size_bytes"] == path.stat().st_size


@pytest.mark.parametrize("mutation", ["missing-bundle", "stale-bundle", "wrong-version", "missing-cli-entry"])
def test_incomplete_or_stale_wheels_do_not_receive_a_success_manifest(package_project, mutation):
    root, wheel, _, files, _ = package_project
    if mutation == "missing-bundle":
        del files[BUNDLE]
    elif mutation == "stale-bundle":
        files[BUNDLE] = b"stale runtime build"
    elif mutation == "wrong-version":
        files[f"{STEM}.dist-info/METADATA"] = METADATA.replace(b"2.3.4", b"2.3.3")
    else:
        files[f"{STEM}.dist-info/entry_points.txt"] = b"[console_scripts]\nlc = libre_claw.cli:main\n"
    write_wheel(wheel, files)
    with pytest.raises(verify_package.PackageVerificationError):
        verify_package.verify_packages(root)
    assert not (root / "dist/package-manifest.json").exists()
    assert not (root / "dist/SHA256SUMS").exists()


@pytest.mark.parametrize("missing", ["pnpm-lock.yaml", "scripts/smoke_package.py"])
def test_sdist_must_support_the_documented_pnpm_build_and_smoke_workflow(package_project, missing):
    root, _, source, _, files = package_project
    del files[missing]
    write_sdist(source, files)
    with pytest.raises(verify_package.PackageVerificationError, match="missing required file"):
        verify_package.verify_packages(root)


@pytest.mark.parametrize("junk", [
    "libre_claw/cordis_runtime/node_modules/unsafe/index.js",
    "libre_claw/__pycache__/cli.cpython-314.pyc",
    "libre_claw/.env",
    "libre_claw/.libre-claw/registry.json",
])
def test_dependency_caches_and_private_files_cannot_be_shipped(package_project, junk):
    root, wheel, _, files, _ = package_project
    files[junk] = b"must not ship"
    write_wheel(wheel, files)
    with pytest.raises(verify_package.PackageVerificationError, match="unwanted"):
        verify_package.verify_packages(root)


def test_source_archive_cannot_hide_links_as_required_runtime_files(package_project):
    root, _, source, _, files = package_project
    with tarfile.open(source, "w:gz") as archive:
        for name, content in files.items():
            member = tarfile.TarInfo(f"{STEM}/{name}")
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        link = tarfile.TarInfo(f"{STEM}/src/libre_claw/unreviewed.mjs")
        link.type = tarfile.SYMTYPE
        link.linkname = "/private/unreviewed.mjs"
        archive.addfile(link)
    with pytest.raises(verify_package.PackageVerificationError, match="link or special file"):
        verify_package.verify_packages(root)


def test_source_control_ignore_files_are_not_required_runtime_assets(package_project):
    root, _, _, _, _ = package_project
    (root / "src/libre_claw/cordis_runtime/.gitignore").write_text("node_modules/\n")
    (root / "src/libre_claw/.gitattributes").write_text("*.py text\n")
    assert verify_package.verify_packages(root)["version"] == "2.3.4"
