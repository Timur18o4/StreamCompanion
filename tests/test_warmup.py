import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from companion.ai import AIError
from test_mentions import ANSWER, chat
from test_workflows import ready_engine, store


@pytest.mark.asyncio
async def test_native_warmup_uses_a_real_small_request_with_a_separate_loading_timeout(store):
    engine = ready_engine(store)
    store.put('settings', store.settings().model_copy(update={'local_ai_cpu_threads': 4}).model_dump())
    store.put('ai_key', 'saved-cloud-key')
    requests=[]
    def answer(request):
        requests.append(request)
        payload=json.loads(request.content)
        assert request.url.path=='/api/chat'
        assert payload['messages'][0]['role']=='system'
        assert 'Milo' in payload['messages'][0]['content']
        assert '340 Zeichen' in payload['messages'][0]['content']
        assert payload['messages'][-1]['content'].endswith('Antworte nur mit bereit.')
        assert payload['options']['num_thread']==4 and payload['options']['num_predict']==4
        assert payload['keep_alive']=='2h' and payload['think'] is False
        assert request.extensions['timeout']['read']==300
        assert 'Authorization' not in request.headers
        return httpx.Response(200,json={'message':{'content':'bereit'},'done':True})
    await engine.ai.http.aclose()
    engine.ai.http=httpx.AsyncClient(transport=httpx.MockTransport(answer))
    try:
        assert await engine.ai.warmup()
        assert len(requests)==1 and not engine.events and engine.pending is None
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_loading_keeps_collecting_context_and_new_mentions_do_not_cancel_it(store):
    engine=ready_engine(store)
    entered,finish=asyncio.Event(),asyncio.Event()
    async def load(context=None):
        entered.set()
        await finish.wait()
        return True
    engine.ai.warmup=load
    engine.ai.generate=AsyncMock(return_value=ANSWER)
    try:
        engine.begin_model_warmup()
        await entered.wait()
        await engine.transcript(store.settings().sources[0],'Milo, bist du da?')
        await engine.on_chat(chat())
        assert engine.model_warming and not engine.model_warmup_task.done()
        assert not engine.can_contribute()[0] and 'vorbereitet' in engine.snapshot()['reason']
        assert any(row['kind']=='audio' for row in engine.events)
        assert engine.reply_target()['kind']=='chat'
        finish.set()
        await engine.model_warmup_task
        assert not engine.model_warming
        await engine.analyze()
        assert engine.pending['text']==ANSWER['message']
        assert engine.pending['reply_to']['kind']=='chat'
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_stop_cancels_loading_and_clears_the_loading_state(store):
    engine=ready_engine(store)
    entered,canceled=asyncio.Event(),asyncio.Event()
    async def load(context=None):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            canceled.set()
    engine.ai.warmup=load
    engine.begin_model_warmup()
    task=engine.model_warmup_task
    await entered.wait()
    await engine.close()
    assert task.cancelled() and canceled.is_set()
    assert not engine.running and not engine.model_warming


@pytest.mark.asyncio
async def test_failed_loading_is_visible_and_pauses_contributions_until_retry(store):
    engine=ready_engine(store)
    engine.ai.warmup=AsyncMock(side_effect=AIError('Test: Modell nicht vorhanden.'))
    try:
        engine.begin_model_warmup()
        await engine.model_warmup_task
        assert engine.paused and not engine.model_warming
        assert engine.logs[0]['title']=='Lokales Modell nicht bereit'
        engine.ai.warmup=AsyncMock(return_value=True)
        engine.paused=False
        engine.begin_model_warmup()
        await engine.model_warmup_task
        assert engine.can_contribute()[0]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_cloud_provider_does_not_make_a_warmup_request(store):
    engine=ready_engine(store)
    store.put('settings', store.settings().model_copy(update={'provider':'openai'}).model_dump())
    engine.ai.http.post=AsyncMock(side_effect=AssertionError('No cloud warmup'))
    try:
        assert await engine.ai.warmup() is False
        engine.ai.http.post.assert_not_awaited()
    finally:
        await engine.close()
