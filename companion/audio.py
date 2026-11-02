import asyncio
import base64
import io
import json
import math
import uuid
import time
from dataclasses import dataclass, field

import numpy as np
import websockets
from PIL import Image, UnidentifiedImageError
from .models import cpu_threads_max


def devices():
    try:
        import sounddevice as sd

        hosts = sd.query_hostapis()
        return [{"index": index, "name": item["name"], "channels": item["max_input_channels"], "sample_rate": int(item["default_samplerate"]), "host": hosts[item["hostapi"]]["name"]} for index, item in enumerate(sd.query_devices()) if item["max_input_channels"] > 0]
    except (ImportError, OSError, Exception) as exc:
        return []


def resample_pcm(samples, old_rate, new_rate=24000):
    samples = np.asarray(samples, dtype=np.float32)
    if samples.ndim == 2:
        samples = samples.mean(axis=1)
    if old_rate != new_rate:
        count = max(1, round(len(samples) * new_rate / old_rate))
        samples = np.interp(np.arange(count) * old_rate / new_rate, np.arange(len(samples)), samples)
    return (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()


def normalize_image(data: bytes):
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("Das Bild ist zu groß (maximal 8 MB).")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 25_000_000:
                raise ValueError("Das Bild hat zu viele Bildpunkte.")
            image = image.convert("RGB")
            image.thumbnail((1280, 720))
            output = io.BytesIO()
            image.save(output, "JPEG", quality=75)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Die Datei ist kein unterstütztes Bild.") from exc
    return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode()


@dataclass
class AudioState:
    # Local inference processes up to 12 seconds at once. Keep incoming audio
    # during that work; 30 capture packets only covered three seconds.
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=150))
    task: asyncio.Task | None = None
    level: float = 0
    last_packet: float = 0
    status: str = "Bereit"
    bytes: int = 0
    dropped: int = 0
    partial_text: str = ""
    queued_bytes: int = 0
    streaming: bool = False


