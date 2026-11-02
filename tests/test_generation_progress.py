import asyncio
import json
import time
from unittest.mock import AsyncMock

import httpx
import pytest

from companion.ai import AIError
from test_mentions import chat
from test_workflows import ready_engine, store


class ReplyStream(httpx.AsyncByteStream):
    def __init__(self, incomplete=False):
        self.entered, self.first, self.emitted, self.finish = (asyncio.Event() for _ in range(4))
        self.incomplete = incomplete

    async def __aiter__(self):
        self.entered.set()
        await self.first.wait()
        yield b'{"message":{"content":"Bin "},"done":false}\n'
        self.emitted.set()
        await self.finish.wait()
        if not self.incomplete:
            yield b'{"message":{"content":"da."},"done":false}\n'
            yield b'{"message":{"content":""},"done":true,"prompt_eval_count":8,"eval_count":3}\n'


@pytest.mark.asyncio
@pytest.mark.parametrize("incomplete", [False, True])
async def test_stream_distinguishes_context_and_writing_and_never_publishes_partial_text(store, incomplete):
    engine = ready_engine(store)
    store.put('settings', store.settings().model_copy(update={'local_ai_cpu_threads': 2}).model_dump())
    stream = ReplyStream(incomplete)
    def answer(request):
        assert json.loads(request.content)['stream'] is True
        return httpx.Response(200, stream=stream, headers={'content-type': 'application/x-ndjson'})
    await engine.ai.http.aclose()
    engine.ai.http = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    await engine.on_chat(chat())
    task = asyncio.create_task(engine.analyze())
    try:
        await asyncio.wait_for(stream.entered.wait(), 2)
        assert engine.snapshot()['analysis_phase'] == 'context'
        assert engine.snapshot()['analysis_label'] == 'Verarbeitet Gesprächskontext'
        assert engine.pending is None
        stream.first.set()
        await asyncio.wait_for(stream.emitted.wait(), 2)
        assert engine.snapshot()['analysis_phase'] == 'writing'
        assert engine.snapshot()['analysis_label'] == 'Schreibt Antwort'
        assert engine.pending is None
        stream.finish.set()
        if incomplete:
            with pytest.raises(AIError, match='nicht vollständig'):
                await task
            assert engine.pending is None
        else:
            await task
            assert engine.pending['text'] == 'Bin da.'
            assert engine.ai.last_metrics['eval_count'] == 3
            assert engine.ai.last_metrics['first_token_seconds'] >= 0
        assert not engine.ai.progress and not engine.snapshot()['analyzing']
    finally:
        stream.first.set()
        stream.finish.set()
        await asyncio.gather(task, return_exceptions=True)
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('direct', [False, True])
async def test_local_result_after_114_seconds_still_has_a_valid_send_window(store, monkeypatch, direct):
    engine = ready_engine(store)
    now = {'time': time.time()}
    monkeypatch.setattr('companion.engine.time.time', lambda: now['time'])
    if direct:
        await engine.on_chat(chat())
    async def generate(*args):
        now['time'] += 114
        return {'action': 'message', 'message': 'Die Antwort kommt an.', 'kind': 'comment'}
    engine.ai.generate = generate
    try:
        await engine.analyze()
        assert engine.pending['text'] == 'Die Antwort kommt an.'
        assert not engine.draft_expired(engine.pending)
        assert engine.pending['expires'] - now['time'] == pytest.approx(36)
        assert engine.last_analysis == now['time']
        if direct:
            assert engine.reply_still_valid(engine.pending['reply_to'])
        now['time'] += 37
        assert engine.draft_expired(engine.pending)
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_deadline_cancels_the_actual_model_work_and_blocks_same_context_until_new_final_speech(store):
    engine = ready_engine(store)
    cancelled = asyncio.Event()
    engine.ai.time_budget = lambda kind: .02
    async def generate(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    engine.ai.generate = generate
    try:
        with pytest.raises(AIError, match='keine vollständige Antwort'):
            await engine.analyze()
        assert cancelled.is_set() and engine.generation_task is None
        assert not engine.analysis_lock.locked() and engine.pending is None
        assert not engine.can_contribute()[0]
        source = store.settings().sources[0]
        await engine.transcript(source, 'Ich beginne', utterance_id='new', final=False)
        assert not engine.can_contribute()[0]
        await engine.transcript(source, 'Heute beginnt ein neues Projekt.', utterance_id='new', final=True)
        assert engine.can_contribute()[0]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_manual_retry_can_recheck_failed_unchanged_context(store):
    engine = ready_engine(store)
    engine.last_failed_analysis_key = engine.analysis_context_key()
    engine.ai.generate = AsyncMock(return_value={'action': 'message', 'message': 'Bereit.', 'kind': 'comment'})
    try:
        assert not engine.can_contribute()[0]
        await engine.analyze(force=True)
        assert engine.pending['text'] == 'Bereit.'
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_due_conversation_runs_before_background_memory_but_memory_can_run_during_cooldown(store):
    engine = ready_engine(store)
    engine.event('audio', 'Mic', 'Ich spiele heute Hades.', role='streamer', subject_id='owner1', remember=True)
    engine.defer_memory(engine.context())
    engine.ai.generate = AsyncMock(return_value={'action': 'skip', 'message': '', 'memories': []})
    try:
        assert engine.analysis_due() and len(engine.memory_backlog) == 1
        await engine.process_memory()
        assert engine.ai.generate.await_count == 0 and len(engine.memory_backlog) == 1
        await engine.analyze()
        assert not engine.analysis_due()
        await engine.process_memory()
        assert engine.ai.generate.await_count == 2 and not engine.memory_backlog
        assert engine.ai.generate.call_args.args[0]['memory_only']
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_character_prefix_is_shared_by_warmup_reply_general_and_memory_requests(store):
    engine = ready_engine(store)
    store.put('settings', store.settings().model_copy(update={'local_ai_cpu_threads': 2}).model_dump())
    requests = []
    def answer(request):
        payload = json.loads(request.content)
        requests.append(payload)
        text = '{"memories":[]}' if payload.get('format') else 'bereit' if payload['options']['num_predict'] == 4 else 'Bin da.'
        return httpx.Response(200, json={'message': {'content': text}, 'done': True})
    await engine.ai.http.aclose()
    engine.ai.http = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    try:
        await engine.on_chat(chat())
        context = engine.context()
        await engine.ai.warmup(context)
        await engine.ai.generate(context)
        await engine.ai.generate({**context, 'reply_to': None})
        await engine.ai.generate({**context, 'reply_to': None, 'memory_only': True})
        assert len(requests) == 4
        assert all(request['messages'][0] == requests[0]['messages'][0] for request in requests)
        assert 'Gedächtnisaufgabe' in requests[-1]['messages'][1]['content']
    finally:
        await engine.close()
