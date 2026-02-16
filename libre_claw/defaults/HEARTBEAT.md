# HEARTBEAT.md

Autonomous heartbeat contract for Libre Claw.

## Goal

Each heartbeat tick must produce one real, verifiable workspace improvement.

## Tick Contract (required every tick)

1. Pick exactly one action from the deterministic rotation list.
2. Execute exactly one concrete action using either:
   - an `apply_patch` / `diff` block, or
   - a `bash` block.
3. Verify the action with minimal proof (command output or file delta signal).
4. Append exactly one concise audit line to `HEARTBEAT-AUDIT.md`:
   - `- <timestamp> | action:<action-id> | result:<short-result>`
5. Update `HEARTBEAT-ROTATION.json`.
6. Return a structured JSON plan with `"done": true`.

Hard rules:
- Do not return `NO_REPLY` if an applicable action exists.
- Do not repeat the same `action-id` twice in a row unless every other action is inapplicable.
- Keep output short and operational.

## Deterministic Rotation

Action order:
1. `inbox-triage`
2. `infra-placeholder-tracking`
3. `summary-refresh`
4. `audit-compact`
5. `status-refresh`

State file:
- `HEARTBEAT-ROTATION.json`

Suggested shape:
```json
{
  "last_action_id": "summary-refresh",
  "last_tick": "2026-02-16T00:00:00Z",
  "enforced_retry": false
}
```