class Audio:
    def __init__(self, engine):
        self.engine = engine
        self.states = {}
        self.streams = []
        self.processes = []
        self.tasks = []
        self.whisper = None
        self.whisper_config = None
        self.whisper_lock = asyncio.Lock()
        self.streaming_recognizer = None
        self.streaming_config = None
        self.streaming_lock = asyncio.Lock()

    def snapshot(self):
        return {source: {"level": round(state.level, 3) if time.time() - state.last_packet < 2 else 0, "last_packet": state.last_packet, "status": state.status, "bytes": state.bytes, "dropped": state.dropped, "partial_text": state.partial_text, "lag_seconds": round(state.queued_bytes / 48000, 2) if state.streaming else None} for source, state in self.states.items()}

    def feed(self, source_id, pcm):
        settings = self.engine.store.settings()
        source = next((item for item in settings.sources if item.id == source_id and item.enabled and item.role != "ignore"), None)
        if not self.engine.running or self.engine.paused or not self.engine.channel_ready() or not source or not pcm or len(pcm) % 2:
            return
        if len(pcm) > 24000 * 2 * 2:
            return
        state = self.states.setdefault(source_id, AudioState())
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        state.level = float(np.sqrt(np.mean(samples * samples))) if len(samples) else 0
        state.last_packet = time.time()
        state.bytes += len(pcm)
        if state.level > (0.008 if settings.stt_provider == "local_streaming" else 0.015) and source.role == "streamer":
            self.engine.last_voice = time.time()
        if state.task is None or state.task.done():
            state.task = asyncio.create_task(self.transcribe(source, state))
        if state.queue.full():
            removed = state.queue.get_nowait()
            state.queued_bytes = max(0, state.queued_bytes - len(removed))
            state.dropped += 1
        state.queue.put_nowait(pcm)
        if settings.stt_provider == "local_streaming":
            state.queued_bytes += len(pcm)

    async def transcribe(self, source, state):
        state.status = "Verbinde Spracherkennung"
        try:
            settings = self.engine.store.settings()
            if settings.stt_provider == "local_streaming":
                await self.streaming_transcription(source, state)
            elif settings.stt_provider == "local_whisper":
                await self.local_transcription(source, state)
            else:
                await self.realtime_transcription(source, state)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            state.status = "Fehler bei Spracherkennung"
            await self.engine.notify("error", source.name, str(exc) if isinstance(exc, (RuntimeError, ValueError, ImportError)) else "Audioverbindung unterbrochen. KI-Schlüssel und Verbindung prüfen.")
            # Backoff prevents one failed connection per incoming audio packet.
            await asyncio.sleep(15)

    async def realtime_transcription(self, source, state):
        key = self.engine.store.get("stt_key") or self.engine.store.get("ai_key")
        if not key:
            raise RuntimeError("Für die Echtzeit-Spracherkennung fehlt der OpenAI-Schlüssel. Alternativ lokale Erkennung auswählen.")
        settings = self.engine.store.settings()
        async with websockets.connect("wss://api.openai.com/v1/realtime?intent=transcription", additional_headers={"Authorization": "Bearer " + key}, max_size=2**22, open_timeout=15) as ws:
            session = {
                "type": "transcription",
                "audio": {"input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "transcription": {
                        "model": settings.transcription_model,
                        "languages": ["de"],
                        "prompt": f"Deutschsprachiger Livestream, Kategorie: {self.engine.channel.get('category', '')}. Quelle: {source.name}.",
                        "delay": "low",
                    },
                    "turn_detection": None,
                }},
            }
            await ws.send(json.dumps({"type": "session.update", "session": session}))

            async def send():
                buffered = 0
                silence = 0
                has_speech = False
                while True:
                    pcm = await state.queue.get()
                    await ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm).decode()}))
                    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
                    speech = float(np.sqrt(np.mean(samples * samples))) > 0.012
                    buffered += len(pcm)
                    silence = 0 if speech else silence + len(pcm)
                    has_speech = has_speech or speech
                    if has_speech and (silence >= 33600 or buffered >= 576000):
                        await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
                        buffered = silence = 0
                        has_speech = False
                    elif not has_speech and buffered >= 240000:
                        await ws.send(json.dumps({"type": "input_audio_buffer.clear"}))
                        buffered = silence = 0

            sender = asyncio.create_task(send())
            state.status = "Hört zu"
            try:
                async for raw in ws:
                    event = json.loads(raw)
                    kind = event.get("type")
                    if kind == "error":
                        code = event.get("error", {}).get("code", "unbekannt")
                        raise RuntimeError(f"Spracherkennung meldet einen Fehler ({code}). Modell und Einstellungen prüfen.")
                    if kind == "conversation.item.input_audio_transcription.completed":
                        text = event.get("transcript", "").strip()
                        if text:
                            await self.engine.transcript(source, text)
                    elif kind == "conversation.item.input_audio_transcription.failed":
                        await self.engine.notify("warning", source.name, "Ein Audioabschnitt konnte nicht erkannt werden.")
            finally:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)

    async def local_transcription(self, source, state):
        # sherpa bundles a newer, backwards-compatible Windows ONNX runtime.
        # Load it first when both optional recognition backends are installed.
        import os
        if os.name == "nt":
            try:
                import sherpa_onnx
            except (ImportError, OSError):
                pass
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("Lokale Spracherkennung ist nicht installiert. Das Zusatzpaket requirements-whisper.txt installieren.") from exc
        async with self.whisper_lock:
            settings = self.engine.store.settings()
            threads = settings.whisper_cpu_threads or min(8, cpu_threads_max())
            configuration = (settings.whisper_model, threads)
            if self.whisper is None or self.whisper_config != configuration:
                state.status = "Lokales Sprachmodell wird geladen"
                self.whisper = await asyncio.to_thread(WhisperModel, settings.whisper_model, device="cpu", compute_type="int8", cpu_threads=threads)
                self.whisper_config = configuration
            whisper = self.whisper
        state.status = "Hört lokal zu"
        buffer = bytearray()
        silence = 0
        has_speech = False
        while True:
            pcm = await state.queue.get()
            samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
            speech = float(np.sqrt(np.mean(samples * samples))) > 0.012
            if speech:
                has_speech = True
                silence = 0
            else:
                silence += len(pcm)
            buffer.extend(pcm)
            # Batch short turns so the legacy recognizer can keep up.
            if len(buffer) >= 24000 * 2 * 12 or (has_speech and silence > 24000 * 2 * 0.7 and len(buffer) >= 24000 * 2 * 8):
                data = np.frombuffer(buffer, dtype="<i2").astype(np.float32) / 32768
                buffer.clear()
                if has_speech:
                    data = np.interp(np.arange(round(len(data) * 2 / 3)) * 1.5, np.arange(len(data)), data).astype(np.float32)
                    def run():
                        segments, _ = whisper.transcribe(data, language="de", vad_filter=True, beam_size=1)
                        return " ".join(segment.text for segment in segments).strip()
                    async with self.whisper_lock:
                        text = await asyncio.to_thread(run)
                    if text:
                        await self.engine.transcript(source, text)
                has_speech = False
                silence = 0

    def clear_streaming_buffers(self):
        for state in self.states.values():
            if state.streaming:
                while not state.queue.empty():
                    state.queue.get_nowait()
                state.queued_bytes = 0
                state.partial_text = ""

    async def run_streaming_native(self, function, *args):
        # A canceled coroutine must not release the recognizer lock while its
        # native worker is still using the same model.
        job = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(job)
        except asyncio.CancelledError:
            await asyncio.gather(job, return_exceptions=True)
            raise

    async def prepare_streaming(self):
        from .streaming_asr import create_recognizer
        settings = self.engine.store.settings()
        configuration = (settings.streaming_model, settings.streaming_cpu_threads or min(8, cpu_threads_max()))
        async with self.streaming_lock:
            if self.streaming_recognizer is None or self.streaming_config != configuration:
                self.streaming_recognizer = await self.run_streaming_native(create_recognizer, *configuration)
                self.streaming_config = configuration
            return self.streaming_recognizer

    async def streaming_transcription(self, source, state):
        from .streaming_asr import ContinuousTranscript
        state.streaming = True
        state.status = "Streaming-Sprachmodell wird geladen"
        recognizer = await self.prepare_streaming()
        async with self.streaming_lock:
            session = await self.run_streaming_native(ContinuousTranscript, recognizer)
        fence = (self.engine.epoch, self.engine.revision)
        utterance = uuid.uuid4().hex
        previous = ""
        state.status = "Hört fortlaufend lokal zu"
        while True:
            pcm = await state.queue.get()
            state.queued_bytes = max(0, state.queued_bytes - len(pcm))
            if self.engine.paused or not self.engine.running:
                continue
            current_fence = (self.engine.epoch, self.engine.revision)
            async with self.streaming_lock:
                if current_fence != fence:
                    session = await self.run_streaming_native(ContinuousTranscript, recognizer)
                    fence, utterance, previous = current_fence, uuid.uuid4().hex, ""
                samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
                samples = np.interp(np.arange(round(len(samples) * 2 / 3)) * 1.5, np.arange(len(samples)), samples).astype(np.float32)
                result = await self.run_streaming_native(session.accept, samples)
            if current_fence != (self.engine.epoch, self.engine.revision) or self.engine.paused or not self.engine.running:
                continue
            state.partial_text = result["partial"][-1000:]
            state.status = "Hört fortlaufend lokal zu" if state.queued_bytes < 48000 * 3 else "Spracherkennung arbeitet Audio nach"
            if result["final"]:
                await self.engine.transcript(source, result["final"], utterance_id=utterance, final=True)
                async with self.streaming_lock:
                    session = await self.run_streaming_native(ContinuousTranscript, recognizer)
                utterance, previous = uuid.uuid4().hex, ""
                state.partial_text = ""
            elif result["stable"] and result["stable"] != previous:
                previous = result["stable"]
                await self.engine.transcript(source, previous, utterance_id=utterance, final=False)

    async def start_local(self):
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("Das Audio-Zusatzpaket ist nicht installiert.") from exc
        loop = asyncio.get_running_loop()
        selected = [item for item in self.engine.store.settings().sources if item.enabled and item.role != "ignore" and item.device is not None]
        if not selected:
            raise RuntimeError("Bitte mindestens eine lokale Audioquelle auswählen.")
        try:
            for source in selected:
                info = sd.query_devices(source.device, "input")
                if source.device_name and info["name"] != source.device_name:
                    raise ValueError(f"Das Gerät für {source.name} hat sich geändert. Bitte die Audioquelle neu auswählen.")
                rate = int(info["default_samplerate"])
                def callback(data, frames, timing, status, source_id=source.id, rate=rate):
                    pcm = resample_pcm(data.copy(), rate)
                    loop.call_soon_threadsafe(self.feed, source_id, pcm)
                stream = sd.InputStream(device=source.device, channels=1, samplerate=rate, blocksize=round(rate * 0.1), dtype="float32", callback=callback)
                stream.start()
                self.streams.append(stream)
        except Exception:
            for stream in self.streams:
                stream.stop()
                stream.close()
            self.streams.clear()
            raise

    async def start_twitch(self):
        import importlib.util
        import shutil
        import sys
        if importlib.util.find_spec("streamlink") is None or not shutil.which("ffmpeg"):
            raise RuntimeError("Für Audio aus Twitch werden Streamlink und FFmpeg benötigt. Siehe Anleitung.")
        source = next((item for item in self.engine.store.settings().sources if item.enabled and item.role != "ignore"), None)
        if not source:
            raise RuntimeError("Bitte eine Quelle für den empfangenen Stream anlegen.")
        # A received stream is a mixture; never attribute its speech to an individual.
        source = source.model_copy(update={"role": "conversation", "remember": False})
        self.engine.twitch_mix_source = source.id
        entry = [sys.executable, "--streamlink-worker"] if getattr(sys, "frozen", False) else [sys.executable, "-m", "streamlink"]
        streamlink = await asyncio.create_subprocess_exec(*entry, "--stdout", "--twitch-low-latency", "https://www.twitch.tv/" + self.engine.store.settings().channel, "audio_only,best", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        ffmpeg = await asyncio.create_subprocess_exec("ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-vn", "-ac", "1", "-ar", "24000", "-f", "s16le", "pipe:1", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        self.processes.extend([streamlink, ffmpeg])
        async def pump():
            try:
                while chunk := await streamlink.stdout.read(65536):
                    ffmpeg.stdin.write(chunk)
                    await ffmpeg.stdin.drain()
            finally:
                ffmpeg.stdin.close()
        async def collect():
            while chunk := await ffmpeg.stdout.read(4800):
                if len(chunk) % 2:
                    chunk += await ffmpeg.stdout.read(1)
                self.feed(source.id, chunk)
            await self.engine.notify("error", "Stream-Audio beendet", "Der empfangene Twitch-Stream liefert kein Audio mehr. Quelle prüfen oder neu starten.")
        self.tasks.extend([asyncio.create_task(pump()), asyncio.create_task(collect())])

    async def stop(self):
        for stream in self.streams:
            stream.stop()
            stream.close()
        self.streams.clear()
        tasks = self.tasks + [state.task for state in self.states.values() if state.task]
        for task in tasks:
            task.cancel()
        for process in self.processes:
            if process.returncode is None:
                process.kill()
        await asyncio.gather(*tasks, return_exceptions=True)
        for process in self.processes:
            await process.wait()
        self.processes.clear()
        self.tasks.clear()
        self.states.clear()


async def obs_screenshot(url, password):
    import hashlib
    import ipaddress
    import secrets
    from urllib.parse import urlparse

    target = urlparse(url)
    try:
        local = ipaddress.ip_address(target.hostname or "").is_private
    except ValueError:
        local = target.hostname == "localhost" or (target.hostname or "").endswith((".local", ".lan"))
    if target.scheme not in {"ws", "wss"} or not target.hostname or target.username or target.password or (target.scheme == "ws" and not local):
        raise ValueError("OBS über eine lokale/LAN-Adresse oder extern über wss:// verbinden.")
    async with websockets.connect(url, open_timeout=5, max_size=8 * 1024 * 1024) as ws:
        hello = json.loads(await asyncio.wait_for(ws.recv(), 5))
        identify = {"rpcVersion": 1, "eventSubscriptions": 0}
        auth = hello.get("d", {}).get("authentication")
        if auth:
            secret = base64.b64encode(hashlib.sha256((password + auth["salt"]).encode()).digest()).decode()
            identify["authentication"] = base64.b64encode(hashlib.sha256((secret + auth["challenge"]).encode()).digest()).decode()
        await ws.send(json.dumps({"op": 1, "d": identify}))
        if json.loads(await asyncio.wait_for(ws.recv(), 5)).get("op") != 2:
            raise RuntimeError("OBS-Anmeldung fehlgeschlagen.")
        async def request(kind, data=None):
            request_id = secrets.token_hex(8)
            await ws.send(json.dumps({"op": 6, "d": {"requestType": kind, "requestId": request_id, "requestData": data or {}}}))
            while True:
                result = json.loads(await asyncio.wait_for(ws.recv(), 8))
                if result.get("op") == 7 and result["d"]["requestId"] == request_id:
                    if not result["d"]["requestStatus"]["result"]:
                        raise RuntimeError("OBS konnte das Streambild nicht liefern.")
                    return result["d"].get("responseData", {})
        scene = await request("GetCurrentProgramScene")
        image = await request("GetSourceScreenshot", {"sourceName": scene["sceneName"], "imageFormat": "jpg", "imageWidth": 1280, "imageCompressionQuality": 70})
        return normalize_image(base64.b64decode(image["imageData"].split(",", 1)[1]))
