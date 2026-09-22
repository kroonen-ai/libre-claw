# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Prepare a local Cordis process with explicit resource grants.

Node's permission model provides guardrails for trusted plugins, not a security
boundary against malicious JavaScript. macOS additionally enforces the offline
network restriction at the OS level. Unsupported offline configurations fail
closed instead of silently starting a process with network access.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


class CordisSecurityError(RuntimeError):
    """The requested Cordis process restrictions cannot be applied."""


@dataclass(frozen=True)
class CordisProcess:
    command: tuple[str, ...]
    env: dict[str, str]
    cwd: Path
    isolation: str


_PROBE_TIMEOUT = 5
_MACOS_OFFLINE_PROFILE = "(version 1) (allow default) (deny network*)"
_NETWORK_PROBE = """
const net = require('node:net');
const server = net.createServer();
server.once('error', error => {
  if (error.code === 'EPERM' || error.code === 'EACCES' || error.code === 'ERR_ACCESS_DENIED') {
    process.stdout.write('cordis-network-denied');
    process.exit(0);
  }
  process.exit(2);
});
try {
  server.listen(0, '127.0.0.1', () => server.close(() => process.exit(3)));
} catch (error) {
  if (error.code === 'ERR_ACCESS_DENIED') {
    process.stdout.write('cordis-network-denied');
    process.exit(0);
  }
  process.exit(2);
}
"""


def prepare_cordis_process(
    node_executable: str,
    runtime_path: Path,
    plugin_root: Path,
    state_dir: Path,
    *,
    allow_network: bool = False,
    read_paths: tuple[Path, ...] = (),
    write_paths: tuple[Path, ...] = (),
) -> CordisProcess:
    """Return argv and a clean environment; never execute plugin code here.

    The bridge, plugin directory, private state, and explicit read grants are
    readable. Only private state and explicit write grants are writable. The
    caller must ensure plugin/dependency directories contain trusted code and
    do not include unrelated private files. Grants are not inherited from the
    Libre Claw process or its current workspace.
    """
    runtime = _grant_path(runtime_path)
    plugin = _grant_path(plugin_root)
    state = _grant_path(state_dir)
    if not state.name:
        raise CordisSecurityError("The Cordis state directory cannot be a filesystem root.")
    reads = tuple(_grant_path(path) for path in read_paths)
    writes = tuple(_grant_path(path) for path in write_paths)
    if not runtime.is_file():
        raise CordisSecurityError("The Cordis runtime must be an existing JavaScript file.")
    if not plugin.is_dir():
        raise CordisSecurityError("The Cordis plugin root must be an existing directory.")
    node = _node_path(node_executable)
    try:
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        state.chmod(0o700)
        temporary = state / "tmp"
        if temporary.is_symlink():
            raise CordisSecurityError("The private Cordis temporary directory cannot be a symbolic link.")
        temporary.mkdir(mode=0o700, exist_ok=True)
        temporary.chmod(0o700)
    except OSError as exc:
        raise CordisSecurityError("Cannot create the private Cordis state directory.") from exc

    env = {
        "PATH": os.defpath,
        "HOME": str(state),
        "TMPDIR": str(temporary),
        "TMP": str(temporary),
        "TEMP": str(temporary),
        "LANG": "C.UTF-8",
        "NO_COLOR": "1",
        "DO_NOT_TRACK": "1",
    }
    version_text = _probe((node, "--version"), env, state)
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)(?:[-+][\w.-]+)?", version_text.strip())
    if match is None or int(match.group(1)) < 22:
        raise CordisSecurityError("Cordis requires Node.js 22 or newer with --permission support.")
    major = int(match.group(1))
    help_text = _probe((node, "--help"), env, state)
    if not _has_flag(help_text, "--permission"):
        raise CordisSecurityError("This Node.js runtime does not support --permission.")

    flags = ["--permission", "--max-old-space-size=256", "--no-global-search-paths"]
    # SQLite has filesystem APIs outside Node's fs permission checks. It did
    # not exist before Node 22.5, where this disabling flag was also introduced.
    if _has_flag(help_text, "--no-experimental-sqlite"):
        flags.append("--no-experimental-sqlite")
    elif (major, int(match.group(2))) >= (22, 5):
        raise CordisSecurityError("This Node.js runtime cannot disable the SQLite filesystem API.")
    for flag in ("--report-exclude-env", "--report-exclude-network"):
        if _has_flag(help_text, flag):
            flags.append(flag)

    for path in dict.fromkeys((runtime.parent, plugin, state, *reads)):
        flags.append(f"--allow-fs-read={path}")
    for path in dict.fromkeys((state, *writes)):
        flags.append(f"--allow-fs-write={path}")

    supports_network_permission = major >= 25 and _has_flag(help_text, "--allow-net")
    if major >= 25 and not supports_network_permission:
        raise CordisSecurityError("This Node.js runtime does not expose the expected network permission controls.")
    prefix: tuple[str, ...] = ()
    isolation = "node-permissions-network-allowed"
    if allow_network:
        if supports_network_permission:
            flags.append("--allow-net")
    elif supports_network_permission:
        isolation = "node-permissions-network-denied"
    elif sys.platform == "darwin":
        sandbox = Path("/usr/bin/sandbox-exec")
        if not sandbox.is_file() or not os.access(sandbox, os.X_OK):
            raise CordisSecurityError("Offline Cordis requires macOS sandbox-exec or Node.js 25 or newer.")
        prefix = (str(sandbox), "-p", _MACOS_OFFLINE_PROFILE)
        probe = _probe((*prefix, node, *flags, "--eval", _NETWORK_PROBE), env, state)
        if probe != "cordis-network-denied":
            raise CordisSecurityError("The macOS sandbox did not enforce offline Cordis access.")
        isolation = "node-permissions-macos-network-denied"
    else:
        raise CordisSecurityError(
            "Offline Cordis requires Node.js 25 or newer on this platform; "
            "older Node.js versions do not restrict network access."
        )
    return CordisProcess((*prefix, node, *flags, str(runtime)), env, state, isolation)


def _grant_path(path: Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    # Node treats '*' as a wildcard and ignores everything after it. A literal
    # filename containing it must not silently broaden a grant.
    if "*" in str(resolved):
        raise CordisSecurityError("Cordis resource paths cannot contain permission wildcards.")
    return resolved


def _node_path(executable: str) -> str:
    found = shutil.which(str(Path(executable).expanduser()))
    if found is None:
        raise CordisSecurityError("Node.js was not found. Install Node.js and make it available on PATH.")
    return str(Path(found).resolve())


def _has_flag(help_text: str, flag: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", help_text) is not None


def _probe(command: tuple[str, ...], env: dict[str, str], cwd: Path) -> str:
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=_PROBE_TIMEOUT,
            check=False,
            env=env,
            cwd=cwd,
            close_fds=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CordisSecurityError("The Cordis runtime security check could not complete.") from exc
    if result.returncode != 0:
        # Do not echo arbitrary runtime output: it can include data from the
        # caller's filesystem or reveal details of a configured environment.
        raise CordisSecurityError("The Cordis runtime security check failed; no plugin was started.")
    return result.stdout.strip()
