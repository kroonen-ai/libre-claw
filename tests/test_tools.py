# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import shlex
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

import pytest

from libre_claw.core.tools import BaseTool, ToolCall, ToolContext, ToolRegistry, ToolRegistryError, ToolResult
from libre_claw.tools_builtin import browser as browser_tools
from libre_claw.tools_builtin import create_builtin_registry
from libre_claw.tools_builtin.browser import (
    BrowserClickTool,
    BrowserDismissCookiesTool,
    BrowserDownloadTool,
    BrowserExecuteTool,
    BrowserExtractTool,
    BrowserNavigateTool,
    BrowserReadTool,
    BrowserScreenshotTool,
    BrowserState,
    BrowserTypeTool,
    BrowserWaitTool,
)
from libre_claw.tools_builtin.filesystem import EditFileTool, ListDirectoryTool, ReadFileTool, WriteFileTool
from libre_claw.tools_builtin.git import GitCommitTool, GitStatusTool
from libre_claw.tools_builtin.http import HTTPRequestTool
from libre_claw.tools_builtin.schedule import ScheduleListTool, ScheduleTool
from libre_claw.tools_builtin.search import GlobTool, SearchFilesTool
from libre_claw.tools_builtin.shell import BashTool
from libre_claw.tools_builtin.skills import SkillsSearchTool
from libre_claw.tools_builtin.think import ThinkTool
from libre_claw.tools_builtin.web_search import WebSearchTool


class ExampleTool(BaseTool):
    name = "example"
    description = "Example tool."
    parameters = {"value": {"type": "string"}}
    required = ("value",)
    permission_level = "allow"

    async def execute(self, value: str) -> ToolResult:
        return ToolResult(content=value)


def context(tmp_path: Path, timeout: int = 120) -> ToolContext:
    return ToolContext(
        working_directory=tmp_path,
        restrict_to_working_dir=True,
        command_timeout=timeout,
        allow_sudo=False,
        blocked_patterns=("rm -rf /",),
    )


async def test_tool_registry_schema_duplicate_missing_and_execute(tmp_path: Path) -> None:
    tool = ExampleTool(context(tmp_path))
    registry = ToolRegistry([tool])

    assert registry.schemas() == [
        {
            "name": "example",
            "description": "Example tool.",
            "input_schema": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        }
    ]
    assert await registry.execute(ToolCall(id="1", name="example", arguments={"value": "ok"})) == ToolResult(
        content="ok"
    )

    with pytest.raises(ToolRegistryError):
        registry.register(ExampleTool(context(tmp_path)))
    with pytest.raises(ToolRegistryError):
        registry.get("missing")


def test_builtin_registry_exposes_production_toolset(monkeypatch, tmp_path: Path) -> None:
    from libre_claw.config import load_config

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    registry = create_builtin_registry(load_config(working_directory=tmp_path))
    names = {schema["name"] for schema in registry.schemas()}

    assert {
        "read_file",
        "write_file",
        "edit_file",
        "list_directory",
        "glob",
        "search_files",
        "git_status",
        "git_commit",
        "think",
        "browser_navigate",
        "browser_read",
        "browser_extract",
        "browser_execute",
        "browser_dismiss_cookies",
        "browser_click",
        "browser_type",
        "browser_wait",
        "browser_download",
        "browser_screenshot",
        "web_search",
        "http_request",
        "schedule_list",
        "schedule",
        "bash",
    }.issubset(names)
    assert "skills_search" not in names


