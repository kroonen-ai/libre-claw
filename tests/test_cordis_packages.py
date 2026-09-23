# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import io
import json
import shutil
import tarfile
import threading
from dataclasses import replace

import httpx
import pytest

from libre_claw.core import cordis_packages as packages
from libre_claw.core.cordis import CordisError, CordisManager, MANIFEST_NAME
from libre_claw.core.cordis_packages import CordisPackagePreviews, catalog
from libre_claw.core.cordis_security import CordisSecurityError, prepare_cordis_process


def plugin_files(**package_updates):
    definition = {
        "name": "echo", "description": "Echo text",
        "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}},
    }
    files = {
        MANIFEST_NAME: json.dumps({"id": "example", "name": "Example", "version": "1.0.0",
                                   "entry": "plugin.mjs", "tools": [definition]}).encode(),
        "plugin.mjs": b"throw new Error('preview and install must not execute this');",
    }
    if package_updates:
        files["package.json"] = json.dumps({"name": "example-plugin", "version": "1.0.0", **package_updates}).encode()
    return files


def local_plugin(tmp_path, files=None):
    root = tmp_path / "source"
    root.mkdir()
    for name, content in (files or plugin_files()).items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return root


def archive_bytes(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, data in entries:
            member = tarfile.TarInfo(name) if isinstance(name, str) else name
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


def packaged_archive(**package_updates):
    return archive_bytes([(f"package/{name}", data) for name, data in plugin_files(name="example-plugin", **package_updates).items()])


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    return root


@pytest.fixture
def previews(tmp_path):
    manager = CordisManager(root=tmp_path / "registry")
    prepared = CordisPackagePreviews(manager)
    yield prepared
    prepared.close()


def npm_transport(monkeypatch, handler):
    factory = httpx.AsyncClient
    options = []

    def create(**kwargs):
        options.append(kwargs)
        return factory(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(packages.httpx, "AsyncClient", create)
    return options


def npm_metadata(data, **updates):
    return {
        "name": "example-plugin", "version": "1.0.0",
        "dist": {"tarball": "https://registry.npmjs.org/example-plugin/-/example-plugin-1.0.0.tgz",
                 "integrity": "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()},
        **updates,
    }


async def test_preview_pins_reviewed_bytes_without_execution_or_registry_writes(previews, workspace, tmp_path):
    source = local_plugin(tmp_path)
    result = await previews.preview(str(source), workspace)
    assert result["id"] == "example" and result["source_kind"] == "directory"
    assert len(result["token"]) >= 40
    assert not previews.manager.root.exists()
    (source / "plugin.mjs").write_text("export default {apply(){}}")

    installed = previews.install(result["token"], workspace)

    assert installed["enabled"] is False
    record = previews.manager._record("example")
    installed_source = previews.manager._snapshot("example", record) / "plugin.mjs"
    assert b"must not execute" in installed_source.read_bytes()
    with pytest.raises(CordisError, match="expired"):
        previews.install(result["token"], workspace)


async def test_preview_does_not_stage_private_files(previews, workspace, tmp_path):
    source = local_plugin(tmp_path, {**plugin_files(), ".env": b"SECRET", "secret.key": b"key"})
    result = await previews.preview(str(source), workspace)
    stage = previews._previews[result["token"]].directory
    assert not (stage / ".env").exists() and not (stage / "secret.key").exists()
    assert (stage / "plugin.mjs").stat().st_mode & 0o222 == 0
    previews.discard(result["token"], workspace)
    assert not stage.exists() and not previews.manager.root.exists()


async def test_identical_reinstall_reports_preserved_project_enablement(previews, workspace, tmp_path):
    source = local_plugin(tmp_path)
    initial = await previews.preview(str(source), workspace)
    previews.install(initial["token"], workspace)
    previews.manager.enable("example", workspace, allow_network=True)
    reviewed = await previews.preview(str(source), workspace)

    result = previews.install(reviewed["token"], workspace)

    assert result["enabled"] is True
    assert result["grants"]["allow_network"] is True
    other = tmp_path / "other"
    other.mkdir()
    separate = await previews.preview(str(source), other)
    assert previews.install(separate["token"], other)["enabled"] is False


async def test_preview_tokens_are_project_bound_and_expire(previews, workspace, tmp_path):
    source = local_plugin(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    result = await previews.preview(str(source), workspace)
    token = result["token"]
    for action in (previews.install, previews.discard):
        with pytest.raises(CordisError, match="this project"):
            action(token, other)
    stage = previews._previews[token].directory
    previews._previews[token] = replace(previews._previews[token], deadline=0)
    with pytest.raises(CordisError, match="expired"):
        previews.install(token, workspace)
    assert not stage.exists() and not previews.manager.root.exists()


async def test_staged_tampering_cannot_install_reviewed_token(previews, workspace, tmp_path):
    result = await previews.preview(str(local_plugin(tmp_path)), workspace)
    stage = previews._previews[result["token"]].directory
    entry = stage / "plugin.mjs"
    entry.chmod(0o600)
    entry.write_text("unreviewed")
    with pytest.raises(CordisError, match="changed|digest|review"):
        previews.install(result["token"], workspace)
    assert not (previews.manager.root / "registry.json").exists()
    assert not stage.exists()


async def test_preview_capacity_is_released_by_discard(previews, workspace, tmp_path, monkeypatch):
    monkeypatch.setattr(packages, "MAX_PREVIEWS", 2)
    source = str(local_plugin(tmp_path))
    first = await previews.preview(source, workspace)
    await previews.preview(source, workspace)
    with pytest.raises(CordisError, match="Too many"):
        await previews.preview(source, workspace)
    previews.discard(first["token"], workspace)
    replacement = await previews.preview(source, workspace)
    assert replacement["token"] != first["token"]


async def test_close_deletes_every_stage_and_rejects_future_previews(previews, workspace, tmp_path):
    result = await previews.preview(str(local_plugin(tmp_path)), workspace)
    stage = previews._previews[result["token"]].directory
    previews.close()
    previews.close()
    assert not stage.exists()
    with pytest.raises(CordisError, match="closed"):
        await previews.preview("builtin:text-utilities", workspace)


@pytest.mark.parametrize("source", ["./local", "../local", "not a package", "https://example.com/plugin.tgz", "git@github.com:owner/repo.git"])
async def test_unsupported_sources_have_compatibility_guidance(previews, workspace, source):
    with pytest.raises(CordisError, match="compiled Cordis package"):
        await previews.preview(source, workspace)


@pytest.mark.parametrize("source", ["https://github.com/example/plugin", "https://github.com/example/plugin.git#main", "github:example/plugin#feature/test"])
async def test_public_github_sources_are_pinned_before_download(previews, workspace, source, monkeypatch):
    commit = "a" * 40
    data = archive_bytes([(f"plugin-{commit}/{name}", value) for name, value in plugin_files().items()])
    seen = []
    def handler(request):
        seen.append(request)
        assert "authorization" not in request.headers and "cookie" not in request.headers
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={"sha": commit, "url": "https://attacker.invalid/ignored"},
                                  headers={"Set-Cookie": "do-not-send=private; Domain=.github.com"})
        assert request.url.host == "codeload.github.com"
        assert request.url.path == f"/example/plugin/tar.gz/{commit}"
        return httpx.Response(200, content=data)
    options = npm_transport(monkeypatch, handler)
    preview = await previews.preview(source, workspace)
    assert preview["source_kind"] == "github" and preview["resolved_commit"] == commit
    assert preview["source"] == f"github:example/plugin#{commit}"
    assert previews.install(preview["token"], workspace)["enabled"] is False
    assert len(seen) == 2
    assert options[0]["trust_env"] is False and options[0]["follow_redirects"] is False


@pytest.mark.parametrize("source", [
    "https://github.com/owner/repo/tree/main", "https://user:secret@github.com/owner/repo",
    "https://github.com/owner/repo?access_token=secret", "github:../repo", "github:owner/repo#bad\\ref",
    "http://github.com/owner/repo", "https://github.com.attacker.invalid/owner/repo",
])
def test_github_source_parser_rejects_credentials_and_other_routes(source):
    with pytest.raises(CordisError):
        packages._github_spec(source)


@pytest.mark.parametrize("response", [
    httpx.Response(302, headers={"Location": "https://attacker.invalid/source.tgz"}),
    httpx.Response(200, json={"sha": "main"}),
    httpx.Response(200, json={"sha": "../private"}),
    httpx.Response(404, json={"message": "Not Found"}),
])
async def test_github_bad_resolution_cannot_fetch_or_stage(previews, workspace, monkeypatch, response):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return response
    npm_transport(monkeypatch, handler)
    with pytest.raises(CordisError):
        await previews.preview("github:owner/repo", workspace)
    assert len(calls) == 1 and not previews._previews and not previews.manager.root.exists()


async def test_github_archive_redirects_are_not_followed(previews, workspace, monkeypatch):
    def handler(request):
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={"sha": "b" * 40})
        assert request.url.host == "codeload.github.com"
        return httpx.Response(302, headers={"Location": "http://127.0.0.1:8766/config"})
    npm_transport(monkeypatch, handler)
    with pytest.raises(CordisError, match="redirect"):
        await previews.preview("github:owner/repo", workspace)
    assert not previews._previews


async def test_github_exact_commit_cannot_be_silently_replaced(previews, workspace, monkeypatch):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"sha": "b" * 40})
    npm_transport(monkeypatch, handler)
    with pytest.raises(CordisError, match="different commit"):
        await previews.preview("github:owner/repo#" + "a" * 40, workspace)
    assert len(calls) == 1


