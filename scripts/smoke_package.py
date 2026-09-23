# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Install the built wheel outside the checkout and exercise its offline engine."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    wheels = sorted((root / "dist").glob("libre_claw-*.whl"))
    if len(wheels) != 1:
        raise SystemExit("Expected exactly one Libre Claw wheel in dist; build a clean distribution first.")

    # The installed commands must not inherit a path back to an editable checkout.
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    }
    environment["PYTHONNOUSERSITE"] = "1"
    with tempfile.TemporaryDirectory(prefix="libre-claw-package-") as temporary:
        workspace = Path(temporary).resolve()
        installed = workspace / "venv"
        venv.EnvBuilder(with_pip=True).create(installed)
        executables = installed / ("Scripts" if os.name == "nt" else "bin")
        python = executables / ("python.exe" if os.name == "nt" else "python")

        def run(*arguments: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
            try:
                return subprocess.run(
                    list(arguments), cwd=workspace, env=environment, check=True,
                    text=True, capture_output=capture, timeout=300,
                )
            except subprocess.CalledProcessError as error:
                if capture:
                    print(error.stdout or "", end="")
                    print(error.stderr or "", end="", file=sys.stderr)
                raise

        run(str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", str(wheels[0]))
        run(
            str(python), "-I", "-c",
            "from pathlib import Path; import sys, libre_claw; "
            "assert Path(libre_claw.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), "
            "'Imported Libre Claw from outside the wheel environment'",
        )
        for name in ("libre-claw", "lc"):
            command = executables / (f"{name}.exe" if os.name == "nt" else name)
            result = run(str(command), "--help", capture=True)
            if "Usage:" not in result.stdout:
                raise SystemExit(f"Installed {name} did not expose its CLI help.")

        command = executables / ("libre-claw.exe" if os.name == "nt" else "libre-claw")
        result = run(str(command), "engine", "check", capture=True)
        status = json.loads(result.stdout)
        components = status.get("components", [])
        if status.get("state") != "running" or not components or any(
            component.get("state") != "ACTIVE" for component in components
        ):
            raise SystemExit("The installed wheel's Cordis services did not activate.")
        if status.get("privacy", {}).get("network") is not False:
            raise SystemExit("The installed wheel's core engine did not deny network access.")
        print(f"Installed wheel verified: both CLI commands and {len(components)} offline Cordis services.")


if __name__ == "__main__":
    main()
