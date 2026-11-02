import json
import time
from unittest.mock import AsyncMock

import httpx
import pytest

from companion.ai import LOCAL_REPLY_SYSTEM
from companion.local_context import compact_context
from companion.mentions import addressed_to_bot
from companion.twitch import TwitchError
from test_workflows import make_draft, ready_engine, store


BOT = {"id": "bot1", "login": "examplebot", "display_name": "ExampleBot"}
ANSWER = {"action": "message", "message": "Ja, ich bin ein KI-Charakter hier im Chat.", "kind": "comment", "memories": []}


def chat(text="@examplebot bist du eine KI?", message_id="incoming-1", user="u1", **extra):
    return {"chatter_user_id": user, "chatter_user_name": "Alice", "message_id": message_id, "message": {"text": text}, **extra}


@pytest.mark.parametrize("text,expected", [
    ("@EXAMPLEBOT bist du da?", "name"),
    ("Was meint ExampleBot dazu?", "name"),
    ("Milo hat das gerade gesagt.", "name"),
    ("@Milo?", "name"),
    ("examplebot123 sagt hallo", None),
    ("superexamplebot sagt hallo", None),
    ("Milow spielt mit", None),
    ("Er hat das gesagt.", None),
])
def test_explicit_names_match_without_substrings_or_guessing_pronouns(text, expected):
    assert addressed_to_bot(chat(text), BOT, "Milo") == expected


def test_twitch_mentions_and_replies_use_account_identity():
    assert addressed_to_bot(chat("Alles klar?", reply={"parent_user_id": "bot1"}), BOT, "Milo") == "reply"
    payload = chat("Alles klar?")
    payload["message"]["fragments"] = [{"mention": {"user_id": "bot1"}}]
    assert addressed_to_bot(payload, BOT, "Milo") == "mention"
    assert addressed_to_bot(chat("Alles klar?", reply={"parent_user_id": "other"}), BOT, "Milo") is None


