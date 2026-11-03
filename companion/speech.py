"""Local speech engines and explicit playback routing; no implicit chat fallback."""
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import wave

import httpx
import numpy as np

PIPER_VOICES = {"de_DE-thorsten-medium": "Thorsten · Deutsch", "de_DE-eva_k-x_low": "Eva · Deutsch", "en_US-lessac-medium": "Lessac · Englisch"}


def output_devices():
    import sounddevice as sd
    hosts = sd.query_hostapis()
    return [{"index": i, "name": d["name"], "host": hosts[d["hostapi"]]["name"], "channels": d["max_output_channels"]} for i, d in enumerate(sd.query_devices()) if d["max_output_channels"] > 0]


def output_device(settings):
    if settings.tts_device is None and not settings.tts_device_name:
        return None
    candidates = output_devices()
    matches = [d for d in candidates if d["name"] == settings.tts_device_name and (not settings.tts_device_host or d["host"] == settings.tts_device_host)]
    selected = next((d for d in matches if d["index"] == settings.tts_device), None)
    if selected:
        return selected["index"]
    if len(matches) == 1:
        return matches[0]["index"]
    raise ValueError("Das TTS-Wiedergabegeraet wurde geaendert oder fehlt. Bitte erneut auswaehlen.")


class Speech:
    def __init__(self, store):
        self.store = store
        self.cancel = threading.Event()
        self.speaking = False
        self.quiet_until = 0
        self.status = "Bereit"
        self.task = None
        self.voices_cache = None
        self.voices_time = 0
        self.model_root = store.root / "tts-models"
        self.model_root.mkdir(exist_ok=True)
        self.install_lock = asyncio.Lock()
        self.piper_lock = asyncio.Lock()
        self.piper_process = None
        self.piper_directory = None

    async def close(self):
        self.stop()
        process, self.piper_process = self.piper_process, None
        if process and process.returncode is None:
            process.kill()
            await process.wait()
        if self.piper_directory:
            self.piper_directory.cleanup()
            self.piper_directory = None

    async def piper_synthesize(self, payload):
        async with self.piper_lock:
            if not self.piper_process or self.piper_process.returncode is not None:
                if self.piper_directory:
                    self.piper_directory.cleanup()
                self.piper_directory = tempfile.TemporaryDirectory(prefix="piper-worker-", dir=self.store.root)
                args = [sys.executable, "--tts-worker", "--server", self.piper_directory.name] if getattr(sys, "frozen", False) else [sys.executable, str(Path(__file__).with_name("tts_worker.py")), "--server", self.piper_directory.name]
                options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
                self.piper_process = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, **options)
            process = self.piper_process
            try:
                folder = Path(self.piper_directory.name)
                request = folder / "request.tmp"
                request.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                request.replace(folder / "request.json")
                response = folder / "response.json"
                async with asyncio.timeout(90):
                    while not response.exists():
                        if process.returncode is not None:
                            raise ValueError("Piper wurde vorzeitig beendet.")
                        await asyncio.sleep(.03)
                result = json.loads(response.read_text(encoding="utf-8"))
                response.unlink()
                if not result.get("ok"):
                    raise ValueError("Piper konnte den Text nicht erzeugen. Sprachmodell und Konfiguration pruefen.")
            except BaseException:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                self.piper_process = None
                raise

    def microphone_muted(self):
        return self.speaking or time.monotonic() < self.quiet_until

    def stop(self):
        self.cancel.set()
        if self.task and self.task is not asyncio.current_task():
            self.task.cancel()

    async def command(self, args, timeout=90):
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **options)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
            if process.returncode:
                raise ValueError("Die lokale Spracherzeugung ist fehlgeschlagen. Stimme und Sprachmodell pruefen.")
            return stdout.decode("utf-8-sig").strip()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def voices(self):
        if self.voices_cache is None or time.monotonic() - self.voices_time > 60:
            windows = []
            if os.name == "nt":
                text = await self.command(["powershell.exe", "-NoLogo", "-NoProfile", "-File", str(Path(__file__).with_name("windows_speech.ps1")), "voices"], timeout=15)
                windows = json.loads(text)
            self.voices_cache, self.voices_time = windows, time.monotonic()
        piper = [{"id": key, "name": name, "engine": "piper", "language": key[:5], "installed": (self.model_root / (key + ".onnx")).exists() and (self.model_root / (key + ".onnx.json")).exists()} for key, name in PIPER_VOICES.items()]
        return {"voices": [*self.voices_cache, *piper], "engines": {"windows": os.name == "nt", "piper": importlib.util.find_spec("piper") is not None}}

    async def install(self, voice):
        if voice not in PIPER_VOICES:
            raise ValueError("Bitte eine angebotene Piper-Stimme auswaehlen.")
        async with self.install_lock:
            async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
                response = await client.get("https://huggingface.co/rhasspy/piper-voices/resolve/main/voices.json")
                response.raise_for_status()
                files = response.json()[voice]["files"]
                for remote, info in files.items():
                    if not remote.endswith((".onnx", ".onnx.json", "MODEL_CARD")):
                        continue
                    name = voice + ".MODEL_CARD" if remote.endswith("MODEL_CARD") else Path(remote).name
                    destination = self.model_root / name
                    if destination.exists():
                        continue
                    partial = destination.with_suffix(destination.suffix + ".download")
                    try:
                        checksum = hashlib.md5()
                        size = 0
                        async with client.stream("GET", "https://huggingface.co/rhasspy/piper-voices/resolve/main/" + remote) as download:
                            download.raise_for_status()
                            with partial.open("wb") as output:
                                async for chunk in download.aiter_bytes():
                                    size += len(chunk)
                                    if size > 256 * 1024 * 1024:
                                        raise ValueError("Die Sprachdatei ist zu gross.")
                                    output.write(chunk)
                                    checksum.update(chunk)
                        if size != info["size_bytes"] or checksum.hexdigest() != info["md5_digest"]:
                            raise ValueError("Die Sprachdatei stimmt nicht mit der offiziellen Pruefsumme ueberein.")
                        partial.replace(destination)
                    finally:
                        partial.unlink(missing_ok=True)
        return {"ok": True, "voice": voice}

    async def synthesize(self, text, path, settings):
        payload = {"text": text, "voice": settings.tts_voice, "rate": settings.tts_rate, "output": str(path)}
        if settings.tts_engine == "piper":
            if settings.tts_voice not in PIPER_VOICES:
                raise ValueError("Bitte eine Piper-Stimme auswaehlen und herunterladen.")
            payload["model"] = str(self.model_root / (settings.tts_voice + ".onnx"))
            if not Path(payload["model"]).exists() or not Path(payload["model"] + ".json").exists():
                raise ValueError("Die gewaehlte Piper-Stimme ist noch nicht heruntergeladen.")
            await self.piper_synthesize(payload)
            return
        with tempfile.TemporaryDirectory(prefix="tts-input-", dir=self.store.root) as folder:
            request = Path(folder) / "request.json"
            request.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            if settings.tts_engine == "windows":
                if os.name != "nt":
                    raise ValueError("Windows-Stimmen sind nur unter Windows verfuegbar. Bitte Piper verwenden.")
                args = ["powershell.exe", "-NoLogo", "-NoProfile", "-File", str(Path(__file__).with_name("windows_speech.ps1")), "synthesize", str(request)]
            elif getattr(sys, "frozen", False):
                args = [sys.executable, "--tts-worker", str(request)]
            else:
                args = [sys.executable, str(Path(__file__).with_name("tts_worker.py")), str(request)]
            await self.command(args)

    def play(self, path, settings, cancelled):
        import sounddevice as sd
        device = output_device(settings)
        with wave.open(str(path), "rb") as source:
            if source.getsampwidth() != 2 or source.getnframes() <= 0 or source.getnframes() > source.getframerate() * 120:
                raise ValueError("Die erzeugte Sprachdatei hat ein ungueltiges Format.")
            rate = source.getframerate()
            samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.float32) / 32768
            samples = samples.reshape(-1, source.getnchannels()).mean(axis=1)
        info = sd.query_devices(device, "output")
        output_rate = int(info["default_samplerate"])
        if rate != output_rate:
            samples = np.interp(np.arange(round(len(samples) * output_rate / rate)) * rate / output_rate, np.arange(len(samples)), samples).astype(np.float32)
        channels = min(2, info["max_output_channels"])
        samples = np.repeat((samples * settings.tts_volume)[:, None], channels, axis=1)
        if cancelled.is_set():
            return False
        with sd.OutputStream(device=device, samplerate=output_rate, channels=channels, dtype="float32") as stream:
            self.speaking = True
            self.status = "Spricht"
            try:
                block = max(1, round(output_rate * .05))
                for start in range(0, len(samples), block):
                    if cancelled.is_set():
                        stream.abort()
                        return False
                    stream.write(samples[start:start + block])
            finally:
                self.speaking = False
                self.quiet_until = time.monotonic() + .6
        return True

    async def say(self, text, guard=lambda: True):
        self.task = asyncio.current_task()
        self.cancel = threading.Event()
        token = self.cancel
        settings = self.store.settings()
        worker = None
        self.status = "Erzeugt Sprache"
        try:
            output_device(settings)  # Reject missing routing before model work.
            with tempfile.TemporaryDirectory(prefix="tts-audio-", dir=self.store.root) as folder:
                path = Path(folder) / "speech.wav"
                await self.synthesize(text, path, settings)
                if not guard() or token.is_set():
                    raise ValueError("Die Sprachausgabe wurde verworfen, weil der Beitrag nicht mehr aktuell ist.")
                worker = asyncio.create_task(asyncio.to_thread(self.play, path, settings, token))
                try:
                    played = await asyncio.shield(worker)
                except asyncio.CancelledError:
                    token.set()
                    await worker
                    raise
                if not played:
                    raise ValueError("Die Sprachausgabe wurde abgebrochen.")
        finally:
            token.set()
            self.status = "Bereit"
            self.task = None
