# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Prepare reviewed plugin packages without running package managers or code."""

from __future__ import annotations

import asyncio
import base64
import binascii
import gzip
import hashlib
import hmac
import io
import json
import re
import secrets
import shutil
import tarfile
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import quote, urlsplit

import httpx
from semantic_version import NpmSpec, Version

from libre_claw.core.cordis import (
    MANIFEST_NAME, MAX_PACKAGE_BYTES, MAX_PACKAGE_FILES, CordisError,
    CordisManager, _regular_bytes, _scan, _workspace,
)
from libre_claw.core.cordis_harness import (
    DEPENDENCY_DIRECTORY, adapt_harness_package, check_package_bounds,
    package_manifest, runtime_dependencies,
)

MAX_DOWNLOAD_BYTES = 4 * 1024 * 1024
MAX_METADATA_BYTES = 256 * 1024
MAX_TAR_BYTES = MAX_PACKAGE_BYTES + MAX_PACKAGE_FILES * 4096
MAX_PREVIEWS = 8
PREVIEW_LIFETIME = 15 * 60
PREVIEW_TIMEOUT = 30
_REGISTRY = "https://registry.npmjs.org"
_PACKAGE = re.compile(r"(?:@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*\Z")
_VERSION = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\Z")
_GITHUB_OWNER = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}\Z")
_GITHUB_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z")
_COMMIT = re.compile(r"[a-fA-F0-9]{40}\Z")
_EXAMPLES = Path(__file__).resolve().parents[1] / "cordis_runtime" / "examples"
_COMPATIBILITY = (
    "Use a local folder, .tgz archive, or npm:package@version containing "
    "a Libre Claw manifest, a Harness bundle, or a compiled Cordis package. "
    "Public GitHub repository URLs are supported; SSH, private repositories, "
    "and arbitrary download URLs are not supported."
)


def catalog() -> list[dict[str, Any]]:
    """Return shipped examples; never contact a registry or create local state."""
    return [{
        "id": "text-utilities", "name": "Text utilities", "version": "1.0.0",
        "description": "Count words, lines, and characters without sending text anywhere.",
        "source": "builtin:text-utilities", "tool_count": 1,
        "offline": True,
    }, {
        "id": "native-provider", "name": "Libre WebUI bridge", "version": "0.1.1",
        "description": "Use Libre Claw models in Libre WebUI over a private local connection.",
        "source": "builtin:native-provider", "tool_count": 0, "offline": False,
        "requires_model_access": True,
    }]


def _package_files(source: Path) -> dict[str, bytes]:
    return _adapt_files(_scan(source, exclude=True))


def _adapt_files(files: dict[str, bytes]) -> dict[str, bytes]:
    if MANIFEST_NAME in files:
        _check_dependencies(files)
        return files
    if "package.json" not in files:
        raise CordisError(_COMPATIBILITY)
    return adapt_harness_package(files)


def _check_dependencies(files: dict[str, bytes]) -> None:
    """Runtime dependencies cannot be installed or inherited by plugin children."""
    if "package.json" not in files:
        return
    try:
        package = json.loads(files["package.json"])
    except (ValueError, UnicodeError) as exc:
        raise CordisError("Plugin package.json is not valid JSON.") from exc
    if not isinstance(package, dict):
        raise CordisError("Plugin package.json must be an object.")
    if any(package.get(key) for key in ("dependencies", "optionalDependencies", "peerDependencies")):
        raise CordisError(
            "This package requires runtime dependencies. Publish a self-contained "
            "JavaScript bundle without runtime dependencies; Libre Claw does not run npm install."
        )


