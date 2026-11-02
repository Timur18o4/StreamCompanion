import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from companion.audio import AudioState, resample_pcm
from companion.models import AudioSource
from companion.local_context import compact_context
from companion.streaming_asr import ContinuousTranscript, create_recognizer
from test_workflows import ready_engine, store, client
from test_mentions import ANSWER
from test_mentions import chat


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["Milo", "Mailo", "Malo", "Melo"])
async def test_configured_spoken_names_keep_original_transcript_and_target_the_character(store, name):
    engine = ready_engine(store)
    store.put("settings", store.settings().model_copy(update={"spoken_name_aliases": "Mailo, Malo, Melo"}).model_dump())
    try:
        text = f"{name}, bist du da?"
        await engine.transcript(store.settings().sources[0], text, final=True)
        target = engine.reply_target()
        assert target and target["text"] == text
        assert compact_context(engine.context(), direct=True)["reply_to"]["addressed_to_character"] == "Milo"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_spoken_name_variants_do_not_change_chat_matching(store):
    engine = ready_engine(store)
    store.put("settings", store.settings().model_copy(update={"spoken_name_aliases": "Mailo, Malo, Melo"}).model_dump())
    try:
        await engine.on_chat(chat("Mailo, bist du da?"))
        assert engine.reply_target() is None
    finally:
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["conversation", "game"])
async def test_spoken_name_aliases_cannot_attribute_other_audio_to_streamer(store, role):
    engine = ready_engine(store)
    store.put("settings", store.settings().model_copy(update={"spoken_name_aliases": "Melo"}).model_dump())
    try:
        await engine.transcript(AudioSource(id="other", name="Other", role=role), "Melo, bist du da?", final=True)
        assert engine.reply_target() is None
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_unconfigured_similar_names_are_not_guessed(store):
    engine = ready_engine(store)
    try:
        await engine.transcript(store.settings().sources[0], "Mailo, bist du da?", final=True)
        assert engine.reply_target() is None
    finally:
        await engine.close()


class FakeStream:
    def __init__(self):
        self.ready = False
        self.text = ""
        self.sizes = []
        self.options = {}
    def set_option(self, key, value):
        self.options[key] = value
    def accept_waveform(self, rate, samples):
        self.sizes.append(len(samples))
        self.ready = len(samples) != 12800


class FakeRecognizer:
    def __init__(self):
        self.texts = iter(["Fort", "Fortnite", "Fortnite ist", "Fortnite ist gut."])
    def create_stream(self):
        return FakeStream()
    def is_ready(self, stream):
        return stream.ready
    def decode_stream(self, stream):
        stream.text = next(self.texts, stream.text)
        stream.ready = False
    def get_result(self, stream):
        return stream.text
    def is_endpoint(self, stream):
        return True


def test_streaming_waits_for_stable_words_and_does_not_cut_ongoing_voice():
    session = ContinuousTranscript(FakeRecognizer())
    assert session.stream.sizes == [12800] and session.stream.options["language"] == "de"
    voice = np.ones(1600, dtype=np.float32) * .1
    assert session.accept(voice)["stable"] == ""
    assert session.accept(voice)["stable"] == ""
    assert session.accept(voice)["stable"] == "Fortnite"
    assert session.accept(voice)["final"] is None
    for _ in range(14):
        result = session.accept(np.zeros(1600, dtype=np.float32))
        if result["final"]:
            break
    assert result["final"] == "Fortnite ist gut."


def test_model_must_exist_locally_instead_of_silently_downloading(tmp_path):
    with pytest.raises(ValueError, match="Ordner"):
        create_recognizer(str(tmp_path), 2)


def test_streaming_settings_use_the_detected_thread_limit(client, monkeypatch):
    monkeypatch.setattr("companion.models.os.cpu_count", lambda: 3)
    api, app = client
    settings = api.get("/api/status").json()["settings"]
    settings.update(stt_provider="local_streaming", streaming_model="C:/local/model", streaming_cpu_threads=99)
    assert api.post("/api/settings", json={"settings": settings}).status_code == 200
    result = api.get("/api/status").json()["settings"]
    assert result["stt_provider"] == "local_streaming" and result["streaming_cpu_threads"] == 3


