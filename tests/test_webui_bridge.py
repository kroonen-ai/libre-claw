# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from libre_claw.bridge import Bridge, Callback, CallbackProvider, MAX_CALLBACK_BYTES, MAX_FRAME


class Harness:
    def __init__(self):
        self.frames = asyncio.Queue()
        self.bridge = Bridge(self.frames.put)

    async def init(self, tmp_path, *, mode='host', persistence=False):
        result = await self.bridge.dispatch('initialize', {
            'protocolVersion': 1, 'mode': mode, 'workspacePath': str(tmp_path),
            'tools': mode == 'host', 'persistence': persistence,
            'sessionStorePath': str(tmp_path / 'sessions'), 'model': 'test-model',
        })
        assert result['engine'] == 'libre-claw'
        assert len(result['services']) == 6
        assert all(row['state'] == 'ready' for row in result['services'])

    async def next(self, kind):
        while True:
            frame = await asyncio.wait_for(self.frames.get(), 15)
            if kind in frame:
                return frame
            if frame.get('event') == kind or frame.get('chunk', {}).get('type') == kind:
                return frame

    async def respond(self, callback, events, result=None):
        key = callback['callbackId']
        for event in events:
            self.bridge.receive_callback({'callbackId': key, 'event': event})
        self.bridge.receive_callback({'callbackId': key, 'done': True, **({'result': result} if result is not None else {})})


@pytest.mark.asyncio
async def test_host_real_agent_tool_approval_history_and_restart(tmp_path):
    h = Harness()
    await h.init(tmp_path, persistence=True)
    try:
        session = await h.bridge.dispatch('session.create', {'cwd': str(tmp_path), 'permissionMode': 'workspace-write'})
        sid = session['id']
        await h.bridge.dispatch('turn.start', {'sessionId': sid, 'turnId': 'turn', 'text': 'Write a note', 'model': 'test-model'})
        call = await h.next('callbackId')
        assert call['method'] == 'provider.stream'
        assert call['params']['messages'][-1]['content'] == 'Write a note'
        assert call['params']['model'] == 'test-model'
        assert {t['name'] for t in call['params']['tools']} >= {'read_file', 'write_file'}
        await h.respond(call, [
            {'type': 'reasoning', 'text': 'Use the approved workspace.'},
            {'type': 'tool-call', 'toolCall': {'id': 'write-1', 'name': 'write_file', 'arguments': '{"path":"note.txt",', 'providerMetadata': {'signature': 'tool-local-signature'}}},
            {'type': 'tool-call', 'toolCall': {'id': 'write-1', 'name': '', 'arguments': '"content":"hello"}'}},
            {'type': 'done', 'reason': 'tool-calls', 'providerMetadata': {'nativeLibreClaw': {'instanceId': 'fixture-generation', 'replayState': {'response': {'signed': True}}}}},
        ])
        approval = (await h.next('approval-request'))['chunk']['approval']
        assert not (tmp_path / 'note.txt').exists()
        assert await h.bridge.dispatch('session.approve', {'sessionId': sid, 'approvalId': approval['id'], 'outcome': 'allowed-once'})
        call = await h.next('callbackId')
        assert (tmp_path / 'note.txt').read_text() == 'hello'
        assert any(m.get('toolCallId') == 'write-1' for m in call['params']['messages'])
        assert any(m.get('thinking') == 'Use the approved workspace.' for m in call['params']['messages'])
        assistant = next(m for m in call['params']['messages'] if m.get('toolCalls'))
        assert assistant['providerMetadata']['nativeLibreClaw']['instanceId'] == 'fixture-generation'
        assert assistant['toolCalls'][0]['providerMetadata'] == {'signature': 'tool-local-signature'}
        await h.respond(call, [{'type': 'text', 'text': 'Saved.'}, {'type': 'done', 'reason': 'stop'}])
        assert (await h.next('done'))['chunk']['reason'] == 'stop'
        await h.bridge.sessions[sid].task
        snapshot = await h.bridge.dispatch('session.get', {'sessionId': sid})
        assert snapshot['messages'][-1]['text'] == 'Saved.'
        assert not snapshot['active']
        files = list((tmp_path / 'sessions/libre-claw').glob('*.json'))
        assert len(files) == 1
        assert files[0].stat().st_mode & 0o777 == 0o600
    finally:
        await h.bridge.close()
    restored = Harness()
    await restored.init(tmp_path, persistence=True)
    try:
        snapshot = await restored.bridge.dispatch('session.get', {'sessionId': sid})
        assert snapshot['messages'][-1]['text'] == 'Saved.'
        await restored.bridge.dispatch('turn.start', {'sessionId': sid, 'turnId': 'restored', 'text': 'Continue'})
        call = await restored.next('callbackId')
        assistant = next(m for m in call['params']['messages'] if m.get('toolCalls'))
        assert assistant['providerMetadata']['nativeLibreClaw']['replayState']['response'] == {'signed': True}
        assert assistant['toolCalls'][0]['providerMetadata'] == {'signature': 'tool-local-signature'}
        assert not any('userId' in m for m in call['params']['messages'])
        await restored.respond(call, [{'type': 'text', 'text': 'Restored.'}, {'type': 'done', 'reason': 'stop'}])
        await restored.next('done')
    finally:
        await restored.bridge.close()


