<!--
Copyright 2026 Kroonen AI (https://kroonen.ai)
SPDX-License-Identifier: Apache-2.0
-->

# Coding workflow implementation checklist

This tracks the requested improvements to Libre Claw's coding workflow.

- [x] Reliable resume and long-task memory: restore conversation, provider,
  model, workspace, plan, and unfinished work; checkpoint active runs; preserve
  requirements and decisions through compaction and retain retrievable history.
- [x] Automatic project instructions: load global, repository, and scoped
  `AGENTS.md` guidance with source attribution and precedence; refresh it as
  the agent works in different directories.
- [x] Managed task worktrees: choose a starting revision, isolate edits,
  optionally run approved setup commands, list and clean up worktrees safely,
  and bring reviewed changes back without overwriting existing work.
- [x] Review workspace: inspect branch, staged, unstaged, and last-turn diffs;
  add line comments; stage, unstage, or revert files and hunks with stale-diff
  protection; run an independent read-only code review.
- [x] Planning and live steering: plan-only execution, editable steps, queued
  follow-ups, and durable steering delivered to active runs at safe boundaries.
- [x] Native subagents: separate contexts, scoped work, model selection,
  budgets, cancellation, visible results, inherited permissions, and coordinated
  writes; execute only independent read-only tools concurrently.
- [x] Capability-aware providers: discover tool, image, reasoning, token-limit,
  and pricing metadata; expose unknown values and explicit overrides; adapt
  actual requests to known capabilities.
- [x] Regression evaluations: fixed coding tasks covering edits, project
  instructions, interruption/recovery, and review; report completion, time,
  usage/cost, and reproducible provider comparisons through the existing harness.
- [x] Integration and verification: document the commands, test the shared
  core and TUI/daemon/Telegram/dashboard flows, visually inspect dashboard changes,
  and run the complete test suite and package checks.

The public website is maintained in a separate repository.

## Verification

- Complete Python suite: 671 tests passed, including concurrent worktree
  launch and durable task-ownership regressions.
- Python compilation and whitespace checks passed.
- Isolated source distribution and wheel builds passed.
- Dashboard browser checks passed on desktop and at a 390-pixel mobile width.
- Live coding smoke: two of two tasks passed; interrupted-session recovery also
  passed in the separate structural harness check. This is a small smoke suite,
  not a comparative provider benchmark.
- [Command and behavior guide](CODING_WORKFLOWS.md).
- [Recorded smoke results](../benchmarks/results/coding-workflow-smoke-2026-09-19.json).

Scoped writing subagents support file tools. Providers that execute their own
tools, including the Codex CLI bridge, remain unavailable as scoped workers;
they continue to work for primary tasks, including read-only planning/review.
