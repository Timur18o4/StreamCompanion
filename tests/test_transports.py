import asyncio
import json
import time
import sys
import threading
from types import SimpleNamespace
from collections import deque
from unittest.mock import AsyncMock

import httpx
import numpy as np
import pytest

from companion.audio import AudioState, resample_pcm
from companion.models import AudioSource
from companion.twitch import TwitchError
from test_workflows import ready_engine, store, client


def test_cpu_thread_choices_follow_current_hardware_and_survive_save(client, monkeypatch):
    monkeypatch.setattr("companion.models.os.cpu_count", lambda: 7)
    api, app = client
    status = api.get("/api/status").json()
    assert status["capabilities"]["cpu_threads_max"] == 7
    settings = status["settings"]
    settings.update(whisper_cpu_threads=99, local_ai_cpu_threads=99)
    assert api.post("/api/settings", json={"settings": settings}).status_code == 200
    saved = api.get("/api/status").json()["settings"]
    assert saved["whisper_cpu_threads"] == saved["local_ai_cpu_threads"] == 7
    settings.update(whisper_cpu_threads=2, local_ai_cpu_threads=3)
    assert api.post("/api/settings", json={"settings": settings}).status_code == 200
    saved = api.get("/api/status").json()["settings"]
    assert (saved["whisper_cpu_threads"], saved["local_ai_cpu_threads"]) == (2, 3)


@pytest.mark.asyncio
async def test_ollama_thread_selection_reaches_model_request(store, monkeypatch):
    monkeypatch.setattr("companion.models.os.cpu_count", lambda: 4)
    engine = ready_engine(store)
    store.put("settings", store.settings().model_copy(update={"local_ai_cpu_threads": 3}).model_dump())
    store.put("ai_key", "saved-cloud-credential")
    observed = []

    def answer(request):
        observed.append(request)
        return httpx.Response(200, json={"message": {"content": json.dumps({"action": "skip", "message": "", "kind": "comment", "reason": "Test", "memories": []})}})

    await engine.ai.http.aclose()
    engine.ai.http = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    try:
        result = await engine.ai.generate({"task": "Connection test"})
        assert result["action"] == "skip"
        assert observed[0].url.path == "/api/chat"
        assert json.loads(observed[0].content)["options"]["num_thread"] == 3
        assert json.loads(observed[0].content)["options"]["num_batch"] == 128
        assert "Authorization" not in observed[0].headers
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_whisper_thread_change_recreates_the_actual_recognizer(store, monkeypatch):
    monkeypatch.setattr("companion.models.os.cpu_count", lambda: 4)
    engine = ready_engine(store)
    initialized = []

    class Whisper:
        def __init__(self, *args, **kwargs):
            initialized.append(kwargs["cpu_threads"])

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Whisper))
    source = store.settings().sources[0]
    task = None

    async def loaded(threads):
        while engine.audio.whisper_config != ("small", threads):
            await asyncio.sleep(0.005)

    try:
        for threads in (2, 4):
            store.put("settings", store.settings().model_copy(update={"stt_provider": "local_whisper", "whisper_cpu_threads": threads}).model_dump())
            task = asyncio.create_task(engine.audio.local_transcription(source, AudioState()))
            await asyncio.wait_for(loaded(threads), 3)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert initialized == [2, 4]
    finally:
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await engine.close()


