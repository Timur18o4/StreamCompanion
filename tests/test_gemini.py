import asyncio
import base64
from contextlib import asynccontextmanager
import json
from unittest.mock import patch

import httpx
import pytest

from companion.audio import AudioState
from companion.gemini import generate, live_connection
from companion.models import AudioSource
from test_workflows import store, ready_engine


@pytest.mark.asyncio
async def test_live_connection_test_reaches_google_instead_of_consuming_an_audio_proposal(store):
    engine = ready_engine(store)
    store.put("gemini_key", "test-secret")
    store.put("settings", store.settings().model_copy(update={
        "provider": "gemini", "stt_provider": "gemini_live", "text_model": "gemini-3.8-flash"
    }).model_dump())
    requests = []
    def response(request):
        requests.append(request)
        return httpx.Response(200, json={"candidates": [{
            "finishReason": "STOP", "content": {"parts": [{"text": json.dumps({
                "action": "skip", "message": "", "memories": []
            })}]}
        }]})
    await engine.ai.http.aclose()
    engine.ai.http = httpx.AsyncClient(transport=httpx.MockTransport(response))
    try:
        assert (await engine.ai.test())["ok"]
        assert len(requests) == 1 and requests[0].url.host == "generativelanguage.googleapis.com"
    finally:
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,finish,valid", [(200, "STOP", True), (200, "MAX_TOKENS", False), (429, None, False), (0, None, False)])
async def test_gemini_decisions_require_completed_output_and_report_limits(store, status, finish, valid):
    engine = ready_engine(store)
    store.put("settings", store.settings().model_copy(update={"provider": "gemini", "text_model": "gemini-3.8-flash"}).model_dump())
    def response(request):
        if status == 0:
            raise httpx.ReadTimeout("simulated provider timeout", request=request)
        assert request.headers["x-goog-api-key"] == "test-secret"
        assert "test-secret" not in str(request.url)
        return httpx.Response(status, json={"candidates": [{
            "finishReason": finish, "content": {"parts": [{"text": json.dumps({
                "action": "message", "message": "Bin da!", "reason": "Antwort.", "memories": []
            })}]}
        }]})
    await engine.ai.http.aclose()
    engine.ai.http = httpx.AsyncClient(transport=httpx.MockTransport(response))
    try:
        if valid:
            assert (await generate(engine.ai, {}, None, "test-secret"))["message"] == "Bin da!"
        else:
            with pytest.raises(RuntimeError):
                await generate(engine.ai, {}, None, "test-secret")
    finally:
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_live_pcm_and_completed_tool_proposal_do_not_use_stt_or_send(store, cancelled):
    engine = ready_engine(store)
    store.put("gemini_key", "test-secret")
    store.put("settings", store.settings().model_copy(update={
        "provider": "gemini", "stt_provider": "gemini_live", "cloud_chat_permission": True
    }).model_dump())
    state = AudioState()
    source = AudioSource(id="microphone", name="Mic")
    sent = []
    finished = asyncio.Event()

    class Socket:
        async def send(self, value):
            sent.append(json.loads(value))

        async def recv(self):
            return '{"setupComplete":{}}'

        async def messages(self):
            yield json.dumps({"toolCall": {"functionCalls": [{
                "id": "one", "name": "community_reply",
                "args": {"message": "Bin da!", "observation": "Blanko wurde gefragt.", "direct_reply": True}
            }]}})
            assert not engine.audio.gemini_proposals
            if cancelled:
                yield '{"toolCallCancellation":{"ids":["one"]}}'
            yield '{"serverContent":{"turnComplete":true}}'
            finished.set()
            await asyncio.Event().wait()

        def __aiter__(self):
            return self.messages()

    @asynccontextmanager
    async def connect(url, **kwargs):
        assert kwargs["additional_headers"]["x-goog-api-key"] == "test-secret"
        assert "test-secret" not in url
        yield Socket()

    try:
        with patch("companion.gemini.websockets.connect", connect):
            task = asyncio.create_task(live_connection(engine.audio, source, state, [None]))
            await asyncio.wait_for(finished.wait(), 2)
            await state.queue.put(bytes(4800))
            for _ in range(20):
                if any("realtimeInput" in item for item in sent):
                    break
                await asyncio.sleep(.01)
            audio = next(item["realtimeInput"]["audio"] for item in sent if "realtimeInput" in item)
            assert len(base64.b64decode(audio["data"])) == 3200
            assert bool(engine.audio.gemini_proposals) is not cancelled
            assert bool(engine.reply_target()) is not cancelled
            assert not engine.sent_times and engine.audio.whisper is None
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    finally:
        await engine.close()
