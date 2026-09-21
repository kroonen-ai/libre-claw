<!--
Copyright 2026 Kroonen AI (https://kroonen.ai)
SPDX-License-Identifier: Apache-2.0
-->

# Coding workflow implementation checklist

This checklist records the item-by-item implementation audit on 2026-09-21.
Each item names its evidence and compatibility limits. A checked feature does
not imply that every provider exposes the same tools or metadata.

| Item | Audit result | Evidence |
| --- | --- | --- |
| [x] Reliable resume and long-task memory | Parent and worker conversations, checkpoints, provider/model, workspace, plans, budgets, and pending work survive serialization. Interrupted tool outcomes remain unknown until inspected. Fixed Telegram runtime attribution and TUI finalization/queued-turn handoffs. | [Task recovery](../tests/test_task_recovery.py), [worker recovery](../tests/test_subagent_recovery.py), [surface recovery](../tests/test_surface_recovery.py) |
| [x] Automatic project instructions | Global, repository, and nested `AGENTS.md` files load with source/scope attribution and precedence; legacy `AGENT.md` remains a fallback. Guidance refreshes at model/tool boundaries. Existing implementation verified. | [Instruction tests](../tests/test_instructions.py), [agent tests](../tests/test_agent.py) |
| [x] Managed task worktrees | Branch/ref selection, isolated edits, optional copied changes, explicit setup, safe cleanup, and reviewed transfers are implemented. Fixed failed setup commands continuing, and competing continuations/queued tasks entering an occupied checkout. | [Worktree tests](../tests/test_worktrees.py), [queue ownership tests](../tests/test_task_queue.py), [API tests](../tests/test_workflow_api.py) |
| [x] Review workspace | Branch/staged/unstaged/last-turn views, exact line comments, file/hunk actions, and independent review are implemented. Added new/deleted text hunk actions and protected mode/type changes. Reviewers inspect immutable snapshot content, including omitted patches. | [Git review](../tests/test_git_review.py), [reviewer snapshots](../tests/test_reviewer.py), [workflow commands](../tests/test_workflow_commands.py) |
| [x] Planning and live steering | Read-only planning, editable steps, durable steering, and queued follow-ups work across clients. Fixed idle and late-arriving local queues, cancellation during finalization, and FIFO return of claimed but unstarted work. Worker resumes honor the parent's plan mode. | [Surface controls](../tests/test_surface_recovery.py), [queue tests](../tests/test_task_queue.py), [worker controls](../tests/test_worker_controls.py) |
| [x] Native subagents | Libre Claw's worker tools provide separate sessions, scope/ownership, model choice, budgets, visible results, cancellation, and explicit recovery. Saved workers resume deterministically through `/agents resume` or `subagent_resume`, with fresh execution checks and retained budgets. | [Subagents](../tests/test_subagents.py), [durable recovery](../tests/test_subagent_recovery.py), [worker API/UI controls](../tests/test_worker_controls.py) |
| [x] Capability-aware providers | Provider metadata and per-model overrides control tool/image/reasoning requests and token limits; missing capabilities and prices remain unknown. Fixed mixed-cost aggregation and displays so unreported costs cannot appear as a complete total or zero. | [Capabilities](../tests/test_capabilities.py), [catalog](../tests/test_model_catalog.py), [usage accounting](../tests/test_usage.py) |
| [x] Regression evaluations | Added a seeded review-quality task with benign-change controls, real snapshot recovery validation, and a bounded multi-provider comparison runner. One frozen source snapshot passed all six coding/review trials across two providers, plus both recovery checks. | [Evaluation tests](../tests/test_workflow_evals.py), [comparison tests](../tests/test_workflow_comparison.py), [recorded comparison](../benchmarks/results/coding-workflow-comparison-2026-09-21.json) |
| [ ] Integration and verification | 770 automated tests pass, including DOM interaction tests and actual daemon HTTP worker recovery. Compilation, whitespace, source-distribution, and wheel checks pass. Fresh visual inspection remains pending because no browser is connected; this item is intentionally not marked fully complete. | [Dashboard tests](../tests/test_workflow_dashboard.py), [workflow API](../tests/test_workflow_api.py), [command guide](CODING_WORKFLOWS.md) |

## Supported boundaries

- Scoped workers use Libre Claw's client-side tools. Providers with their own
  native execution runtime, including the Codex CLI bridge, remain rejected as
  workers because that bridge cannot enforce the declared scope and exact tool
  budget. They remain supported for primary tasks. This compatibility boundary
  is not advertised as implemented native-runtime delegation.
- Worker recovery requires an explicit resume. It starts from saved conversation
  and execution state; it does not resurrect an old OS process or automatically
  repeat actions with an unknown outcome. Exhausted budgets require new work.
- Binary, empty-file, and mode-only changes have no text hunks and use whole-file
  actions. Added, deleted, modified, executable, and type-changing text files
  support the applicable text hunk operations.
- Project instructions have bounded input sizes. Arbitrary file paths embedded
  inside shell strings are not inferred as instruction scopes; explicit file
  tool paths load their applicable nested guidance.
- A small provider comparison checks these fixtures and this runtime. It does
  not establish general model rankings; absent usage and cost remain null.

The public website is maintained in a separate repository.

## Audit evidence

- The stable comparison uses identical source and fixture hashes for Codex and
  OpenRouter; `source_changed_during_run` is false. Each provider passed the
  pagination, catalog/instruction, and seeded review tasks. Recovery checks are
  reported separately from model performance.
- Both seeded reviews found the two expected regressions without reporting the
  benign control as a bug. This is fixture coverage, not a general review-quality
  claim.
- Provider-reported OpenRouter cost was $0.009624435; Codex cost was unavailable
  and remains null. Token and elapsed-time measurements are in the report.
- Browser-free UI checks exercised the real HTTP controller and DOM handlers.
  They do not substitute for the pending fresh visual inspection.
