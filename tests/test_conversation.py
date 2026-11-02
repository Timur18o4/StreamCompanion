import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from companion.conversation import conversation_followup, memory_candidate
from companion.local_context import compact_context, dialogue_messages
from companion.models import AudioSource
from test_mentions import ANSWER, chat
from test_workflows import ready_engine, store


async def confirmed_exchange(engine, text="@examplebot bist du da?", user="u1"):
    engine.store.put("settings", engine.store.settings().model_copy(update={"mode": "automatic"}).model_dump())
    engine.ai.generate = AsyncMock(return_value=ANSWER)
    engine.check_channel = AsyncMock()
    engine.twitch.send = AsyncMock(return_value="confirmed-first")
    await engine.on_chat(chat(text, user=user))
    await engine.analyze()


@pytest.mark.asyncio
async def test_chat_continuation_keeps_both_sides_of_the_prior_exchange(store):
    engine = ready_engine(store)
    try:
        await confirmed_exchange(engine)
        await engine.on_chat(chat("Und was meinst du genau damit?", message_id="followup"))
        assert engine.reply_target()["address_kind"] == "chat_followup"
        projected = compact_context(engine.context(), direct=True)
        previous = [row["text"] for row in projected["context"]]
        assert "@examplebot bist du da?" in previous
        assert ANSWER["message"] in previous
        assert projected["reply_to"]["text"] == "Und was meinst du genau damit?"
        assert previous.count("Und was meinst du genau damit?") == 0
        engine.last_contribution -= 21
        await engine.analyze()
        assert engine.twitch.send.await_args.kwargs["reply_parent_message_id"] == "followup"
    finally:
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", ["other_viewer", "expired", "acknowledgment"])
async def test_unrelated_chat_does_not_become_a_direct_followup(store, condition):
    engine = ready_engine(store)
    try:
        await confirmed_exchange(engine)
        if condition == "expired":
            engine.events[-1]["time"] -= 100
        await engine.on_chat(chat("Okay danke" if condition == "acknowledgment" else "Und warum?", message_id="next", user="u2" if condition == "other_viewer" else "u1"))
        assert engine.reply_target() is None
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_forget_removes_associated_bot_replies_and_followup_context(store):
    engine = ready_engine(store)
    try:
        await confirmed_exchange(engine)
        await engine.on_chat(chat("!forget", message_id="forget"))
        assert not any(row.get("subject_id") == "u1" or row.get("conversation_subject") == "u1" for row in engine.events)
        await engine.on_chat(chat("Und warum?", message_id="new"))
        assert engine.reply_target() is None
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_spoken_followup_uses_same_streamer_but_not_another_audio_role(store):
    engine = ready_engine(store)
    engine.event("bot", "Milo", "Welche Waffe meinst du?", confirmed=True, conversation_subject="owner1", conversation_question=True)
    try:
        await engine.transcript(AudioSource(id="game", name="Game", role="game"), "Die blaue Waffe.")
        assert engine.reply_target() is None
        await engine.transcript(store.settings().sources[0], "Die blaue Waffe.")
        assert engine.reply_target()["address_kind"] == "audio_followup"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_streamer_can_follow_up_on_a_general_comment_without_repeating_the_bot_name(store):
    engine = ready_engine(store)
    store.put("settings", store.settings().model_copy(update={"mode": "automatic"}).model_dump())
    engine.ai.generate = AsyncMock(return_value={**ANSWER, "message": "Die Falle kassiert heute Stammkundenbonus."})
    engine.check_channel = AsyncMock()
    engine.twitch.send = AsyncMock(return_value="confirmed-comment")
    try:
        await engine.analyze()
        assert not engine.events[-1]["conversation_question"]
        await engine.transcript(store.settings().sources[0], "Warum denn?")
        assert engine.reply_target()["address_kind"] == "audio_followup"
    finally:
        await engine.close()


@pytest.mark.parametrize("text,expected", [
    ("Milo, bist du da?", False),
    ("Milo, was hältst du davon, wenn ich jetzt Fortnite spiele", False),
    ("Ich könnte morgen Fortnite spielen.", False),
    ("Vielleicht mag ich Hades.", False),
    ("Ich glaube, dass ich gestern Minecraft gespielt habe.", False),
    ("Wahrscheinlich spiele ich morgen Hades.", False),
    ("Ich vermute, dass mein erstes Spiel Minecraft war.", False),
    ("I think my favourite game is Hades.", False),
    ("What is my favourite game", False),
    ("Weißt du noch, welches Spiel ich mag", False),
    ("Ich spiele am liebsten Hades.", True),
    ("Mein Lieblingsspiel ist Hades.", True),
    ("Heute probiere ich den Bogen.", True),
    ("I like playing Hades.", True),
    ("Mein erstes Spiel war Minecraft.", True),
    ("Milo wie hieß mein allererstes Spiel im Stream", False),
    ("Okay Milo wie hieß mein allererstes Spiel im Stream", False),
])
def test_only_explicit_declarations_are_candidates_for_memory(text, expected):
    assert memory_candidate(text) is expected