async def test_bare_npm_is_supported_and_existing_relative_folder_wins(previews, workspace, monkeypatch):
    data = packaged_archive()
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=data) if request.url.path.endswith("tgz") else httpx.Response(200, json=npm_metadata(data))
    npm_transport(monkeypatch, handler)
    result = await previews.preview("example-plugin@1.0.0", workspace)
    assert result["source"] == "npm:example-plugin@1.0.0"
    calls.clear()
    source = workspace / "example-plugin"
    source.mkdir()
    for name, content in plugin_files().items():
        (source / name).write_bytes(content)
    result = await previews.preview("example-plugin", workspace)
    assert result["source_kind"] == "directory" and calls == []


async def test_tilde_local_path_is_supported(previews, workspace, tmp_path, monkeypatch):
    local_plugin(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    result = await previews.preview("~/source", workspace)
    assert result["id"] == "example"


@pytest.mark.parametrize("relative", ["./source", "source", "../source"])
async def test_relative_plugin_paths_use_selected_workspace(previews, workspace, tmp_path, relative):
    local_plugin(tmp_path if relative.startswith("../") else workspace)
    result = await previews.preview(relative, workspace)
    assert result["id"] == "example"


async def test_relative_source_symlink_is_not_resolved_away(previews, workspace, tmp_path):
    source = local_plugin(tmp_path)
    (workspace / "linked").symlink_to(source, target_is_directory=True)
    with pytest.raises(CordisError, match="symlink"):
        await previews.preview("./linked", workspace)


@pytest.mark.parametrize("root", ["", "package/", "exported-plugin/", "./package/"])
async def test_local_archives_preview_and_install(previews, workspace, tmp_path, root):
    archive = tmp_path / "example.tgz"
    archive.write_bytes(archive_bytes([(root + name, content) for name, content in plugin_files().items()]))
    preview = await previews.preview(str(archive), workspace)
    assert preview["source_kind"] == "archive"
    assert previews.install(preview["token"], workspace)["id"] == "example"


@pytest.mark.parametrize("path", ["../escaped", "/escaped", "package/../../escaped", "package/./file", "package\\escaped", "C:/escaped", "package//file"])
def test_archive_rejects_unsafe_paths(path):
    data = archive_bytes([(f"package/{name}", content) for name, content in plugin_files().items()] + [(path, b"unsafe")])
    with pytest.raises(CordisError, match="paths"):
        packages._archive_files(data)


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE])
def test_archive_rejects_links_and_special_files(kind):
    member = tarfile.TarInfo("package/link")
    member.type = kind
    member.linkname = "../../outside"
    data = archive_bytes([(f"package/{name}", content) for name, content in plugin_files().items()] + [(member, b"")])
    with pytest.raises(CordisError, match="links or special"):
        packages._archive_files(data)


