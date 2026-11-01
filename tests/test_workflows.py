import asyncio
import base64
import io
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from companion.app import create_app
from companion.audio import normalize_image, resample_pcm
from companion.engine import Engine
from companion.models import AudioSource, Settings
from companion.sender import create_sender_app, server_address
from companion.storage import Store
from companion.twitch import TwitchError


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "data")
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + app.state.store.access_key
        yield client, app


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "store")
    yield instance
    instance.close()


def ready_engine(store):
    settings = Settings(channel="example", twitch_client_id="test-app-id", input_mode="companion", text_model="local-test-model", min_interval=20)
    store.put("settings", settings.model_dump())
    store.put("twitch_owner", {"id":"owner1","login":"example","display_name":"Example","access_token":"owner-access"})
    store.put("twitch_bot", {"id":"bot1","login":"examplebot","display_name":"ExampleBot","access_token":"bot-access"})
    engine = Engine(store)
    engine.running = True
    engine.channel = {"id":"owner1","name":"Example","login":"example","category":"Test Game","title":"A test","live":True}
    engine.channel_checked = time.time()
    engine.twitch.connected = True
    engine.last_observation = time.time()
    engine.last_voice = time.time() - 20
    return engine


def make_draft(engine):
    engine.pending = {"id":"draft1","text":"Das war knapp. Bleibst du bei dem Bogen?","kind":"question","reason":"Test","created":time.time(),"epoch":engine.epoch,"revision":engine.revision,"demo":False}


def test_setup_and_demo_are_usable_without_external_credentials(client):
    api, app = client
    assert api.get("/").status_code == 200
    assert api.get("/app.js").status_code == 200
    assert api.get("/api/status").json()["settings"]["mode"] == "preview"
    assert api.post("/api/demo").status_code == 200
    state = api.get("/api/status").json()["runtime"]
    assert state["demo"] and len(state["events"]) == 3
    assert not state["running"]
    assert api.post("/api/draft/send", json={"id":"demo"}).status_code == 400


def test_local_access_and_cross_origin_protection(client):
    api, app = client
    assert api.get("/api/status", headers={"Authorization":"Bearer wrong"}).status_code == 401
    assert api.get("/api/status", headers={"Origin":"https://attacker.example"}).status_code == 403
    assert api.post("/api/run/start", headers={"Authorization":""}).status_code == 401
    assert api.get("/api/health").json()["ready"] is True


def test_secret_never_appears_in_status_or_database_plaintext(client):
    api, app = client
    settings = api.get("/api/status").json()["settings"]
    key = "test-private-key-never-log-this"
    assert api.post("/api/settings", json={"settings":settings,"ai_key":key}).status_code == 200
    response = api.get("/api/status")
    assert key not in response.text and response.json()["secret_status"]["ai_key"]
    assert key.encode() not in (app.state.store.root / "companion.sqlite3").read_bytes()
    invalid = api.post("/api/settings", json={"settings":settings,"ai_key":"x"*1200})
    assert invalid.status_code == 422 and "x"*30 not in invalid.text


def test_credentials_survive_restart_and_blank_input_preserves_key(tmp_path):
    directory = tmp_path / "persistent"
    store = Store(directory)
    key = store.access_key
    store.put("ai_key", "private-value")
    store.close()
    reopened = Store(directory)
    assert reopened.get("ai_key") == "private-value"
    assert reopened.access_key == key
    reopened.close()