@pytest.mark.asyncio
async def test_partial_updates_replace_one_observation_and_cannot_change_an_in_flight_snapshot(store):
    engine = ready_engine(store)
    source = store.settings().sources[0]
    try:
        await engine.transcript(source, "Ich spiele", utterance_id="one", final=False)
        snapshot = engine.context()
        original = engine.events[0]["id"]
        await engine.transcript(source, "Ich spiele Hades.", utterance_id="one", final=True)
        assert len(engine.events) == 1 and engine.events[0]["id"] == original
        assert engine.events[0]["remember"] and not engine.events[0]["provisional"]
        assert snapshot["context"][0]["text"] == "Ich spiele" and not snapshot["context"][0]["remember"]
        assert snapshot["context"][0]["provisional"]
        assert engine.events[0]["version"] == 2
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_final_spoken_address_uses_the_reply_path_and_defers_owner_memory(store):
    engine = ready_engine(store)
    store.put("settings", store.settings().model_copy(update={"mode": "automatic"}).model_dump())
    engine.ai.generate = AsyncMock(return_value=ANSWER)
    engine.check_channel = AsyncMock()
    engine.twitch.send = AsyncMock(return_value="confirmed-spoken")
    try:
        await engine.transcript(store.settings().sources[0], "Milo, ich spiele am liebsten Hades.", utterance_id="spoken", final=False)
        assert engine.reply_target() is None
        await engine.transcript(store.settings().sources[0], "Milo, ich spiele am liebsten Hades.", utterance_id="spoken", final=True)
        target = engine.reply_target()
        assert target["address_kind"] == "audio_name" and target["speaker_name"] == "Example"
        await engine.analyze()
        engine.twitch.send.assert_awaited_once_with("owner1", ANSWER["message"])
        assert engine.events[-1]["response_seconds"] is not None
        assert len(engine.memory_backlog) == 1 and engine.memory_backlog[0]["owner"]
        evidence = engine.memory_backlog[0]["context"]["context"][0]
        engine.ai.generate.return_value = {"action": "skip", "memories": [{"subject_id": "owner1", "evidence_id": evidence["id"], "fact": "Beispielnotiz.", "confidence": .99}]}
        await engine.process_memory()
        assert store.memories("example")[0]["subject_id"] == "owner1"
    finally:
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["conversation", "game"])
async def test_other_audio_roles_do_not_impersonate_the_streamer(store, role):
    engine = ready_engine(store)
    try:
        source = AudioSource(id="other", name="Andere Stimme", role=role, remember=True)
        await engine.transcript(source, "Milo, bist du da?", utterance_id="other", final=True)
        assert engine.reply_target() is None
        assert not engine.events[0]["remember"] and not engine.events[0]["subject_id"]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_provisional_audio_is_never_saved_as_a_fact(store):
    engine = ready_engine(store)
    try:
        row = engine.event("audio", "Mic", "Noch unvollstaendig", subject_id="owner1", role="streamer", remember=True, provisional=True)
        engine.save_memories({"memories": [{"subject_id": "owner1", "evidence_id": row["id"], "fact": "Eine ungesicherte Notiz.", "confidence": .99}]}, engine.context())
        assert not store.memories("example")
    finally:
        await engine.close()


def test_pause_discards_pending_streaming_audio_and_temporary_text(client):
    api, app = client
    engine = app.state.engine
    engine.event("audio", "Mic", "Vorlaeufig", provisional=True)
    state = AudioState(streaming=True, partial_text="Vorlaeufig", queued_bytes=4800)
    state.queue.put_nowait(bytes(4800))
    engine.audio.states["microphone"] = state
    assert api.post("/api/run/pause").status_code == 200
    assert not engine.events and state.queue.empty() and not state.partial_text and state.queued_bytes == 0


@pytest.mark.asyncio
async def test_pause_during_native_inference_cannot_publish_old_text_after_resume(store, monkeypatch):
    engine = ready_engine(store)
    store.put("settings", store.settings().model_copy(update={"stt_provider": "local_streaming", "streaming_model": "test", "streaming_cpu_threads": 2}).model_dump())
    entered, release = threading.Event(), threading.Event()
    initialized = []
    class Session:
        def __init__(self, model):
            initialized.append(self)
        def accept(self, samples):
            if len(initialized) == 1:
                entered.set()
                assert release.wait(5)
                return {"partial": "Alt", "stable": "Alt", "final": None}
            return {"partial": "Neu", "stable": "Neu", "final": None}
    monkeypatch.setattr("companion.streaming_asr.create_recognizer", lambda *args: object())
    monkeypatch.setattr("companion.streaming_asr.ContinuousTranscript", Session)
    try:
        packet = resample_pcm(np.ones(2400) * .1, 24000)
        engine.audio.feed("microphone", packet)
        assert await asyncio.to_thread(entered.wait, 2)
        engine.paused = True
        engine.epoch += 1
        engine.clear_provisional()
        release.set()
        await asyncio.sleep(.05)
        assert not engine.events
        engine.paused = False
        engine.epoch += 1
        engine.audio.feed("microphone", packet)
        for _ in range(100):
            if engine.events:
                break
            await asyncio.sleep(.01)
        assert engine.events[0]["text"] == "Neu" and len(initialized) == 2
        assert engine.audio.states["microphone"].dropped == 0
    finally:
        release.set()
        await engine.close()


@pytest.mark.asyncio
async def test_cancel_keeps_the_native_model_locked_until_its_worker_finishes(store):
    engine = ready_engine(store)
    entered, release = threading.Event(), threading.Event()
    def native():
        entered.set()
        assert release.wait(3)
    async def locked_call():
        async with engine.audio.streaming_lock:
            await engine.audio.run_streaming_native(native)
    task = asyncio.create_task(locked_call())
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done() and engine.audio.streaming_lock.locked()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert not engine.audio.streaming_lock.locked()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await engine.close()
