#!/usr/bin/env python3
# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Verify built Python distributions against this checkout without extracting them."""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import re
import stat
import sys
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path
from typing import Any


class PackageVerificationError(ValueError):
    """A built distribution is incomplete, stale, or contains unintended files."""


PACKAGE = "libre_claw"
REQUIRED_PACKAGE_FILES = (
    "__init__.py", "cli.py", "core/cordis.py", "core/cordis_engine.py",
    "cordis_runtime/runtime.mjs", "cordis_runtime/engine.mjs",
    "cordis_runtime/build.mjs", "cordis_runtime/build-tools-compat.mjs",
    "cordis_runtime/package.json", "cordis_runtime/THIRD_PARTY_NOTICES.md",
    "cordis_runtime/vendor/cordis.mjs", "cordis_runtime/vendor/cosmokit.mjs",
    "cordis_runtime/vendor/schemastery.mjs", "cordis_runtime/vendor/zod.mjs",
    "cordis_runtime/vendor/harness-tools.mjs", "cordis_runtime/vendor/harness-messages.mjs",
    "cordis_runtime/vendor/harness-values.mjs", "cordis_runtime/vendor/diff.mjs", "cordis_runtime/vendor/DIFF-LICENSE",
    "cordis_runtime/vendor/CORDIS-LICENSE", "cordis_runtime/vendor/COSMOKIT-LICENSE",
    "cordis_runtime/vendor/SCHEMASTERY-LICENSE", "cordis_runtime/vendor/ZOD-LICENSE",
    "cordis_runtime/compat/tools.mjs", "cordis_runtime/compat/config.mjs",
    "cordis_runtime/compat/services.mjs", "cordis_runtime/compat/loader.mjs",
    "cordis_runtime/compat/host-services.mjs", "core/cordis_harness_services.py",
    "cordis_runtime/ptc-runner.mjs", "core/cordis_ptc.py", "cordis_runtime/vendor/client-react.mjs",
    "cordis_runtime/compat/ptc-typescript.mjs", "cordis_runtime/vendor/ptc-typescript.mjs",
    "cordis_runtime/vendor/TYPESCRIPT-LICENSE", "cordis_runtime/vendor/TYPESCRIPT-THIRD-PARTY-NOTICES",
    "core/cordis_bindings.py", "core/cordis_engine_plugins.py", "core/cordis_lsp.py", "core/cordis_process.py",
    "cordis_runtime/client.mjs", "cordis_runtime/client-schema.json",
    "core/cordis_client.py", "core/cordis_client_manifest.py", "web/assets/client-view.mjs", "web/client_plugin_api.py",
    "cordis_runtime/vendor/REACT-LICENSE", "cordis_runtime/vendor/REACT-RECONCILER-LICENSE",
    "cordis_runtime/vendor/SCHEDULER-LICENSE", "cordis_runtime/vendor/LOOSE-ENVIFY-LICENSE", "cordis_runtime/vendor/JS-TOKENS-LICENSE",
    "cordis_runtime/compat/upstream.json", "cordis_runtime/compat/upstream/LICENSE",
    "cordis_runtime/examples/text-utilities/plugin.mjs",
    "cordis_runtime/examples/text-utilities/libre-claw-plugin.json",
    "cordis_runtime/examples/orchestration/libre-claw-plugin.json",
    "cordis_runtime/examples/orchestration/plugin.mjs",
    "cordis_runtime/examples/native-provider/package.json",
    "cordis_runtime/examples/native-provider/cordis.patch.yml",
    "cordis_runtime/examples/native-provider/runtime/native-provider-plugin.js",
    "cordis_runtime/examples/native-provider/runtime/native-provider-protocol.js",
    "cordis_runtime/examples/native-provider/LICENSE",
    "web/assets/cordis-ui.mjs", "web/dashboard.py", "web/dashboard_styles.py",
)
SDIST_ROOT_FILES = (
    "pyproject.toml", "package.json", "pnpm-workspace.yaml", "pnpm-lock.yaml",
    "scripts/verify_package.py", "scripts/smoke_package.py", "LICENSE",
)
_IGNORED_PARTS = frozenset({
    "node_modules", "__pycache__", ".git", ".venv", "venv", ".tox", ".nox",
    ".cache", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".pnpm-store",
    ".pnpm", ".libre-claw",
})
_PRIVATE_FILES = frozenset({
    ".env", ".npmrc", ".pypirc", ".DS_Store", "Thumbs.db", ".coverage",
    "auth.json", "credentials.json", "secrets.json", "keys.json",
})
_PRIVATE_SUFFIXES = frozenset({".pyc", ".pyo", ".pem", ".key", ".p12", ".pfx", ".log"})
_SOURCE_CONTROL_FILES = frozenset({".gitignore", ".gitattributes", ".gitkeep"})
_MAX_FILE_BYTES = 64 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 256 * 1024 * 1024


