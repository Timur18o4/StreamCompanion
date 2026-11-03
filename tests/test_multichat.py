import asyncio
from datetime import datetime, timedelta, timezone
import json
import time
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from companion.models import Settings
from companion.multichat import MultiChat, normalize_message
from test_workflows import ready_engine, store, make_draft


def packet(platform="twitch", **extra):
    return {"platform": platform, "username": "Alice", "userId": "u1", "id": "msg1", "message": "Ich spiele am liebsten Hades.", **extra}


@pytest.mark.parametrize("kind", ["system", "stream_status", "follow", "subscription", "delete", "ping"])
def test_non_chat_events_are_not_conversation(kind):
    assert normalize_message(packet(event=kind)) is None


def test_platform_scoped_identities_and_native_reply_ids():
    twitch = normalize_message(packet())
    youtube = normalize_message(packet("youtube"))
    assert twitch["chatter_user_id"] == "u1"
    assert youtube["chatter_user_id"] == "youtube:u1"
    assert twitch["message_id"] != youtube["message_id"]
    assert twitch["source_message_id"] == "msg1"
    assert youtube["source_message_id"] is None
    assert not normalize_message(packet(userId=""))["memory_identity_verified"]


@pytest.mark.parametrize("timestamp", [time.time() - 500, (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(), time.time() + 500])
def test_old_or_future_replayed_messages_are_rejected(timestamp):
    assert normalize_message(packet(timestamp=timestamp)) is None


def test_timestamp_with_timezone_and_multiline_message():
    stamp = datetime.now(timezone(timedelta(hours=2))).isoformat()
    result = normalize_message(packet(timestamp=stamp, message="Hallo\nWelt"))
    assert result["message"]["text"] == "Hallo\nWelt"


class EventStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b": keepalive\n\ndata: {broken}\n\n"
        data = ("data: " + json.dumps(packet()) + "\n\n").encode()
        yield data[:40]
        yield data[40:] + data
        await asyncio.Future()


@pytest.mark.asyncio
async def test_sse_connection_filters_bad_frames_and_deduplicates(store):
    bridge = MultiChat(store, AsyncMock())
    await bridge.http.aclose()
    bridge.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=EventStream())))
    messages = []
    received = asyncio.Event()
    async def callback(message):
        messages.append(message)
        received.set()
    task = asyncio.create_task(bridge.listen(callback))
    try:
        await asyncio.wait_for(received.wait(), 2)
        await asyncio.sleep(.01)
        assert bridge.connected and len(messages) == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert not bridge.connected
        await bridge.close()


@pytest.mark.asyncio
async def test_multichat_person_memories_do_not_merge_platforms_or_unverified_names(store):
    engine = ready_engine(store)
    store.put("settings", store.settings().model_copy(update={"chat_source": "multichat", "viewer_memory": True, "viewer_memory_mode": "automatic"}).model_dump())
    engine.multichat.connected = True
    try:
        for data in [packet(), packet("youtube"), packet("kick", userId="")]:
            await engine.on_chat(normalize_message(data))
        assert [e["subject_id"] for e in engine.events] == ["u1", "youtube:u1", "kick:name:alice"]
        assert [e["remember"] for e in engine.events] == [True, True, False]
        assert len(engine.memory_backlog) == 2
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_multichat_filters_bot_echo_by_twitch_account_name(store):
    engine = ready_engine(store)
    try:
        await engine.on_chat(normalize_message(packet(username="ExampleBot", userId="")))
        assert not engine.events
        await engine.on_chat(normalize_message(packet("youtube", username="ExampleBot", userId="different")))
        assert len(engine.events) == 1
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_multichat_channel_metadata_uses_bot_token_without_owner(store):
    engine = ready_engine(store)
    store.put("settings", store.settings().model_copy(update={"chat_source": "multichat"}).model_dump())
    store.delete("twitch_owner")
    engine.twitch.request = AsyncMock(side_effect=[{"data": [{"id": "owner1", "display_name": "Example", "login": "example"}]}, {"data": [{"game_name": "Hades"}]}, {"data": []}])
    try:
        channel = await engine.twitch.channel()
        assert channel["id"] == "owner1" and channel["category"] == "Hades"
        assert all(call.kwargs["role"] == "bot" for call in engine.twitch.request.await_args_list)
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_speech_multichat_starts_without_any_twitch_login(store):
    engine = ready_engine(store)
    engine.running = False
    store.put("settings", store.settings().model_copy(update={"chat_source": "multichat", "output_mode": "speech", "stt_provider": "local_whisper"}).model_dump())
    store.delete("twitch_owner")
    store.delete("twitch_bot")
    engine.twitch.token = AsyncMock(side_effect=AssertionError("OAuth must not be needed"))
    engine.twitch.channel = AsyncMock(side_effect=AssertionError("Twitch metadata must not be needed"))
    engine.speech.voices = AsyncMock(return_value={"engines": {"windows": True}, "voices": []})
    engine.begin_model_warmup = Mock()
    async def listen(callback):
        engine.multichat.connected = True
        await asyncio.Future()
    engine.multichat.listen = listen
    try:
        await engine.start()
        await asyncio.sleep(.01)
        assert engine.running and engine.channel_ready()
        engine.twitch.token.assert_not_awaited()
        assert engine.channel["id"] == "local:example"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_cross_platform_reply_never_passes_non_twitch_id_to_twitch(store):
    engine = ready_engine(store)
    try:
        row = engine.event("chat", "Alice", "@examplebot bist du da?", subject_id="youtube:u1", platform="youtube", message_id="youtube:m1", addressed_to_bot=True)
        await engine.schedule_reply(row)
        make_draft(engine)
        draft = engine.pending
        draft["reply_to"] = row
        engine.check_channel = AsyncMock()
        engine.twitch.send = AsyncMock(return_value="sent")
        await engine.send_pending(draft["id"])
        assert not engine.twitch.send.call_args.kwargs
    finally:
        await engine.close()


def test_speech_only_owner_memories_survive_later_channel_id_resolution(store):
    store.add_memory("example", "local:example", "Example", "Mag Hades.", "Aussage")
    store.add_memory("other", "local:example", "Other", "Andere Notiz.", "Aussage")
    store.resolve_local_owner("example", "owner1", "Example")
    assert store.memories("example")[0]["subject_id"] == "owner1"
    assert store.memories("other")[0]["subject_id"] == "local:example"


def test_bridge_twitch_millisecond_timestamp_string_is_accepted():
    assert normalize_message(packet(timestamp=str(int(time.time() * 1000)))) is not None
    assert normalize_message(packet(timestamp=str(int((time.time() - 500) * 1000)))) is None
