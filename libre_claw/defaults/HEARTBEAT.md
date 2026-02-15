# HEARTBEAT.md

Checklist for autonomous heartbeat ticks.
Add tasks here that should run periodically.

## Checks

- [ ] Example: Check service health
- [ ] Example: Sync workspace to git
- [ ] Example: Check for new messages

## Rules

- Continue until no more work is found or the configured proactive limit is reached.
- Report actions through structured JSON plan; when complete and verified, set done: true.
- "Alive" means proactive and autonomous loop execution: plan → execute → verify → continue until done, then report NO further action.
- If useful, update memory by including a MEMORY_UPDATE: entry in your output
- Update timestamps in heartbeat-state.json after each check
