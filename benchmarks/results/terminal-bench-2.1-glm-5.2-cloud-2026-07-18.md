# Terminal-Bench 2.1 Smoke Result

This is a bounded integration and capability smoke test. It is not a full
Terminal-Bench leaderboard submission.

## Result

| Field | Value |
| --- | --- |
| Date | 2026-07-18 |
| Dataset | `terminal-bench/terminal-bench-2-1@latest` |
| Agent | Libre Claw Harbor adapter |
| Model | Ollama Cloud `glm-5.2:cloud` |
| Libre Claw commit | `3887f48` |
| Harbor | `0.19.0` |
| Attempts | 1 per task |
| Concurrency | 1 |
| Completed | 3 / 3 |
| Exceptions | 0 |
| Mean reward | `1.000` |
| Runtime | 7m 53s |

## Tasks

| Task | Difficulty | Category | Reward |
| --- | --- | --- | --- |
| `terminal-bench/regex-log` | Medium | Data processing | `1.0` |
| `terminal-bench/fix-git` | Easy | Software engineering | `1.0` |
| `terminal-bench/fix-code-vulnerability` | Hard | Security | `1.0` |

Harbor wrote the unmodified local result to
`jobs/libre-claw-glm52-tbench21-sample/result.json`. Its SHA-256 digest is:

```text
35483bffca56314963859a29ba0263abb20396f4308c9d4099eb74d8940f7ef8
```

Raw job artifacts are intentionally ignored by Git because they contain full
task instructions, model transcripts, and verifier logs. Re-run the sample with
the command in the parent benchmark README to produce a fresh local record.