def test_builtin_registry_filters_disabled_and_configured_tools(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from libre_claw.config import load_config

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config(working_directory=tmp_path)
    config = replace(
        config,
        agent=replace(
            config.agent,
            tool_allowlist=("read_file", "web_search", "schedule", "skills_search"),
        ),
        automations=replace(config.automations, enabled=False),
        skills=replace(
            config.skills,
            enabled=True,
            external_discovery_enabled=False,
        ),
        web_search=replace(config.web_search, enabled=False),
    )

    names = {schema["name"] for schema in create_builtin_registry(config).schemas()}

    assert names == {"read_file"}


def test_builtin_registry_honors_denylist_and_skills_discovery(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from libre_claw.config import load_config

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config(working_directory=tmp_path)
    config = replace(
        config,
        agent=replace(config.agent, tool_denylist=("bash",)),
        skills=replace(config.skills, external_discovery_enabled=True),
    )

    names = {schema["name"] for schema in create_builtin_registry(config).schemas()}

    assert "skills_search" in names
    assert "bash" not in names


async def test_read_file_with_offset_and_limit(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_text("a\nb\nc\n", encoding="utf-8")

    result = await ReadFileTool(context(tmp_path)).execute(path="sample.txt", offset=1, limit=1)

    assert result.content == "1: b"
    assert result.metadata["returned_lines"] == 1


async def test_read_file_reports_truncation_and_can_hide_line_numbers(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_text("a\nb\nc\n", encoding="utf-8")

    result = await ReadFileTool(context(tmp_path)).execute(
        path="sample.txt",
        offset=0,
        limit=2,
        show_line_numbers=False,
    )

    assert result.content == "a\nb"
    assert result.metadata["truncated"] is True


async def test_list_directory_with_depth(tmp_path: Path) -> None:
    (tmp_path / "dir" / "nested").mkdir(parents=True)
    (tmp_path / "dir" / "nested" / "deep.txt").write_text("x", encoding="utf-8")
    (tmp_path / "root.txt").write_text("x", encoding="utf-8")

    result = await ListDirectoryTool(context(tmp_path)).execute(path=".", depth=1)

    assert "dir/" in result.content
    assert "  nested/" in result.content
    assert "deep.txt" not in result.content
    assert "root.txt" in result.content


async def test_list_directory_can_limit_entries_and_skip_hidden(tmp_path: Path) -> None:
    (tmp_path / ".hidden").write_text("x", encoding="utf-8")
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "b.txt").write_text("x", encoding="utf-8")

    hidden = await ListDirectoryTool(context(tmp_path)).execute(path=".", include_hidden=False)
    limited = await ListDirectoryTool(context(tmp_path)).execute(path=".", max_entries=1)

    assert ".hidden" not in hidden.content
    assert limited.metadata["truncated"] is True
    assert "... truncated after 1 entries" in limited.content


async def test_glob_finds_paths_and_respects_hidden_and_limit(tmp_path: Path) -> None:
    (tmp_path / "src" / "nested").mkdir(parents=True)
    (tmp_path / "src" / "app.py").write_text("print('hi')", encoding="utf-8")
    (tmp_path / "src" / "nested" / "test_app.py").write_text("x", encoding="utf-8")
    (tmp_path / ".hidden.py").write_text("x", encoding="utf-8")

    visible = await GlobTool(context(tmp_path)).execute(pattern="**/*.py")
    limited = await GlobTool(context(tmp_path)).execute(pattern="*.py", max_results=1, include_hidden=True)

    assert "src/app.py" in visible.content
    assert "src/nested/test_app.py" in visible.content
    assert ".hidden.py" not in visible.content
    assert limited.metadata["truncated"] is True


async def test_search_files_uses_python_fallback_when_rg_is_unavailable(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("Alpha\nbeta\nALPHA again\n", encoding="utf-8")

    result = await SearchFilesTool(context(tmp_path)).execute(
        query="alpha",
        path="src",
        glob="*.py",
        case_sensitive=False,
        max_matches=1,
    )

    assert result.error is None
    assert "app.py:1:Alpha" in result.content
    assert result.metadata["engine"] == "python"
    assert result.metadata["truncated"] is True


async def test_search_files_reports_validation_errors(tmp_path: Path) -> None:
    tool = SearchFilesTool(context(tmp_path))

    assert (await tool.execute(query="")).error == "query must not be empty"
    assert (await tool.execute(query="x", context=99)).error == "context must be <= 5"
    assert (await tool.execute(query="x", max_matches=0)).error == "max_matches must be >= 1"


async def test_write_file(tmp_path: Path) -> None:
    result = await WriteFileTool(context(tmp_path)).execute(path="new/file.txt", content="hello")

    assert (tmp_path / "new" / "file.txt").read_text(encoding="utf-8") == "hello"
    assert "Created" in result.content
    assert result.metadata["changed"] is True
    assert result.metadata["bytes_written"] == 5


async def test_write_file_refuses_overwrite_and_detects_noop(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_text("hello", encoding="utf-8")

    refused = await WriteFileTool(context(tmp_path)).execute(path="sample.txt", content="new", overwrite=False)
    noop = await WriteFileTool(context(tmp_path)).execute(path="sample.txt", content="hello")

    assert refused.error is not None
    assert "overwrite is false" in refused.error
    assert noop.error is None
    assert noop.metadata["changed"] is False


async def test_edit_file_exact_match(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_text("hello world", encoding="utf-8")

    result = await EditFileTool(context(tmp_path)).execute(
        path="sample.txt",
        old_text="world",
        new_text="Libre Claw",
    )

    assert path.read_text(encoding="utf-8") == "hello Libre Claw"
    assert result.error is None
    assert "Replaced 1 occurrence" in result.content
    assert "--- " in result.content
    assert result.metadata["replacements"] == 1


async def test_edit_file_missing_match_returns_error(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_text("hello world", encoding="utf-8")

    result = await EditFileTool(context(tmp_path)).execute(
        path="sample.txt",
        old_text="missing",
        new_text="Libre Claw",
    )

    assert result.error == "old_text was not found"


async def test_edit_file_requires_precision_for_multiple_matches(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_text("same same same", encoding="utf-8")

    ambiguous = await EditFileTool(context(tmp_path)).execute(
        path="sample.txt",
        old_text="same",
        new_text="changed",
    )
    second = await EditFileTool(context(tmp_path)).execute(
        path="sample.txt",
        old_text="same",
        new_text="changed",
        occurrence=2,
    )

    assert ambiguous.error == "old_text matched 3 times; set occurrence or replace_all"
    assert path.read_text(encoding="utf-8") == "same changed same"
    assert second.metadata["matches"] == 3


async def test_bash_success_failure_and_timeout(tmp_path: Path) -> None:
    tool = BashTool(context(tmp_path, timeout=1))

    success = await tool.execute(command="printf hello")
    failure = await tool.execute(command="exit 3")
    timeout = await tool.execute(command="sleep 2", timeout=1)

    assert "stdout:\nhello" in success.content
    assert failure.metadata["exit_code"] == 3
    assert timeout.error == "Command timed out after 1 seconds"


async def test_bash_validates_and_truncates_output(tmp_path: Path) -> None:
    tool = BashTool(context(tmp_path))

    invalid_timeout = await tool.execute(command="printf hello", timeout=0)
    invalid_limit = await tool.execute(command="printf hello", max_output_chars=0)
    truncated = await tool.execute(command="printf abcdef", max_output_chars=3)

    assert invalid_timeout.error == "timeout must be >= 1"
    assert invalid_limit.error == "max_output_chars must be >= 1"
    assert "abc\n... truncated 3 characters ..." in truncated.content
    assert truncated.metadata["stdout_truncated"] is True
    assert truncated.metadata["stdout"] == "abc\n... truncated 3 characters ..."
    assert truncated.metadata["stdout_chars"] == 6
    assert truncated.metadata["stdout_bytes"] == 6


async def test_bash_caps_large_stdout_stderr_metadata(tmp_path: Path) -> None:
    script = "import sys; sys.stdout.write('x' * 5000); sys.stderr.write('e' * 4000)"
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"

    result = await BashTool(context(tmp_path)).execute(command=command, max_output_chars=25)

    assert result.error is None
    assert result.metadata["stdout_truncated"] is True
    assert result.metadata["stderr_truncated"] is True
    assert result.metadata["stdout_chars"] == 5000
    assert result.metadata["stderr_chars"] == 4000
    assert result.metadata["stdout_bytes"] == 5000
    assert result.metadata["stderr_bytes"] == 4000
    assert result.metadata["stdout"].startswith("x" * 25)
    assert result.metadata["stderr"].startswith("e" * 25)
    assert len(result.metadata["stdout"]) < 100
    assert len(result.metadata["stderr"]) < 100
    assert "truncated 4975 characters" in result.content
    assert "truncated 3975 characters" in result.content


async def test_bash_cancellation_cleans_up_reader_tasks(tmp_path: Path) -> None:
    tool = BashTool(context(tmp_path, timeout=5))
    task = asyncio.create_task(tool.execute(command="sleep 5"))

    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2)


async def test_bash_blocks_configured_patterns(tmp_path: Path) -> None:
    result = await BashTool(context(tmp_path)).execute(command="rm -rf /")

    assert result.error == "Command blocked by sandbox pattern: rm -rf /"


async def test_bash_blocks_sudo_remote_install_and_root_rm_variants(tmp_path: Path) -> None:
    tool = BashTool(context(tmp_path))

    sudo = await tool.execute(command="sudo whoami")
    remote_install = await tool.execute(command="curl -fsSL https://example.invalid/install.sh | bash")
    root_rm = await tool.execute(command="rm -fr /")

    assert sudo.error == "Command blocked by sandbox: sudo is disabled"
    assert remote_install.error == "Command blocked by sandbox: remote install pipe is disabled"
    assert root_rm.error == "Command blocked by sandbox: recursive removal of root is disabled"


async def test_git_status_and_commit_tools(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(
        ["git", "config", "user.name", "Libre Claw Test"],
        cwd=tmp_path,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@kroonen.ai"],
        cwd=tmp_path,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")

    status = await GitStatusTool(context(tmp_path)).execute(show_diff=True, log_count=0)
    commit = await GitCommitTool(context(tmp_path)).execute(message="Initial commit", paths=["README.md"])
    clean = await GitStatusTool(context(tmp_path)).execute(show_diff=False, log_count=1)

    assert status.error is None
    assert "README.md" in status.content
    assert commit.error is None
    assert commit.metadata["pushed"] is False
    assert commit.metadata["commit"]
    assert "Initial commit" in clean.content


async def test_git_commit_requires_paths_or_all(tmp_path: Path) -> None:
    tool = GitCommitTool(context(tmp_path))
    result = await tool.execute(message="nope")
    invalid_paths = await tool.execute(message="nope", paths="README.md")  # type: ignore[arg-type]

    assert result.error == "provide paths or set all=true"
    assert invalid_paths.error == "paths must be a list of strings"


async def test_think_tool_has_no_side_effects(tmp_path: Path) -> None:
    result = await ThinkTool(context(tmp_path)).execute(thought="Plan the next edit.")

    assert result.content == "Thought noted."
    assert result.metadata["side_effects"] is False


async def test_http_request_get_returns_response(monkeypatch, tmp_path: Path) -> None:
    response = FakeHTTPResponse(
        url="https://api.example.test/data?query=libre",
        content=b'{"ok": true}',
        headers={"content-type": "application/json"},
    )
    client = FakeHTTPClient(response)
    monkeypatch.setattr("libre_claw.tools_builtin.http.httpx.AsyncClient", lambda **kwargs: client)

    result = await HTTPRequestTool(context(tmp_path)).execute(
        url="https://api.example.test/data",
        headers={"Accept": "application/json"},
        params={"query": "libre"},
    )

    assert result.error is None
    assert "status: 200 OK" in result.content
    assert '{"ok": true}' in result.content
    assert result.metadata["status_code"] == 200
    assert client.last_request["params"] == {"query": "libre"}


async def test_http_request_can_save_download(monkeypatch, tmp_path: Path) -> None:
    response = FakeHTTPResponse(
        url="https://cdn.example.test/image.png",
        content=b"image-bytes",
        headers={"content-type": "image/png"},
    )
    monkeypatch.setattr("libre_claw.tools_builtin.http.httpx.AsyncClient", lambda **kwargs: FakeHTTPClient(response))

    result = await HTTPRequestTool(context(tmp_path)).execute(
        url="https://cdn.example.test/image.png",
        output_path="downloads/image.png",
    )

    saved = tmp_path / "downloads" / "image.png"
    assert result.error is None
    assert saved.read_bytes() == b"image-bytes"
    assert result.metadata["saved_path"] == str(saved)


async def test_http_request_validates_inputs_and_sandbox(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "libre_claw.tools_builtin.http.httpx.AsyncClient",
        lambda **kwargs: FakeHTTPClient(FakeHTTPResponse(url="https://api.example.test")),
    )
    tool = HTTPRequestTool(context(tmp_path))

    invalid_method = await tool.execute(url="https://api.example.test", method="TRACE")
    body_conflict = await tool.execute(url="https://api.example.test", body="raw", json_body={"ok": True})
    outside = await tool.execute(url="https://api.example.test", output_path=str(tmp_path.parent / "outside.bin"))

    assert invalid_method.error is not None
    assert "method must be one of" in invalid_method.error
    assert body_conflict.error == "provide body or json_body, not both"
    assert outside.error is not None
    assert "outside the working directory" in outside.error


async def test_web_search_returns_normalized_searxng_results(monkeypatch, tmp_path: Path) -> None:
    response = FakeWebSearchResponse(
        {
            "results": [
                {
                    "title": "Libre Claw",
                    "url": "https://libreclaw.sh",
                    "content": "Terminal native agent harness",
                    "engine": "duckduckgo",
                    "score": 1.0,
                },
                {
                    "title": "Kroonen AI",
                    "url": "https://kroonen.ai",
                    "content": "Builder of Libre Claw",
                    "engine": "brave",
                },
            ]
        }
    )
    client = FakeWebSearchClient(response)
    monkeypatch.setattr("libre_claw.tools_builtin.web_search.httpx.AsyncClient", lambda **kwargs: client)

    result = await WebSearchTool(context(tmp_path)).execute(
        query="libre claw",
        max_results=1,
        categories=["general", "it"],
    )

    assert result.error is None
    assert "web_search: libre claw" in result.content
    returned_urls = [urlparse(str(item["url"])) for item in result.metadata["results"]]
    assert [(url.scheme, url.netloc, url.path) for url in returned_urls] == [("https", "libreclaw.sh", "")]
    assert result.metadata["returned_results"] == 1
    assert result.metadata["result_count"] == 2
    assert client.last_request["params"]["categories"] == "general,it"


async def test_web_search_reports_json_disabled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "libre_claw.tools_builtin.web_search.httpx.AsyncClient",
        lambda **kwargs: FakeWebSearchClient(FakeWebSearchResponse({}, status_code=403, text="Forbidden")),
    )

    result = await WebSearchTool(context(tmp_path)).execute(query="libre claw")

    assert result.error is not None
    assert "JSON search output" in result.error


async def test_web_search_validates_inputs(tmp_path: Path) -> None:
    tool = WebSearchTool(context(tmp_path))

    assert (await tool.execute(query="")).error == "query must not be empty"
    assert (await tool.execute(query="x", max_results=0)).error == "max_results must be >= 1"
    assert (await tool.execute(query="x", max_results=99)).error == "max_results must be <= 50"
    assert (await tool.execute(query="x", page=0)).error == "page must be >= 1"


async def test_skills_search_uses_configured_cli(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_create_subprocess_exec(*args: str, **kwargs: object) -> "FakeSkillSearchProcess":
        calls.append(args)
        assert kwargs["cwd"] == tmp_path
        return FakeSkillSearchProcess(stdout=b"web-design-guidelines 120000 installs\n")

    monkeypatch.setattr("libre_claw.tools_builtin.skills.asyncio.create_subprocess_exec", fake_create_subprocess_exec)
    ctx = ToolContext(
        working_directory=tmp_path,
        restrict_to_working_dir=True,
        command_timeout=120,
        allow_sudo=False,
        blocked_patterns=(),
        skills_external_discovery_enabled=True,
        skills_cli_command="npx -y skills@latest",
    )

    result = await SkillsSearchTool(ctx).execute(query="web design", owner="vercel-labs")

    assert result.error is None
    assert "skills_search: web design" in result.content
    assert "web-design-guidelines" in result.content
    assert calls == [("npx", "-y", "skills@latest", "find", "web design", "--owner", "vercel-labs")]


async def test_skills_search_requires_external_discovery(tmp_path: Path) -> None:
    result = await SkillsSearchTool(context(tmp_path)).execute(query="react")

    assert result.error == "skills_search is disabled by [skills].external_discovery_enabled"


async def test_schedule_tool_creates_updates_and_deletes_automation(tmp_path: Path) -> None:
    automations_root = tmp_path / "automations"
    tool_context = ToolContext(
        working_directory=tmp_path,
        automations_root=automations_root,
        default_provider="openrouter",
        default_model="openrouter/auto",
    )
    tool = ScheduleTool(tool_context)

    created = await tool.execute(
        action="create",
        name="Daily forecast",
        schedule="daily 08:00",
        prompt="Send a compact weather forecast.",
        route="report",
    )

    assert created.error is None
    automation = created.metadata["automation"]
    automation_id = automation["automation_id"]
    assert automation["provider"] == "openrouter"
    assert automation["model"] == "openrouter/auto"
    assert automation["schedule"] == "daily 08:00"

    listed = await ScheduleListTool(tool_context).execute()
    assert listed.error is None
    assert automation_id in listed.content
    assert listed.metadata["count"] == 1

    updated = await tool.execute(
        action="update",
        automation_id=automation_id,
        schedule="0 8 * * *",
        model="deepseek/deepseek-v4-flash",
    )

    assert updated.error is None
    assert updated.metadata["automation"]["schedule"] == "0 8 * * *"
    assert updated.metadata["automation"]["model"] == "deepseek/deepseek-v4-flash"
    assert updated.metadata["automation"]["route"] == "report"

    paused = await tool.execute(action="pause", automation_id=automation_id)
    assert paused.error is None
    assert paused.metadata["automation"]["status"] == "paused"

    resumed = await tool.execute(action="resume", automation_id=automation_id)
    assert resumed.error is None
    assert resumed.metadata["automation"]["status"] == "active"

    deleted = await tool.execute(action="delete", automation_id=automation_id)
    assert deleted.error is None
    assert deleted.metadata["deleted"] is True


async def test_schedule_tool_requires_telegram_chat_for_telegram_route(tmp_path: Path) -> None:
    tool = ScheduleTool(ToolContext(working_directory=tmp_path, automations_root=tmp_path / "automations"))

    missing_chat = await tool.execute(
        action="create",
        name="Telegram forecast",
        schedule="daily 08:00",
        prompt="Send the forecast.",
        route="telegram",
    )
    with_chat = await tool.execute(
        action="create",
        name="Telegram forecast",
        schedule="daily 08:00",
        prompt="Send the forecast.",
        route="telegram",
        telegram_chat_id="42",
    )

    assert missing_chat.error == "telegram_chat_id is required when route is telegram"
    assert with_chat.error is None
    assert with_chat.metadata["automation"]["telegram_chat_id"] == 42


async def test_browser_tools_gracefully_handle_missing_session_or_dependency(monkeypatch, tmp_path: Path) -> None:
    def missing_playwright(name: str):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(browser_tools.importlib, "import_module", missing_playwright)

    navigate = await BrowserNavigateTool(context(tmp_path)).execute(url="https://example.com")
    read = await BrowserReadTool(context(tmp_path)).execute()
    extract = await BrowserExtractTool(context(tmp_path)).execute()
    execute = await BrowserExecuteTool(context(tmp_path)).execute(script="() => true")
    dismiss = await BrowserDismissCookiesTool(context(tmp_path)).execute()
    screenshot = await BrowserScreenshotTool(context(tmp_path)).execute()

    assert navigate.error is not None
    assert "Playwright is not installed" in navigate.error or "Executable doesn't exist" in navigate.error
    for result in (read, extract, execute, dismiss, screenshot):
        assert result.error is not None
        assert "No browser page is open" in result.error or "Playwright is not installed" in result.error


async def test_browser_policy_blocks_denied_domains(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    ctx = ToolContext(
        working_directory=ctx.working_directory,
        restrict_to_working_dir=ctx.restrict_to_working_dir,
        command_timeout=ctx.command_timeout,
        allow_sudo=ctx.allow_sudo,
        blocked_patterns=ctx.blocked_patterns,
        browser_denied_domains=("example.com",),
    )

    result = await BrowserNavigateTool(ctx).execute(url="https://example.com")

    assert result.error is not None
    assert "denied" in result.error


def test_browser_artifact_dirs_relocate_to_active_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    app_root = tmp_path / "app"
    workspace.mkdir()
    app_root.mkdir()
    ctx = ToolContext(
        working_directory=workspace,
        restrict_to_working_dir=True,
        command_timeout=120,
        allow_sudo=False,
        blocked_patterns=(),
        browser_downloads_dir=app_root / ".libre-claw" / "browser" / "downloads",
        browser_screenshots_dir=app_root / ".libre-claw" / "browser" / "screenshots",
    )
    tool = BrowserReadTool(ctx)

    downloads = browser_tools._resolve_browser_output_dir(tool, ctx.browser_downloads_dir)
    download_file = browser_tools._resolve_browser_file(tool, "", ctx.browser_downloads_dir, "system-card.pdf")
    screenshot_file = browser_tools._resolve_browser_file(tool, "", ctx.browser_screenshots_dir, "page.png")

    assert downloads == workspace / ".libre-claw" / "browser" / "downloads"
    assert download_file == workspace / ".libre-claw" / "browser" / "downloads" / "system-card.pdf"
    assert screenshot_file == workspace / ".libre-claw" / "browser" / "screenshots" / "page.png"


async def test_browser_selector_actions_and_artifacts_use_shared_profile(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    page = FakePage()
    read_tool = BrowserReadTool(ctx)
    state = BrowserState(read_tool, "default")
    state.page = page
    ctx.shared_state["browser_states"] = {"default": state}

    read = await read_tool.execute(selector="main")
    extracted = await BrowserExtractTool(ctx).execute(kind="images")
    executed = await BrowserExecuteTool(ctx).execute(script="(arg) => ({ ok: true, arg })", arg={"x": 1})
    dismissed = await BrowserDismissCookiesTool(ctx).execute()
    clicked = await BrowserClickTool(ctx).execute(selector="button.save")
    typed = await BrowserTypeTool(ctx).execute(selector="input[name=q]", text="hello", press_enter=True)
    waited = await BrowserWaitTool(ctx).execute(selector=".ready")
    screenshot = await BrowserScreenshotTool(ctx).execute(selector="main")
    download = await BrowserDownloadTool(ctx).execute(selector="a.download")
    outside = await BrowserScreenshotTool(ctx).execute(path=str(tmp_path.parent / "outside.png"))

    assert "selector: main" in read.content
    assert '"images"' in extracted.content
    assert executed.metadata["artifact_type"] == "browser_execute"
    assert dismissed.metadata["action"] == "dismiss_cookies"
    assert clicked.metadata["action"] == "click"
    assert typed.metadata["action"] == "type"
    assert waited.metadata["action"] == "wait"
    assert screenshot.metadata["artifact_type"] == "browser_screenshot"
    assert Path(str(screenshot.metadata["path"])).exists()
    assert download.metadata["artifact_type"] == "browser_download"
    assert Path(str(download.metadata["path"])).exists()
    assert outside.error is not None
    assert "outside the working directory" in outside.error


async def test_browser_state_survives_new_tool_context_for_same_profile(tmp_path: Path) -> None:
    first_context = context(tmp_path)
    second_context = context(tmp_path)
    page = FakePage()
    state = browser_tools._browser_state(BrowserReadTool(first_context), "default")
    state.page = page
    state.last_url = page.url

    result = await BrowserReadTool(second_context).execute(selector="main")

    assert result.error is None
    assert "text for main" in result.content
    assert second_context.shared_state["browser_states"]["default"] is state


class FakeHTTPResponse:
    def __init__(
        self,
        url: str,
        content: bytes = b"",
        headers: dict[str, str] | None = None,
        status_code: int = 200,
        reason_phrase: str = "OK",
    ) -> None:
        self.url = url
        self.content = content
        self.headers = headers or {}
        self.status_code = status_code
        self.reason_phrase = reason_phrase

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")


class FakeHTTPClient:
    def __init__(self, response: FakeHTTPResponse) -> None:
        self.response = response
        self.last_request: dict[str, object] = {}

    async def __aenter__(self) -> "FakeHTTPClient":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback

    async def request(self, method: str, url: str, **kwargs: object) -> FakeHTTPResponse:
        self.last_request = {"method": method, "url": url, **kwargs}
        return self.response


class FakeWebSearchResponse:
    def __init__(self, payload: dict[str, object], status_code: int = 200, text: str = "") -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self) -> dict[str, object]:
        return self._payload


class FakeWebSearchClient:
    def __init__(self, response: FakeWebSearchResponse) -> None:
        self.response = response
        self.last_request: dict[str, object] = {}

    async def __aenter__(self) -> "FakeWebSearchClient":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback

    async def get(self, url: str, **kwargs: object) -> FakeWebSearchResponse:
        self.last_request = {"url": url, **kwargs}
        return self.response


class FakeSkillSearchProcess:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode


class FakePage:
    url = "https://kroonen.ai/app"

    def __init__(self) -> None:
        self.download = FakeDownload()
        self.clicked_selectors: list[str] = []

    async def title(self) -> str:
        return "Fake Page"

    def locator(self, selector: str) -> "FakeLocator":
        return FakeLocator(selector, self)

    async def evaluate(self, script: str, arg: object | None = None) -> object:
        if "querySelectorAll" in script:
            return {
                "url": self.url,
                "title": "Fake Page",
                "images": [{"src": "https://kroonen.ai/logo.png", "alt": "Libre Claw"}],
                "links": [{"href": "https://kroonen.ai", "text": "Kroonen AI"}],
            }
        return {"ok": True, "arg": arg}

    async def screenshot(self, path: str, full_page: bool = True) -> None:
        del full_page
        Path(path).write_bytes(b"png")

    def expect_download(self, timeout: int) -> "FakeDownloadContext":
        del timeout
        return FakeDownloadContext(self.download)

    async def wait_for_load_state(self, state: str, timeout: int) -> None:
        del state, timeout


class FakeLocator:
    def __init__(self, selector: str, page: FakePage) -> None:
        self.selector = selector
        self.page = page

    @property
    def first(self) -> "FakeLocator":
        return self

    async def inner_text(self, timeout: int) -> str:
        del timeout
        return f"text for {self.selector}"

    async def click(self, timeout: int) -> None:
        del timeout
        self.page.clicked_selectors.append(self.selector)

    async def fill(self, text: str, timeout: int) -> None:
        del text, timeout

    async def type(self, text: str, timeout: int) -> None:
        del text, timeout

    async def press(self, key: str, timeout: int) -> None:
        del key, timeout

    async def wait_for(self, state: str, timeout: int) -> None:
        del state, timeout

    async def screenshot(self, path: str) -> None:
        Path(path).write_bytes(b"png")


class FakeDownload:
    suggested_filename = "artifact.txt"

    async def save_as(self, path: str) -> None:
        Path(path).write_text("downloaded", encoding="utf-8")


class FakeDownloadContext:
    def __init__(self, download: FakeDownload) -> None:
        self._download = download

    @property
    def value(self):
        async def _download() -> FakeDownload:
            return self._download

        return _download()

    async def __aenter__(self) -> "FakeDownloadContext":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback


async def test_file_tools_restrict_paths_to_working_directory(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-libre-claw-test.txt"
    outside.write_text("secret", encoding="utf-8")

    result = await ReadFileTool(context(tmp_path)).execute(path=str(outside))

    assert result.error is not None
    assert "outside the working directory" in result.error
