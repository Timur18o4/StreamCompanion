"""Optional local Windows/Linux collector. Pairing credentials never reach the browser."""
import asyncio
import base64
import ipaddress
import json
import socket
import time
from urllib.parse import urlparse

import httpx
import websockets

from .audio import devices, normalize_image, resample_pcm
from .models import AudioSource


def server_address(value):
    parsed = urlparse(value.strip().rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("Bitte nur die Workspace-Adresse eingeben, z. B. http://192.168.1.20:8787.")
    if parsed.scheme == "http":
        try:
            local = ipaddress.ip_address(parsed.hostname).is_private
        except ValueError:
            local = parsed.hostname in {"localhost"} or parsed.hostname.endswith((".local", ".lan"))
        if not local:
            raise ValueError("Außerhalb des lokalen Netzwerks ist HTTPS erforderlich.")
    return f"{parsed.scheme}://{parsed.netloc}"


class Sender:
    def __init__(self, store):
        self.store = store
        self.running = False
        self.remote_active = False
        self.remote_images = False
        self.last_heartbeat = 0
        self.queue = asyncio.Queue(maxsize=40)
        self.task = None
        self.streams = []
        self.status = "Noch nicht verbunden"
        self.error = ""
        self.sent_bytes = 0
        self.dropped = 0
        self.levels = {}

    def config(self):
        return self.store.get("sender_config", {"server": "", "sources": [], "monitor": 0})

    def snapshot(self):
        return {"running": self.running, "status": self.status, "error": self.error, "remote_active": self.remote_active and time.time() - self.last_heartbeat < 8, "sent_bytes": self.sent_bytes, "dropped": self.dropped, "levels": self.levels, "paired": bool(self.store.get("sender_token")), "config": self.config()}

    async def pair(self, server, code):
        server = server_address(server)
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.post(server + "/api/agent/pair", json={"code": code.strip()})
        if response.status_code != 200:
            raise ValueError("Der Workspace hat den Code abgelehnt. Einen neuen Code erzeugen und die Adresse prüfen.")
        config = self.config()
        config["server"] = server
        self.store.put("sender_config", config)
        self.store.put("sender_token", response.json()["token"])
        self.status = "Gekoppelt"

    async def start(self, sources, monitor=0):
        if self.running:
            return
        if not self.store.get("sender_token"):
            raise ValueError("Bitte zuerst mit dem Workspace koppeln.")
        import sounddevice as sd
        sources = [AudioSource.model_validate(source) for source in sources]
        if not sources or len(sources) > 8 or len({source.id for source in sources}) != len(sources):
            raise ValueError("Bitte mindestens eine und höchstens acht eindeutige Quellen wählen.")
        if len({source.device for source in sources}) != len(sources):
            raise ValueError("Jedes Aufnahmegerät nur einer Rolle zuweisen.")
        config = self.config()
        config.update(sources=[source.model_dump() for source in sources], monitor=int(monitor))
        self.store.put("sender_config", config)
        loop = asyncio.get_running_loop()
        self.running = True
        self.error = ""
        self.sent_bytes = 0
        self.remote_active = False

        def enqueue(source_id, pcm):
            if not self.running or not self.remote_active or time.time() - self.last_heartbeat > 8:
                return
            if self.queue.full():
                self.queue.get_nowait()
                self.dropped += 1
            self.queue.put_nowait({"type": "audio", "source": source_id, "pcm": base64.b64encode(pcm).decode()})

        try:
            for source in sources:
                if source.device is None:
                    raise ValueError("Ein ausgewähltes Aufnahmegerät fehlt.")
                info = sd.query_devices(source.device, "input")
                if source.device_name and info["name"] != source.device_name:
                    raise ValueError(f"Das Gerät für {source.name} hat sich geändert. Bitte neu auswählen.")
                rate = int(info["default_samplerate"])
                def callback(data, frames, timing, status, source_id=source.id, rate=rate):
                    if self.remote_active and time.time() - self.last_heartbeat < 8:
                        pcm = resample_pcm(data.copy(), rate)
                        loop.call_soon_threadsafe(enqueue, source_id, pcm)
                stream = sd.InputStream(device=source.device, samplerate=rate, channels=1, blocksize=round(rate * 0.1), dtype="float32", callback=callback)
                stream.start()
                self.streams.append(stream)
            self.task = asyncio.create_task(self.connection())
        except BaseException:
            await self.stop()
            raise

    async def connection(self):
        config = self.config()
        server = server_address(config["server"])
        ws_url = server.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/api/agent/stream"
        configured = False
        retry = 2
        while self.running:
            tasks = []
            self.remote_active = False
            try:
                self.status = "Verbindet mit dem Workspace"
                async with websockets.connect(ws_url, open_timeout=10, max_size=2**20) as ws:
                    await ws.send(json.dumps({"token": self.store.get("sender_token")}))
                    greeting = json.loads(await asyncio.wait_for(ws.recv(), 8))
                    if greeting.get("type") != "connected":
                        raise ValueError("Die Verbindung wurde nicht bestätigt.")
                    if not configured:
                        if greeting.get("running"):
                            raise ValueError("Den Begleiter im Workspace kurz stoppen, damit die PC-Quellen übernommen werden können.")
                        await ws.send(json.dumps({"type": "configure", "sources": config["sources"]}))
                        result = json.loads(await asyncio.wait_for(ws.recv(), 8))
                        if result.get("type") != "configured":
                            raise ValueError(result.get("message", "Die Quellen wurden nicht übernommen."))
                        configured = True
                    self.status = "Verbunden · wartet auf den Livestream"
                    self.error = ""
                    retry = 2

                    async def transmit():
                        while True:
                            message = await self.queue.get()
                            if self.remote_active and time.time() - self.last_heartbeat < 8:
                                await ws.send(json.dumps(message))
                                self.sent_bytes += len(message.get("pcm", "")) * 3 // 4

                    async def heartbeat():
                        while True:
                            await ws.send(json.dumps({"type": "heartbeat"}))
                            await asyncio.sleep(2)

                    async def pictures():
                        if not config.get("monitor"):
                            return
                        import mss
                        def capture():
                            from PIL import Image
                            import io
                            with mss.mss() as screen:
                                index = config["monitor"]
                                if index < 1 or index >= len(screen.monitors):
                                    raise ValueError("Der ausgewählte Bildschirm ist nicht mehr verfügbar.")
                                shot = screen.grab(screen.monitors[index])
                                image = Image.frombytes("RGB", shot.size, shot.rgb)
                                image.thumbnail((1280, 720))
                                output = io.BytesIO()
                                image.save(output, "JPEG", quality=70)
                                return base64.b64encode(output.getvalue()).decode()
                        while True:
                            if self.remote_active and self.remote_images and time.time() - self.last_heartbeat < 8:
                                image = await asyncio.to_thread(capture)
                                await ws.send(json.dumps({"type": "image", "image": image}))
                            await asyncio.sleep(30)

                    tasks = [asyncio.create_task(transmit()), asyncio.create_task(heartbeat()), asyncio.create_task(pictures())]
                    while True:
                        message = json.loads(await asyncio.wait_for(ws.recv(), 12))
                        if message.get("type") == "heartbeat":
                            self.last_heartbeat = time.time()
                            self.remote_active = bool(message.get("running") and message.get("live") and not message.get("paused"))
                            self.remote_images = bool(message.get("images_enabled"))
                            self.status = "Überträgt ausgewählte Quellen" if self.remote_active else "Verbunden · Workspace pausiert, gestoppt oder offline"
                            if not self.remote_active:
                                while not self.queue.empty():
                                    self.queue.get_nowait()
                        for task in tasks:
                            if task.done() and not task.cancelled() and task.exception():
                                raise task.exception()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.remote_active = False
                self.status = "Verbindung unterbrochen"
                self.error = str(exc) if isinstance(exc, ValueError) else "Workspace nicht erreichbar oder Kopplung widerrufen. Adresse und Code prüfen."
                await asyncio.sleep(retry)
                retry = min(30, retry * 2)
            finally:
                self.remote_active = False
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def stop(self):
        self.running = False
        self.remote_active = False
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        for stream in self.streams:
            stream.stop()
            stream.close()
        self.streams.clear()
        while not self.queue.empty():
            self.queue.get_nowait()
        self.status = "Gestoppt"


def create_sender_app(data_dir=None):
    from contextlib import asynccontextmanager
    from pathlib import Path
    from fastapi import Depends, FastAPI, Header, HTTPException, Request
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel, Field
    from .storage import Store

    root = Path(__file__).resolve().parent.parent
    store = Store(data_dir or root / "sender-data")
    sender = Sender(store)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await sender.stop()
        store.close()

    app = FastAPI(title="Stream Companion · PC-Sender", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.store = store
    app.state.sender = sender

    async def auth(authorization: str = Header(default="")):
        if not secrets_compare(authorization, "Bearer " + store.access_key):
            raise HTTPException(401, "Der lokale Zugangscode fehlt.")

    @app.middleware("http")
    async def origin_guard(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Fremde Herkunft ist nicht zugelassen."}, status_code=403)
        response = await call_next(request)
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/api/status", dependencies=[Depends(auth)])
    async def status():
        return sender.snapshot()

    @app.get("/api/devices", dependencies=[Depends(auth)])
    async def inputs():
        return {"devices": await asyncio.to_thread(devices)}

    @app.get("/api/monitors", dependencies=[Depends(auth)])
    async def monitors():
        def read():
            import mss
            with mss.mss() as screen:
                return [{"index": index, "width": monitor["width"], "height": monitor["height"]} for index, monitor in enumerate(screen.monitors) if index]
        try:
            return {"monitors": await asyncio.to_thread(read)}
        except Exception:
            return {"monitors": []}

    class PairBody(BaseModel):
        server: str
        code: str = Field(min_length=8, max_length=8)

    class StartBody(BaseModel):
        sources: list[AudioSource]
        monitor: int = Field(default=0, ge=0, le=16)

    @app.post("/api/pair", dependencies=[Depends(auth)])
    async def pair(body: PairBody):
        await sender.stop()
        await sender.pair(body.server, body.code)
        return {"ok": True}

    @app.post("/api/start", dependencies=[Depends(auth)])
    async def start(body: StartBody):
        try:
            await sender.start([source.model_dump() for source in body.sources], body.monitor)
        except (ImportError, OSError) as exc:
            raise ValueError("Audioeingänge sind nicht verfügbar. Audio-Zusatzpaket und Geräte prüfen.") from exc
        return {"ok": True}

    @app.post("/api/stop", dependencies=[Depends(auth)])
    async def stop():
        await sender.stop()
        return {"ok": True}

    @app.post("/api/shutdown", dependencies=[Depends(auth)])
    async def shutdown():
        await sender.stop()
        callback = getattr(app.state, "shutdown_callback", None)
        if callback:
            asyncio.get_running_loop().call_later(0.3, callback)
        return {"ok": True, "app": "stream-companion-sender"}

    @app.get("/")
    async def index():
        return FileResponse(root / "web" / "sender.html")

    app.mount("/", StaticFiles(directory=root / "web"), name="assets")
    return app


def secrets_compare(a, b):
    import secrets
    return secrets.compare_digest(a, b)