@pytest.mark.asyncio
async def test_read_only_scoping_cancellation_and_current_actor_absent(tmp_path):
    h = Harness()
    await h.init(tmp_path)
    try:
        with pytest.raises(ValueError, match='inside'):
            await h.bridge.dispatch('session.create', {'cwd': str(tmp_path.parent)})
        session = await h.bridge.dispatch('session.create', {'cwd': str(tmp_path)})
        await h.bridge.dispatch('turn.start', {'sessionId': session['id'], 'turnId': 'cancel', 'text': 'Wait'})
        call = await h.next('callbackId')
        assert 'userId' not in call['params']
        assert 'write_file' not in {t['name'] for t in call['params']['tools']}
        assert await h.bridge.dispatch('turn.cancel', {'sessionId': session['id']})
        cancelled = await h.next('callback.cancel')
        assert cancelled['callbackId'] == call['callbackId']
        assert (await h.next('done'))['chunk']['interrupted']
        assert not h.bridge.callbacks
    finally:
        await h.bridge.close()


@pytest.mark.asyncio
async def test_work_real_agent_step_preserves_request_and_never_executes_tools(tmp_path, monkeypatch):
    from libre_claw.core.agent import Agent, AgentDone
    reported_usage = []
    original_step = Agent.step
    async def observe_step(agent):
        async for event in original_step(agent):
            if isinstance(event, AgentDone):
                reported_usage.append(event.usage)
            yield event
    monkeypatch.setattr(Agent, "step", observe_step)
    h = Harness()
    await h.init(tmp_path, mode='work')
    request = {'model': 'exact/model', 'messages': [{'role': 'user', 'content': 'write file'}],
               'tools': [{'type': 'function', 'function': {'name': 'write_file', 'parameters': {'type': 'object'}}}],
               'options': {'temperature': 0.3}}
    response = {'model': 'exact/model', 'message': {'role': 'assistant', 'content': 'Preparing.', 'thinking': 'Planning.', 'providerMetadata': {'privateReplay': 'fixture'},
                 'tool_calls': [{'id': 'call-1', 'function': {'name': 'write_file', 'arguments': {'path': str(tmp_path / 'owned.txt'), 'content': 'no'}}}]}, 'done': True}
    try:
        task = asyncio.create_task(h.bridge.dispatch('work.generate', {'turnId': 'work-1', 'request': request}))
        call = await h.next('callbackId')
        assert call['method'] == 'work.provider'
        assert call['params']['request'] == request
        await h.respond(call, [{'type': 'text', 'text': 'Preparing.'}, {'type': 'reasoning', 'text': 'Planning.'},
                               {'type': 'usage', 'promptTokens': 7, 'completionTokens': 9, 'totalTokens': 16}], result=response)
        assert (await h.next('text'))['chunk']['text'] == 'Preparing.'
        assert (await h.next('reasoning'))['chunk']['text'] == 'Planning.'
        assert (await h.next('usage'))['chunk'] == {'type': 'usage', 'promptTokens': 7, 'completionTokens': 9, 'totalTokens': 16}
        assert await asyncio.wait_for(task, 15) == response
        assert len(reported_usage) == 1
        assert reported_usage[0].input_tokens == 7
        assert reported_usage[0].output_tokens == 9
        assert reported_usage[0].total_tokens == 16
        assert not (tmp_path / 'owned.txt').exists()
        assert h.bridge.sessions == {}
        assert h.bridge.store is None
        request['messages'].extend([response['message'], {'role': 'tool', 'tool_call_id': 'call-1', 'content': 'Approved sandbox result'}])
        task = asyncio.create_task(h.bridge.dispatch('work.generate', {'turnId': 'work-2', 'request': request}))
        call = await h.next('callbackId')
        assert call['params']['request']['messages'][-1]['content'] == 'Approved sandbox result'
        final = {'model': 'exact/model', 'message': {'role': 'assistant', 'content': 'Done'}, 'done': True}
        await h.respond(call, [], result=final)
        assert await task == final
        with pytest.raises(ValueError, match='Host operations'):
            await h.bridge.dispatch('session.create', {'cwd': str(tmp_path)})
    finally:
        await h.bridge.close()


