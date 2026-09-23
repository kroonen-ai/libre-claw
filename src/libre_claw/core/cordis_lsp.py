# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Configured, read-only language servers behind the Harness LSP seam."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import stat
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from libre_claw.core.cordis_process import prepare_host_process
from libre_claw.core.runs import settle_finalization


MAX_DOCUMENT = 1024 * 1024
MAX_FRAME = 1024 * 1024
MAX_TRAFFIC = 4 * 1024 * 1024
OPERATIONS = {"goToDefinition": "textDocument/definition", "findReferences": "textDocument/references",
              "goToImplementation": "textDocument/implementation", "hover": "textDocument/hover"}
_ID = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")
_EXT = re.compile(r"\.[a-z0-9_+-]{1,24}\Z")


def normalize_lsp_servers(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or len(value) > 16:
        raise ValueError("cordis.lsp_servers must contain at most sixteen server tables.")
    result, claimed = {}, set()
    for identity, raw in value.items():
        if (not isinstance(identity, str) or not _ID.fullmatch(identity) or not isinstance(raw, dict)
                or set(raw) - {"command", "extensions", "runtime_read_paths", "timeout_seconds"}):
            raise ValueError("Invalid configured LSP server identity or fields.")
        command = raw.get("command")
        if (not isinstance(command, list) or not 1 <= len(command) <= 64
                or any(not isinstance(item, str) or not item or "\0" in item or len(item) > 8192 for item in command)
                or sum(len(item.encode()) for item in command) > 32 * 1024):
            raise ValueError("An LSP command must be an explicit, bounded argv list.")
        extensions = raw.get("extensions")
        if not isinstance(extensions, dict) or not 1 <= len(extensions) <= 64:
            raise ValueError("An LSP server requires explicit extension-to-language mappings.")
        languages = {}
        for extension, language in extensions.items():
            if (not isinstance(extension, str) or not _EXT.fullmatch(extension.lower())
                    or extension.lower() in claimed or not isinstance(language, str)
                    or not re.fullmatch(r"[A-Za-z0-9_+.-]{1,64}", language)):
                raise ValueError("LSP extensions must be unique leading-dot mappings with valid language IDs.")
            claimed.add(extension.lower())
            languages[extension.lower()] = language
        runtime = raw.get("runtime_read_paths", [])
        if not isinstance(runtime, list) or len(runtime) > 16:
            raise ValueError("LSP runtime_read_paths must be a bounded path list.")
        paths = []
        broad = {Path("/"), Path("/home"), Path("/Users"), Path("/etc"), Path("/var"),
                 Path("/private"), Path("/tmp"), Path.home().resolve(), Path.home().resolve().parent}
        for item in runtime:
            if not isinstance(item, str) or not item or "\0" in item or len(item) > 8192:
                raise ValueError("Invalid LSP runtime read path.")
            path = Path(item).expanduser()
            if not path.is_absolute() or path.resolve() in broad:
                raise ValueError("LSP runtime paths must name specific absolute installation directories.")
            paths.append(str(path.resolve()))
        timeout = raw.get("timeout_seconds", 30)
        if type(timeout) is not int or not 1 <= timeout <= 120:
            raise ValueError("LSP timeout_seconds must be between one and 120.")
        result[identity] = {"command": list(command), "extensions": languages,
                            "runtime_read_paths": list(dict.fromkeys(paths)), "timeout_seconds": timeout}
    return result


def _position(value: Any) -> dict[str, int]:
    if (not isinstance(value, dict) or set(value) != {"line", "character"}
            or any(type(item) is not int or not 0 <= item <= 10_000_000 for item in value.values())):
        raise ValueError("LSP positions require zero-based line and UTF-16 character coordinates.")
    return {"line": value["line"], "character": value["character"]}


def _range(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"start", "end"}:
        raise ValueError("Language server returned an invalid range.")
    result = {name: _position(value[name]) for name in ("start", "end")}
    if tuple(result["end"].values()) < tuple(result["start"].values()):
        raise ValueError("Language server returned a reversed range.")
    return result


class CordisLspPool:
    def __init__(self, workspace: Path, authorize, execute_effect, servers: Any) -> None:
        self.workspace = Path(workspace).resolve()
        self.authorize = authorize
        self.execute_effect = execute_effect
        self.servers = normalize_lsp_servers(servers)
        self.closed = False
        self._tasks: set[asyncio.Task] = set()
        self._slots = asyncio.Semaphore(2)

    def providers(self) -> list[dict[str, Any]]:
        return [{"id": identity, "extensionToLanguage": dict(row["extensions"])}
                for identity, row in self.servers.items()]

    def _check(self) -> dict[str, Any]:
        if self.closed:
            raise PermissionError("The language-server task has ended.")
        grants = self.authorize()
        if not isinstance(grants, dict):
            raise PermissionError("Language servers require explicit workspace grants.")
        return grants

    def _path(self, value: Any) -> Path:
        if not isinstance(value, str) or not value or "\0" in value or len(value) > 8192:
            raise ValueError("Invalid LSP source path.")
        candidate = Path(value)
        path = (candidate if candidate.is_absolute() else self.workspace / candidate).resolve()
        grants = self._check()
        allowed = [Path(item).resolve() for item in [*grants.get("read_paths", []), *grants.get("write_paths", [])]]
        if not path.is_relative_to(self.workspace) or not any(path.is_relative_to(root) for root in allowed):
            raise PermissionError("LSP source and target files must stay inside the task's granted workspace.")
        return path

    async def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._check()
        if (method != "query" or set(params) != {"provider", "request"}
                or not isinstance(params.get("provider"), str) or params["provider"] not in self.servers):
            raise ValueError("Select a configured language-server provider; commands cannot be supplied by a plugin.")
        server = self.servers[params["provider"]]
        request = params["request"]
        if (not isinstance(request, dict) or set(request) != {"operation", "filePath", "position", "workspaceRoot", "languageId"}
                or not isinstance(request["operation"], str) or request["operation"] not in OPERATIONS
                or not isinstance(request["workspaceRoot"], str)
                or Path(request["workspaceRoot"]).resolve() != self.workspace):
            raise ValueError("Invalid LSP query or foreign workspace.")
        path = self._path(request["filePath"])
        if request["languageId"] != server["extensions"].get(path.suffix.lower()):
            raise ValueError("The selected server does not handle this file's language.")
        position = _position(request["position"])
        arguments = {"path": str(path), "operation": request["operation"], "position": position,
                     "command": list(server["command"]), "provider": params["provider"]}

        async def operation():
            async with asyncio.timeout(server["timeout_seconds"]):
                async with self._slots:
                    self._check()
                    return await self._query(server, request, path, position)

        return await self.execute_effect("lsp", arguments, operation, read_only=True)

    def _read_document(self, path: Path) -> str:
        from libre_claw.core.cordis_harness_services import HarnessHostServices
        parent = HarnessHostServices._parent_fd(path)
        try:
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        finally:
            os.close(parent)
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError("LSP queries require a regular source file.")
            data = handle.read(MAX_DOCUMENT + 1)
        if len(data) > MAX_DOCUMENT:
            raise ValueError("LSP source exceeds the one-MiB document limit.")
        try:
            return data.decode("utf-8")
        except UnicodeError:
            raise ValueError("LSP source must be UTF-8 text.") from None

    async def _query(self, server: dict, request: dict, path: Path, position: dict) -> dict[str, Any]:
        self._path(str(path))
        content = await asyncio.to_thread(self._read_document, path)
        lines = content.split("\n")
        if position["line"] >= len(lines) or position["character"] > len(lines[position["line"]].encode("utf-16-le")) // 2:
            raise ValueError("The LSP cursor is outside the source document.")
        argv = list(server["command"])
        executable = Path(argv[0]).expanduser()
        if not executable.is_absolute():
            found = shutil.which(argv[0], path=os.defpath)
            if found is None:
                raise ValueError("Configured language-server executable was not found; use its absolute path.")
            executable = Path(found)
        executable = executable.resolve()
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError("Configured language-server executable is unavailable.")
        argv[0] = str(executable)
        runtime = [Path(item) for item in server["runtime_read_paths"]]
        if any(not item.is_dir() or item.is_relative_to(self.workspace) or self.workspace.is_relative_to(item) for item in runtime):
            raise ValueError("Runtime read paths must be existing installation directories separate from the workspace.")
        grants = self._check()
        reads = [Path(item).resolve() for item in [*grants.get("read_paths", []), *grants.get("write_paths", [])]
                 if Path(item).resolve().is_relative_to(self.workspace)]
        owner = asyncio.current_task()
        self._tasks.add(owner)
        temporary = tempfile.TemporaryDirectory(prefix="libre-claw-lsp-")
        process = None
        stderr = None
        failed = False
        try:
            command, env = prepare_host_process(argv, self.workspace, Path(temporary.name),
                read_paths=reads, write_paths=(), allow_network=False, runtime_paths=[*runtime, executable])
            process = await asyncio.create_subprocess_exec(*command, cwd=self.workspace, env=env,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True, limit=MAX_FRAME + 8192)

            async def discard_stderr():
                while await process.stderr.read(8192):
                    pass

            stderr = asyncio.create_task(discard_stderr())
            channel = _LspChannel(process, self._check, self.workspace)
            initialized = await channel.request("initialize", {"processId": None, "rootUri": self.workspace.as_uri(),
                "workspaceFolders": [{"uri": self.workspace.as_uri(), "name": self.workspace.name}],
                "capabilities": {"general": {"positionEncodings": ["utf-16"]},
                                 "workspace": {"applyEdit": False}, "textDocument": {}}})
            if (not isinstance(initialized, dict) or not isinstance(initialized.get("capabilities", {}), dict)
                    or initialized.get("capabilities", {}).get("positionEncoding", "utf-16") != "utf-16"):
                raise ValueError("Language server must support UTF-16 positions.")
            await channel.notify("initialized", {})
            await channel.notify("textDocument/didOpen", {"textDocument": {
                "uri": path.as_uri(), "languageId": request["languageId"], "version": 1, "text": content}})
            params = {"textDocument": {"uri": path.as_uri()}, "position": position}
            if request["operation"] == "findReferences":
                params["context"] = {"includeDeclaration": True}
            result = await channel.request(OPERATIONS[request["operation"]], params)
            self._path(str(path))
            normalized = self._result(request["operation"], result)
            await channel.notify("textDocument/didClose", {"textDocument": {"uri": path.as_uri()}})
            # Servers that ignore shutdown still cannot keep their process alive.
            try:
                async with asyncio.timeout(1):
                    await channel.request("shutdown", None)
                    await channel.notify("exit", None)
            except TimeoutError:
                # The finalizer forcibly stops and joins servers that ignore shutdown.
                pass
            return normalized
        except BaseException:
            failed = True
            raise
        finally:
            async def close():
                try:
                    if process is not None:
                        await _stop_process(process)
                    if stderr is not None:
                        stderr.cancel()
                        await asyncio.gather(stderr, return_exceptions=True)
                finally:
                    temporary.cleanup()
            try:
                _, cancelled = await settle_finalization(asyncio.create_task(close()))
                if cancelled and not failed:
                    raise asyncio.CancelledError
            finally:
                self._tasks.discard(owner)

    def _result(self, operation: str, value: Any) -> dict[str, Any]:
        if operation == "hover":
            if value is None:
                return {"kind": "hover", "hover": None}
            if not isinstance(value, dict) or "contents" not in value:
                raise ValueError("Language server returned an invalid hover.")
            contents = value["contents"] if isinstance(value["contents"], list) else [value["contents"]]
            texts = []
            for item in contents:
                text = item if isinstance(item, str) else item.get("value") if isinstance(item, dict) else None
                if not isinstance(text, str):
                    raise ValueError("Language server returned invalid hover content.")
                texts.append(text)
            text = "\n\n".join(texts)
            if len(text.encode()) > 64 * 1024:
                raise ValueError("Language-server hover exceeds its output limit.")
            return {"kind": "hover", "hover": {"contents": text,
                    **({"range": _range(value["range"])} if "range" in value else {})}}
        locations = [] if value is None else value if isinstance(value, list) else [value]
        if len(locations) > 512:
            raise ValueError("Language server returned too many locations.")
        result = []
        for item in locations:
            if not isinstance(item, dict):
                raise ValueError("Language server returned an invalid location.")
            uri = item.get("uri", item.get("targetUri"))
            if not isinstance(uri, str) or len(uri) > 8192:
                raise ValueError("Language server returned an invalid location URI.")
            parsed = urlsplit(uri)
            if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"} or parsed.query or parsed.fragment:
                raise ValueError("Language-server locations must be local granted source files.")
            target = self._path(unquote(parsed.path, errors="strict"))
            result.append({"uri": target.as_uri(), "range": _range(item.get("range", item.get("targetSelectionRange")))})
        return {"kind": "locations", "locations": result, "resolvedWorkspaceUri": self.workspace.as_uri()}

    async def aclose(self) -> None:
        self.closed = True
        tasks = [task for task in self._tasks if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        if tasks:
            async def join():
                await asyncio.gather(*tasks, return_exceptions=True)
            _, cancelled = await settle_finalization(asyncio.create_task(join()))
            if cancelled:
                raise asyncio.CancelledError


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    # Kill the owned process group even if its leader already exited, so a
    # language server cannot leave background indexers alive after the query.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        # The process group exited before graceful termination was requested.
        pass
    if process.returncode is None:
        try:
            await asyncio.wait_for(process.wait(), 0.5)
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                # The group exited between the wait deadline and the kill request.
                pass
            await process.wait()
    # A child can outlive a gracefully exited leader and ignore SIGTERM.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        # Neither the leader nor a background indexer remains in this group.
        pass
    if process.stdin is not None:
        process.stdin.close()


class _LspChannel:
    def __init__(self, process, authorize, workspace: Path) -> None:
        self.process, self.authorize, self.workspace = process, authorize, workspace
        self.sequence = 0
        self.traffic = 0

    async def send(self, message):
        self.authorize()
        data = json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        if len(data) > MAX_FRAME:
            raise ValueError("Language-server request exceeds its frame limit.")
        self.process.stdin.write(f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
        await self.process.stdin.drain()

    async def notify(self, method, params):
        await self.send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _read(self):
        header = await self.process.stdout.readuntil(b"\r\n\r\n")
        if len(header) > 8192:
            raise ValueError("Language-server header is oversized.")
        lengths = [line.split(b":", 1)[1].strip() for line in header.split(b"\r\n") if line.lower().startswith(b"content-length:")]
        if len(lengths) != 1 or not lengths[0].isdigit() or not 0 < int(lengths[0]) <= MAX_FRAME:
            raise ValueError("Invalid language-server frame length.")
        data = await self.process.stdout.readexactly(int(lengths[0]))
        self.traffic += len(header) + len(data)
        if self.traffic > MAX_TRAFFIC:
            raise ValueError("Language server exceeded its response traffic limit.")
        message = json.loads(data)
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise ValueError("Language server returned invalid JSON-RPC.")
        return message

    async def receive(self):
        task = asyncio.create_task(self._read())
        try:
            while not task.done():
                self.authorize()
                await asyncio.wait({task}, timeout=0.1)
            self.authorize()
            return task.result()
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def request(self, method, params):
        self.sequence += 1
        identity = self.sequence
        await self.send({"jsonrpc": "2.0", "id": identity, "method": method, "params": params})
        for _ in range(256):
            message = await self.receive()
            if "method" in message:
                if "id" not in message:
                    continue
                name = message["method"]
                if name == "workspace/configuration":
                    items = message.get("params", {}).get("items", [])
                    if not isinstance(items, list) or len(items) > 64:
                        raise ValueError("Invalid language-server configuration request.")
                    result = [None] * len(items)
                elif name == "workspace/workspaceFolders":
                    result = [{"uri": self.workspace.as_uri(), "name": self.workspace.name}]
                elif name == "workspace/applyEdit":
                    result = {"applied": False, "failureReason": "Language-server operations are read-only."}
                elif name in {"client/registerCapability", "client/unregisterCapability", "window/workDoneProgress/create"}:
                    result = None
                else:
                    await self.send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "Unsupported client request"}})
                    continue
                await self.send({"jsonrpc": "2.0", "id": message["id"], "result": result})
                continue
            if type(message.get("id")) is not int or message["id"] != identity:
                raise ValueError("Language server returned a response for an unknown request.")
            if "error" in message:
                raise ValueError("Language server rejected the semantic request.")
            if "result" not in message:
                raise ValueError("Language server returned no semantic result.")
            return message["result"]
        raise ValueError("Language server exceeded its message limit.")