@pytest.mark.asyncio
async def test_own_messages_are_ignored_and_target_survives_new_audio(store):
    engine = ready_engine(store)
    try:
        await engine.on_chat(chat(user="bot1"))
        assert not engine.events and not engine.reply_target()
        await engine.on_chat(chat())
        for index in range(20):
            engine.event("audio", "Mic", f"Aktuelles Spielgeschehen {index}", role="streamer")
        context = engine.context()
        assert context["reply_to"]["message_id"] == "incoming-1"
        assert context["reply_to"] in context["context"]
        assert len(compact_context(context, direct=True)["context"]) <= 6
        assert context["character"]["account_login"] == "examplebot"
    finally:
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", [None, "cooldown", "hour_limit", "paused", "offline", "disconnected"])
async def test_replies_bypass_open_question_and_speaking_but_keep_hard_limits(store, blocked):
    engine = ready_engine(store)
    store.put("settings", store.settings().model_copy(update={"min_interval": 90}).model_dump())
    engine.last_contribution = time.time() - 21
    engine.last_voice = time.time()
    engine.last_question = time.time() + 1
    try:
        assert not engine.can_contribute()[0]
        await engine.on_chat(chat())
        if blocked == "cooldown":
            engine.last_contribution = time.time() - 19
        elif blocked == "hour_limit":
            engine.sent_times.extend([time.time()] * store.settings().max_per_hour)
        elif blocked == "paused":
            engine.paused = True
        elif blocked == "offline":
            engine.channel["live"] = False
        elif blocked == "disconnected":
            engine.twitch.connected = False
        assert engine.can_contribute()[0] == (blocked is None)
    finally:
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,base", [("openai", "http://127.0.0.1:11434/v1"), ("compatible", "https://example.org/v1"), ("local", "https://example.org/v1")])
async def test_priority_cannot_leak_chat_to_external_models_without_permission(store, provider, base):
    engine = ready_engine(store)
    store.put("ai_key", "test-key")
    store.put("settings", store.settings().model_copy(update={"provider": provider, "api_base": base}).model_dump())
    try:
        await engine.on_chat(chat("@examplebot PRIVATE CHAT"))
        assert engine.reply_target() is None
        context = engine.context()
        assert context["reply_to"] is None
        assert "PRIVATE CHAT" not in json.dumps(context)
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_reply_is_linked_and_confirmed_once_then_next_person_can_receive_same_answer(store):
    engine = ready_engine(store)
    store.put("settings", store.settings().model_copy(update={"mode": "automatic"}).model_dump())
    engine.ai.generate = AsyncMock(return_value=ANSWER)
    engine.check_channel = AsyncMock()
    engine.twitch.send = AsyncMock(return_value="confirmed-1")
    try:
        await engine.on_chat(chat())
        await engine.on_chat(chat())
        assert len(engine.reply_requests) == 1
        await engine.analyze()
        engine.twitch.send.assert_awaited_once_with("owner1", ANSWER["message"], reply_parent_message_id="incoming-1")
        assert engine.reply_target() is None and engine.pending is None
        old = next(item for item in engine.context()["context"] if item.get("message_id") == "incoming-1")
        assert old["resolved"] and not old["addressed_to_bot"]
        await engine.on_chat(chat())
        assert engine.reply_target() is None
        assert len(engine.sent_times) == 1
        engine.last_contribution -= 21
        await engine.on_chat(chat(message_id="incoming-2", user="u2"))
        await engine.analyze()
        assert engine.twitch.send.await_count == 2
        assert engine.twitch.send.await_args.kwargs["reply_parent_message_id"] == "incoming-2"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_direct_reply_preview_does_not_send_and_expired_request_cannot_send(store):
    engine = ready_engine(store)
    engine.ai.generate = AsyncMock(return_value=ANSWER)
    engine.twitch.send = AsyncMock()
    try:
        await engine.on_chat(chat())
        await engine.analyze()
        assert engine.pending["reply_to"]["message_id"] == "incoming-1"
        engine.twitch.send.assert_not_awaited()
        engine.reply_requests[0]["time"] -= 181
        with pytest.raises(ValueError, match="nicht mehr aktuell"):
            await engine.send_pending(engine.pending["id"])
        engine.twitch.send.assert_not_awaited()
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_skip_finishes_one_request_and_keeps_next_in_order(store):
    engine = ready_engine(store)
    engine.ai.generate = AsyncMock(return_value={"action": "skip", "reason": "Spam", "memories": []})
    try:
        await engine.on_chat(chat())
        await engine.on_chat(chat(message_id="incoming-2", user="u2"))
        await engine.analyze()
        assert engine.reply_target()["message_id"] == "incoming-2"
        assert [item["message_id"] for item in engine.context()["context"]] == ["incoming-2"]
        await engine.analyze()
        assert engine.reply_target() is None
        assert engine.pending is None
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_forget_during_targeted_generation_removes_request_and_draft(store):
    engine = ready_engine(store)
    async def generate(*args):
        await engine.on_chat(chat("!forget", message_id="forget-1"))
        return ANSWER
    engine.ai.generate = generate
    try:
        await engine.on_chat(chat())
        await engine.analyze()
        assert engine.reply_target() is None and engine.pending is None
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_mention_preempts_ordinary_draft_and_in_flight_general_response(store):
    engine = ready_engine(store)
    make_draft(engine)
    try:
        await engine.on_chat(chat())
        assert engine.pending is None
        engine.finish_reply(engine.reply_target())
        async def generate(*args):
            await engine.on_chat(chat(message_id="incoming-2"))
            return ANSWER
        engine.ai.generate = generate
        await engine.analyze()
        assert engine.pending is None
        assert engine.reply_target()["message_id"] == "incoming-2"
        assert engine.logs[0]["title"] == "Ansprache hat Vorrang"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_twitch_reply_body_and_rejection_are_checked(store):
    engine = ready_engine(store)
    engine.twitch.request = AsyncMock(return_value={"data": [{"is_sent": True, "message_id": "confirmed"}]})
    try:
        assert await engine.twitch.send("owner1", "Antwort", reply_parent_message_id="incoming-1") == "confirmed"
        assert engine.twitch.request.await_args.kwargs["json"]["reply_parent_message_id"] == "incoming-1"
        engine.twitch.request.return_value = {"data": [{"is_sent": False, "drop_reason": {"message": "Rejected"}}]}
        with pytest.raises(TwitchError):
            await engine.twitch.send("owner1", "Antwort", reply_parent_message_id="incoming-1")
    finally:
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("threads", [0, 2])
async def test_reply_instruction_reaches_both_local_transports(store, threads):
    engine = ready_engine(store)
    store.put("settings", store.settings().model_copy(update={"local_ai_cpu_threads": threads}).model_dump())
    observed = []
    def answer(request):
        observed.append(json.loads(request.content))
        content = json.dumps(ANSWER)
        return httpx.Response(200, json={"message": {"content": content}, "choices": [{"message": {"content": content}}]})
    await engine.ai.http.aclose()
    engine.ai.http = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    try:
        await engine.on_chat(chat())
        await engine.ai.generate(engine.context())
        assert engine.ai.last_metrics["kind"] == "reply"
        assert engine.ai.last_metrics["seconds"] >= 0
        assert engine.ai.last_metrics["input_characters"] > 0
        assert observed[0]["messages"][0]["content"].startswith(LOCAL_REPLY_SYSTEM)
        assert observed[0]["messages"][-1]["role"] == "user"
        if threads:
            assert observed[0]["think"] is False
        assert "format" not in observed[0] and "response_format" not in observed[0]
    finally:
        await engine.close()
