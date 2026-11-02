import asyncio
import json
import time
from unittest.mock import AsyncMock

import httpx
import pytest

from companion.ai import AIError
from companion.local_context import compact_context
from test_workflows import ready_engine, store
from test_mentions import ANSWER, chat


@pytest.mark.asyncio
async def test_direct_model_context_is_bounded_and_keeps_identity_and_existing_memory(store):
    engine = ready_engine(store)
    try:
        store.add_memory("example", "owner1", "Example", "Mag den Bogen.", "Beispiel")
        await engine.on_chat(chat("@examplebot Was mag Example am Bogen?"))
        for index in range(20):
            engine.event("audio", "Mic", f"Aussage {index}: " + "Neuer Spielkontext. " * 30, role="streamer")
        full = engine.context()
        compact = compact_context(full, direct=True)
        assert len(compact["context"]) <= 6
        assert sum(len(row["text"]) for row in compact["context"]) + len(compact["reply_to"]["text"]) <= 2400
        assert json.dumps(compact).count("@examplebot Was mag Example am Bogen?") == 1
        assert compact["character"]["account_login"] == "examplebot"
        assert compact["memories"][0]["fact"] == "Mag den Bogen."
        assert compact["memories"][0]["date"]
        assert "message_id" not in json.dumps(compact) and "now" not in compact
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_general_compaction_keeps_evidence_for_permitted_memory_only(store):
    engine = ready_engine(store)
    try:
        allowed = engine.event("audio", "Mic", "Ich probiere den Bogen.", role="streamer", subject_id="owner1", remember=True)
        engine.event("chat", "Alice", "Ich mag Hades.", subject_id="u1", remember=False)
        compact = compact_context(engine.context())
        assert compact["context"][0]["id"] == allowed["id"]
        assert compact["context"][0]["subject_id"] == "owner1"
        assert "id" not in compact["context"][1] and "subject_id" not in compact["context"][1]
    finally:
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("message,action,kind", [("Ja, bin da.", "message", "comment"), ("Welche Runde meinst du?", "message", "question"), ("", "skip", "comment")])
async def test_short_reply_format_is_normalized_without_model_generated_notes(store, message, action, kind):
    engine = ready_engine(store)
    def respond(request):
        payload = json.loads(request.content)
        assert "response_format" not in payload
        assert payload["max_tokens"] == 256
        return httpx.Response(200, json={"choices": [{"message": {"content": message}}]})
    await engine.ai.http.aclose()
    engine.ai.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        await engine.on_chat(chat())
        result = await engine.ai.generate(engine.context())
        assert (result["action"], result["kind"], result["memories"]) == (action, kind, [])
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_invalid_short_reply_cannot_be_used_as_chat_text(store):
    engine = ready_engine(store)
    await engine.ai.http.aclose()
    engine.ai.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": '{"message": 7}'}}]})))
    try:
        await engine.on_chat(chat())
        with pytest.raises(AIError):
            await engine.ai.generate(engine.context())
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_mention_cancels_actual_in_flight_task_and_next_reply_starts_immediately(store):
    engine = ready_engine(store)
    started, canceled = asyncio.Event(), asyncio.Event()
    async def generate(context, image=None):
        if context.get("reply_to"):
            return ANSWER
        started.set()
        try:
            await asyncio.Future()
        finally:
            canceled.set()
    engine.ai.generate = generate
    task = asyncio.create_task(engine.analyze())
    try:
        await asyncio.wait_for(started.wait(), 1)
        await engine.on_chat(chat())
        await asyncio.wait_for(task, 1)
        assert canceled.is_set() and not engine.analysis_lock.locked()
        assert engine.last_analysis == 0 and not engine.pending
        await engine.analyze()
        assert engine.pending["reply_to"]["message_id"] == "incoming-1"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await engine.close()


@pytest.mark.asyncio
async def test_parent_cancellation_is_not_mistaken_for_priority_preemption(store):
    engine = ready_engine(store)
    started = asyncio.Event()
    async def generate(*args):
        started.set()
        await asyncio.Future()
    engine.ai.generate = generate
    task = asyncio.create_task(engine.analyze())
    try:
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not engine.analysis_lock.locked() and engine.generation_task is None
    finally:
        await engine.close()


async def prepared_memory(engine, store):
    store.put("settings", store.settings().model_copy(update={"viewer_memory": True}).model_dump())
    store.consent("example", "u1", "Alice", True)
    await engine.on_chat(chat("@examplebot ich spiele am liebsten Hades."))
    engine.defer_memory(engine.context())
    engine.finish_reply(engine.reply_target())
    # Memory runs in the cooldown after the conversation attempt has finished.
    engine.last_analysis = time.time()


@pytest.mark.asyncio
async def test_consented_chat_memory_is_checked_after_the_reply_and_saved_with_its_evidence(store):
    engine = ready_engine(store)
    try:
        await prepared_memory(engine, store)
        evidence = engine.memory_backlog[0]["context"]["context"][0]
        def respond(request):
            payload = json.loads(request.content)
            assert set(payload["response_format"]["json_schema"]["schema"]["properties"]) == {"memories"}
            observed = json.loads(payload["messages"][-1]["content"][0]["text"])
            assert observed["context"][0]["id"] == evidence["id"]
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"memories": [{"subject_id": "u1", "fact": "Spielt am liebsten Hades.", "evidence_id": evidence["id"], "confidence": .99}]})}}]})
        await engine.ai.http.aclose()
        engine.ai.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        await engine.process_memory()
        assert len(store.memories("example")) == 1 and not engine.memory_backlog
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_memory_opt_out_removes_deferred_work_before_any_model_call(store):
    engine = ready_engine(store)
    try:
        await prepared_memory(engine, store)
        await engine.on_chat(chat("!forget", message_id="forget"))
        engine.ai.generate = AsyncMock()
        await engine.process_memory()
        engine.ai.generate.assert_not_awaited()
        assert not engine.memory_backlog and not store.memories("example")
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_new_reply_preempts_memory_work_and_keeps_it_for_later(store):
    engine = ready_engine(store)
    started = asyncio.Event()
    async def generate(*args):
        started.set()
        await asyncio.Future()
    task = None
    try:
        await prepared_memory(engine, store)
        engine.ai.generate = generate
        task = asyncio.create_task(engine.process_memory())
        await asyncio.wait_for(started.wait(), 1)
        await engine.on_chat(chat(message_id="incoming-2", user="u2"))
        await asyncio.wait_for(task, 1)
        assert len(engine.memory_backlog) == 1 and engine.reply_target()["message_id"] == "incoming-2"
        assert not store.memories("example")
    finally:
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await engine.close()
