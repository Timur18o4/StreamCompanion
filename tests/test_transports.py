import asyncio
import json
import time
from collections import deque
from unittest.mock import AsyncMock

import httpx
import numpy as np
import pytest

from companion.audio import AudioState, resample_pcm
from companion.models import AudioSource
from companion.twitch import TwitchError
from test_workflows import ready_engine, store


@pytest.mark.asyncio
async def test_device_login_pending_then_correct_identity_and_scopes(store,monkeypatch):
    engine=ready_engine(store)
    await engine.twitch.http.aclose()
    token_calls=0
    def transport(request):
        nonlocal token_calls
        if request.url.path=="/oauth2/device":
            return httpx.Response(200,json={"device_code":"private-device-code","user_code":"ABCDEFGH","verification_uri":"https://www.twitch.tv/activate","expires_in":300,"interval":5})
        if request.url.path=="/oauth2/token":
            token_calls+=1
            if token_calls==1:return httpx.Response(400,json={"message":"authorization_pending"})
            return httpx.Response(200,json={"access_token":"new-bot-access","refresh_token":"rotating-refresh","expires_in":3600,"scope":["user:read:chat","user:write:chat"]})
        if request.url.path=="/oauth2/validate":
            return httpx.Response(200,json={"client_id":"test-app-id","user_id":"newbot","expires_in":3600,"scopes":["user:read:chat","user:write:chat"]})
        if request.url.path=="/helix/users":
            return httpx.Response(200,json={"data":[{"id":"newbot","login":"newbot","display_name":"NewBot","profile_image_url":""}]})
        raise AssertionError(str(request.url))
    engine.twitch.http=httpx.AsyncClient(transport=httpx.MockTransport(transport))
    monkeypatch.setattr("companion.twitch.asyncio.sleep",AsyncMock())
    initial=await engine.twitch.device_start("bot")
    assert initial["status"]=="pending" and "device_code" not in initial
    await engine.twitch.oauth_tasks["bot"]
    assert engine.twitch.pending["bot"]["status"]=="connected"
    assert store.profiles()["bot"]["id"]=="newbot"
    assert store.get("twitch_bot")["refresh_token"]=="rotating-refresh"
    assert token_calls==2
    await engine.close()


@pytest.mark.asyncio
async def test_wrong_application_is_not_accepted_during_token_validation(store):
    engine=ready_engine(store)
    await engine.twitch.http.aclose()
    engine.twitch.http=httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={"client_id":"different-app","scopes":["user:read:chat","user:write:chat"]})))
    with pytest.raises(TwitchError):
        await engine.twitch._validate("access","test-app-id",["user:read:chat"])
    await engine.close()


@pytest.mark.asyncio
async def test_eventsub_reconnect_preserves_subscription_and_deduplicates(store,monkeypatch):
    engine=ready_engine(store)
    engine.twitch.token=AsyncMock(return_value="valid-token")
    engine.twitch.request=AsyncMock(return_value={})
    received=[]
    class Socket:
        def __init__(self,messages):self.messages=deque(messages);self.closed=False
        async def recv(self):
            if not self.messages:raise asyncio.CancelledError()
            return json.dumps(self.messages.popleft())
        async def close(self):self.closed=True
    def notification(identifier,text):
        return {"metadata":{"message_type":"notification","message_id":identifier},"payload":{"event":{"message":{"text":text}}}}
    welcome={"metadata":{"message_type":"session_welcome"},"payload":{"session":{"id":"s1","keepalive_timeout_seconds":30}}}
    old=Socket([welcome,notification("one","first"),notification("one","duplicate"),{"metadata":{"message_type":"session_reconnect"},"payload":{"session":{"reconnect_url":"wss://eventsub.wss.twitch.tv/ws?reconnect=test"}}}])
    replacement=Socket([welcome,notification("two","after reconnect")])
    sockets=deque([old,replacement])
    async def connect(*args,**kwargs):return sockets.popleft()
    monkeypatch.setattr("companion.twitch.websockets.connect",connect)
    async def callback(event):received.append(event["message"]["text"])
    with pytest.raises(asyncio.CancelledError):await engine.twitch.chat("owner1",callback)
    assert received==["first","after reconnect"]
    assert old.closed and replacement.closed
    engine.twitch.request.assert_awaited_once()
    await engine.close()


@pytest.mark.asyncio
async def test_realtime_audio_protocol_commits_turn_and_associates_source(store,monkeypatch):
    engine=ready_engine(store)
    store.put("stt_key","synthetic-test-key")
    committed=asyncio.Event()
    sent=[]
    class Socket:
        async def send(self,message):
            event=json.loads(message);sent.append(event)
            if event["type"]=="input_audio_buffer.commit":committed.set()
        def __aiter__(self):return self.events()
        async def events(self):
            await asyncio.wait_for(committed.wait(),2)
            yield json.dumps({"type":"conversation.item.input_audio_transcription.completed","item_id":"audio-one","transcript":"Ich probiere den Bogen aus."})
            raise asyncio.CancelledError()
    class Connection:
        async def __aenter__(self):return Socket()
        async def __aexit__(self,*args):pass
    monkeypatch.setattr("companion.audio.websockets.connect",lambda *args,**kwargs:Connection())
    state=AudioState()
    state.queue.put_nowait(resample_pcm(np.ones(2400)*0.1,24000))
    for _ in range(8):state.queue.put_nowait(bytes(4800))
    source=AudioSource(id="microphone",name="Mikrofon",role="streamer",remember=True)
    with pytest.raises(asyncio.CancelledError):await engine.audio.realtime_transcription(source,state)
    session=sent[0]["session"]
    assert session["type"]=="transcription"
    assert session["audio"]["input"]["format"]=={"type":"audio/pcm","rate":24000}
    assert committed.is_set() and any(event["type"]=="input_audio_buffer.append" for event in sent)
    assert engine.events[-1]["subject_id"]=="owner1" and engine.events[-1]["name"]=="Mikrofon"
    await engine.close()


@pytest.mark.asyncio
async def test_double_start_produces_only_one_runtime(store,monkeypatch):
    engine=ready_engine(store)
    engine.running=False
    engine.check_channel=AsyncMock()
    engine.twitch.token=AsyncMock(return_value="valid")
    store.put("stt_key","synthetic-test-key")
    async def wait_forever(*args):await asyncio.Event().wait()
    engine.twitch.chat=wait_forever
    engine.monitor=wait_forever
    engine.conversation=wait_forever
    await asyncio.gather(engine.start(),engine.start())
    assert engine.running and len(engine.tasks)==3
    engine.check_channel.assert_awaited_once()
    await engine.close()


@pytest.mark.asyncio
async def test_changed_context_during_live_check_cannot_be_sent(store):
    from test_workflows import make_draft
    engine=ready_engine(store)
    make_draft(engine)
    async def check():engine.event("chat","Alice","A different subject",subject_id="u1")
    engine.check_channel=check
    engine.twitch.send=AsyncMock()
    with pytest.raises(ValueError):await engine.send_pending("draft1")
    engine.twitch.send.assert_not_awaited()
    await engine.close()


@pytest.mark.asyncio
async def test_new_human_chat_can_answer_open_question(store):
    engine=ready_engine(store)
    engine.last_question=time.time()-5
    engine.last_owner_text=0
    assert not engine.can_contribute()[0]
    await engine.on_chat({"chatter_user_id":"u2","chatter_user_name":"Alice","message":{"text":"Ja, heute wird der Bogen getestet."}})
    assert engine.can_contribute()[0]
    await engine.close()
