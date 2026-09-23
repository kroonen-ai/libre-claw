# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Prepare explicitly confined host subprocesses without inherited credentials."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from collections.abc import Sequence


def prepare_host_process(
    argv: list[str], cwd: Path, private: Path, *,
    read_paths: Sequence[str | Path], write_paths: Sequence[str | Path],
    allow_network: bool = False, runtime_paths: Sequence[str | Path] = (),
) -> tuple[list[str], dict[str, str]]:
    """Return a sandbox command; callers retain process ownership and approvals."""
    if (not isinstance(argv, list) or not 1 <= len(argv) <= 128
            or any(not isinstance(item, str) or "\0" in item for item in argv)
            or not argv[0] or sum(len(item.encode()) for item in argv) > 512 * 1024):
        raise ValueError("Invalid confined process command.")
    cwd, private = cwd.resolve(), private.resolve()
    if not private.is_dir() or private == Path(private.anchor):
        raise ValueError("A private subprocess directory is required.")
    reads = list(dict.fromkeys(str(Path(item).resolve()) for item in (*read_paths, *runtime_paths)))
    writes = list(dict.fromkeys(str(Path(item).resolve()) for item in write_paths))
    if sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file():
        def paths(items):
            return " ".join(f"(subpath {json.dumps(str(item))})" for item in items)

        profile = f'''(version 1)(deny default)(allow process*)(allow sysctl-read)(allow mach-lookup)(allow file-read-metadata)
        (allow file-read* {paths(['/bin','/sbin','/usr/bin','/usr/sbin','/usr/lib','/System','/Library/Apple',private,*reads])}
          (literal "/") (literal "/dev/null") (literal "/dev/urandom"))
        (allow file-write* {paths([private,*writes])} (literal "/dev/null"))'''
        if allow_network:
            profile += "(allow network*)"
        command = ["/usr/bin/sandbox-exec", "-p", profile, *argv]
    elif sys.platform == "linux" and (bwrap := shutil.which("bwrap")):
        command = [bwrap, "--die-with-parent", "--new-session", "--unshare-all"]
        if allow_network:
            command += ["--share-net"]
        for directory in ("/usr", "/bin", "/lib", "/lib64"):
            if Path(directory).exists():
                command += ["--ro-bind", directory, directory]
        command += ["--proc", "/proc", "--dev", "/dev", "--bind", str(private), str(private)]
        for directory in reads:
            command += ["--ro-bind", directory, directory]
        for directory in writes:
            command += ["--bind", directory, directory]
        command += ["--chdir", str(cwd), *argv]
    else:
        raise PermissionError("Confined host processes require macOS sandbox-exec or Linux bubblewrap.")
    env = {"PATH": os.defpath, "HOME": str(private), "TMPDIR": str(private),
           "XDG_CACHE_HOME": str(private / "cache"), "LANG": "C.UTF-8", "DO_NOT_TRACK": "1"}
    return command, env