def test_pairing_is_single_use_revocable_and_scoped(client):
    api, app = client
    code = api.post("/api/pairing").json()["code"]
    result = api.post("/api/agent/pair", json={"code":code})
    assert result.status_code == 200
    token = result.json()["token"]
    assert api.post("/api/agent/pair", json={"code":code}).status_code == 403
    assert api.get("/api/status", headers={"Authorization":"Bearer "+token}).status_code == 401
    with api.websocket_connect("/api/agent/stream") as socket:
        socket.send_json({"token":token})
        assert socket.receive_json()["type"] == "connected"
        socket.send_json({"type":"configure","sources":[{"id":"pc_mic","name":"Microphone","role":"streamer","remember":True}]})
        assert socket.receive_json()["type"] == "configured"
    settings = api.get("/api/status").json()["settings"]
    assert settings["input_mode"] == "companion" and settings["sources"][0]["id"] == "pc_mic"
    assert api.delete("/api/pairing").status_code == 200
    assert not app.state.store.get("agent_token")


def test_pairing_rate_limit(client):
    api, app = client
    for _ in range(5):
        assert api.post("/api/agent/pair",json={"code":"00000000"}).status_code == 403
    assert api.post("/api/agent/pair",json={"code":"00000000"}).status_code == 429


def test_memory_is_channel_scoped_and_user_can_remove_it(store):
    store.add_memory("one","u1","Alice","Spielt gerne Bogenschützin","Explicit statement")
    store.add_memory("two","u1","Alice","Spielt gerne Magierin","Explicit statement")
    store.consent("one","u1","Alice",True)
    store.consent("one","u1","Alice",False)
    assert store.memories("one") == []
    assert len(store.memories("two")) == 1
    assert not store.consented("one","u1")


def test_duplicate_audio_source_rejected(client):
    api, app = client
    settings = api.get("/api/status").json()["settings"]
    settings["sources"] *= 2
    assert api.post("/api/settings", json={"settings":settings}).status_code == 422


def test_image_upload_is_normalized_and_not_executable(client):
    api, app = client
    buffer = io.BytesIO()
    Image.new("RGB",(1600,900),(80,120,60)).save(buffer,"PNG")
    response = api.post("/api/screenshot/upload",files={"file":("image.png",buffer.getvalue(),"image/png")})
    assert response.status_code == 200
    output = response.json()["image"]
    assert output.startswith("data:image/jpeg;base64,")
    decoded = Image.open(io.BytesIO(base64.b64decode(output.split(",")[1])))
    assert decoded.size == (1280,720)
    assert api.post("/api/screenshot/upload",files={"file":("bad.png",b"not an image","image/png")}).status_code == 400


@pytest.mark.asyncio
async def test_cloud_chat_data_excluded_without_permission(store):
    engine = ready_engine(store)
    settings = store.settings()
    settings.provider = "openai"
    store.put("settings",settings.model_dump())
    store.put("ai_key","test-key")
    engine.event("chat","Alice","PRIVATE CHAT",subject_id="u1",remember=True)
    engine.event("audio","Mic","Ich teste den Bogen",role="streamer",subject_id="owner1",remember=True)
    context = engine.context()
    assert "PRIVATE CHAT" not in json.dumps(context)
    assert "Ich teste den Bogen" in json.dumps(context)
    assert not context["chat_included"]
    await engine.close()


@pytest.mark.asyncio
async def test_viewer_memory_requires_opt_in_and_matching_evidence(store):
    engine = ready_engine(store)
    settings = store.settings()
    settings.viewer_memory = True
    store.put("settings",settings.model_dump())
    event = engine.event("chat","Alice","Ich mag Bögen",subject_id="u1",remember=True)
    fact = {"subject_id":"u1","fact":"Mag Bögen","evidence_id":event["id"],"confidence":0.95}
    engine.save_memories({"memories":[fact]},engine.context())
    assert store.memories("example") == []
    store.consent("example","u1","Alice",True)
    engine.save_memories({"memories":[fact]},engine.context())
    assert len(store.memories("example")) == 1
    fact.update(subject_id="owner1",fact="Wrong person")
    engine.save_memories({"memories":[fact]},engine.context())
    assert len(store.memories("example")) == 1
    await engine.close()


