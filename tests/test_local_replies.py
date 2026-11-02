import json

import httpx
import pytest

from test_mentions import chat
from test_workflows import ready_engine, store


@pytest.mark.asyncio
@pytest.mark.parametrize("reply,expected", [
    ("Alice [chat]: Bin da!", "Bin da!"),
    ("Milo: Bin da!", "Bin da!"),
    ("Timur sagt: Bin da!", "Timur sagt: Bin da!"),
])
async def test_input_speaker_captions_are_not_published_as_the_bot_author(store, reply, expected):
    engine = ready_engine(store)
    await engine.ai.http.aclose()
    engine.ai.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(404) if request.method == "GET" else httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})))
    try:
        await engine.on_chat(chat())
        result = await engine.ai.generate(engine.context())
        assert result["message"] == expected
    finally:
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("reply,action", [
    ("Stimmt, ich löse mich von der „Gaming-Rolle“ und bleibe beim Thema.", "message"),
    ("__STILL__", "skip"),
    ('{"message":"Bin da!"}', "message"),
])
async def test_local_replies_accept_natural_text_without_json_grammar_or_leaking_the_skip_marker(store, reply, action):
    engine = ready_engine(store)
    def answer(request):
        if request.method == "GET":
            return httpx.Response(404)
        payload = json.loads(request.content)
        assert "response_format" not in payload
        assert payload["messages"][-1]["role"] == "user"
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})
    await engine.ai.http.aclose()
    engine.ai.http = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    try:
        await engine.on_chat(chat())
        result = await engine.ai.generate(engine.context())
        assert result["action"] == action and result["memories"] == []
        assert "__STILL__" not in result["message"]
        if "Gaming-Rolle" in reply:
            assert result["message"] == reply
        if reply.startswith("{"):
            assert result["message"] == "Bin da!"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_model_default_threads_still_detect_ollama_and_disable_thinking(store):
    engine = ready_engine(store)
    calls = []
    def answer(request):
        calls.append(request.url.path)
        if request.method == "GET":
            return httpx.Response(200, json={"version": "0.40.13"})
        assert request.url.path == "/api/chat"
        payload = json.loads(request.content)
        assert payload["think"] is False and "format" not in payload
        assert "num_thread" not in payload["options"]
        return httpx.Response(200, json={"message": {"content": "Bin da!"}, "eval_count": 4})
    await engine.ai.http.aclose()
    engine.ai.http = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    try:
        await engine.on_chat(chat())
        for _ in range(2):
            result = await engine.ai.generate(engine.context())
            assert result["message"] == "Bin da!" and engine.ai.last_metrics["kind"] == "reply"
        assert calls == ["/api/version", "/api/chat", "/api/chat"]
    finally:
        await engine.close()
