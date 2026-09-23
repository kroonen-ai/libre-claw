# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import asyncio
import re
import shutil
import subprocess
from dataclasses import replace
from html.parser import HTMLParser

import pytest

from libre_claw.web.dashboard import dashboard_html


class DashboardElements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.scripts = []
        self._script_parts = None

    def handle_starttag(self, tag, attrs):
        self.ids.extend(value for name, value in attrs if name == "id")
        if tag == "script":
            self._script_parts = []

    def handle_data(self, data):
        if self._script_parts is not None:
            self._script_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._script_parts is not None:
            self.scripts.append("".join(self._script_parts))
            self._script_parts = None


def test_dashboard_control_ids_are_unique_and_all_static_bindings_exist():
    html = dashboard_html()
    elements = DashboardElements()
    elements.feed(html)
    assert len(elements.ids) == len(set(elements.ids))
    bindings = set(re.findall(r'\$\("([^"$]+)"\)', html))
    assert bindings <= set(elements.ids)
    assert {"tabChanges", "tabWorktrees", "tabPlan", "messageAction", "runWorktree", "runMode", "taskWorkers", "workerStatus"} <= set(elements.ids)


@pytest.mark.parametrize("script_tag", ["script", 'SCRIPT data-label="a > b"'])
def test_dashboard_javascript_is_valid(tmp_path, script_tag):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for dashboard JavaScript verification")
    elements = DashboardElements()
    elements.feed(dashboard_html().replace("<script>", f"<{script_tag}>").replace("</script>", "</SCRIPT>"))
    elements.close()
    assert elements.scripts and all(script.strip() for script in elements.scripts)
    source = tmp_path / "dashboard.js"
    source.write_text("\n".join(elements.scripts))
    completed = subprocess.run([node, "--check", str(source)], text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr


def test_diff_line_numbers_exclude_headers_and_follow_each_hunk(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for line-number verification")
    html = dashboard_html()
    source = html[html.index("    function parseDiffLines("):html.index("    function reviewActions(")]
    patch = "diff --git a/sample.py b/sample.py\n--- a/sample.py\n+++ b/sample.py\n@@ -4,2 +4,3 @@\n context\n-old\n+new\n+extra\n\\ No newline at end of file\n@@ -20 +21 @@\n-final\n+changed\n"
    source += "\nprocess.stdout.write(JSON.stringify(parseDiffLines(" + json.dumps(patch) + ")));"
    script = tmp_path / "lines.js"; script.write_text(source)
    result = subprocess.run([node, str(script)], check=True, text=True, capture_output=True)
    rows = json.loads(result.stdout)
    assert [(row["kind"], row["oldLine"], row["newLine"]) for row in rows] == [
        ("context", 4, 4), ("remove", 5, None), ("add", None, 5), ("add", None, 6),
        ("remove", 20, None), ("add", None, 21),
    ]


def run_dashboard_script(tmp_path, source):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for dashboard interaction verification")
    script = tmp_path / "workers.js"
    script.write_text(source)
    return subprocess.run([node, str(script)], check=True, text=True, capture_output=True)


def test_worker_recovery_requires_known_remaining_budgets(tmp_path):
    html = dashboard_html()
    source = html[html.index("    function workerControls("):html.index("    async function sendWorkerControl(")]
    source += """
const assert = require('node:assert/strict');
const worker = {status: 'interrupted', max_tool_calls: 8, tool_calls: 3, max_seconds: 100, elapsed_seconds: 35};
assert.deepEqual(workerControls(worker), {tools: 5, seconds: 65, active: false, pending: false, canResume: true});
for (const change of [{resume_pending: true}, {tool_calls: 8}, {elapsed_seconds: 100}, {max_seconds: null}, {status: 'done'}, {status: 'running'}]) {
  assert.equal(workerControls({...worker, ...change}).canResume, false);
}
assert.equal(workerControls({...worker, status: 'blocked'}).active, true);
assert.equal(workerControls({...worker, status: 'failed'}).canResume, true);
assert.equal(workerControls({...worker, status: 'cancelled'}).canResume, true);
"""
    run_dashboard_script(tmp_path, source)


WORKER_DOM = """
const assert = require('node:assert/strict');
const bindUI = (_service, target, event, handler, options) => target.addEventListener(event, handler, options);
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; this.listeners = {}; this.disabled = false; this.value = ''; this._text = ''; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(' '); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; this._text = ''; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  focus() { document.activeElement = this; }
  setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
}
const document = {activeElement: null, createElement: tag => new Element(tag)};
const elements = {taskWorkers: new Element('div'), workerStatus: new Element('p')};
const $ = id => elements[id];
const state = {selectedRunId: 'task-1'};
const workflow = {workerSignature: '', workers: [], workerDrafts: new Map(), workerPending: new Set()};
const notices = [], requests = [];
const setNotice = message => notices.push(message);
const descendants = element => [element, ...element.children.flatMap(descendants)];
const find = label => descendants(elements.taskWorkers).find(element => element.textContent === label);
"""


def test_worker_resume_cancel_requests_render_public_results_and_block_duplicates(tmp_path):
    html = dashboard_html()
    source = html[html.index("    function workerControls("):html.index("    async function loadPlan(")]
    source = WORKER_DOM + source + """
(async () => {
  let resolveRequest;
  const worker = {id: 'worker-1', task: 'Inspect parser', provider: 'test', model: 'scripted', scope: '/fixture/parser', read_only: true, status: 'interrupted', max_tool_calls: 8, tool_calls: 3, max_seconds: 100, elapsed_seconds: 35, output: 'Saved public result', session: {messages: ['PRIVATE_CHILD_REASONING']}};
  let nextWorkers = [worker];
  global.request = async (path, options) => { requests.push({path, ...JSON.parse(options.body)}); return new Promise(resolve => { resolveRequest = resolve; }); };
  global.loadPlan = async () => renderWorkers(nextWorkers, true);
  renderWorkers([worker]);
  assert.ok(elements.taskWorkers.textContent.includes('/fixture/parser'));
  assert.ok(elements.taskWorkers.textContent.includes('test / scripted'));
  assert.ok(elements.taskWorkers.textContent.includes('65 seconds'));
  assert.ok(elements.taskWorkers.textContent.includes('Saved public result'));
  assert.ok(!elements.taskWorkers.textContent.includes('PRIVATE_CHILD_REASONING'));
  const guidance = descendants(elements.taskWorkers).find(element => element.tag === 'textarea');
  guidance.value = 'Keep existing parser output'; guidance.listeners.input();
  const action = find('Resume worker').listeners.click();
  assert.equal(find('Requesting resume...').disabled, true);
  await sendWorkerControl('task-1', worker, 'agent_resume', 'duplicate');
  assert.equal(requests.length, 1);
  assert.deepEqual(requests[0], {path: '/runs/task-1/control', action: 'agent_resume', text: 'worker-1 Keep existing parser output'});
  nextWorkers = [{...worker, resume_pending: true}]; resolveRequest({text: 'Resume queued', subagents: nextWorkers}); await action;
  assert.equal(find('Resume queued').disabled, true);
  nextWorkers = [{...worker, resume_pending: false, status: 'running', output: 'New public progress'}]; renderWorkers(nextWorkers, true);
  assert.ok(!find('Resume worker')); assert.ok(find('Cancel worker'));
  const cancel = find('Cancel worker').listeners.click();
  assert.equal(find('Cancelling...').disabled, true);
  nextWorkers = [{...worker, resume_pending: false, status: 'cancelled'}]; resolveRequest({text: 'Cancelled', subagents: nextWorkers}); await cancel;
  assert.equal(requests[1].action, 'agent_cancel'); assert.equal(requests[1].text, 'worker-1');
  nextWorkers = [{...worker, status: 'done', resume_pending: false, output: 'Recovered parser verified'}]; renderWorkers(nextWorkers, true);
  assert.ok(elements.taskWorkers.textContent.includes('Recovered parser verified')); assert.ok(!find('Resume worker'));
  state.selectedRunId = 'other-task'; await sendWorkerControl('task-1', worker, 'agent_resume'); assert.equal(requests.length, 2);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    run_dashboard_script(tmp_path, source)


def test_plan_polling_uses_compact_controls_during_streaming():
    html = dashboard_html()
    assert "`/runs/${runId}/session?controls=1`" in html
    assert 'else if (state.selectedRunId && state.view === "plan") await loadPlan();' in html
    assert 'renderWorkers(Array.isArray(payload.subagents) ? payload.subagents : [], force);' in html


async def test_worker_controls_resume_saved_worker_through_real_daemon(tmp_path):
    from aiohttp import web
    from libre_claw.config import load_config
    from libre_claw.core.memory import MemoryStore
    from libre_claw.core.runs import RunStore
    from libre_claw.core.session import Session
    from libre_claw.core.subagents import SubagentState
    from libre_claw.core.tools import ToolContext, ToolRegistry
    from libre_claw.daemon import DaemonServer
    from libre_claw.providers.base import Done, LLMProvider, TextDelta
    from libre_claw.tools_builtin.filesystem import ReadFileTool
    from libre_claw.tools_builtin.subagents import SubagentSpawnTool

    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for dashboard HTTP interaction verification")

    class FixtureProvider(LLMProvider):
        async def complete(self, messages, **kwargs):
            await asyncio.sleep(.05)
            yield TextDelta("Recovered worker verified the parser.")
            yield Done()

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = load_config(working_directory=workspace)
    config = replace(config, general=replace(config.general, default_provider="openai", default_model="fixture-model"), memory=replace(config.memory, enabled=False, inject_relevant=False, archive_sessions=False), skills=replace(config.skills, enabled=False), automations=replace(config.automations, enabled=False, root=tmp_path / "automations"), telegram=replace(config.telegram, enabled=False), fallback=replace(config.fallback, enabled=False), petdex=replace(config.petdex, enabled=False))

    def registry_factory(config, memory):
        context = ToolContext(working_directory=workspace, default_provider="openai", default_model="fixture-model", subagent_provider_factory=lambda *args: FixtureProvider())
        return ToolRegistry([ReadFileTool(context), SubagentSpawnTool(context)])

    server = DaemonServer(config, run_store=RunStore(tmp_path / "runs"), provider_factory=lambda _: FixtureProvider(), registry_factory=registry_factory, start_telegram_bridge=False)
    server.memory_store = MemoryStore(tmp_path / "memory.sqlite")
    record = await server.run_store.create_run("Recover parser worker", kind="chat", provider="openai", model="fixture-model", working_directory=workspace, state="cancelled")
    worker = SubagentState(id="saved-parser", task="Inspect parser", scope=workspace, read_only=True, write_paths=(), provider="openai", model="fixture-model", max_tool_calls=8, max_seconds=60, tool_calls=2, elapsed_seconds=4, status="interrupted", output="Saved public progress")
    worker.session.add_user_message("PRIVATE_CHILD_REASONING")
    session = Session()
    session.add_user_message("PRIVATE_PARENT_HISTORY")
    session.subagents[worker.id] = worker.durable_snapshot()
    await server.run_store.save_session(record.run_id, session)
    runner = web.AppRunner(server.app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        html = dashboard_html()
        functions = html[html.index("    function workerControls("):html.index("    async function loadPlan(")]
        source = WORKER_DOM + functions + "\n" + f"state.selectedRunId = {json.dumps(record.run_id)}; const baseURL = 'http://127.0.0.1:{port}';\n" + """
const request = async (path, options = {}) => {
  if (options.body) requests.push({path, ...JSON.parse(options.body)});
  const response = await fetch(baseURL + path, {...options, headers: {'Content-Type': 'application/json'}});
  const payload = await response.json(); if (!response.ok) throw Error(payload.error || 'Request failed'); return payload;
};
let lastPayload;
async function loadPlan() {
  lastPayload = await request(`/runs/${state.selectedRunId}/session?controls=1`);
  assert.ok(!JSON.stringify(lastPayload).includes('PRIVATE_CHILD_REASONING'));
  assert.ok(!JSON.stringify(lastPayload).includes('PRIVATE_PARENT_HISTORY'));
  assert.ok(!('messages' in lastPayload.session)); renderWorkers(lastPayload.subagents, true);
}
(async () => {
  await loadPlan(); assert.ok(elements.taskWorkers.textContent.includes('fixture-model'));
  assert.ok(elements.taskWorkers.textContent.includes('Saved public progress'));
  const input = descendants(elements.taskWorkers).find(element => element.tag === 'textarea'); input.value = 'Continue with saved parser context'; input.listeners.input();
  await find('Resume worker').listeners.click();
  assert.equal(requests.length, 1); assert.equal(requests[0].action, 'agent_resume'); assert.equal(requests[0].text, 'saved-parser Continue with saved parser context');
  for (let attempt = 0; attempt < 80 && lastPayload.subagents[0].status !== 'done'; attempt++) {
    await new Promise(resolve => setTimeout(resolve, 25)); await loadPlan();
  }
  assert.equal(lastPayload.subagents[0].status, 'done');
  assert.ok(elements.taskWorkers.textContent.includes('Recovered worker verified the parser.'));
  assert.ok(!find('Resume worker')); assert.ok(!find('Cancel worker'));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        script = tmp_path / "real-daemon-worker-ui.js"
        script.write_text(source)
        process = await asyncio.create_subprocess_exec(node, str(script), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 15)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        assert process.returncode == 0, stderr.decode() + stdout.decode()
        restored = await server.run_store.load_session(record.run_id)
        assert restored.subagents[worker.id]["status"] == "done"
    finally:
        await runner.cleanup()