def _unwanted(parts: tuple[str, ...]) -> bool:
    name = parts[-1]
    return (any(part in _IGNORED_PARTS or part.endswith(".egg-info") for part in parts)
            or name in _PRIVATE_FILES or name.startswith(".env.")
            or Path(name).suffix.lower() in _PRIVATE_SUFFIXES)


def _member_path(name: str, *, source_archive: bool = False) -> tuple[str, ...]:
    parts = tuple(name.rstrip("/").split("/"))
    if (not parts or "\\" in name or "\0" in name or re.match(r"^[A-Za-z]:", name)
            or any(part in {"", ".", ".."} for part in parts)):
        raise PackageVerificationError("Distribution contains an unsafe archive path.")
    relative = parts[1:] if source_archive else parts
    if relative and (_unwanted(relative) or relative[0] in {"build", "dist"}):
        raise PackageVerificationError(f"Distribution contains unwanted build or private files: {name}")
    return parts


def _source_files(root: Path) -> dict[str, bytes]:
    package_root = root / "src" / PACKAGE
    files = {}
    for directory, directories, names in os.walk(package_root, followlinks=False):
        parent = Path(directory)
        directories[:] = sorted(name for name in directories if not _unwanted((name,)))
        for name in directories:
            if (parent / name).is_symlink():
                raise PackageVerificationError("Package source directories cannot be symbolic links.")
        for name in sorted(names):
            relative = (parent / name).relative_to(package_root)
            if _unwanted(relative.parts) or name in _SOURCE_CONTROL_FILES:
                continue
            path = parent / name
            if path.is_symlink() or not path.is_file():
                raise PackageVerificationError(f"Package source is not a regular file: {relative}")
            files[relative.as_posix()] = path.read_bytes()
    missing = sorted(set(REQUIRED_PACKAGE_FILES) - files.keys())
    if missing:
        raise PackageVerificationError("Required source assets are missing: " + ", ".join(missing))
    upstream = json.loads(files["cordis_runtime/compat/upstream.json"])
    for item in upstream["files"]:
        relative = "cordis_runtime/compat/" + item["file"]
        _member_path(relative)
        if relative not in files or hashlib.sha256(files[relative]).hexdigest() != item["sha256"]:
            raise PackageVerificationError(f"Pinned Harness source is missing or changed: {relative}")
    return files


def _wheel_files(path: Path) -> dict[str, bytes]:
    files = {}
    total = 0
    seen = set()
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            parts = _member_path(member.filename)
            key = "/".join(parts)
            if key in seen:
                raise PackageVerificationError(f"Wheel contains a duplicate path: {key}")
            seen.add(key)
            kind = stat.S_IFMT(member.external_attr >> 16)
            if kind not in {0, stat.S_IFREG, stat.S_IFDIR} or member.flag_bits & 1:
                raise PackageVerificationError("Wheel contains a link, special file, or encrypted entry.")
            if member.is_dir():
                continue
            total += member.file_size
            if member.file_size > _MAX_FILE_BYTES or total > _MAX_ARCHIVE_BYTES:
                raise PackageVerificationError("Wheel exceeds the verification size limit.")
            files[key] = archive.read(member)
    return files


def _sdist_files(path: Path, prefix: str) -> dict[str, bytes]:
    files = {}
    seen = set()
    total = 0
    with tarfile.open(path, mode="r:gz") as archive:
        for member in archive:
            parts = _member_path(member.name, source_archive=True)
            if parts[0] != prefix:
                raise PackageVerificationError("Source archive has an unexpected root directory.")
            key = "/".join(parts[1:])
            if key in seen:
                raise PackageVerificationError(f"Source archive contains a duplicate path: {key}")
            seen.add(key)
            if not (member.isdir() or member.isfile()) or member.issparse():
                raise PackageVerificationError("Source archive contains a link or special file.")
            if member.isdir():
                continue
            total += member.size
            if member.size > _MAX_FILE_BYTES or total > _MAX_ARCHIVE_BYTES:
                raise PackageVerificationError("Source archive exceeds the verification size limit.")
            handle = archive.extractfile(member)
            if handle is None:
                raise PackageVerificationError("Source archive contains an unreadable file.")
            with handle:
                files[key] = handle.read()
    return files