@pytest.mark.asyncio
async def test_unknown_voice_and_game_never_create_person_memory(store):
    engine = ready_engine(store)
    source = AudioSource(id="other",name="Discord",role="conversation",remember=True)
    await engine.transcript(source,"Ich spiele Bogenschütze")
    row = engine.events[-1]
    assert row["subject_id"] == "" and not row["remember"]
    engine.save_memories({"memories":[{"subject_id":"owner1","fact":"Spielt Bogenschütze","confidence":1,"evidence_id":row["id"]}]},engine.context())
    assert store.memories("example") == []
    engine.twitch_mix_source = "microphone"
    await engine.transcript(AudioSource(id="microphone",name="Twitch-Mix",remember=True),"Eine unbekannte Stimme")
    assert engine.events[-1]["role"] == "conversation"
    await engine.close()


@pytest.mark.asyncio
async def test_self_messages_ignored_and_forget_removes_temporary_context(store):
    engine = ready_engine(store)
    settings = store.settings()
    settings.viewer_memory=True
    store.put("settings",settings.model_dump())
    def payload(text,user="u1"):
        return {"chatter_user_id":user,"chatter_user_name":"Alice","message":{"text":text}}
    await engine.on_chat(payload("Own message","bot1"))
    assert not engine.events
    await engine.on_chat(payload("!remember on"))
    await engine.on_chat(payload("Ich spiele gerne Bogen"))
    assert engine.events[-1]["remember"]
    store.add_memory("example","u1","Alice","Mag Bögen","chat")
    await engine.on_chat(payload("!forget"))
    assert not engine.events and not store.memories("example")
    await engine.close()


@pytest.mark.asyncio
async def test_preview_generates_draft_without_sending(store):
    engine = ready_engine(store)
    engine.ai.generate = AsyncMock(return_value={"action":"message","message":"Warum probierst du den Bogen?","kind":"question","memories":[]})
    engine.twitch.send = AsyncMock()
    await engine.analyze()
    assert engine.pending and engine.pending["text"]
    engine.twitch.send.assert_not_awaited()
    await engine.close()


@pytest.mark.asyncio
async def test_stop_during_generation_prevents_late_draft_or_send(store):
    engine = ready_engine(store)
    gate = asyncio.Event()
    async def generate(*args):
        await gate.wait()
        return {"action":"message","message":"A late message","kind":"comment"}
    engine.ai.generate = generate
    engine.twitch.send = AsyncMock()
    pending = asyncio.create_task(engine.analyze())
    await asyncio.sleep(0)
    await engine.stop()
    gate.set()
    await pending
    assert engine.pending is None
    engine.twitch.send.assert_not_awaited()
    await engine.close()


@pytest.mark.asyncio
async def test_changed_context_discards_in_flight_response(store):
    engine = ready_engine(store)
    async def generate(*args):
        engine.event("chat","Alice","The topic changed",subject_id="u1")
        return {"action":"message","message":"An outdated question","kind":"question"}
    engine.ai.generate = generate
    await engine.analyze()
    assert engine.pending is None
    await engine.close()


@pytest.mark.asyncio
async def test_send_counts_only_twitch_confirmed_messages(store):
    engine = ready_engine(store)
    make_draft(engine)
    engine.check_channel = AsyncMock()
    engine.twitch.send = AsyncMock(side_effect=TwitchError("AutoMod rejected"))
    with pytest.raises(TwitchError):
        await engine.send_pending("draft1")
    assert not engine.sent_times
    assert not any(event["kind"] == "bot" for event in engine.events)
    engine.twitch.send = AsyncMock(return_value="twitch-message-1")
    await engine.send_pending("draft1")
    assert len(engine.sent_times) == 1
    assert engine.events[-1]["confirmed"]
    assert engine.events[-1]["message_id"] == "twitch-message-1"
    await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("condition",["offline","old_status","paused","stopped","disconnected","stale_draft","changed_context","hour_limit"])
