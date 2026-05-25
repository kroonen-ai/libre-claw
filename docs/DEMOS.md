# Demo Scripts

These scripts are the canonical flows for demo videos and GIFs. They keep the
product story consistent while the UI continues to evolve.

## Five-Minute Build

1. Open a fresh terminal.
2. Run the one-command installer from `docs/GETTING_STARTED.md`.
3. Launch `libre-claw`.
4. Run `/setup status`.
5. Run `/setup provider openrouter`.
6. Run `/setup key openrouter`.
7. Run `/model openrouter:qwen/qwen3.7-max --global`.
8. Ask: `Explain this repo and run the tests.`
9. Show `/artifacts verify` and `/usage openrouter`.

## Durable Run Review

1. Ask Libre Claw to make a small code change.
2. Approve one file edit.
3. Run `/runs`.
4. Run `/run <id>`.
5. Run `/artifacts plan`, `/artifacts diff`, and `/changes <id>`.

## Autonomous Goal

1. Run `/goal Find a small improvement, implement it, and verify it.`
2. Show the judge messages between turns.
3. Open `/artifacts summary` at completion.

## Browser Tooling

1. Install browser extras: `python -m pip install -e ".[browser]"`.
2. Run `python -m playwright install chromium`.
3. Ask Libre Claw to navigate a documentation page and capture a screenshot.
4. Open `/artifacts browser`.

## Recording Notes

- Keep terminal width at 160 columns when recording.
- Start with the file explorer hidden, which is the default.
- Avoid showing real API keys. Use `/setup key ...`, paste from a password
  manager, and cut that moment from the final video.
