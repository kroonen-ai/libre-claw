# INFRA.md - Infrastructure Source of Truth

Purpose: single-file operational reference for machines, services, networking, backups, and recovery.
Rule: keep this factual, current, and secret-free.

---

## Document Control

| Field | Value |
| --- | --- |
| Owner | (name) |
| Maintainers | (names) |
| Status | Active |
| Version | 1.0 |
| Last Reviewed | (YYYY-MM-DD) |
| Review Cadence | Monthly (or after major infra changes) |

### Conventions

- Dates: `YYYY-MM-DD`
- Criticality: `Low | Medium | High | Critical`
- Service tier: `Dev | Internal | External | Production`
- Monitoring: `None | Basic | Full`
- Backup status: `None | Configured | Tested | Failed`
- Secrets: never store here; reference secret paths only

---

## 1) Machines

| Hostname | Role | Environment | Local IP | Public IP | OS | Location | Owner | Criticality | Mgmt Access | Config Mgmt | Last Verified | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| (hostname) | (role) | (env) | (local ip) | (public ip or N/A) | (os) | (location) | (owner) | (level) | (ssh/vpn) | (manual/ansible/etc) | (YYYY-MM-DD) | (notes) |

---

## 2) Services

| Service | Tier | Hostname | URL | Port(s) | Purpose | Owner | Auth Method | Dependencies | Monitoring | Runbook | Last Verified |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| (service-name) | (tier) | (host) | (url or N/A) | (ports) | (purpose) | (owner) | (auth) | (deps) | (status) | (link/path) | (YYYY-MM-DD) |

---

## 3) Networking

| Segment | CIDR | VLAN ID | Gateway | DHCP Source | DNS Resolver | Purpose | Trust Level | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LAN | (cidr) | (vlan) | (gw) | (source) | (resolver) | trusted devices | High | (notes) |
| SERVERS | (cidr) | (vlan) | (gw) | (source) | (resolver) | self-hosted services | Medium | (notes) |
| IOT | (cidr) | (vlan) | (gw) | (source) | (resolver) | isolated devices | Low | (notes) |
| GUEST | (cidr) | (vlan) | (gw) | (source) | (resolver) | guest wifi | Untrusted | internet-only |

---

## 4) Backups and Disaster Recovery

| Asset | Source Path | Destination | Schedule | Retention | Encryption | Backup Status | Last Success | Restore Tested | Last Restore Test | Owner | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| (asset) | (path) | (target) | (schedule) | (retention) | (yes/no/type) | (status) | (YYYY-MM-DD) | (yes/no) | (YYYY-MM-DD) | (owner) | (notes) |

---

## 5) Hardware Inventory

| Device | Role | CPU | GPU | RAM | Storage | OS | Network | Location | Lifecycle State | Last Verified | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| (device) | (role) | (cpu) | (gpu) | (ram) | (storage) | (os) | (network) | (location) | Active | (YYYY-MM-DD) | (notes) |

---

## 6) Access and Secret References

Do not store raw secrets here.
Reference only:
- SSH keys: `.secrets/id_ed25519`
- API tokens: `.secrets/<service>.token`
- VPN configs: `.secrets/wireguard/`
- Password vault location: (tool + location)

---

## 7) Change Log

| Date | Change | By | Risk | Verified |
| --- | --- | --- | --- | --- |
| (YYYY-MM-DD) | (change summary) | (who) | Low/Medium/High | Yes/No |
