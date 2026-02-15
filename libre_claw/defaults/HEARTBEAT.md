# HEARTBEAT.md

Checklist for autonomous heartbeat ticks.
Add tasks here that should run periodically.

## Checks

- [ ] Example: Check service health
- [ ] Example: Sync workspace to git
- [ ] Example: Check for new messages

## Rules

- Run one check per heartbeat to minimize token burn
- Report if actionable, otherwise reply HEARTBEAT_OK
- Update timestamps in heartbeat-state.json after each check