def _archive_files(data: bytes, *, adapt: bool = True) -> dict[str, bytes]:
    """Read a bounded gzip tar archive, validating every entry before staging."""
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
            expanded = compressed.read(MAX_TAR_BYTES + 1)
        if len(expanded) > MAX_TAR_BYTES:
            raise CordisError("Expanded plugin archive exceeds the allowed size.")
        files: dict[str, bytes] = {}
        seen: dict[str, bool] = {}
        total = 0
        count = 0
        with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as archive:
            for member in archive:
                count += 1
                if count > MAX_PACKAGE_FILES * 2:
                    raise CordisError("Plugin archive contains too many entries.")
                name = member.name
                while name.startswith("./"):
                    name = name[2:]
                name = name.rstrip("/") if member.isdir() else name
                if name in {"", "."} and member.isdir():
                    continue
                parts = name.split("/")
                if (not name or "\\" in name or "\x00" in name or name.startswith("/")
                        or any(part in {"", ".", ".."} for part in parts)
                        or re.match(r"^[A-Za-z]:", name)):
                    raise CordisError("Plugin archive paths must remain inside the package.")
                if not (member.isfile() or member.isdir()) or member.issparse():
                    raise CordisError("Plugin archives cannot contain links or special files.")
                key = unicodedata.normalize("NFC", name).casefold()
                if key in seen:
                    raise CordisError("Plugin archive contains duplicate paths.")
                for parent in PurePosixPath(key).parents:
                    if parent.as_posix() in seen and not seen[parent.as_posix()]:
                        raise CordisError("Plugin archive contains conflicting paths.")
                if member.isfile() and any(path.startswith(key + "/") for path in seen):
                    raise CordisError("Plugin archive contains conflicting paths.")
                seen[key] = member.isdir()
                if member.isdir():
                    continue
                if len(files) >= MAX_PACKAGE_FILES:
                    raise CordisError("Plugin contains too many files.")
                if member.size < 0 or total + member.size > MAX_PACKAGE_BYTES:
                    raise CordisError("Plugin exceeds the allowed size.")
                handle = archive.extractfile(member)
                if handle is None:
                    raise CordisError("Plugin archive contains an unreadable file.")
                with handle:
                    content = handle.read(member.size + 1)
                if len(content) != member.size:
                    raise CordisError("Plugin archive contains a truncated file.")
                total += len(content)
                files[name] = content
    except (tarfile.TarError, OSError, EOFError, ValueError) as exc:
        raise CordisError("Plugin must be a valid .tgz or .tar.gz archive.") from exc
    if MANIFEST_NAME not in files and "package.json" not in files:
        roots = {name.split("/")[0] for name in files}
        if len(roots) != 1:
            raise CordisError(_COMPATIBILITY)
        prefix = next(iter(roots)) + "/"
        files = {name[len(prefix):]: content for name, content in files.items()}
    if MANIFEST_NAME not in files and "package.json" not in files:
        raise CordisError(_COMPATIBILITY)
    # Match local installation's private-file exclusions, without writing them.
    files = {
        name: content for name, content in files.items()
        if not any(part.startswith(".") or part in {"node_modules", "__pycache__"}
                   for part in PurePosixPath(name).parts)
        and PurePosixPath(name).suffix.lower() not in {".pem", ".key", ".p12", ".pfx"}
    }
    return _adapt_files(files) if adapt else files


def _npm_spec(source: str) -> tuple[str, str]:
    spec = source[4:]
    separator = spec.find("@", 1)
    name = spec if separator < 0 else spec[:separator]
    version = "latest" if separator < 0 else spec[separator + 1:]
    if len(name) > 214 or not _PACKAGE.fullmatch(name) or (
        version != "latest" and not _VERSION.fullmatch(version)
    ):
        raise CordisError("Use npm:package@1.2.3, npm:@scope/package@1.2.3, or omit the version for latest.")
    return name, version


def _registry_url(value: Any) -> str:
    if not isinstance(value, str):
        raise CordisError("The npm package has no valid archive URL.")
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == "https" and parsed.hostname == "registry.npmjs.org"
                 and parsed.port in {None, 443} and not parsed.username and not parsed.password
                 and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise CordisError("Plugin downloads must use https://registry.npmjs.org without redirects.")
    return value


def _verify_integrity(data: bytes, integrity: Any) -> None:
    if not isinstance(integrity, str):
        raise CordisError("The npm package must publish SHA-256 or stronger archive integrity.")
    candidates: dict[str, list[bytes]] = {}
    for item in integrity.split():
        algorithm, separator, encoded = item.partition("-")
        if separator and algorithm in {"sha256", "sha384", "sha512"}:
            try:
                expected = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                continue
            if len(expected) == hashlib.new(algorithm).digest_size:
                candidates.setdefault(algorithm, []).append(expected)
    for algorithm in ("sha512", "sha384", "sha256"):
        if algorithm in candidates:
            actual = hashlib.new(algorithm, data).digest()
            if any(hmac.compare_digest(actual, expected) for expected in candidates[algorithm]):
                return
            raise CordisError("Downloaded plugin archive failed its integrity check.")
    raise CordisError("The npm package must publish SHA-256 or stronger archive integrity.")