@pytest.mark.parametrize("names", [["package/file", "package/file"], ["package/File", "package/file"], ["package/\u00e9", "package/e\u0301"], ["package/file", "package/file/child"], ["package/file/child", "package/file"]])
def test_archive_rejects_duplicate_and_conflicting_paths(names):
    data = archive_bytes([(name, b"text") for name in names])
    with pytest.raises(CordisError, match="duplicate|conflicting"):
        packages._archive_files(data)


def test_archive_expansion_and_file_count_are_bounded(monkeypatch):
    monkeypatch.setattr(packages, "MAX_TAR_BYTES", 128)
    with pytest.raises(CordisError, match="Expanded"):
        packages._archive_files(gzip.compress(b"\x00" * 129))
    monkeypatch.setattr(packages, "MAX_TAR_BYTES", 1024 * 1024)
    monkeypatch.setattr(packages, "MAX_PACKAGE_FILES", 2)
    with pytest.raises(CordisError, match="too many"):
        packages._archive_files(archive_bytes([(f"package/{number}", b"x") for number in range(3)]))


def test_archive_rejects_invalid_gzip_tar_and_oversized_members(monkeypatch):
    for data in (b"not-gzip", gzip.compress(b"not-tar")):
        with pytest.raises(CordisError, match="valid .tgz"):
            packages._archive_files(data)
    monkeypatch.setattr(packages, "MAX_PACKAGE_BYTES", 10)
    with pytest.raises(CordisError, match="allowed size"):
        packages._archive_files(archive_bytes([("package/large", b"x" * 11)]))


