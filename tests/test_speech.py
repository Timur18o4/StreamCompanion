import asyncio
from pathlib import Path
import threading
from unittest.mock import AsyncMock, Mock
import wave

import numpy as np
import pytest

from companion.models import Settings
from companion.speech import Speech, output_device
from test_workflows import ready_engine, store, make_draft, client


def test_output_device_is_resolved_by_name_and_host_after_renumbering(monkeypatch):
    monkeypatch.setattr("companion.speech.output_devices", lambda: [{"index": 7, "name": "VoiceMeeter Input", "host": "Windows WASAPI"}, {"index": 2, "name": "VoiceMeeter Input", "host": "MME"}])
    settings = Settings(tts_device=3, tts_device_name="VoiceMeeter Input", tts_device_host="Windows WASAPI")
    assert output_device(settings) == 7
    settings.tts_device_name = "Missing"
    with pytest.raises(ValueError, match="fehlt"):
        output_device(settings)


def test_playback_routes_to_selected_device_and_aborts_between_blocks(store, tmp_path, monkeypatch):
    import sounddevice as sd
    path = tmp_path / "test.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(np.ones(16000, dtype="<i2").tobytes())
    speech = Speech(store)
    cancel = threading.Event()
    stream = Mock()
    stream.__enter__ = Mock(return_value=stream)
    stream.__exit__ = Mock(return_value=False)
    stream.write.side_effect = lambda data: cancel.set()
    factory = Mock(return_value=stream)
    monkeypatch.setattr(sd, "OutputStream", factory)
    monkeypatch.setattr(sd, "query_devices", lambda *a: {"default_samplerate": 48000, "max_output_channels": 2})
    monkeypatch.setattr("companion.speech.output_device", lambda settings: 17)
    assert not speech.play(path, Settings(tts_volume=.5), cancel)
    assert factory.call_args.kwargs["device"] == 17
    assert factory.call_args.kwargs["samplerate"] == 48000
    assert stream.write.call_args.args[0].shape[1] == 2
    stream.abort.assert_called_once()
    assert not speech.speaking


@pytest.mark.asyncio
async def test_stale_speech_is_rejected_after_synthesis_before_playback(store):
    speech = Speech(store)
    speech.synthesize = AsyncMock()
    speech.play = Mock()
    with pytest.raises(ValueError, match="verworfen"):
        await speech.say("Eine Antwort.", guard=lambda: False)
    speech.play.assert_not_called()
    assert speech.task is None


@pytest.mark.asyncio
async def test_speech_output_confirms_local_playback_and_never_sends_chat(store):
    engine = ready_engine(store)
    store.put("settings", store.settings().model_copy(update={"output_mode": "speech"}).model_dump())
    engine.check_channel = AsyncMock()
    engine.speech.say = AsyncMock()
    engine.twitch.send = AsyncMock()
    make_draft(engine)
    try:
        await engine.send_pending("draft1")
        engine.speech.say.assert_awaited_once()
        engine.twitch.send.assert_not_awaited()
        assert engine.events[-1]["delivery"] == "speech" and engine.events[-1]["confirmed"]
        assert engine.pending is None and len(engine.sent_times) == 1
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_failed_speech_does_not_fall_back_to_chat_or_claim_success(store):
    engine = ready_engine(store)
    store.put("settings", store.settings().model_copy(update={"output_mode": "speech"}).model_dump())
    engine.check_channel = AsyncMock()
    engine.speech.say = AsyncMock(side_effect=ValueError("Stimme fehlt"))
    engine.twitch.send = AsyncMock()
    make_draft(engine)
    try:
        with pytest.raises(ValueError, match="Stimme fehlt"):
            await engine.send_pending("draft1")
        engine.twitch.send.assert_not_awaited()
        assert not engine.sent_times and not [e for e in engine.events if e["kind"] == "bot"]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_own_speech_audio_is_not_fed_back_into_transcription(store):
    engine = ready_engine(store)
    engine.speech.speaking = True
    try:
        engine.audio.feed("microphone", b"\x01\x00" * 400)
        assert not engine.audio.states and not engine.events
    finally:
        engine.speech.speaking = False
        await engine.close()


def test_speech_test_requires_stopped_companion_and_never_posts_chat(client):
    api, app = client
    app.state.engine.running = True
    app.state.engine.speech.say = AsyncMock()
    response = api.post("/api/tts/test")
    assert response.status_code == 400
    app.state.engine.speech.say.assert_not_awaited()
    app.state.engine.running = False


@pytest.mark.parametrize("url", ["https://example.com/events", "http://192.168.1.2:8765/events", "http://user:pass@localhost:8765/events", "http://localhost:8765/events?token=secret"])
def test_multichat_endpoint_is_local_and_has_no_embedded_credentials(url):
    with pytest.raises(ValueError):
        Settings(multichat_url=url)