async def _download(client: httpx.AsyncClient, url: str, limit: int, *,
                    validator: Callable[[Any], str] = _registry_url,
                    not_found: str = "The npm package or version was not found.") -> bytes:
    async with client.stream("GET", validator(url)) as response:
        if response.status_code == 404:
            raise CordisError(not_found)
        if response.is_redirect:
            raise CordisError("Plugin registry redirects are not allowed.")
        response.raise_for_status()
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            raise CordisError("The npm registry must honor uncompressed HTTP downloads.")
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
            size += len(chunk)
            if size > limit:
                raise CordisError("Plugin download exceeds the allowed size.")
            chunks.append(chunk)
        return b"".join(chunks)


async def _npm_files(source: str) -> tuple[dict[str, bytes], str]:
    name, requested = _npm_spec(source)
    # No .npmrc, proxy environment, browser session, or inherited credentials.
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=10,
                                 headers={"Accept-Encoding": "identity", "Accept": "application/vnd.npm.install-v1+json"}) as client:
        metadata = await _npm_metadata(client, name, requested)
        version = metadata["version"]
        files = await _fetch_archive(client, metadata, adapt=True)
        files = await _resolve_dependencies(files, client)
        return files, f"npm:{name}@{version}"


def normalize_package_source(source: str, workspace: Path) -> str:
    """Prefer existing/explicit filesystem sources; otherwise accept bare npm."""
    if source.startswith(("builtin:", "npm:", "github:", "https://")):
        return source
    path = Path(source).expanduser()
    candidate = path if path.is_absolute() else workspace / path
    if (path.is_absolute() or source.startswith(("./", "../", "~/"))
            or source.endswith((".tgz", ".tar.gz")) or candidate.exists() or candidate.is_symlink()):
        return source
    try:
        _npm_spec("npm:" + source)
    except CordisError:
        return source
    return "npm:" + source


def _github_spec(source: str) -> tuple[str, str, str]:
    if source.startswith("github:"):
        location, _, ref = source[7:].partition("#")
        parts = location.split("/")
    else:
        try:
            parsed = urlsplit(source)
            if (parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.port not in {None, 443}
                    or parsed.username or parsed.password or parsed.query):
                raise ValueError
        except ValueError:
            raise CordisError("Use a public https://github.com/owner/repository URL or github:owner/repository, optionally followed by #ref.") from None
        parts = parsed.path.strip("/").split("/")
        ref = parsed.fragment
    if len(parts) != 2:
        raise CordisError("Use a GitHub repository root URL, optionally followed by #branch, #tag, or #commit.")
    owner, name = parts
    if name.endswith(".git"):
        name = name[:-4]
    if (not _GITHUB_OWNER.fullmatch(owner) or not _GITHUB_REPOSITORY.fullmatch(name) or name in {".", ".."}
            or len(ref) > 200 or any(ord(char) < 32 for char in ref) or "#" in ref or "\\" in ref):
        raise CordisError("Invalid public GitHub repository or reference.")
    return owner, name, ref or "HEAD"


def _is_github_source(source: str) -> bool:
    if source.startswith("github:"):
        return True
    try:
        return urlsplit(source).hostname == "github.com"
    except ValueError:
        return False


def _github_url(value: Any) -> str:
    if not isinstance(value, str):
        raise CordisError("Invalid public GitHub download URL.")
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == "https" and parsed.hostname in {"api.github.com", "codeload.github.com"}
                 and parsed.port in {None, 443} and not parsed.username and not parsed.password
                 and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise CordisError("GitHub packages must use generated public API and codeload HTTPS URLs without redirects.")
    return value


async def _github_files(source: str) -> tuple[dict[str, bytes], str]:
    owner, name, ref = _github_spec(source)
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=10,
                                 headers={"Accept-Encoding": "identity", "Accept": "application/vnd.github+json",
                                          "User-Agent": "libre-claw-plugin-installer"}) as client:
        raw = await _download(client, f"https://api.github.com/repos/{owner}/{name}/commits/{quote(ref, safe='')}", MAX_METADATA_BYTES,
                              validator=_github_url, not_found="The public GitHub repository or reference was not found. Private repositories require a local checkout.")
        try:
            response = json.loads(raw)
        except (ValueError, UnicodeError):
            raise CordisError("GitHub returned invalid commit metadata.") from None
        sha = response.get("sha") if isinstance(response, dict) else None
        if not isinstance(sha, str) or not _COMMIT.fullmatch(sha):
            raise CordisError("GitHub did not resolve the source to a full commit SHA.")
        sha = sha.lower()
        if _COMMIT.fullmatch(ref) and ref.lower() != sha:
            raise CordisError("GitHub resolved a different commit than the requested source.")
        client.cookies.clear()
        data = await _download(client, f"https://codeload.github.com/{owner}/{name}/tar.gz/{sha}", MAX_DOWNLOAD_BYTES,
                               validator=_github_url, not_found="The pinned public GitHub source archive was not found.")
        files = await _run_thread(_archive_files, data)
        client.headers["Accept"] = "application/vnd.npm.install-v1+json"
        files = await _resolve_dependencies(files, client)
        return files, f"github:{owner}/{name}#{sha}"