def _matching_bytes(files: dict[str, bytes], name: str, expected: bytes, artifact: str) -> None:
    if name not in files:
        raise PackageVerificationError(f"{artifact} is missing required file: {name}")
    if files[name] != expected:
        raise PackageVerificationError(f"{artifact} contains stale or modified source bytes: {name}")


def _metadata(data: bytes, project: dict[str, Any], artifact: str) -> None:
    metadata = BytesParser().parsebytes(data)
    normalize = lambda value: re.sub(r"[-_.]+", "-", value).lower()
    if (normalize(metadata.get("Name", "")) != normalize(project["name"])
            or metadata.get("Version") != project["version"]):
        raise PackageVerificationError(f"{artifact} metadata does not match the project name and version.")


def _artifact_record(path: Path, kind: str) -> dict[str, Any]:
    with path.open("rb") as handle:
        checksum = hashlib.file_digest(handle, "sha256").hexdigest()
    return {"filename": path.name, "kind": kind, "size_bytes": path.stat().st_size, "sha256": checksum}


def verify_packages(root: Path, distribution_directory: Path | None = None) -> dict[str, Any]:
    """Verify source equivalence and publish checksums only after both archives pass."""
    root = root.resolve()
    distribution_directory = distribution_directory or root / "dist"
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    name, version = project["name"], project["version"]
    stem = re.sub(r"[-_.]+", "_", name).lower() + "-" + version
    wheels = sorted(distribution_directory.glob("*.whl"))
    sources = sorted(distribution_directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise PackageVerificationError("Build exactly one wheel and one source archive in dist before verifying.")
    wheel, source = wheels[0], sources[0]
    if not wheel.name.startswith(stem + "-") or source.name != stem + ".tar.gz":
        raise PackageVerificationError("Distribution filenames do not match the project name and version.")
    for artifact in (wheel, source):
        if artifact.is_symlink() or not artifact.is_file():
            raise PackageVerificationError("Distribution artifacts must be regular files, not links.")
    expected = _source_files(root)
    wheel_files = _wheel_files(wheel)
    sdist_files = _sdist_files(source, stem)
    for path, data in expected.items():
        _matching_bytes(wheel_files, PACKAGE + "/" + path, data, "Wheel")
        _matching_bytes(sdist_files, "src/" + PACKAGE + "/" + path, data, "Source archive")
    for path in SDIST_ROOT_FILES:
        _matching_bytes(sdist_files, path, (root / path).read_bytes(), "Source archive")
    metadata_prefix = stem + ".dist-info/"
    if {path.split("/", 1)[0] for path in wheel_files if ".dist-info/" in path} != {stem + ".dist-info"}:
        raise PackageVerificationError("Wheel must contain exactly its own distribution metadata.")
    for path in (metadata_prefix + "METADATA", metadata_prefix + "entry_points.txt", "PKG-INFO"):
        files = sdist_files if path == "PKG-INFO" else wheel_files
        if path not in files:
            raise PackageVerificationError(f"Distribution is missing required metadata: {path}")
    _metadata(wheel_files[metadata_prefix + "METADATA"], project, "Wheel")
    _metadata(sdist_files["PKG-INFO"], project, "Source archive")
    entry_points = configparser.ConfigParser(interpolation=None)
    entry_points.optionxform = str
    entry_points.read_string(wheel_files[metadata_prefix + "entry_points.txt"].decode())
    if not entry_points.has_section("console_scripts") or dict(entry_points["console_scripts"]) != project["scripts"]:
        raise PackageVerificationError("Wheel console entry points do not match [project.scripts].")
    _matching_bytes(wheel_files, metadata_prefix + "licenses/LICENSE", (root / "LICENSE").read_bytes(), "Wheel")
    artifacts = [_artifact_record(wheel, "wheel"), _artifact_record(source, "sdist")]
    manifest = {"schema_version": 1, "name": name, "version": version,
                "verified_package_files": len(expected), "artifacts": artifacts}
    checksums = "".join(f"{item['sha256']}  {item['filename']}\n" for item in sorted(artifacts, key=lambda item: item["filename"]))
    (distribution_directory / "SHA256SUMS").write_text(checksums)
    (distribution_directory / "package-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main() -> int:
    try:
        manifest = verify_packages(Path(__file__).resolve().parents[1])
    except (PackageVerificationError, OSError, ValueError, KeyError, tarfile.TarError, zipfile.BadZipFile, configparser.Error) as error:
        print(f"Package verification failed: {error}", file=sys.stderr)
        return 1
    print(f"Verified {manifest['name']} {manifest['version']}: wheel and sdist, {manifest['verified_package_files']} matching package files.")
    print("Wrote dist/SHA256SUMS and dist/package-manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
