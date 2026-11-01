import asyncio
import base64
import importlib.util
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .ai import AIError
from .audio import devices, normalize_image, obs_screenshot
from .engine import Engine
from .models import AudioSource, Settings
from .storage import Store
from .twitch import TwitchError


class SettingsInput(BaseModel):
    settings: Settings
    ai_key: str | None = Field(default=None, max_length=1000)
    stt_key: str | None = Field(default=None, max_length=1000)
    obs_password: str | None = Field(default=None, max_length=1000)
    twitch_client_secret: str | None = Field(default=None, max_length=1000)
    clear_secrets: list[str] = Field(default_factory=list)


class DraftInput(BaseModel):
    id: str


class MemoryInput(BaseModel):
    fact: str = Field(min_length=3, max_length=600)


class PairInput(BaseModel):
    code: str = Field(min_length=8, max_length=8)


class ModeInput(BaseModel):
    mode: str


def create_app(data_dir: Path | None = None):
    root = Path(__file__).resolve().parent.parent
    store = Store(data_dir or root / "data")
    engine = Engine(store)
    pairing = {}
    attempts = {}

    @asynccontextmanager
    async def lifespan(app):
        yield
        await engine.close()
        store.close()

    app = FastAPI(title="Stream Companion", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.engine = engine

    async def auth(authorization: str = Header(default="")):
        if not secrets.compare_digest(authorization, "Bearer " + store.access_key):
            raise HTTPException(401, "Bitte den lokalen Zugangscode eingeben.")

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.url.path.startswith("/api/") and origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Fremde Browserherkunft ist nicht zugelassen."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://static-cdn.jtvnw.net; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"detail": "Bitte die Eingaben prüfen.", "fields": [{"field": ".".join(str(item) for item in error["loc"]), "message": error["msg"]} for error in exc.errors()]}, status_code=422)

    async def known_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    for error in (ValueError, TwitchError, AIError):
        app.add_exception_handler(error, known_error)

    @app.get("/api/health")
    async def health():
        return {"app": "stream-companion", "version": __version__, "ready": True}

    @app.get("/api/status", dependencies=[Depends(auth)])
    async def status():
        settings = store.settings()
        return {"version": __version__, "settings": settings.model_dump(), "profiles": store.profiles(), "oauth": engine.twitch.pending, "secret_status": {name: bool(store.get(name)) for name in ("ai_key", "stt_key", "obs_password", "twitch_client_secret")}, "runtime": engine.snapshot(), "capabilities": {name: importlib.util.find_spec(name) is not None for name in ("sounddevice", "faster_whisper", "mss", "streamlink")}}

    @app.post("/api/settings", dependencies=[Depends(auth)])
    async def save_settings(payload: SettingsInput):
        old = store.settings()
        changed_identity = old.twitch_client_id != payload.settings.twitch_client_id
        structural = ("channel", "sources", "input_mode", "stt_provider", "provider", "api_base", "text_model", "transcription_model")
        secrets_changed = any(getattr(payload, name) for name in ("ai_key", "stt_key", "obs_password", "twitch_client_secret")) or bool(payload.clear_secrets)
        if engine.running and (secrets_changed or any(getattr(old, key) != getattr(payload.settings, key) for key in structural)):
            await engine.stop()
        if changed_identity:
            for role in ("owner", "bot"):
                await engine.twitch.cancel_auth(role)
                store.delete("twitch_" + role)
        store.put("settings", payload.settings.model_dump())
        for name in ("ai_key", "stt_key", "obs_password", "twitch_client_secret"):
            value = getattr(payload, name)
            if value:
                store.put(name, value.strip())
            if name in payload.clear_secrets:
                store.delete(name)
        if old.channel != payload.settings.channel:
            engine.events.clear()
            engine.channel = {}
            engine.pending = None
            engine.screenshot = None
        await engine.notify("success", "Einrichtung gespeichert", "Die Einstellungen wurden übernommen.")
        return {"ok": True}

    @app.post("/api/twitch/{role}/connect", dependencies=[Depends(auth)])
    async def connect(role: str):
        if role not in engine.twitch.SCOPES:
            raise HTTPException(404)
        if engine.running:
            await engine.stop()
        return await engine.twitch.device_start(role)

    @app.post("/api/twitch/{role}/cancel", dependencies=[Depends(auth)])
    async def cancel(role: str):
        if role not in engine.twitch.SCOPES:
            raise HTTPException(404)
        await engine.twitch.cancel_auth(role)
        return {"ok": True}

    @app.post("/api/twitch/{role}/disconnect", dependencies=[Depends(auth)])
    async def disconnect(role: str):
        if role not in engine.twitch.SCOPES:
            raise HTTPException(404)
        await engine.stop()
        await engine.twitch.logout(role)
        return {"ok": True}

    @app.post("/api/twitch/check", dependencies=[Depends(auth)])
    async def check():
        return await engine.check_channel()

    @app.post("/api/run/start", dependencies=[Depends(auth)])
    async def start():
        await engine.start()
        return {"ok": True}

    @app.post("/api/run/stop", dependencies=[Depends(auth)])
    async def stop():
        await engine.stop()
        return {"ok": True}

    @app.post("/api/shutdown", dependencies=[Depends(auth)])
    async def shutdown():
        await engine.stop()
        callback = getattr(app.state, "shutdown_callback", None)
        if callback:
            asyncio.get_running_loop().call_later(0.3, callback)
        return {"ok": True, "app": "stream-companion"}

    @app.post("/api/run/pause", dependencies=[Depends(auth)])
    async def pause():
        engine.paused = not engine.paused
        engine.pending = None
        engine.epoch += 1
        engine.last_voice = time.time()
        return {"paused": engine.paused}

    @app.post("/api/run/mode", dependencies=[Depends(auth)])
    async def mode(payload: ModeInput):
        if payload.mode not in {"preview", "automatic"}:
            raise ValueError("Unbekannte Betriebsart.")
        settings = store.settings()
        settings.mode = payload.mode
        store.put("settings", settings.model_dump())
        engine.pending = None
        engine.epoch += 1
        return {"ok": True}

    @app.post("/api/run/analyze", dependencies=[Depends(auth)])
    async def analyze():
        await engine.analyze()
        return {"ok": True}

    @app.post("/api/draft/send", dependencies=[Depends(auth)])
    async def send_draft(payload: DraftInput):
        await engine.send_pending(payload.id)
        return {"ok": True}

    @app.post("/api/draft/discard", dependencies=[Depends(auth)])
    async def discard(payload: DraftInput):
        if engine.pending and engine.pending["id"] == payload.id:
            engine.pending = None
            engine.last_analysis = time.time()
        return {"ok": True}

    @app.post("/api/demo", dependencies=[Depends(auth)])
    async def demo():
        await engine.demo_start()
        return {"ok": True}

    @app.get("/api/ai/models", dependencies=[Depends(auth)])
    async def ai_models():
        return {"models": await engine.ai.models()}

    @app.post("/api/ai/test", dependencies=[Depends(auth)])
    async def ai_test():
        return await engine.ai.test()

    @app.get("/api/audio/devices", dependencies=[Depends(auth)])
    async def audio_devices():
        return {"devices": await asyncio.to_thread(devices)}

    @app.post("/api/screenshot/obs", dependencies=[Depends(auth)])
    async def screenshot_obs():
        settings = store.settings()
        engine.screenshot = await obs_screenshot(settings.obs_url, store.get("obs_password", ""))
        engine.screenshot_time = time.time()
        return {"image": engine.screenshot}

    @app.post("/api/screenshot/upload", dependencies=[Depends(auth)])
    async def screenshot_upload(file: UploadFile = File()):
        data = await file.read(8 * 1024 * 1024 + 1)
        engine.screenshot = await asyncio.to_thread(normalize_image, data)
        engine.screenshot_time = time.time()
        return {"image": engine.screenshot}

    @app.get("/api/memories", dependencies=[Depends(auth)])
    async def memories():
        return {"memories": store.memories(store.settings().channel)}

    @app.post("/api/memories", dependencies=[Depends(auth)])
    async def memory_add(payload: MemoryInput):
        settings = store.settings()
        if not settings.channel:
            raise ValueError("Bitte zuerst einen Kanal einrichten, damit die Notiz korrekt zugeordnet wird.")
        owner = store.profiles()["owner"] or {}
        store.add_memory(settings.channel, owner.get("id", "owner"), owner.get("display_name", "Streamer"), payload.fact, "Vom Betreiber eingetragen")
        return {"ok": True}

    @app.delete("/api/memories/{memory_id}", dependencies=[Depends(auth)])
    async def memory_delete(memory_id: int):
        store.remove_memory(memory_id, store.settings().channel)
        return {"ok": True}

    @app.delete("/api/memories", dependencies=[Depends(auth)])
    async def memory_reset():
        store.forget_channel(store.settings().channel)
        engine.events.clear()
        engine.pending = None
        engine.last_observation = 0
        engine.epoch += 1
        return {"ok": True}

    @app.post("/api/pairing", dependencies=[Depends(auth)])
    async def pairing_start():
        pairing.clear()
        code = f"{secrets.randbelow(100_000_000):08d}"
        pairing.update(code=code, expires=time.time() + 300)
        return {"code": code, "expires": pairing["expires"]}

    @app.post("/api/agent/pair")
    async def agent_pair(payload: PairInput, request: Request):
        address = request.client.host if request.client else "unknown"
        now = time.time()
        attempts[address] = [value for value in attempts.get(address, []) if now - value < 60]
        if len(attempts[address]) >= 5:
            raise HTTPException(429, "Zu viele Versuche. Bitte eine Minute warten.")
        attempts[address].append(now)
        if pairing.get("expires", 0) < now or not secrets.compare_digest(payload.code, pairing.get("code", "")):
            raise HTTPException(403, "Der Verbindungscode ist ungültig oder abgelaufen.")
        token = secrets.token_urlsafe(32)
        store.put("agent_token", token)
        pairing.clear()
        return {"token": token}

    @app.delete("/api/pairing", dependencies=[Depends(auth)])
    async def pairing_revoke():
        store.delete("agent_token")
        pairing.clear()
        if engine.running and store.settings().input_mode == "companion":
            await engine.stop()
        return {"ok": True}

    @app.websocket("/api/agent/stream")
    async def agent_stream(socket: WebSocket):
        await socket.accept()
        try:
            initial = await asyncio.wait_for(socket.receive_json(), 8)
            token = initial.get("token", "")
            expected = store.get("agent_token", "")
            if not expected or not isinstance(token, str) or not secrets.compare_digest(token, expected):
                await socket.close(4401)
                return
            await socket.send_json({"type": "connected", "running": engine.running})
            while True:
                if not secrets.compare_digest(token, store.get("agent_token", "")):
                    await socket.close(4401)
                    return
                message = await socket.receive_json()
                kind = message.get("type")
                if kind == "configure":
                    if engine.running:
                        await socket.send_json({"type": "error", "message": "Begleiter vor dem Ändern der Quellen stoppen."})
                        continue
                    sources = [AudioSource.model_validate(item) for item in message.get("sources", [])]
                    settings = store.settings()
                    settings = Settings.model_validate({**settings.model_dump(), "sources": [item.model_dump() for item in sources], "input_mode": "companion"})
                    store.put("settings", settings.model_dump())
                    await socket.send_json({"type": "configured"})
                elif kind == "audio" and store.settings().input_mode == "companion":
                    encoded = message.get("pcm", "")
                    if isinstance(encoded, str) and len(encoded) <= 128_000:
                        pcm = base64.b64decode(encoded, validate=True)
                        engine.audio.feed(message.get("source", ""), pcm)
                elif kind == "image" and store.settings().input_mode == "companion" and store.settings().screenshots_enabled and store.settings().screenshot_source == "companion" and engine.running and not engine.paused and engine.channel.get("live"):
                    encoded = message.get("image", "")
                    if isinstance(encoded, str) and len(encoded) <= 4_000_000:
                        engine.screenshot = await asyncio.to_thread(normalize_image, base64.b64decode(encoded, validate=True))
                        engine.screenshot_time = time.time()
                elif kind == "heartbeat":
                    await socket.send_json({"type": "heartbeat", "running": engine.running, "paused": engine.paused, "live": bool(engine.channel.get("live")), "images_enabled": store.settings().screenshots_enabled and store.settings().screenshot_source == "companion"})
        except (WebSocketDisconnect, asyncio.TimeoutError):
            pass
        except (ValueError, KeyError, TypeError):
            await socket.close(4400)

    app.mount("/", StaticFiles(directory=root / "web", html=True), name="frontend")
    return app