def _npm_archive(data: bytes, integrity: Any, name: str, version: str, adapt: bool = True) -> dict[str, bytes]:
    _verify_integrity(data, integrity)
    files = _archive_files(data, adapt=adapt)
    if "package.json" not in files:
        raise CordisError("The npm archive requires package.json.")
    package = json.loads(files["package.json"])
    if package.get("name") != name or package.get("version") != version:
        raise CordisError("The npm archive does not match the reviewed package identity.")
    return files


async def _npm_metadata(client: httpx.AsyncClient, name: str, requested: str) -> dict[str, Any]:
    raw = await _download(client, f"{_REGISTRY}/{quote(name, safe='')}/{quote(requested, safe='')}", MAX_METADATA_BYTES)
    try:
        metadata = json.loads(raw)
    except (ValueError, UnicodeError):
        raise CordisError("The npm registry returned invalid package metadata.") from None
    if not isinstance(metadata, dict) or metadata.get("name") != name:
        raise CordisError("The npm registry returned a different package identity.")
    version = metadata.get("version")
    if not isinstance(version, str) or not _VERSION.fullmatch(version) or (requested != "latest" and version != requested):
        raise CordisError("The npm registry returned a different package version.")
    return metadata


async def _fetch_archive(client: httpx.AsyncClient, metadata: dict[str, Any], *, adapt: bool) -> dict[str, bytes]:
    dist = metadata.get("dist")
    if not isinstance(dist, dict):
        raise CordisError("The npm package has no downloadable archive.")
    client.cookies.clear()
    data = await _download(client, dist.get("tarball"), MAX_DOWNLOAD_BYTES)
    return await _run_thread(_npm_archive, data, dist.get("integrity"), metadata["name"], metadata["version"], adapt)


def _dependency_spec(name: str, requested: str) -> tuple[str, str]:
    if requested.startswith("npm:"):
        alias = requested[4:]
        split = alias.find("@", 1)
        name, requested = (alias, "*") if split < 0 else (alias[:split], alias[split + 1:])
    if not _PACKAGE.fullmatch(name) or len(name) > 214:
        raise CordisError("Plugin dependencies must use public npm package names.")
    try:
        NpmSpec(requested)
    except ValueError:
        raise CordisError("Plugin dependencies require npm version ranges; file, Git, URL, and workspace dependencies are not installed.") from None
    return name, requested


async def _dependency_metadata(client: httpx.AsyncClient, name: str, requested: str) -> dict[str, Any]:
    if _VERSION.fullmatch(requested):
        return await _npm_metadata(client, name, requested)
    spec = NpmSpec(requested)
    raw = await _download(client, f"{_REGISTRY}/{quote(name, safe='')}", 4 * 1024 * 1024)
    try:
        package = json.loads(raw)
    except (ValueError, UnicodeError):
        raise CordisError("The npm registry returned invalid dependency metadata.") from None
    if not isinstance(package, dict) or package.get("name") != name or not isinstance(package.get("versions"), dict):
        raise CordisError("The npm registry returned a different dependency identity.")
    if len(package["versions"]) > 10_000:
        raise CordisError("The npm dependency version catalog exceeds its limit; use an exact version.")
    candidates = []
    for value in package["versions"]:
        if isinstance(value, str) and _VERSION.fullmatch(value):
            try:
                candidates.append(Version(value))
            except ValueError:
                continue
    selected = spec.select(candidates)
    if selected is None:
        raise CordisError("No published dependency version satisfies this package's range.")
    metadata = package["versions"][str(selected)]
    if not isinstance(metadata, dict) or metadata.get("name") != name or metadata.get("version") != str(selected):
        raise CordisError("The npm dependency metadata has a mismatched identity.")
    return metadata