def test_archive_accepts_tar_root_directory_and_excludes_private_bytes():
    root = tarfile.TarInfo(".")
    root.type = tarfile.DIRTYPE
    data = archive_bytes([(root, b"")] + [("./" + name, content) for name, content in plugin_files().items()] + [("./.env", b"SECRET")])
    files = packages._archive_files(data)
    assert set(files) == {MANIFEST_NAME, "plugin.mjs"}


@pytest.mark.parametrize("key", ["dependencies", "optionalDependencies", "peerDependencies"])
async def test_runtime_dependencies_are_refused_without_installing_anything(previews, workspace, tmp_path, key):
    source = local_plugin(tmp_path, plugin_files(**{key: {"external": "*"}}))
    with pytest.raises(CordisError, match="self-contained"):
        await previews.preview(str(source), workspace)
    assert not previews.manager.root.exists()


async def test_npm_inspection_pins_version_and_never_inherits_auth_or_proxies(previews, workspace, monkeypatch):
    data = packaged_archive(scripts={"install": "do-not-run"})
    requests = []
    monkeypatch.setenv("HTTPS_PROXY", "http://private.invalid")
    monkeypatch.setenv("NPM_TOKEN", "secret-that-must-not-leave")

    def respond(request):
        requests.append(request)
        if request.url.path.endswith("/latest"):
            return httpx.Response(200, json=npm_metadata(data), headers={"Set-Cookie": "tracking=unwanted"})
        return httpx.Response(200, content=data)

    options = npm_transport(monkeypatch, respond)
    preview = await previews.preview("npm:example-plugin", workspace)
    assert preview["source"] == "npm:example-plugin@1.0.0"
    assert preview["source_kind"] == "npm"
    assert len(requests) == 2
    assert all("authorization" not in request.headers and "cookie" not in request.headers for request in requests)
    assert options[0]["trust_env"] is False and options[0]["follow_redirects"] is False
    assert not previews.manager.root.exists()
    previews.install(preview["token"], workspace)
    assert previews.manager.list_plugins(workspace)[0]["enabled"] is False


async def test_scoped_npm_spec_resolves_exact_package_and_version(previews, workspace, monkeypatch):
    name = "@author/example-plugin"
    data = archive_bytes([(f"package/{path}", content) for path, content in plugin_files(name=name).items()])
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, content=data) if request.url.path.endswith(".tgz") else httpx.Response(200, json=npm_metadata(data, name=name))
    npm_transport(monkeypatch, respond)
    result = await previews.preview("npm:@author/example-plugin@1.0.0", workspace)
    assert result["source"] == "npm:@author/example-plugin@1.0.0"
    assert requests[0].url.raw_path == b"/%40author%2Fexample-plugin/1.0.0"


@pytest.mark.parametrize("spec", ["npm:../secret", "npm:PACKAGE", "npm:pkg@", "npm:pkg@^1", "npm:pkg@https://evil", "npm:--registry=elsewhere"])
async def test_invalid_npm_specs_never_contact_registry(previews, workspace, monkeypatch, spec):
    def no_request(request):
        pytest.fail("Invalid package spec reached the network")
    npm_transport(monkeypatch, no_request)
    with pytest.raises(CordisError, match="Use npm:"):
        await previews.preview(spec, workspace)


