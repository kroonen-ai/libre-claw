# Model orchestration

The included **Model orchestration** Cordis plugin coordinates workers using
different models and providers. One orchestrator plans and integrates the work;
each worker gets its own conversation, assigned scope, and explicit limits.

It adapts the task contracts and role prompts from the optional Pi budget profile
without installing Pi, copying its credentials, or hardcoding its model catalog.
Providers and model suggestions come from Libre Claw's existing discovery APIs.
Custom model IDs remain supported.

## Set up a team

1. Open **Dashboard → Settings → Plugins → Included → Model orchestration**.
2. Add it to the workspace and open its settings.
3. Select the orchestrator's provider and model, then configure workers. Blank
   worker routes inherit the orchestrator; a different provider needs its own
   model ID. A blank orchestrator uses the application's model at task creation.
4. Save the profile and choose **Enable team**. This grants model access through
   the Python host; provider keys never enter the JavaScript plugin.
5. **Check routes** verifies configuration, credentials, and available catalog
   information without generating a response. Missing metadata is reported;
   a successful check does not prove a model can complete a coding task.
6. Choose **Use for next task**, then send a task with the composer’s **Team**
   selector set to this profile.

Installation and enablement do not invoke models. Selecting the team for a task
authorizes the configured orchestration routes for that task. Starting a task
consumes the selected providers' normal usage.

## Roles and concurrent work

The default profile includes:

| Role | Work | Tools |
| --- | --- | --- |
| Scout | Locate relevant files, behavior, and tests. | Scoped source reads and search. |
| Builder | Implement bounded changes. | Scoped reads plus edits to declared paths. |
| Reviewer | Independently inspect a change and report issues. | Scoped source reads and search. |

Add or duplicate worker profiles to use different providers for the same role.
For example, select a planning model as orchestrator, a local model as a scout,
and separate cloud models as builder and reviewer. Keep a local worker's
concurrency at one if its server cannot serve simultaneous requests.

The default global limit is three concurrent workers and twelve launches per
turn; each default worker profile permits one active instance. Increase the
builder's limit to two, or add a second builder, for parallel implementation.
Every writing worker must declare non-overlapping `write_paths` inside its scope.
The host rejects overlapping ownership, routes outside the saved profile, and
dispatches that exceed capacity. The orchestrator waits before starting the next
batch; exceeding a limit does not silently create an unbounded queue.

Assignments should include the objective, scope, owned files, relevant evidence,
existing user changes, acceptance criteria, and checks. Workers cannot spawn
other workers or run unrestricted shell commands. The orchestrator runs the
integration checks after joining the workers and respecting their ownership.
Workers report what they actually inspected or changed; they cannot claim to
have run tests they did not execute.

An example task:

> Inspect the API and dashboard independently. Then have two builders update
> the backend and frontend in separate owned paths. Wait for both, run the
> integration checks, and ask the reviewer to inspect the final diff.

The existing task worker panel shows each worker's role, provider/model, state,
reported usage, and result. Cancellation and explicit recovery use the existing
worker controls. The orchestrator uses the plugin's `delegate`, `wait`, `status`,
and `cancel` tools through the authorized Cordis host bridge.

## Terminal and CLI

Enable the plugin in the same working directory used by your task:

```sh
libre-claw cordis install builtin:orchestration
# Configure provider/model choices in the dashboard, or supply a JSON config:
libre-claw cordis config orchestration --file team.json
libre-claw cordis enable orchestration --allow-model
libre-claw run --team orchestration "Inspect the API and UI concurrently and report a plan"
```

`team.json` contains the plugin's configuration object, which can be inspected
with `libre-claw cordis config orchestration`. Credentials belong in Libre Claw's
provider authentication settings, not this file. Use the global
`--working-directory` option when the CLI and daemon otherwise use different
projects.

In the TUI, use `/team orchestration` before starting a fresh task, `/team` to
show the selection, and `/agents` to inspect workers. `/new` starts a fresh task
with the selected team; `/team off` leaves team mode in a fresh session.
Use durable `/runs` and `/resume` for team work. Named-session `/save` and `/load`
cannot preserve the team's authorization snapshot and are blocked in team mode.

Noninteractive `run` retains the normal approval behavior. Editing tasks need
an interactive surface for approvals, or the existing explicit `--auto-approve`
option for the isolated run. Scoped write ownership and route limits remain
enforced with this option.

## Limits and privacy

Global and per-role concurrency, launches per turn, tool calls, worker duration,
working context, and response-output limits are enforced by the host. Advertised
model limits can further reduce the configured context/output ceilings. Provider
retries and automatic provider fallback are disabled for team work; automatic
memory-extraction model calls are also disabled. A failed route does not silently
switch subscription, account, or API-credit providers.

Usage reflects provider reports, not an account balance. These controls are not
a hard dollar cap, and cancellation cannot retract requests already sent.

The task stores its resolved team configuration and the installed plugin digest.
Use a new task to select changed routes; recovery never substitutes the current
global model. Revocation stops associated calls, and recovery requires the
original profile and grants. Invalid or missing snapshots fail closed, and
authorization failures preserve unstarted queued follow-ups.

Workers receive their assignment and scoped tool results. The parent's full
chat, memory history, and provider credentials are not automatically copied.
The orchestrator can include relevant context in an assignment, and worker
summaries return to it. If that orchestrator is a cloud model, local-worker summaries are
sent to it. Select local endpoints for every role to keep model requests local;
this setting is not an OS sandbox for the orchestrator's approved shell tools.

The JavaScript plugin stays offline and uses brokered host operations. It adds
no telemetry and needs no filesystem or network grants. Its source fingerprint,
workspace model grant, active task, and configured worker routes are rechecked
before use and during execution. File edits retain the parent's normal approval
rules. Other model-capable plugins and duplicate native delegation tools are
excluded from the selected team's tool list.

Native coding providers such as Codex run their own tools and are not exposed as
scoped team providers. Use an API-backed provider or local model endpoint for
these roles. Team profiles currently run ordinary chat tasks; autonomous goal
runs have their own judge and routing workflow.
