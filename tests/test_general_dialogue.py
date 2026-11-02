import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from companion.local_context import general_dialogue_context
from test_workflows import ready_engine, store


@pytest.mark.asyncio
@pytest.mark.parametrize('reply,expected', [
    ('Geschafft! Jetzt erstmal kurz durchatmen.', 'Geschafft! Jetzt erstmal kurz durchatmen.'),
    ('__STILL__', ''),
    ('{"action":"skip","message":"Keine weitere Antwort."}', ''),
    ('Example [streamer]: Geschafft!', 'Geschafft!'),
])
async def test_spontaneous_comments_use_plain_dialogue_and_share_the_prepared_profile(store,reply,expected):
    engine=ready_engine(store)
    store.put('settings',store.settings().model_copy(update={'local_ai_cpu_threads':2}).model_dump())
    engine.event('audio','Mic','Endlich ist das Projekt fertig.',role='streamer',speaker_name='Example',subject_id='owner1',remember=True)
    requests=[]
    def answer(request):
        payload=json.loads(request.content)
        requests.append(payload)
        assert 'format' not in payload and 'response_format' not in payload
        assert payload['think'] is False
        return httpx.Response(200,json={'message':{'content':'bereit' if payload['options']['num_predict']==4 else reply},'done':True})
    await engine.ai.http.aclose()
    engine.ai.http=httpx.AsyncClient(transport=httpx.MockTransport(answer))
    try:
        context=engine.context()
        await engine.ai.warmup(context)
        result=await engine.ai.generate(context)
        assert result['message']==expected and result['memories']==[]
        assert result['action']==('message' if expected else 'skip')
        prefix=requests[0]['messages'][0]['content'].split('\nAktueller Gesprächspartner:')[0]
        assert requests[1]['messages'][0]['content'].startswith(prefix)
        assert sum('Endlich ist das Projekt fertig.' in row['content'] for row in requests[1]['messages'])==1
    finally:
        await engine.close()


def test_spontaneous_context_is_bounded_but_keeps_recent_chat_among_audio():
    rows=[{'source':'chat','name':'Nora','text':'Chatbezug: '+('lange Aussage '*80)}]
    rows += [{'source':'streamer','name':'Timur','text':'Beobachtung '+('langer Text '*80)} for _ in range(6)]
    rows.append({'source':'streamer','name':'Timur','text':('langer Text '*80)+' Ich starte Hades.'})
    projected=general_dialogue_context({'context':rows,'memories':[{'fact':'Mag Hades.'},{'fact':'Mag Tetris.'}]})
    assert len(projected['context'])==4
    assert sum(len(row['text']) for row in projected['context'])<=1200
    assert projected['context'][0]['name']=='Nora'
    assert projected['context'][-1]['text'].endswith('Ich starte Hades.')
    assert [note['fact'] for note in projected['memories']]==['Mag Hades.']
    assert len(rows[0]['text'])>300


@pytest.mark.asyncio
async def test_unaddressed_declaration_is_checked_in_background_once_even_after_silence_decision(store):
    engine=ready_engine(store)
    engine.event('audio','Mic','Ich spiele am liebsten Hades.',role='streamer',speaker_name='Example',subject_id='owner1',remember=True)
    checked=asyncio.Event()
    async def generate(context,image=None):
        if context.get('memory_only'):
            checked.set()
        return {'action':'skip','message':'','kind':'comment','reason':'Test','memories':[]}
    engine.ai.generate=generate
    worker=None
    try:
        await engine.analyze()
        assert len(engine.memory_backlog)==1 and engine.can_contribute()[0]
        worker=asyncio.create_task(engine.memory_worker())
        await asyncio.wait_for(checked.wait(),2)
        await asyncio.sleep(0)
        assert not engine.memory_backlog
        await engine.analyze()
        assert not engine.memory_backlog
    finally:
        if worker:
            worker.cancel()
            await asyncio.gather(worker,return_exceptions=True)
        await engine.close()


@pytest.mark.asyncio
async def test_unaddressed_viewer_without_consent_does_not_start_memory_work(store):
    engine=ready_engine(store)
    engine.event('chat','Nora','Ich spiele am liebsten Hades.',subject_id='viewer',remember=False)
    engine.ai.generate=AsyncMock(return_value={'action':'skip','message':'','kind':'comment','memories':[]})
    try:
        await engine.analyze()
        assert not engine.memory_backlog and not store.memories('example')
    finally:
        await engine.close()