@pytest.mark.parametrize("target", ["http://registry.npmjs.org/package.tgz", "https://evil.example/package.tgz", "https://registry.npmjs.org.evil.example/package.tgz", "https://user:pass@registry.npmjs.org/package.tgz", "https://registry.npmjs.org:8443/package.tgz"])
async def test_npm_refuses_untrusted_archive_hosts(previews, workspace, monkeypatch, target):
    requests = []
    metadata = npm_metadata(b"unused")
    metadata["dist"]["tarball"] = target
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=metadata)
    npm_transport(monkeypatch, respond)
    with pytest.raises(CordisError, match="registry.npmjs.org"):
        await previews.preview("npm:example-plugin@1.0.0", workspace)
    assert len(requests) == 1


async def test_npm_does_not_follow_redirects(previews, workspace, monkeypatch):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.example/archive"})
    npm_transport(monkeypatch, respond)
    with pytest.raises(CordisError, match="redirects"):
        await previews.preview("npm:example-plugin@1.0.0", workspace)
    assert len(requests) == 1


async def test_npm_archive_integrity_failure_never_stages_or_installs(previews, workspace, monkeypatch):
    metadata = npm_metadata(b"different bytes")
    npm_transport(monkeypatch, lambda request: httpx.Response(200, content=packaged_archive()) if request.url.path.endswith(".tgz") else httpx.Response(200, json=metadata))
    with pytest.raises(CordisError, match="integrity"):
        await previews.preview("npm:example-plugin@1.0.0", workspace)
    assert not previews.manager.root.exists() and not previews._previews


@pytest.mark.parametrize("bad_metadata", [{"name": "other-plugin"}, {"version": "2.0.0"}, {"dist": None}])
async def test_npm_metadata_identity_is_checked_before_downloading(previews, workspace, monkeypatch, bad_metadata):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=npm_metadata(b"unused", **bad_metadata))
    npm_transport(monkeypatch, respond)
    with pytest.raises(CordisError, match="identity|version|archive"):
        await previews.preview("npm:example-plugin@1.0.0", workspace)
    assert len(requests) == 1 and not previews._previews


async def test_npm_archive_identity_cannot_differ_from_registry_metadata(previews, workspace, monkeypatch):
    data = packaged_archive(version="2.0.0")
    npm_transport(monkeypatch, lambda request: httpx.Response(200, content=data) if request.url.path.endswith(".tgz") else httpx.Response(200, json=npm_metadata(data)))
    with pytest.raises(CordisError, match="reviewed package identity"):
        await previews.preview("npm:example-plugin@1.0.0", workspace)
    assert not previews._previews and not previews.manager.root.exists()


def test_integrity_uses_strongest_advertised_algorithm():
    weak_match = base64.b64encode(hashlib.sha256(b"data").digest()).decode()
    strong_mismatch = base64.b64encode(hashlib.sha512(b"other").digest()).decode()
    with pytest.raises(CordisError, match="integrity"):
        packages._verify_integrity(b"data", f"sha256-{weak_match} sha512-{strong_mismatch}")
    for invalid in (None, "sha1-deprecated", "sha512-malformed", "sha512-***"):
        with pytest.raises(CordisError, match="SHA-256"):
            packages._verify_integrity(b"data", invalid)


async def test_npm_download_size_is_bounded(previews, workspace, monkeypatch):
    monkeypatch.setattr(packages, "MAX_METADATA_BYTES", 20)
    npm_transport(monkeypatch, lambda request: httpx.Response(200, content=b"x" * 21))
    with pytest.raises(CordisError, match="exceeds"):
        await previews.preview("npm:example-plugin", workspace)


@pytest.mark.parametrize("status,expected", [(404, "not found"), (503, "could not be reached")])
async def test_npm_error_responses_are_actionable(previews, workspace, monkeypatch, status, expected):
    npm_transport(monkeypatch, lambda request: httpx.Response(status))
    with pytest.raises(CordisError, match=expected):
        await previews.preview("npm:example-plugin", workspace)
    assert not previews._previews and previews._active == 0


async def test_npm_timeout_does_not_keep_preview_capacity(previews, workspace, monkeypatch):
    def timeout(request):
        raise httpx.ReadTimeout("timed out", request=request)
    npm_transport(monkeypatch, timeout)
    with pytest.raises(CordisError, match="timed out"):
        await previews.preview("npm:example-plugin", workspace)
    assert previews._active == 0