async def _resolve_dependencies(files: dict[str, bytes], client: httpx.AsyncClient) -> dict[str, bytes]:
    manifest = json.loads(files[MANIFEST_NAME])
    if manifest.get("format") != "deepseek-harness":
        return files
    result = dict(files)
    root = package_manifest(files)
    root_record = {"name": root["name"], "version": root["version"], "path": ".", "dependencies": {}}
    records = [root_record]
    resolved = {(root["name"], root["version"]): root_record}
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    pending = [(root, root_record, 0)]
    edges = 0
    while pending:
        package, record, depth = pending.pop(0)
        dependencies = runtime_dependencies(package)
        if dependencies and depth >= 12:
            raise CordisError("Plugin dependency graph exceeds the allowed depth.")
        for alias, requested in sorted(dependencies.items()):
            edges += 1
            if edges > 128:
                raise CordisError("Plugin dependency graph contains too many edges.")
            name, requested = _dependency_spec(alias, requested)
            selection = (name, requested)
            if selection not in selected:
                client.cookies.clear()
                selected[selection] = await _dependency_metadata(client, name, requested)
            metadata = selected[selection]
            key = (name, metadata["version"])
            child = resolved.get(key)
            if child is None:
                if len(records) >= 32:
                    raise CordisError("Plugin dependency graph contains too many packages.")
                dependency = await _fetch_archive(client, metadata, adapt=False)
                child_package = package_manifest(dependency)
                digest = hashlib.sha256((name + "@" + metadata["version"]).encode()).hexdigest()[:16]
                slug = re.sub(r"[^a-z0-9]+", "-", name).strip("-")[:32]
                prefix = f"{DEPENDENCY_DIRECTORY}/{digest}-{slug}"
                child = {"name": name, "version": metadata["version"], "path": prefix, "dependencies": {}}
                resolved[key] = child
                records.append(child)
                for path, content in dependency.items():
                    result[prefix + "/" + path] = content
                check_package_bounds(result)
                pending.append((child_package, child, depth + 1))
            record["dependencies"][alias] = child["path"]
    manifest["harness"]["packages"] = records
    result[MANIFEST_NAME] = json.dumps(manifest, ensure_ascii=True, sort_keys=True).encode()
    check_package_bounds(result)
    return result


async def _local_dependencies(files: dict[str, bytes]) -> dict[str, bytes]:
    manifest = json.loads(files[MANIFEST_NAME])
    if manifest.get("format") != "deepseek-harness" or not runtime_dependencies(package_manifest(files)):
        return files
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=10,
                                 headers={"Accept-Encoding": "identity", "Accept": "application/vnd.npm.install-v1+json"}) as client:
        return await _resolve_dependencies(files, client)


async def _run_thread(operation: Callable[..., Any], *args: Any,
                      cleanup: Callable[[Any], None] | None = None) -> Any:
    """Join file operations after cancellation, then discard any staged result."""
    task = asyncio.create_task(asyncio.to_thread(operation, *args))
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            # Cancelling to_thread cannot stop its underlying filesystem work.
            # Keep ownership until it finishes, including repeated cancellation.
            cancelled = True
        except Exception:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
    if cancelled:
        if cleanup is not None:
            await _run_thread(cleanup, result)
        raise asyncio.CancelledError
    return result


def _discard_stage(result: tuple[Any, ...]) -> None:
    shutil.rmtree(result[0])


def _stage_files(manager: CordisManager, files: dict[str, bytes]) -> tuple[Path, dict[str, Any]]:
    stage = Path(tempfile.mkdtemp(prefix="libre-claw-plugin-preview-"))
    try:
        for name, content in files.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_bytes(content)
            target.chmod(0o400)
        return stage, manager.preview(stage)
    except BaseException:
        shutil.rmtree(stage)
        raise


def _read_local(source: str, workspace: Path) -> tuple[dict[str, bytes], str, str]:
    included = {"builtin:text-utilities": "text-utilities", "builtin:native-provider": "native-provider"}
    if source in included:
        files = _package_files(_EXAMPLES / included[source])
        kind = "builtin"
    else:
        path = Path(source).expanduser()
        if not path.is_absolute():
            candidate = workspace / path
            if source.startswith(("./", "../")) or (":" not in source and candidate.exists()):
                path = candidate.absolute()
            else:
                raise CordisError(_COMPATIBILITY)
        if path.is_symlink():
            raise CordisError("Plugin source cannot be a symlink.")
        if path.is_dir():
            files = _package_files(path)
            kind = "directory"
        elif source.endswith((".tgz", ".tar.gz")):
            files = _archive_files(_regular_bytes(path, MAX_DOWNLOAD_BYTES))
            kind = "archive"
        else:
            raise CordisError(_COMPATIBILITY)
        source = str(path)
    return files, kind, source


