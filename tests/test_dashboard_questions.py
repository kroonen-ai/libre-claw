# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import shutil
import subprocess

import pytest

from libre_claw.web.dashboard import dashboard_html


QUESTION_DOM = r"""
const assert = require('node:assert/strict');
class Element {
  constructor(tag = 'div') { this.tagName = tag; this.children = []; this.attrs = {}; this.listeners = {}; this.textContent = ''; this.disabled = false; this.checked = false; this.value = ''; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  setAttribute(name, value) { this.attrs[name] = value; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  querySelectorAll(selector) { const tags = selector.split(',').map(tag => tag.trim()); return this.children.flatMap(child => [...(tags.includes(child.tagName) ? [child] : []), ...child.querySelectorAll(selector)]); }
  set innerHTML(value) { throw new Error('Question text must never be parsed as HTML'); }
}
const container = new Element();
const $ = id => { assert.equal(id, 'questions'); return container; };
const document = {createElement: tag => new Element(tag)};
const state = {selectedRunId:'run /1', selectedRunState:'blocked'};
const text = node => [node.textContent, ...node.children.map(text)].filter(Boolean).join(' ');
const pending = [{request_id:'question /2', questions:[{id:'kind', question:'What should we build?', header:'Direction',
  options:[{label:'An app',description:'Build the interface'},{label:'An API',description:'Build the backend'}],multiSelect:false}]}];
const calls = [];
let hold = false, complete, error = '', refreshed = 0, refreshError = false;
const request = async (path, options) => {
  calls.push({path,options});
  if (hold) await new Promise(resolve => { complete = resolve; });
  if (error) throw new Error(error);
  return {ok:true};
};
const refreshRunDetail = async () => { refreshed++; if (refreshError) throw new Error('offline'); };
const current = () => [...questionForms.values()][0];
"""


def run_questions(tmp_path, assertions: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js required for dashboard question interaction tests")
    html = dashboard_html()
    source = html[html.index("    /* Structured user questions */"):html.index("    function renderPermissions(")]
    script = tmp_path / "questions.cjs"
    script.write_text(QUESTION_DOM + source + assertions)
    result = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_questions_require_explicit_selection_and_encode_the_answer_route(tmp_path):
    run_questions(tmp_path, r"""
(async () => {
  renderQuestions(pending);
  const record = current();
  assert.equal(record.fields[0].options[0].input.type, 'radio');
  assert.equal(record.fields[0].options.some(option => option.input.checked), false);
  await submitQuestion(record);
  assert.equal(calls.length, 0);
  assert.match(record.status.textContent, /Choose an option or write an answer/);
  record.fields[0].options[1].input.checked = true;
  await submitQuestion(record);
  assert.equal(calls[0].path, '/runs/run%20%2F1/questions/question%20%2F2');
  assert.deepEqual(JSON.parse(calls[0].options.body), {answers:[{id:'kind',selected:['An API']}]});
  assert.equal(record.button.disabled, true); assert.equal(record.button.textContent, 'Answer sent');
  assert.equal(refreshed, 1);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_question_multiselect_and_written_answers_are_delivered_without_html(tmp_path):
    run_questions(tmp_path, r"""
(async () => {
  pending[0].questions[0].multiSelect = true;
  pending[0].questions[0].question = '<img src=x onerror=alert(1)>';
  pending[0].questions.push({id:'details',question:'Describe it',options:[],multiSelect:false});
  renderQuestions(pending);
  const record = current();
  assert.match(text(container), /<img src=x onerror=alert\(1\)>/);
  assert.equal(record.fields[0].options[0].input.type, 'checkbox');
  record.fields[0].options.forEach(option => { option.input.checked = true; });
  await submitQuestion(record); assert.equal(calls.length, 0);
  record.fields[1].custom.value = '  Local first  ';
  await submitQuestion(record);
  assert.deepEqual(JSON.parse(calls[0].options.body), {answers:[{id:'kind',selected:['An app','An API']},{id:'details',selected:[],custom:'Local first'}]});
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_question_poll_preserves_drafts_and_duplicate_submission_is_blocked(tmp_path):
    run_questions(tmp_path, r"""
(async () => {
  renderQuestions(pending);
  const record = current(), form = container.children[0];
  record.fields[0].custom.value = 'My preference';
  renderQuestions(JSON.parse(JSON.stringify(pending)));
  assert.equal(container.children[0], form);
  assert.equal(current().fields[0].custom.value, 'My preference');
  hold = true; const sending = submitQuestion(record);
  await submitQuestion(record); assert.equal(calls.length, 1);
  assert.equal(record.button.disabled, true);
  assert.equal(record.fields[0].custom.disabled, true);
  complete(); await sending;
  renderQuestions(pending); await submitQuestion(record);
  assert.equal(calls.length, 1);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_failed_question_keeps_input_for_retry_but_successful_answer_never_resubmits(tmp_path):
    run_questions(tmp_path, r"""
(async () => {
  renderQuestions(pending); const record = current(); record.fields[0].custom.value = 'Answer';
  error = 'Request failed'; await submitQuestion(record);
  assert.match(record.status.textContent, /Could not send answer/);
  assert.equal(record.button.disabled, false); assert.equal(record.fields[0].custom.value, 'Answer');
  error = ''; refreshError = true; await submitQuestion(record);
  assert.match(record.status.textContent, /Answer sent. Refresh the task/);
  await submitQuestion(record); assert.equal(calls.length, 2);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_only_pending_questions_for_current_active_run_are_answerable(tmp_path):
    run_questions(tmp_path, r"""
(async () => {
  renderQuestions(pending); const record = current(); record.fields[0].custom.value = 'Answer';
  state.selectedRunId = 'another-run'; await submitQuestion(record); assert.equal(calls.length, 0);
  state.selectedRunId = record.runId; renderQuestions([]); await submitQuestion(record); assert.equal(calls.length, 0);
  renderQuestions(pending); const next = current(); next.fields[0].custom.value = 'Answer';
  state.selectedRunState = 'done'; await submitQuestion(next); assert.equal(calls.length, 0);
  renderQuestions(pending); assert.equal(container.children.length, 0);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")