@pytest.mark.parametrize("close", [False, True])
async def test_aborted_preview_cannot_create_registry_or_leave_stages(previews, workspace, monkeypatch, close):
    reached = asyncio.Event()
    release = asyncio.Event()
    data = packaged_archive()
    async def respond(request):
        reached.set()
        await release.wait()
        return httpx.Response(200, content=data) if request.url.path.endswith(".tgz") else httpx.Response(200, json=npm_metadata(data))
    npm_transport(monkeypatch, respond)
    task = asyncio.create_task(previews.preview("npm:example-plugin", workspace))
    await reached.wait()
    if close:
        previews.close()
        release.set()
        with pytest.raises(CordisError, match="closed"):
            await task
    else:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not previews._previews and previews._active == 0 and not previews.manager.root.exists()


async def test_local_staging_runs_off_event_loop_and_joins_cancellation(previews, workspace, tmp_path, monkeypatch):
    source = local_plugin(tmp_path)
    original = previews.manager.preview
    reached = threading.Event()
    release = threading.Event()
    stages = []

    def paused_preview(stage):
        stages.append(stage)
        reached.set()
        assert release.wait(2), "Test did not release plugin staging"
        return original(stage)

    monkeypatch.setattr(previews.manager, "preview", paused_preview)
    task = asyncio.create_task(previews.preview(str(source), workspace))
    try:
        assert await asyncio.to_thread(reached.wait, 1)
        assert not task.done(), "Staging blocked the event loop until its worker finished"
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done(), "Cancellation abandoned a worker that still owns staged files"
        task.cancel()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stages and all(not stage.exists() for stage in stages)
    assert not previews._previews and previews._active == 0 and not previews.manager.root.exists()


async def test_timed_out_staging_cleans_worker_result(previews, workspace, tmp_path, monkeypatch):
    source = local_plugin(tmp_path)
    original = previews.manager.preview
    stages = []
    release = threading.Event()

    def paused_preview(stage):
        stages.append(stage)
        assert release.wait(2), "Test did not release plugin staging"
        return original(stage)

    async def release_worker():
        await asyncio.sleep(0.05)
        release.set()

    monkeypatch.setattr(packages, "PREVIEW_TIMEOUT", 0.01)
    monkeypatch.setattr(previews.manager, "preview", paused_preview)
    releaser = asyncio.create_task(release_worker())
    try:
        with pytest.raises(CordisError, match="timed out"):
            await previews.preview(str(source), workspace)
    finally:
        release.set()
        await releaser
    assert stages and all(not stage.exists() for stage in stages)
    assert not previews._previews and previews._active == 0


async def test_builtin_catalog_and_install_work_without_network(previews, workspace, monkeypatch):
    def no_network(**kwargs):
        pytest.fail("A built-in catalog or package contacted the network")
    monkeypatch.setattr(packages.httpx, "AsyncClient", no_network)
    items = catalog()
    assert items[0]["source"] == "builtin:text-utilities"
    assert not previews.manager.root.exists()
    reviewed = await previews.preview(items[0]["source"], workspace)
    installed = previews.install(reviewed["token"], workspace)
    assert installed["id"] == "text-utilities" and not installed["enabled"]


async def test_builtin_tool_respects_saved_configuration_offline(previews, workspace, tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    reviewed = await previews.preview("builtin:text-utilities", workspace)
    previews.install(reviewed["token"], workspace)
    manager = previews.manager
    snapshot = manager._snapshot("text-utilities", manager._record("text-utilities"))
    try:
        prepare_cordis_process(node, manager.runtime_path, snapshot, tmp_path / "probe-state")
    except CordisSecurityError as exc:
        pytest.skip(f"An enforced offline Node runtime is unavailable: {exc}")
    manager.enable("text-utilities", workspace)
    first = await manager._invoke("text-utilities", workspace, "count_text", {"text": "Hello 🌎\nNext line"})
    assert json.loads(first["content"]) == {"words": 4, "lines": 2, "characters": 17}
    manager.configure("text-utilities", workspace, {"include_characters": False})
    second = await manager._invoke("text-utilities", workspace, "count_text", {"text": "Hello 🌎\nNext line"})
    assert json.loads(second["content"]) == {"words": 4, "lines": 2}