@pytest.mark.asyncio
async def test_a_question_cannot_start_memory_work_or_be_stored_as_a_fact(store):
    engine = ready_engine(store)
    try:
        await engine.transcript(store.settings().sources[0], "Milo, was hältst du davon, wenn ich jetzt Fortnite spiele")
        context = engine.context()
        engine.defer_memory(context)
        assert not engine.memory_backlog
        target = engine.reply_target()
        engine.save_memories({"memories": [{"subject_id": "owner1", "evidence_id": target["id"], "fact": "Spielt Fortnite.", "confidence": 1}]}, context)
        assert not store.memories("example")
    finally:
        await engine.close()


def test_followup_requires_confirmed_delivery():
    row = {"kind": "bot", "time": time.time(), "conversation_subject": "u1"}
    assert not conversation_followup([row], "u1", "Und warum?")
    row["confirmed"] = True
    assert conversation_followup([row], "u1", "Und warum?")


@pytest.mark.asyncio
async def test_personal_viewer_question_does_not_receive_the_owners_preferences(store):
    engine = ready_engine(store)
    store.put("settings", store.settings().model_copy(update={"viewer_memory": True}).model_dump())
    store.consent("example", "u1", "Nora", True)
    store.add_memory("example", "owner1", "Mikrofon", "Mag Hades.", "Ich mag Hades.")
    store.add_memory("example", "u1", "Nora", "Mag Stardew Valley.", "Ich mag Stardew Valley.")
    try:
        await engine.on_chat(chat("@examplebot Welches Spiel mag ich?"))
        notes = compact_context(engine.context(), direct=True)["memories"]
        assert [note["fact"] for note in notes] == ["Mag Stardew Valley."]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_explicitly_named_owner_can_be_recalled_without_using_the_audio_device_as_a_name(store):
    engine = ready_engine(store)
    store.add_memory("example", "owner1", "Mikrofon", "Mag Hades.", "Ich mag Hades.")
    try:
        await engine.on_chat(chat("@examplebot Welches Spiel mag Example?"))
        notes = compact_context(engine.context(), direct=True)["memories"]
        assert notes[0]["name"] == "Example" and notes[0]["fact"] == "Mag Hades."
        assert store.memories("example")[0]["subject_name"] == "Mikrofon"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_new_audio_memory_is_saved_under_the_verified_streamer_name(store):
    engine = ready_engine(store)
    try:
        await engine.transcript(store.settings().sources[0], "Ich mag Hades.")
        evidence = engine.events[-1]
        engine.save_memories({"memories": [{"subject_id": "owner1", "fact": "Mag Hades.", "evidence_id": evidence["id"], "confidence": 1}]}, engine.context())
        assert store.memories("example")[0]["subject_name"] == "Example"
    finally:
        await engine.close()


def test_dialogue_uses_assistant_turns_and_only_one_current_question():
    question = "Und warum ist das so?"
    context = {"character": {"name": "Milo", "account_login": "examplebot"}, "max_characters": 340,
               "context": [{"source": "chat", "name": "Nora", "text": "Was meinst du?"},
                           {"source": "bot", "name": "Milo", "text": "Ich meinte den Bogen."}],
               "reply_to": {"name": "Nora", "source": "chat", "text": question}}
    messages = dialogue_messages(context, "Anweisung")
    assert messages[0]["role"] == "system"
    assert [message["role"] for message in messages if message["role"] != "system"] == ["user", "assistant", "user"]
    assert messages[-1]["content"] == "Nora [chat]: " + question
    assert sum(message["content"].count(question) for message in messages) == 1
    assert [message["content"] for message in messages if message["role"] == "assistant"] == ["Ich meinte den Bogen."]


def test_existing_memory_carries_its_unclear_source_instead_of_only_a_confident_fact():
    context = {"context": [], "memories": [{"fact": "Spielt Fortnite.", "subject_name": "Timur",
                 "source": "2026-11-02 · Was hältst du davon, wenn ich Fortnite spiele"}]}
    note = compact_context(context)["memories"][0]
    assert note["evidence_unclear"] and "wenn ich" in note["evidence"]
    assert "2026-11-02" not in note["evidence"]


