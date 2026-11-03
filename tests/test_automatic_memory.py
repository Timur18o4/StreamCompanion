import time
from unittest.mock import AsyncMock

import pytest

from companion.storage import Store
from test_workflows import ready_engine, store
from test_mentions import chat


def automatic(store):
    store.put("settings", store.settings().model_copy(update={"viewer_memory": True, "viewer_memory_mode": "automatic"}).model_dump())


@pytest.mark.asyncio
async def test_unaddressed_chat_is_checked_and_saved_without_a_public_reply(store):
    engine = ready_engine(store)
    automatic(store)
    try:
        await engine.on_chat(chat("Ich spiele am liebsten Hades.", message_id="fact"))
        assert engine.reply_target() is None
        assert len(engine.memory_backlog) == 1
        event = engine.events[-1]
        assert event["remember"]
        engine.ai.generate = AsyncMock(return_value={"memories": [{"subject_id": "u1", "evidence_id": event["id"], "fact": "Spielt am liebsten Hades.", "confidence": .99}]})
        engine.last_analysis = time.time()
        await engine.process_memory()
        notes = store.memories("example")
        assert len(notes) == 1 and notes[0]["subject_id"] == "u1"
        assert "Ich spiele am liebsten Hades." in notes[0]["source"]
        assert not engine.pending and not engine.reply_requests
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_new_fact_is_queued_even_while_someone_else_is_the_reply_target(store):
    engine = ready_engine(store)
    automatic(store)
    try:
        await engine.on_chat(chat("@examplebot bist du da?", user="other", message_id="question"))
        await engine.on_chat(chat("Ich baue gerade eine Farm in Minecraft.", message_id="fact"))
        assert engine.reply_target()["subject_id"] == "other"
        assert len(engine.memory_backlog) == 1
        assert engine.memory_backlog[0]["subject_id"] == "u1"
        engine.ai.generate = AsyncMock()
        await engine.process_memory()
        engine.ai.generate.assert_not_awaited()
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_automatic_memory_respects_opt_out_and_can_be_reenabled(store):
    engine = ready_engine(store)
    automatic(store)
    try:
        await engine.on_chat(chat("Ich spiele am liebsten Hades.", message_id="fact"))
        stale_context = engine.memory_backlog[0]["context"]
        event = stale_context["context"][0]
        await engine.on_chat(chat("!forget", message_id="forget"))
        assert not engine.memory_backlog
        engine.save_memories({"memories": [{"subject_id": "u1", "evidence_id": event["id"], "fact": "Mag Hades.", "confidence": 1}]}, stale_context)
        assert not store.memories("example")
        await engine.on_chat(chat("Ich spiele am liebsten Hades.", message_id="ignored"))
        assert not engine.events[-1]["remember"] and not engine.memory_backlog
        await engine.on_chat(chat("!remember on", message_id="allow"))
        await engine.on_chat(chat("Ich spiele am liebsten Hades.", message_id="allowed"))
        assert engine.events[-1]["remember"] and len(engine.memory_backlog) == 1
    finally:
        await engine.close()


def test_opt_out_and_notes_survive_reopening_and_stay_channel_scoped(tmp_path):
    root = tmp_path / "data"
    first = Store(root)
    automatic(first)
    first.consent("example", "u1", "Alice", False)
    first.add_memory("example", "u2", "Nora", "Mag Hades.", "Beleg")
    first.close()
    second = Store(root)
    try:
        assert not second.viewer_memory_allowed("example", "u1")
        assert second.viewer_memory_allowed("other-channel", "u1")
        assert second.viewer_memory_allowed("example", "new-viewer")
        assert not second.viewer_memory_allowed("example", "")
        assert len(second.memories("example")) == 1
        assert not second.memories("other-channel")
        second.consent("example", "u2", "Nora", True)
        second.forget_channel("example")
        assert not second.memories("example")
        assert not second.viewer_memory_allowed("example", "u1")
        assert not second.consented("example", "u2")
        second.put("settings", second.settings().model_copy(update={"viewer_memory": False}).model_dump())
        assert not second.viewer_memory_allowed("example", "new-viewer")
    finally:
        second.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Welches Spiel mag ich?", "Vielleicht spiele ich morgen Hades.", '"Ich spiele am liebsten Hades."'])
async def test_automatic_memory_does_not_queue_questions_speculation_or_quotes(store, text):
    engine = ready_engine(store)
    automatic(store)
    try:
        await engine.on_chat(chat(text))
        assert not engine.memory_backlog
    finally:
        await engine.close()