@pytest.mark.asyncio
async def test_work_cancellation_recovery_provider_error_and_malformed_tools(tmp_path):
    h = Harness()
    await h.init(tmp_path, mode='work')
    request = {'model': 'm', 'messages': [{'role': 'user', 'content': 'test'}]}
    try:
        task = asyncio.create_task(h.bridge.dispatch('work.generate', {'turnId': 'cancel', 'request': request}))
        await h.next('callbackId')
        assert await h.bridge.dispatch('turn.cancel', {'turnId': 'cancel'})
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not h.bridge.callbacks
        task = asyncio.create_task(h.bridge.dispatch('work.generate', {'turnId': 'retry', 'request': request}))
        call = await h.next('method')
        h.bridge.receive_callback({'callbackId': call['callbackId'], 'error': {'message': 'explicit provider failure'}})
        with pytest.raises(RuntimeError, match='explicit provider failure'):
            await task
        task = asyncio.create_task(h.bridge.dispatch('work.generate', {'turnId': 'invalid', 'request': request}))
        call = await h.next('method')
        await h.respond(call, [], result={'message': {'content': '', 'tool_calls': [{'id': 'x', 'function': {'name': 'write', 'arguments': '['}}]}})
        with pytest.raises(RuntimeError):
            await task
    finally:
        await h.bridge.close()


@pytest.mark.asyncio
async def test_stdio_protocol_shutdown_without_personal_config(tmp_path):
    env = {**os.environ, 'HOME': str(tmp_path), 'PYTHONPATH': str(Path(__file__).parents[1] / 'src')}
    process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'libre_claw.bridge', stdin=asyncio.subprocess.PIPE,
                                                  stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
    try:
        async def request(frame):
            process.stdin.write((json.dumps(frame) + '\n').encode())
            await process.stdin.drain()
            return json.loads(await asyncio.wait_for(process.stdout.readline(), 20))
        started = await request({'id': '1', 'method': 'initialize', 'params': {'protocolVersion': 1, 'mode': 'work'}})
        assert started['result']['engine'] == 'libre-claw'
        assert (await request({'id': '2', 'method': 'shutdown'})) == {'id': '2', 'result': True}
        assert await asyncio.wait_for(process.wait(), 10) == 0
        assert not (tmp_path / '.libre-claw').exists()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.asyncio
