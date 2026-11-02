import asyncio
import time
from unittest.mock import AsyncMock

import numpy as np
import pytest

from companion.audio import resample_pcm
from test_mentions import ANSWER
from test_workflows import ready_engine, store, client


@pytest.mark.asyncio
@pytest.mark.parametrize("case,allowed", [
    ("enabled", True), ("default", False), ("companion", False),
    ("twitch", False), ("paused", False), ("stopped", False),
    ("old_status", False), ("unknown_channel", False),
])
async def test_offline_audio_and_contributions_require_an_explicit_local_test(store, case, allowed):
    engine = ready_engine(store)
    settings = store.settings().model_copy(update={"offline_test": case != "default", "input_mode": "local"})
    if case in {"companion", "twitch"}:
        settings.input_mode = case
    store.put("settings", settings.model_dump())
    engine.channel["live"] = False
    engine.audio.transcribe = AsyncMock()
    if case == "paused":
        engine.paused = True
    elif case == "stopped":
        engine.running = False
    elif case == "old_status":
        engine.channel_checked -= 100
    elif case == "unknown_channel":
        engine.channel.clear()
    try:
        assert engine.can_contribute()[0] is allowed
        engine.audio.feed("microphone", resample_pcm(np.ones(2400) * .1, 24000))
        await asyncio.sleep(0)
        assert engine.audio.transcribe.await_count == int(allowed)
        assert not engine.channel.get("live")
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_offline_spoken_question_is_confirmed_in_chat_without_claiming_to_be_live(store):
    engine = ready_engine(store)
    store.put("settings", store.settings().model_copy(update={"offline_test": True, "input_mode": "local", "mode": "automatic"}).model_dump())
    engine.channel["live"] = False
    engine.ai.generate = AsyncMock(return_value=ANSWER)
    engine.check_channel = AsyncMock()
    engine.twitch.send = AsyncMock(return_value="offline-confirmed")
    try:
        await engine.transcript(store.settings().sources[0], "Milo, bist du da?", utterance_id="offline", final=True)
        await engine.analyze()
        engine.check_channel.assert_awaited_once()
        engine.twitch.send.assert_awaited_once_with("owner1", ANSWER["message"])
        snapshot = engine.snapshot()
        assert snapshot["offline_test"] and not snapshot["channel"]["live"]
        assert engine.events[-1]["confirmed"] and engine.events[-1]["message_id"] == "offline-confirmed"
        assert engine.context()["channel"]["category"] is None
        assert "Offline-Test" in engine.context()["channel"]["title"]
    finally:
        await engine.close()


def test_changing_offline_test_stops_capture_and_cancels_an_old_draft(client):
    api, app = client
    engine = app.state.engine
    engine.running = True
    engine.pending = {"id": "old"}
    settings = api.get("/api/status").json()["settings"]
    assert not settings["offline_test"]
    settings["offline_test"] = True
    assert api.post("/api/settings", json={"settings": settings}).status_code == 200
    assert not engine.running and engine.pending is None
    assert api.get("/api/status").json()["settings"]["offline_test"]


@pytest.mark.asyncio
async def test_twitch_stream_input_still_needs_a_live_broadcast(store):
    engine = ready_engine(store)
    engine.running = False
    engine.channel["live"] = False
    store.put("settings", store.settings().model_copy(update={"offline_test": True, "input_mode": "twitch", "stt_provider": "local_whisper"}).model_dump())
    engine.check_channel = AsyncMock()
    engine.twitch.token = AsyncMock(return_value="test-access")
    engine.audio.start_twitch = AsyncMock()
    try:
        with pytest.raises(ValueError, match="live"):
            await engine.start()
        engine.audio.start_twitch.assert_not_awaited()
        assert not engine.running
    finally:
        await engine.close()