@pytest.mark.asyncio
async def test_empty_filtered_context_cannot_trigger_an_invented_contribution(store):
    engine = ready_engine(store)
    engine.ai.http.post = AsyncMock(side_effect=AssertionError("No model call without observations"))
    try:
        result = await engine.ai.generate({"context": [], "chat_included": False})
        assert result["action"] == "skip" and result["message"] == ""
        engine.ai.http.post.assert_not_awaited()
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_switching_to_local_ai_keeps_cloud_key_private_and_available_for_later(store):
    engine = ready_engine(store)
    store.put("ai_key", "saved-cloud-credential")
    seen = []

    def answer(request):
        seen.append((request.url.host, request.headers.get("Authorization")))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"action": "skip", "message": "", "memories": []})}}]})

    await engine.ai.http.aclose()
    engine.ai.http = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    try:
        await engine.ai.generate({"task": "Connection test"})
        assert seen[-1] == ("127.0.0.1", None)
        assert store.get("ai_key") == "saved-cloud-credential"
        settings = store.settings().model_copy(update={"provider": "openai"})
        store.put("settings", settings.model_dump())
        await engine.ai.generate({"task": "Connection test"})
        assert seen[-1] == ("api.openai.com", "Bearer saved-cloud-credential")
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_local_whisper_keeps_following_speech_during_cpu_inference(store, monkeypatch):
    engine = ready_engine(store)
    settings = store.settings().model_copy(update={"stt_provider": "local_whisper"})
    store.put("settings", settings.model_dump())
    entered, release = threading.Event(), threading.Event()
    completed = asyncio.Event()
    durations, transcripts = [], []

    class Whisper:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, samples, **kwargs):
            durations.append(len(samples) / 16000)
            if len(durations) == 1:
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("Inference test was not released")
            return iter([SimpleNamespace(text=f"Satz {len(durations)}")]), None

    async def received(source, text):
        transcripts.append(text)
        if len(transcripts) == 2:
            completed.set()

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Whisper))
    engine.transcript = received
    speech = resample_pcm(np.ones(2400) * 0.1, 24000)
    try:
        for _ in range(80):
            engine.audio.feed("microphone", speech)
        for _ in range(8):
            engine.audio.feed("microphone", bytes(4800))
        assert await asyncio.to_thread(entered.wait, 2)
        # Replay the next utterance while the first inference is occupied.
        for _ in range(90):
            engine.audio.feed("microphone", speech)
        for _ in range(8):
            engine.audio.feed("microphone", bytes(4800))
        release.set()
        await asyncio.wait_for(completed.wait(), 2)
        assert transcripts == ["Satz 1", "Satz 2"]
        assert durations[1] >= 9.7
        assert engine.audio.states["microphone"].dropped == 0
    finally:
        release.set()
        await engine.close()


@pytest.mark.asyncio
async def test_local_whisper_batches_short_turns_without_losing_either_utterance(store, monkeypatch):
    engine = ready_engine(store)
    store.put("settings", store.settings().model_copy(update={"stt_provider": "local_whisper"}).model_dump())
    decoded = []
    completed = asyncio.Event()

    class Whisper:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, samples, **kwargs):
            decoded.append(samples.copy())
            return iter([SimpleNamespace(text="Beide Aussagen erkannt.")]), None

    async def received(source, text):
        completed.set()

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Whisper))
    engine.transcript = received
    speech = resample_pcm(np.ones(2400) * 0.1, 24000)
    try:
        for utterance in range(2):
            for _ in range(35):
                engine.audio.feed("microphone", speech)
            for _ in range(8):
                engine.audio.feed("microphone", bytes(4800))
        await asyncio.wait_for(completed.wait(), 3)
        voiced = np.abs(decoded[0]) > 0.05
        starts = np.diff(np.r_[False, voiced, False].astype(int)) == 1
        assert starts.sum() == 2
        assert engine.audio.states["microphone"].dropped == 0
    finally:
        await engine.close()


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
    engine.memory_worker=wait_forever
    try:
        await asyncio.gather(engine.start(),engine.start())
        assert engine.running and engine.tasks
        initial_tasks = tuple(engine.tasks)
        await engine.start()
        assert tuple(engine.tasks) == initial_tasks
        engine.check_channel.assert_awaited_once()
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_verified_category_change_during_live_check_cannot_be_sent(store):
    from test_workflows import make_draft
    engine=ready_engine(store)
    make_draft(engine)
    engine.twitch.channel=AsyncMock(return_value={**engine.channel,"category":"Another Game"})
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