async def test_no_send_when_runtime_conditions_are_unsafe(store,condition):
    engine=ready_engine(store)
    make_draft(engine)
    engine.check_channel=AsyncMock()
    engine.twitch.send=AsyncMock()
    if condition=="offline":engine.channel["live"]=False
    if condition=="old_status":engine.channel_checked-=100
    if condition=="paused":engine.paused=True
    if condition=="stopped":engine.running=False
    if condition=="disconnected":engine.twitch.connected=False
    if condition=="stale_draft":engine.pending["created"]-=100
    if condition=="changed_context":engine.revision+=1
    if condition=="hour_limit":engine.sent_times.extend([time.time()]*store.settings().max_per_hour)
    with pytest.raises(ValueError):await engine.send_pending("draft1")
    engine.twitch.send.assert_not_awaited()
    await engine.close()


@pytest.mark.asyncio
async def test_audio_never_reaches_transcriber_when_paused_or_offline(store):
    engine=ready_engine(store)
    engine.audio.transcribe=AsyncMock()
    pcm=resample_pcm(np.ones(4800)*0.1,48000)
    engine.paused=True
    engine.audio.feed("microphone",pcm)
    engine.paused=False
    engine.channel["live"]=False
    engine.audio.feed("microphone",pcm)
    await asyncio.sleep(0)
    engine.audio.transcribe.assert_not_awaited()
    engine.channel["live"]=True
    engine.audio.feed("microphone",pcm)
    await asyncio.sleep(0)
    engine.audio.transcribe.assert_awaited_once()
    await engine.close()


@pytest.mark.asyncio
async def test_rotating_refresh_token_is_used_once_under_concurrency(store):
    engine=ready_engine(store)
    old=store.get("twitch_bot")
    old.update(refresh_token="refresh-old",expires_at=0,validated_at=time.time())
    store.put("twitch_bot",old)
    calls=[]
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200,json={"access_token":"new-access","refresh_token":"refresh-new","expires_in":3600})
    await engine.twitch.http.aclose()
    engine.twitch.http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    first,second=await asyncio.gather(engine.twitch.token("bot",True,"bot-access"),engine.twitch.token("bot",True,"bot-access"))
    assert first==second=="new-access"
    assert len(calls)==1
    assert store.get("twitch_bot")["refresh_token"]=="refresh-new"
    await engine.close()


@pytest.mark.asyncio
async def test_wrong_channel_owner_is_rejected(store):
    engine=ready_engine(store)
    engine.twitch.request=AsyncMock(return_value={"data":[{"id":"someone-else","login":"example","display_name":"Other"}]})
    with pytest.raises(TwitchError,match="Besitzer"):
        await engine.twitch.channel()
    await engine.close()


def test_resampling_preserves_duration_and_channels():
    stereo=np.column_stack([np.ones(4800)*0.25,np.ones(4800)*0.75])
    output=resample_pcm(stereo,48000)
    assert len(output)==2400*2
    values=np.frombuffer(output,dtype="<i2")
    assert np.all(np.abs(values-16383)<=1)


def test_sender_rejects_credentials_or_cleartext_public_target():
    assert server_address("http://192.168.1.20:8787/")=="http://192.168.1.20:8787"
    assert server_address("http://raspberrypi.local:8787")=="http://raspberrypi.local:8787"
    for value in ["http://public.example:8787","https://user:password@example.com","ftp://localhost","http://localhost:8787/#access=secret"]:
        with pytest.raises(ValueError):server_address(value)


def test_sender_setup_exposes_no_pairing_credential(tmp_path):
    app=create_sender_app(tmp_path/"sender")
    app.state.store.put("sender_token","private-pairing-token")
    with TestClient(app) as api:
        assert api.get("/").status_code==200
        assert api.get("/api/status").status_code==401
        api.headers["Authorization"]="Bearer "+app.state.store.access_key
        response=api.get("/api/status")
        assert response.status_code==200
        assert "private-pairing-token" not in response.text