@pytest.mark.asyncio
async def test_long_reply_ends_at_a_sentence_instead_of_cutting_off_a_word(store):
    engine = ready_engine(store)
    store.put("settings", store.settings().model_copy(update={"max_message_length": 90}).model_dump())
    try:
        first = "Das ist eine vollständige längere Aussage zum heutigen Spiel."
        assert engine.clean_message(first + " Danach folgt noch eine sehr lange Erklärung über verschiedene andere Themen.") == first
        result = engine.clean_message("Eine " + "ausführlichere Erklärung " * 10)
        assert len(result) <= 90 and result.endswith("…") and not result.endswith(" …")
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_clipped_question_does_not_wait_for_an_answer_to_an_unpublished_question(store):
    engine = ready_engine(store)
    store.put("settings", store.settings().model_copy(update={"max_message_length": 90, "mode": "automatic"}).model_dump())
    first = "Das ist eine vollständige längere Aussage zum heutigen Spiel."
    engine.ai.generate = AsyncMock(return_value={**ANSWER, "message": first + " Welche von den vielen möglichen Waffen möchtest du als Nächstes ausprobieren?", "kind": "question"})
    engine.check_channel = AsyncMock()
    engine.twitch.send = AsyncMock(return_value="confirmed")
    try:
        await engine.on_chat(chat())
        await engine.analyze()
        assert engine.twitch.send.await_args.args[1] == first
        assert not engine.last_question
        assert not engine.events[-1]["conversation_question"]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_spoken_addendum_cancels_the_in_flight_answer_and_keeps_the_full_question(store):
    engine = ready_engine(store)
    entered, canceled = asyncio.Event(), asyncio.Event()
    async def slow_answer(context, image=None):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            canceled.set()
    engine.ai.generate = slow_answer
    task = None
    try:
        await engine.transcript(store.settings().sources[0], "Milo, welches Spiel passt dazu?", utterance_id="question")
        task = asyncio.create_task(engine.analyze())
        await asyncio.wait_for(entered.wait(), 2)
        await engine.transcript(store.settings().sources[0], "Ich meine ein Spiel mit einem Bogen.", utterance_id="addition")
        await asyncio.wait_for(task, 2)
        assert canceled.is_set() and engine.pending is None
        target = engine.reply_target()
        assert target["utterance_id"] == "addition"
        assert "welches Spiel passt" in target["text"] and "mit einem Bogen" in target["text"]
        assert not target["remember"]
        engine.ai.generate = AsyncMock(return_value=ANSWER)
        await engine.analyze()
        assert engine.pending["reply_to"]["id"] == target["id"]
        raw = next(row for row in engine.events if row.get("utterance_id") == "addition")
        assert raw["text"] == "Ich meine ein Spiel mit einem Bogen."
    finally:
        if task and not task.done():
            task.cancel()
        await engine.close()


@pytest.mark.asyncio
async def test_chat_correction_replies_to_latest_message_without_losing_the_first_question(store):
    engine = ready_engine(store)
    try:
        await engine.on_chat(chat("@examplebot Welches Spiel meinst du?"))
        await engine.on_chat(chat("Ich meinte das mit dem Bogen.", message_id="correction"))
        target = engine.reply_target()
        assert target["message_id"] == "correction" and target["address_kind"] == "chat_continuation"
        assert "Welches Spiel" in target["text"] and "mit dem Bogen" in target["text"]
        assert len(engine.reply_requests) == 1
        await engine.on_chat(chat("Das meinte ich auch.", user="u2", message_id="other"))
        assert engine.reply_target()["message_id"] == "correction"
        engine.store.put("settings", engine.store.settings().model_copy(update={"mode": "automatic"}).model_dump())
        engine.ai.generate = AsyncMock(return_value=ANSWER)
        engine.check_channel = AsyncMock()
        engine.twitch.send = AsyncMock(return_value="combined-confirmed")
        await engine.analyze()
        await engine.on_chat(chat("Und was meinst du jetzt?", message_id="followup"))
        context = compact_context(engine.context(), direct=True)
        assert any("Welches Spiel meinst du" in row["text"] for row in context["context"])
        assert any("Ich meinte das mit dem Bogen" in row["text"] for row in context["context"])
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_chat_address_preempts_audio_answer_and_audio_waits_for_finished_speech(store):
    engine = ready_engine(store)
    try:
        await engine.transcript(store.settings().sources[0], "Milo, bist du da?", utterance_id="spoken")
        engine.last_voice = time.time()
        assert not engine.can_contribute()[0]
        await engine.on_chat(chat())
        assert engine.reply_target()["kind"] == "chat"
        assert engine.can_contribute()[0]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_audio_answer_waits_for_provisional_continuation_even_after_a_pause(store):
    engine = ready_engine(store)
    try:
        await engine.transcript(store.settings().sources[0], "Milo, welches Spiel meinst du?", utterance_id="spoken")
        await engine.transcript(store.settings().sources[0], "Ich meine", utterance_id="continuation", final=False)
        engine.last_voice = time.time() - 3
        assert not engine.can_contribute()[0]
        await engine.transcript(store.settings().sources[0], "Ich meine das mit dem Bogen.", utterance_id="continuation", final=True)
        assert engine.reply_target()["address_kind"] == "audio_continuation"
        assert engine.can_contribute()[0]
    finally:
        await engine.close()
