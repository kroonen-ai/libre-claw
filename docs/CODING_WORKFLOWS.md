<!--
Copyright 2026 Kroonen AI (https://kroonen.ai)
SPDX-License-Identifier: Apache-2.0
-->

# Coding workflows

Libre Claw shares task state and agent tools across the TUI, daemon dashboard,
and Telegram. Git worktree and review controls are available in the TUI,
dashboard, and `libre-claw workflow` CLI. The
[implementation checklist](CODING_WORKFLOW_CHECKLIST.md) tracks verification.

## Continue a task

In the TUI or Telegram:

```text
/runs
/resume <run-id>
```

Resume restores the conversation, provider/model, workspace, plan, and task
checkpoint. An active daemon task reconnects to its existing stream. For a
finished or interrupted task, send the next instruction after resuming. In the
dashboard, select the existing task and send a reply. `/new` in Telegram starts
a fresh conversation.

A run's `session.json` stores current messages, archived originals, structured
requirements and decisions, plan steps, and pending steering. Checkpoints are
saved at tool and turn boundaries. An interrupted tool call is marked as having
an unknown outcome so the agent can inspect the workspace before retrying it.
Missing workspaces produce an error instead of silently selecting another one.

Compaction shortens the active prompt while retaining original messages. The
agent can use `task_checkpoint` to record requirements, decisions, changed files,
verification, and outstanding work. Its `task_history` tool searches or pages
archived and current messages, with message indexes and continuation offsets.
Images return metadata; their encoded data is omitted from history excerpts.
These are agent tools, so ask for an earlier requirement or decision in normal
language rather than typing a slash command for them.

## Project instructions

Instructions load automatically in this order:

1. `~/.libre-claw/AGENTS.md`.
2. The repository root's `AGENTS.md`.
3. Applicable `AGENTS.md` files down to the working directory or explicit file
   paths used by tools.

`AGENT.md` is a compatibility fallback where the same directory has no
`AGENTS.md`. A deeper file overrides broader guidance within its own directory
scope. Direct user instructions and tool permissions still take precedence.
Discovery stops at the repository root; outside Git, it starts at the working
directory. Each injected instruction includes its source and scope.

Reads are bounded and skip symlinks and nonregular instruction files. If a write
first exposes new or changed scoped guidance, the agent receives that guidance
and must reconsider the write. For shell operations in nested directories, read
the relevant files or instructions first; arbitrary paths embedded in shell
commands are not parsed as instruction scopes.

## Plan, steer, and queue

The TUI and Telegram share these commands:

```text
/plan on
/plan set inspect the failure; fix the cause; verify the result
/plan add update the documentation
/plan edit 2 fix the parser without changing its public API
/plan running 1
/plan done 1
/plan pending 2
/plan remove 4
/plan show
/plan clear
/plan off
/steer preserve the existing command names
/queue run the integration tests when this turn finishes
```

Plan mode permits read-only tools and read-only delegation. Switching it off
restores the usual permission policy. The Codex CLI provider uses a read-only
sandbox during plan mode. A provider with an unsupported native tool runtime
cannot run in plan mode.

Steering updates the active turn at the next safe model/tool boundary. Pending
actions are held for reconsideration when new guidance arrives. An action already
in progress may finish. Queuing creates a separate follow-up on the same task;
completed turns drain queued work in order. Cancellation or failure leaves
unstarted follow-ups saved for later continuation. The daemon can also start an
explicitly queued follow-up on an idle task.

The dashboard's **Plan** view edits steps and switches between **Build** and
**Plan only**. Its composer offers **Steer active task** and **Queue follow-up**;
the Plan view also shows queued messages.

## Worktrees

Worktrees are opt-in isolated checkouts associated with durable tasks. In the TUI:

```text
/worktree create HEAD --branch task/parser-fix
/worktree list
/worktree use <worktree-id>
/worktree setup <worktree-id> python -m unittest discover -s tests
/worktree preview <worktree-id>
/worktree apply <worktree-id> --confirm
/worktree remove <worktree-id>
```

`create` defaults to `HEAD`; supply a branch, tag, or commit as its starting
revision. `--branch` creates the named branch; without it the worktree is
detached. Add `--include-changes` to copy current staged, unstaged, and nonignored
untracked content. The TUI switches the associated task to the new workspace.
Setup runs only the command you explicitly supply, through shell sandbox checks.

Preview shows changes made since the worktree's initial content, or since its
last transfer. Applying requires the previewed source and destination revisions
still to match. The source branch must remain at the worktree's starting commit;
conflicting changes or a moved branch require reconciliation first. Transfer
applies file changes to the original checkout and preserves its index. It does
not merge commits or create a commit.

Workspaces must be idle for Git mutations and setup. Removal refuses active
tasks, modified or untracked/ignored files, and commits absent from the source
branch. Preserve the work and clean the checkout before removing it.

The dashboard's **Worktrees** view provides creation, task selection, explicit
setup approval, transfer preview/application, and removal. Managed checkouts live
beside the run store under `worktrees/`; `LIBRE_CLAW_WORKTREE_ROOT` overrides that
location and must point outside the source repository.

CLI equivalents use `libre-claw workflow`:

```bash
libre-claw workflow --repository /path/to/repo worktree create HEAD --branch task/parser-fix
libre-claw workflow worktree list
libre-claw workflow worktree preview <worktree-id>
libre-claw workflow worktree apply <worktree-id> --confirm
libre-claw workflow worktree setup <worktree-id> --command 'python -m unittest discover -s tests'
```

Use `workflow --run-id <run-id>` to select a saved task. Other workflow options
are `--repository`, `--runs-root`, and `--worktrees-root`. `worktree use` selects
the workspace for that workflow/task; it does not change your shell directory.

## Review changes

Open a scope before acting on it:

```text
/review unstaged
/review staged
/review branch main
/review last-turn
/review stage src/parser.py [hunk-id]
/review unstage src/parser.py [hunk-id]
/review revert src/parser.py [hunk-id]
/review comment src/parser.py:42 check the empty-input case
/review comment src/parser.py:40 --left preserve this behavior
/review analyze
```

| Scope | Comparison |
| --- | --- |
| Unstaged | Index to current files, including nonignored untracked content. |
| Staged | `HEAD` to the index. |
| Branch | Merge base with the supplied reference to current files. |
| Last turn | Recorded tree before the turn to current files. |

Stage and revert use an unstaged review; unstage uses a staged review. Omit the
optional hunk ID for a whole-file action. Use the displayed hunk ID or its unique
prefix for one hunk. Binary, added, deleted, and mode-changing files require
whole-file actions. Revert discards the selected unstaged change. A stale diff
is rejected and must be refreshed.

Comments attach to a line present in the reviewed diff; the default side is the
new text, and `--left` selects the original text. Comments remain local and are
included in analysis of that revision. `analyze` starts a separate read-only
reviewer using the configured provider/model, with up to 32 tool calls and a
180-second timeout. It reports actionable findings and verification limits; it
does not edit the coding task or publish comments.

The existing `/review` or `Ctrl+E` edit drawer remains available. The dashboard's
**Changes** view offers scopes, file/hunk actions, line comments, and
**Independent review**. CLI equivalents include:

```bash
libre-claw workflow review unstaged
libre-claw workflow review stage src/parser.py
libre-claw workflow review branch main
libre-claw workflow review comment src/parser.py:42 'Check the empty-input case'
libre-claw workflow review analyze
```

## Delegation

Ask the agent to delegate a bounded task, such as investigating a failing test
while it works on a separate module. The native tools are `subagent_spawn`,
`subagent_list`, `subagent_wait`, and `subagent_cancel`. Inspect or cancel workers
from the TUI or Telegram with `/agents` and `/agents cancel <worker-id>`.

A spawn requires a task and an existing directory `scope` inside the parent
workspace. Workers get a separate conversation and default to read-only tools.
`provider` and `model` optionally select another configured provider and arbitrary
model ID. Writing workers must set `read_only=false` and declare `write_paths`
relative to their scope. Workers and the parent cannot edit overlapping owned
paths while those workers are active. The parent must wait for writing workers
to finish before using tools with unbounded write effects.

Default limits are three concurrent workers and twelve spawns per parent turn.
Each worker defaults to 20 tool calls and 180 seconds; a spawn can request up to
100 calls and 900 seconds, still bounded by the parent's call ceiling and deadline.
Workers cannot delegate recursively. Permission decisions are inherited without
turning a spawn into approval of its edits; new approvals reach the parent UI.
Results expose status, scope, tool count, output, errors, and reported usage.

Writing workers use scoped file tools. Arbitrary shell/browser mutations and
providers that execute their own tools, including the Codex CLI provider, are
not supported as scoped workers. Choose a provider using Libre Claw's client-side
tools for delegation. Unfinished workers are cancelled when the parent turn ends;
an in-flight file write finishes before its ownership is released. Worker result
events are durable, but worker processes are not resumed after a restart.

## Model discovery and capability overrides

`/models <provider> [search] --refresh` refreshes the shared catalog; manual IDs
remain available when discovery fails. The selected model's metadata is refreshed
before its first request when the provider supports discovery. Published tool,
image, reasoning, context/output limits, and pricing appear in the catalog and
dashboard. Missing values remain unknown.

Requests omit tools or temperature where explicitly unsupported, reject images
for known nonvision models, respect published token limits, and validate known
reasoning choices. Unknown capabilities preserve the existing provider behavior.
Override missing or incorrect metadata in your user config, using an actual ID
from your endpoint:

```toml
[providers.openrouter.model_capabilities."vendor/model-id"]
supports_tools = true
supports_vision = false
supports_reasoning = true
supports_temperature = false
supported_reasoning_efforts = ["low", "medium", "high"]
context_window_tokens = 64000
max_completion_tokens = 8000
input_cost_per_token = 0.000001
output_cost_per_token = 0.000003
```

These are example values, not model recommendations or published prices. Set an
individual override to `"unknown"` to clear that metadata. Provider setting
`auto_context_window = false` keeps your configured agent context budget; published
output limits still apply. Prices are per token and are shown only when published
or explicitly configured.

## Local API and regression checks

The dashboard uses the daemon's local API. Task state is available
at `GET /runs/<id>/session`; send `{"message":"continue with the tests"}` to
`POST /runs/<id>/messages` for an idle task. `POST /runs/<id>/control` accepts
`{"action":"steer","text":"preserve the public API"}`. Other actions are
`queue`, `plan` (text uses the `/plan` arguments), `agents`, and `agent_cancel`.

Git review endpoints are `/workspace/review`, `/workspace/review/action`,
`/workspace/review/comments`, and `/workspace/review/analyze`. Select a task with
`run_id`, and a diff with `scope` and optional `base_ref`. Mutations require the
`revision` returned by the review. Worktree endpoints are `/worktrees` and
`/worktrees/<id>/{setup,transfer}`; transfer requires both previewed `revision`
and `target_revision` values. Use the dashboard or CLI for the complete workflow.

See [the evaluation guide](../benchmarks/README.md#fixed-coding-workflow-regression-suite)
for the offline CI gate and opt-in live provider runs. The recorded
[two-task smoke result](../benchmarks/results/coding-workflow-smoke-2026-09-19.json)
checks basic coding and instruction following. Its scripted recovery check is
reported separately; it is not a comparative provider benchmark.