async def test_callback_bounds_are_cumulative_and_completion_is_explicit():
    h = Harness()
    state = Callback(received_bytes=MAX_CALLBACK_BYTES - 1)
    h.bridge.callbacks['bounded'] = state
    with pytest.raises(ValueError, match='buffer limit'):
        h.bridge.receive_callback({'callbackId': 'bounded', 'event': {'type': 'text', 'text': 'x'}})
    assert state.queue.empty()
    h.bridge.callbacks.clear()
    for events in [
        [{'type': 'text', 'text': 'No completion event'}],
        [{'type': 'done', 'reason': 'stop'}, {'type': 'text', 'text': 'Too late'}],
        [{'type': 'done', 'reason': 'aborted'}],
        [{'type': 'usage', 'inputTokens': -1, 'outputTokens': 2}],
    ]:
        provider = CallbackProvider(h.bridge, 'turn', 'fixture')
        async def consume():
            return [event async for event in provider.complete([])]
        task = asyncio.create_task(consume())
        call = await h.next('callbackId')
        await h.respond(call, events)
        with pytest.raises((ValueError, RuntimeError), match='completion|unsuccessful|token usage'):
            await task
        assert h.bridge.callbacks == {}


@pytest.mark.asyncio
async def test_read_only_rejects_write_and_workspace_escape_without_approval(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + '-private.txt')
    outside.write_text('must stay private')
    h = Harness()
    await h.init(tmp_path)
    try:
        session = await h.bridge.dispatch('session.create', {'cwd': str(tmp_path)})
        await h.bridge.dispatch('turn.start', {'sessionId': session['id'], 'turnId': 'scope', 'text': 'Try invalid tools'})
        call = await h.next('callbackId')
        await h.respond(call, [
            {'type': 'tool-call', 'toolCall': {'id': 'w', 'name': 'write_file', 'arguments': json.dumps({'path': 'forbidden.txt', 'content': 'no'})}},
            {'type': 'tool-call', 'toolCall': {'id': 'r', 'name': 'read_file', 'arguments': json.dumps({'path': str(outside)})}},
            {'type': 'done', 'reason': 'tool-calls'},
        ])
        call = await h.next('callbackId')
        results = [m for m in call['params']['messages'] if m.get('role') == 'tool']
        assert len(results) == 2
        assert all('must stay private' not in m['content'] for m in results)
        assert not (tmp_path / 'forbidden.txt').exists()
        assert not h.bridge.sessions[session['id']].approvals
        await h.respond(call, [{'type': 'text', 'text': 'Denied safely.'}, {'type': 'done', 'reason': 'stop'}])
        await h.next('done')
    finally:
        await h.bridge.close()
        outside.unlink()


@pytest.mark.asyncio
async def test_rejected_write_approval_and_failed_persistence_finish_turn(tmp_path, monkeypatch):
    h = Harness()
    await h.init(tmp_path)
    try:
        session = await h.bridge.dispatch('session.create', {'permissionMode': 'workspace-write'})
        await h.bridge.dispatch('turn.start', {'sessionId': session['id'], 'turnId': 'reject', 'text': 'Write'})
        call = await h.next('callbackId')
        await h.respond(call, [
            {'type': 'tool-call', 'toolCall': {'id': 'w', 'name': 'write_file', 'arguments': '{"path":"denied.txt","content":"no"}'}},
            {'type': 'done', 'reason': 'tool-calls'},
        ])
        approval = (await h.next('approval-request'))['chunk']['approval']
        assert await h.bridge.dispatch('session.approve', {'sessionId': session['id'], 'approvalId': approval['id'], 'outcome': 'rejected'})
        call = await h.next('callbackId')
        assert not (tmp_path / 'denied.txt').exists()
        assert any(m.get('toolCallId') == 'w' for m in call['params']['messages'])
        def failed_persist(_record):
            raise OSError('fixture disk full')
        monkeypatch.setattr(h.bridge, 'persist', failed_persist)
        await h.respond(call, [{'type': 'text', 'text': 'Write was denied.'}, {'type': 'done', 'reason': 'stop'}])
        done = await h.next('done')
        assert done['chunk']['reason'] == 'error'
        await h.bridge.sessions[session['id']].task
    finally:
        await h.bridge.close()


@pytest.mark.asyncio
async def test_invalid_node_and_work_host_capabilities_fail_closed(tmp_path, monkeypatch):
    for params in [{'tools': True}, {'persistence': True}]:
        h = Harness()
        with pytest.raises(ValueError, match='cannot mount'):
            await h.bridge.dispatch('initialize', {'protocolVersion': 1, 'mode': 'work', **params})
        assert not h.bridge.initialized
        assert not h.bridge.engine.running
        await h.bridge.close()
    monkeypatch.setenv('LIBRE_CLAW_NODE', str(tmp_path / 'missing-node'))
    h = Harness()
    with pytest.raises(RuntimeError, match='Node.js'):
        await h.init(tmp_path, mode='work')
    assert not h.bridge.initialized
    assert not h.bridge.engine.running
    await h.bridge.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('termination', ['eof', 'signal', 'oversize', 'partial'])