@dataclass(frozen=True)
class _Preview:
    directory: Path
    workspace: str
    digest: str
    deadline: float


class CordisPackagePreviews:
    """Hold expiring, project-bound snapshots until the person chooses Install."""

    def __init__(self, manager: CordisManager) -> None:
        self.manager = manager
        self._previews: dict[str, _Preview] = {}
        self._active = 0
        self._closed = False

    def _expire(self) -> None:
        for token, preview in tuple(self._previews.items()):
            if preview.deadline <= time.monotonic():
                self._delete(token)

    def _delete(self, token: str) -> None:
        preview = self._previews.pop(token, None)
        if preview is not None:
            shutil.rmtree(preview.directory)

    def _lookup(self, token: str, workspace: str | Path) -> _Preview:
        self._expire()
        if not isinstance(token, str):
            raise CordisError("Plugin preview expired or is unavailable; preview the package again.")
        preview = self._previews.get(token)
        if preview is None or preview.workspace != _workspace(workspace)[1]:
            raise CordisError("Plugin preview expired or is unavailable for this project; preview the package again.")
        return preview

    async def preview(self, source: str, workspace: str | Path) -> dict[str, Any]:
        """Read and stage a compatible package; no code runs and nothing installs."""
        self._expire()
        if self._closed:
            raise CordisError("Plugin previews are closed.")
        if len(self._previews) + self._active >= MAX_PREVIEWS:
            raise CordisError("Too many pending plugin previews; close an existing preview first.")
        if not isinstance(source, str) or not source.strip() or len(source) > 4096:
            raise CordisError("Enter a plugin package or absolute local path.")
        workspace_path, workspace_key = _workspace(workspace)
        source = source.strip()
        source = normalize_package_source(source, workspace_path)
        self._active += 1
        stage: Path | None = None
        try:
            async with asyncio.timeout(PREVIEW_TIMEOUT):
                if source.startswith("npm:"):
                    files, source = await _npm_files(source)
                    kind = "npm"
                    stage, metadata = await _run_thread(_stage_files, self.manager, files, cleanup=_discard_stage)
                elif _is_github_source(source):
                    files, source = await _github_files(source)
                    kind = "github"
                    stage, metadata = await _run_thread(_stage_files, self.manager, files, cleanup=_discard_stage)
                else:
                    files, kind, source = await _run_thread(_read_local, source, workspace_path)
                    files = await _local_dependencies(files)
                    stage, metadata = await _run_thread(_stage_files, self.manager, files, cleanup=_discard_stage)
                if self._closed:
                    raise CordisError("Plugin previews are closed.")
                token = secrets.token_urlsafe(32)
                self._previews[token] = _Preview(stage, workspace_key, metadata["digest"], time.monotonic() + PREVIEW_LIFETIME)
                stage = None
                return {**metadata, "token": token, "source": source, "source_kind": kind,
                        "expires_at": time.time() + PREVIEW_LIFETIME, "execution_network_required": False,
                        **({"resolved_commit": source.rsplit("#", 1)[1]} if kind == "github" else {})}
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise CordisError("Plugin lookup timed out; try again.") from exc
        except httpx.HTTPError as exc:
            raise CordisError("The public package service could not be reached; check the source and try again.") from exc
        except OSError as exc:
            raise CordisError("Cannot read or stage this plugin package.") from exc
        finally:
            self._active -= 1
            if stage is not None:
                await _run_thread(shutil.rmtree, stage)

    def install(self, token: str, workspace: str | Path) -> dict[str, Any]:
        """Install exactly the reviewed snapshot, leaving its activation separate."""
        preview = self._lookup(token, workspace)
        try:
            installed = self.manager.install(preview.directory, expected_digest=preview.digest)
            # An identical reinstall keeps grants and configuration. Return the
            # selected project's actual state so an Enable action cannot revoke it.
            return self.manager.details(installed["id"], workspace)
        finally:
            self._delete(token)

    def discard(self, token: str, workspace: str | Path) -> None:
        """Delete an unused preview after validating its project association."""
        self._lookup(token, workspace)
        self._delete(token)

    def close(self) -> None:
        """Drop all staged bytes; in-flight lookups cannot publish a new preview."""
        self._closed = True
        for token in tuple(self._previews):
            self._delete(token)
