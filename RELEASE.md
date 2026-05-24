# Release Checklist

Use this checklist before publishing Libre Claw.

1. Update `src/libre_claw/__init__.py` and `pyproject.toml` to the same version.
2. Update `CHANGELOG.md` with the release date and summary.
3. Run:

   ```bash
   python3 -m pytest
   python3 -m compileall src tests
   git diff --check
   python3 -m build
   ```

4. Inspect the wheel contents and confirm `libre_claw/default.toml` is included.
5. Install the wheel into a clean environment and run:

   ```bash
   libre-claw --version
   libre-claw --help
   libre-claw config defaults
   libre-claw auth status
   ```

6. Smoke-test at least one real provider before tagging.