async def test_stdio_disconnects_reap_the_offline_engine(tmp_path, termination):
    if os.name != 'posix':
        pytest.skip('POSIX process lifecycle probe')
    env = {key: value for key, value in os.environ.items() if key in {'PATH', 'LANG', 'TMPDIR', 'LIBRE_CLAW_NODE'}}
    env.update(HOME=str(tmp_path), PYTHONPATH=str(Path(__file__).parents[1] / 'src'))
    process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'libre_claw.bridge',
                                                  stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                  stderr=asyncio.subprocess.PIPE, env=env)
    try:
        process.stdin.write((json.dumps({'id': 'init', 'method': 'initialize', 'params': {'protocolVersion': 1, 'mode': 'work'}}) + '\n').encode())
        await process.stdin.drain()
        started = json.loads(await asyncio.wait_for(process.stdout.readline(), 20))
        assert started['result']['engine'] == 'libre-claw'
        ps = await asyncio.create_subprocess_exec('ps', '-axo', 'pid=,ppid=', stdout=asyncio.subprocess.PIPE)
        output, _ = await ps.communicate()
        children = [int(line.split()[0]) for line in output.decode().splitlines() if len(line.split()) == 2 and int(line.split()[1]) == process.pid]
        assert children, 'initialization must own the real Cordis child'
        process.stdin.write((json.dumps({'id': 'active', 'method': 'work.generate', 'params': {
            'turnId': 'active', 'request': {'model': 'fixture', 'messages': [{'role': 'user', 'content': 'Wait for parent'}]},
        }}) + '\n').encode())
        await process.stdin.drain()
        callback = json.loads(await asyncio.wait_for(process.stdout.readline(), 20))
        assert callback['method'] == 'work.provider'
        if termination == 'signal':
            process.terminate()
        else:
            if termination == 'oversize':
                process.stdin.write(b'x' * (MAX_FRAME + 1) + b'\n')
                try:
                    await process.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            elif termination == 'partial':
                process.stdin.write(b'{"id":"unfinished"}')
                await process.stdin.drain()
            process.stdin.close()
        _, errors = await asyncio.wait_for(process.communicate(), 20)
        assert process.returncode == 0 if termination in {'eof', 'signal'} else process.returncode != 0
        assert b'Task exception was never retrieved' not in errors
        assert b'ResourceWarning' not in errors
        for pid in children:
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
        assert not (tmp_path / '.libre-claw').exists()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.asyncio
async def test_migration_provenance_survives_resume_and_persistence(tmp_path):
    store = tmp_path / 'sessions' / 'libre-claw'
    store.mkdir(parents=True)
    session_id = 'a' * 32
    provenance = {'source': 'legacy-session', 'sha256': 'b' * 64, 'format': 'harness-v3'}
    path = store / f'{session_id}.json'
    path.write_text(json.dumps({'version': 1, 'id': session_id, 'cwd': str(tmp_path), 'title': 'Imported',
                                'model': 'test', 'permission': 'read-only', 'created': 1,
                                'session': {'messages': []}, 'migration': provenance}))
    h = Harness()
    await h.init(tmp_path, persistence=True)
    try:
        await h.bridge.dispatch('turn.start', {'sessionId': session_id, 'turnId': 'resume', 'text': 'Continue'})
        call = await h.next('callbackId')
        await h.respond(call, [{'type': 'text', 'text': 'Resumed'}, {'type': 'done', 'reason': 'stop'}])
        await h.next('done')
        await h.bridge.sessions[session_id].task
        data = json.loads(path.read_text())
        assert data['migration'] == provenance
        assert data['session']['messages'][-1]['content'][0]['text'] == 'Resumed'
    finally:
        await h.bridge.close()
