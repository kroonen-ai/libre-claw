<!--
Copyright 2026 Kroonen AI (https://kroonen.ai)
SPDX-License-Identifier: Apache-2.0
-->

# Libre Claw Roadmap

Libre Claw is a terminal-native agent harness focused on durable local tasks
and provider choice. The current release is 0.1.0; the sections below distinguish
its existing features from additions available on the main branch.

## Existing foundation

- Durable runs with append-only events, artifacts, and timeline replay.
- A daemon API, local dashboard, Telegram bridge, and scheduled automations.
- User/project skills, `SOUL.md` customization, and MCP stdio tools.
- Browser tools with persistent profiles and screenshots.
- Provider usage analytics, setup commands, and installation helpers.

## Implemented on the main branch

- Shared live model discovery, manual model IDs, capability metadata, and overrides.
- Resume with conversation, model, workspace, plans, structured checkpoints, and
  retrievable original history after compaction.
- Hierarchical project instructions with source attribution and scoped refresh.
- Opt-in task worktrees, explicit setup, reviewed transfers, and protected cleanup.
- Git review scopes, line comments, file/hunk actions, and independent read-only review.
- Plan-only execution, editable steps, queued follow-ups, and live steering.
- Scoped subagents with separate sessions, inherited permissions, bounded work,
  cancellation, and coordinated file ownership.
- Fixed coding and review fixtures, offline harness checks in CI, and a bounded
  two-provider comparison with immutable source/fixture hashes and durable
  recovery validation.
- Explicit recovery of saved subagents with retained budgets and ownership checks.

Command syntax and current limits are in the [coding workflow guide](docs/CODING_WORKFLOWS.md).
The [implementation checklist](docs/CODING_WORKFLOW_CHECKLIST.md) records final
integration verification. These additions have not been published as a new release.

## Next priorities

- Run broader repeated coding evaluations across configured providers; the small
  smoke suite does not establish comparative model quality.
- Extend delegated execution beyond scoped file tools while preserving ownership,
  permission, and exact budget guarantees for native provider runtimes.
- Harden daemon authentication for remote deployments.
- Add packaged releases and signed binaries.
- Expand MCP interoperability tests with common local servers.
- Improve browser previews and provider usage exports.

## Design principles

- Read before editing and preserve unrelated changes.
- Enforce permissions and workspace boundaries at execution time.
- Keep task state durable and incomplete work retrievable.
- Discover model capabilities where possible; leave missing metadata unknown.
- Keep credentials out of project configuration.
