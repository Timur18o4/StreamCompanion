import asyncio
import difflib
import re
import time
import uuid
from collections import deque
from datetime import datetime, timezone

from .ai import AI, AIError
from .audio import Audio, obs_screenshot
from .twitch import Twitch, TwitchError


class Engine:
    def __init__(self, store):
        self.store = store
        self.running = False
        self.paused = False
        self.demo = False
        self.epoch = 0
        self.revision = 0
        self.channel = {}
        self.channel_checked = 0
        self.events = deque(maxlen=400)
        self.logs = deque(maxlen=150)
        self.tasks = []
        self.outgoing = set()
        self.last_voice = 0
        self.last_contribution = 0
        self.last_analysis = 0
        self.last_error = 0
        self.last_observation = 0
        self.last_question = 0
        self.last_owner_text = 0
        self.sent_times = deque(maxlen=200)
        self.recent_sent = deque(maxlen=10)
        self.pending = None
        self.screenshot = None
        self.screenshot_time = 0
        self.twitch_mix_source = None
        self.send_lock = asyncio.Lock()
        self.analysis_lock = asyncio.Lock()
        self.lifecycle_lock = asyncio.Lock()
        self.twitch = Twitch(store, self.notify)
        self.ai = AI(store)
        self.audio = Audio(self)

    async def notify(self, level, title, message):
        self.logs.appendleft({"id": uuid.uuid4().hex, "time": time.time(), "level": level, "title": title, "message": message[:500]})

    def event(self, kind, name, text, **extra):
        row = {"id": uuid.uuid4().hex, "kind": kind, "name": name, "text": text[:2000], "time": time.time(), **extra}
        self.events.append(row)
        self.revision += 1
        if kind in {"audio", "chat"}:
            self.last_observation = row["time"]
        return row

    async def transcript(self, source, text):
        if not self.running or self.paused:
            return
        role = "conversation" if source.id == self.twitch_mix_source else source.role
        subject = self.channel.get("id", "") if role == "streamer" else ""
        self.event("audio", source.name, text, role=role, subject_id=subject, remember=source.remember and role == "streamer")
        if role == "streamer":
            self.last_owner_text = time.time()

    async def on_chat(self, payload):
        bot = self.store.profiles()["bot"]
        if bot and payload.get("chatter_user_id") == bot["id"]:
            return
        user_id = payload.get("chatter_user_id", "")
        name = payload.get("chatter_user_name", "Zuschauer")
        text = payload.get("message", {}).get("text", "")
        settings = self.store.settings()
        command = text.lower().strip()
        if command in {"!remember on", "!remember off", "!forget", "!forget me"}:
            enable = command == "!remember on" and settings.viewer_memory
            self.store.consent(settings.channel, user_id, name, enable)
            # Remove the viewer's temporary context too when they opt out.
            if not enable:
                self.events = deque((item for item in self.events if item.get("subject_id") != user_id), maxlen=400)
            await self.notify("info", "Zuschauer-Gedächtnis", f'{name}: {"aktiviert" if enable else "deaktiviert und gelöscht"}.')
            return
        if not text or text.startswith("!") or len(text) > 1000:
            return
        self.event("chat", name, text, subject_id=user_id, remember=settings.viewer_memory and self.store.consented(settings.channel, user_id))
        self.last_owner_text = time.time()

    async def check_channel(self):
        self.channel = await self.twitch.channel()
        self.channel_checked = time.time()
        return self.channel

    async def start(self):
        async with self.lifecycle_lock:
            await self._start()

    async def _start(self):
        if self.running:
            return
        settings = self.store.settings()
        if not settings.channel:
            raise ValueError("Bitte zuerst einen Kanal einrichten.")
        await self.check_channel()
        await self.twitch.token("bot")
        if not settings.text_model:
            raise ValueError("Bitte zuerst ein KI-Modell einrichten.")
        if settings.stt_provider == "openai_realtime" and not (self.store.get("stt_key") or self.store.get("ai_key")):
            raise ValueError("Der Schlüssel für die Spracherkennung fehlt.")
        self.demo = False
        self.running = True
        self.paused = False
        self.epoch += 1
        self.events.clear()
        self.pending = None
        self.screenshot = None
        self.last_contribution = time.time() - settings.min_interval
        self.last_voice = time.time()
        self.last_analysis = 0
        self.last_owner_text = 0
        self.last_question = 0
        self.twitch_mix_source = None
        try:
            if settings.input_mode == "local":
                await self.audio.start_local()
            elif settings.input_mode == "twitch":
                if not self.channel.get("live"):
                    raise ValueError("Für den Twitch-Audioempfang muss der Kanal live sein.")
                await self.audio.start_twitch()
            self.tasks = [asyncio.create_task(self.twitch.chat(self.channel["id"], self.on_chat)), asyncio.create_task(self.monitor()), asyncio.create_task(self.conversation())]
        except BaseException:
            await self._stop()
            raise
        await self.notify("success", "Begleiter gestartet", "Vorschläge erscheinen hier." if settings.mode == "preview" else "Automatische Beiträge sind eingeschaltet.")

    async def stop(self):
        async with self.lifecycle_lock:
            await self._stop()

    async def _stop(self):
        self.running = False
        self.epoch += 1
        self.pending = None
        tasks = self.tasks + list(self.outgoing)
        self.tasks = []
        current = asyncio.current_task()
        for task in tasks:
            if task is not current:
                task.cancel()
        await asyncio.gather(*(task for task in tasks if task is not current), return_exceptions=True)
        await self.audio.stop()
        self.screenshot = None
        self.last_voice = 0
        await self.notify("info", "Begleiter gestoppt", "Audioverarbeitung und automatische Beiträge sind beendet.")

    async def monitor(self):
        last_image_attempt = 0
        while self.running:
            try:
                if time.time() - self.channel_checked > 25:
                    before = self.channel.get("category")
                    await self.check_channel()
                    if not self.channel.get("live"):
                        self.pending = None
                        self.screenshot = None
                    if before != self.channel.get("category"):
                        self.revision += 1
                settings = self.store.settings()
                if settings.screenshots_enabled and settings.screenshot_source == "obs" and self.channel.get("live") and not self.paused and time.time() - last_image_attempt > settings.screenshot_interval:
                    last_image_attempt = time.time()
                    self.screenshot = await obs_screenshot(settings.obs_url, self.store.get("obs_password", ""))
                    self.screenshot_time = time.time()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.notify("warning", "Status oder Bild nicht verfügbar", str(exc) if isinstance(exc, (RuntimeError, ValueError)) else "Verbindung prüfen. Ohne aktuellen Live-Status wird nichts gesendet.")
            await asyncio.sleep(3)

    def can_contribute(self, now=None):
        now = time.time() if now is None else now
        settings = self.store.settings()
        if not self.running or self.paused or self.demo:
            return False, "Begleiter ist angehalten."
        if not self.channel.get("live") or now - self.channel_checked > 70:
            return False, "Kein bestätigter aktueller Livestream."
        if not self.twitch.connected:
            return False, "Chat-Verbindung wird hergestellt."
        if now - self.last_contribution < settings.min_interval:
            return False, "Gesprächspause zwischen Beiträgen."
        if sum(sent > now - 3600 for sent in self.sent_times) >= settings.max_per_hour:
            return False, "Stundenlimit erreicht."
        if now - self.last_observation > 180:
            return False, "Es fehlt aktueller Gesprächskontext."
        if now - self.last_voice < 2:
            return False, "Der Streamer spricht gerade."
        if self.last_question and self.last_owner_text <= self.last_question:
            return False, "Die letzte Frage ist noch offen."
        return True, "Bereit für einen passenden Beitrag."

    def context(self):
        settings, _, _, external = self.ai.connection()
        now = time.time()
        owner = self.channel.get("id", "owner")
        eligible = {owner} if settings.owner_memory else set()
        rows = []
        for event in self.events:
            if event["time"] < now - 600:
                continue
            if external and not settings.cloud_chat_permission and event["kind"] == "chat":
                continue
            rows.append(event)
            if event.get("remember") and event["kind"] == "chat" and settings.viewer_memory and self.store.consented(settings.channel, event.get("subject_id", "")):
                eligible.add(event["subject_id"])
        memories = [item for item in self.store.memories(settings.channel) if item["subject_id"] in eligible]
        # Select bounded relevant memory rather than appending an unlimited history.
        words = set(re.findall(r"\w{4,}", " ".join(item["text"] for item in rows[-12:]).lower()))
        memories.sort(key=lambda item: len(words.intersection(re.findall(r"\w{4,}", item["fact"].lower()))), reverse=True)
        return {"now": datetime.now(timezone.utc).isoformat(), "channel": {key: self.channel.get(key) for key in ("name", "title", "category")}, "character": {"name": settings.bot_name, "personality": settings.personality}, "context": rows[-60:], "memories": memories[:12], "memory_subjects": list(eligible), "silence_seconds": round(max(0, now - self.last_voice)), "open_question": bool(self.last_question and self.last_owner_text <= self.last_question), "chat_included": not external or settings.cloud_chat_permission}

    async def conversation(self):
        while self.running:
            try:
                allowed, _ = self.can_contribute()
                settings = self.store.settings()
                quiet = time.time() - self.last_voice
                if allowed and not self.pending and time.time() - self.last_analysis >= max(25, settings.min_interval / 2) and (quiet >= settings.silence_after or time.time() - self.last_observation < 25):
                    await self.analyze()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.notify("error", "Gesprächsverarbeitung", str(exc) if isinstance(exc, (AIError, TwitchError, ValueError)) else "Ein Beitrag konnte nicht verarbeitet werden. Es wurde kein Erfolg angenommen.")
                self.last_analysis = time.time()
            if self.pending and time.time() - self.pending["created"] > 90:
                self.pending = None
            await asyncio.sleep(2)

    async def analyze(self):
        if self.analysis_lock.locked():
            return
        async with self.analysis_lock:
            allowed, reason = self.can_contribute()
            if not allowed:
                raise ValueError(reason)
            epoch, revision = self.epoch, self.revision
            self.last_analysis = time.time()
            context = self.context()
            image = self.screenshot if self.store.settings().screenshots_enabled and time.time() - self.screenshot_time < 90 else None
            result = await self.ai.generate(context, image)
            if not self.running or self.paused or epoch != self.epoch:
                return
            self.save_memories(result, context)
            if result["action"] == "skip":
                await self.notify("info", "Hört weiter zu", str(result.get("reason", "Kein passender Anlass.")))
                return
            if revision != self.revision:
                await self.notify("info", "Beitrag verworfen", "Das Gespräch hat sich während der Formulierung verändert.")
                return
            text = self.clean_message(result.get("message", ""))
            if not text:
                return
            self.pending = {"id": uuid.uuid4().hex, "text": text, "kind": result.get("kind", "comment"), "reason": str(result.get("reason", ""))[:300], "created": time.time(), "epoch": epoch, "revision": revision, "demo": False}
            if self.store.settings().mode == "automatic":
                await self.send_pending(self.pending["id"], automatic=True)

    def save_memories(self, result, context):
        settings = self.store.settings()
        subjects = set(context["memory_subjects"])
        events = {item["id"]: item for item in context["context"]}
        candidates = result.get("memories", [])
        if not isinstance(candidates, list):
            return
        for memory in candidates[:5]:
            if not isinstance(memory, dict):
                continue
            evidence = events.get(memory.get("evidence_id"))
            try:
                confidence = float(memory.get("confidence", 0))
            except (TypeError, ValueError):
                continue
            subject = memory.get("subject_id")
            fact = memory.get("fact")
            if not evidence or subject not in subjects or confidence < 0.9 or not isinstance(fact, str) or evidence.get("subject_id") != subject or not evidence.get("remember"):
                continue
            if evidence["kind"] == "audio" and evidence.get("role") != "streamer":
                continue
            if evidence["kind"] == "chat" and not self.store.consented(settings.channel, subject):
                continue
            self.store.add_memory(settings.channel, subject, evidence["name"], fact, f'{datetime.fromtimestamp(evidence["time"], timezone.utc).isoformat()} · {evidence["text"][:600]}')

    def clean_message(self, text):
        if not isinstance(text, str):
            return ""
        text = " ".join(text.split()).strip('"')
        if not text or text.startswith(("/", ".", "!")) or re.search(r"https?://|www\.", text, re.I):
            return ""
        text = text[:self.store.settings().max_message_length]
        if any(difflib.SequenceMatcher(None, text.lower(), previous.lower()).ratio() > 0.88 for previous in self.recent_sent):
            return ""
        return text

    async def send_pending(self, draft_id, automatic=False):
        task = asyncio.current_task()
        self.outgoing.add(task)
        try:
            async with self.send_lock:
                draft = self.pending
                if not draft or draft["id"] != draft_id or draft.get("demo"):
                    raise ValueError("Dieser Vorschlag ist nicht mehr verfügbar.")
                epoch = self.epoch
                settings = self.store.settings()
                if automatic and settings.mode != "automatic":
                    return
                if settings.stream_delay:
                    await asyncio.sleep(settings.stream_delay)
                allowed, reason = self.can_contribute()
                if not allowed:
                    raise ValueError(reason)
                if not self.pending or self.pending["id"] != draft_id or epoch != self.epoch or draft["epoch"] != self.epoch or time.time() - draft["created"] > 90 or draft["revision"] != self.revision:
                    self.pending = None
                    raise ValueError("Der Vorschlag ist veraltet. Bitte einen neuen Beitrag erzeugen.")
                # Refresh the live state just before every externally visible message.
                await self.check_channel()
                if not self.running or self.paused or not self.channel.get("live") or self.epoch != epoch or draft["revision"] != self.revision or not self.pending or self.pending["id"] != draft_id or (automatic and self.store.settings().mode != "automatic"):
                    raise ValueError("Der Versand wurde abgebrochen.")
                message_id = await self.twitch.send(self.channel["id"], draft["text"])
                self.pending = None
                now = time.time()
                self.last_contribution = now
                self.sent_times.append(now)
                self.recent_sent.append(draft["text"])
                self.event("bot", settings.bot_name, draft["text"], message_id=message_id, confirmed=True)
                if draft["kind"] == "question":
                    self.last_question = now
                await self.notify("success", "Auf Twitch veröffentlicht", "Twitch hat den Versand bestätigt.")
        finally:
            self.outgoing.discard(task)

    async def demo_start(self):
        await self.stop()
        self.demo = True
        self.events.clear()
        self.event("audio", "Mikrofon", "Ich probiere heute mal den Bogen. Mit dem Schwert bin ich gestern dauernd gescheitert.", role="streamer", demo=True)
        self.event("chat", "Lena", "Der Bogen passt auch besser zu deinem Ausweichen :D", demo=True)
        self.event("audio", "Gameplay", "Ein Kampf beginnt; weitere Details sind aus dem Audio allein unklar.", role="game", demo=True)
        self.pending = {"id": "demo", "text": "Mit dem Bogen hast du wenigstens etwas Abstand. Willst du diesmal komplett auf Fernkampf gehen?", "kind": "question", "reason": "Greift die genannte Spielentscheidung und den Chatbeitrag auf. Beispielsituation, ohne KI-Aufruf und ohne Twitch-Versand.", "created": time.time(), "epoch": self.epoch, "revision": self.revision, "demo": True}
        await self.notify("info", "Demo geöffnet", "Alle angezeigten Gesprächsinhalte sind Beispieldaten. Es wird nichts aufgenommen oder gesendet.")

    def snapshot(self):
        settings = self.store.settings()
        _, reason = self.can_contribute()
        return {"running": self.running, "paused": self.paused, "demo": self.demo, "channel": self.channel, "chat_connected": self.twitch.connected, "reason": reason, "events": list(self.events)[-100:], "logs": list(self.logs)[:80], "pending": self.pending, "sources": self.audio.snapshot(), "screenshot_time": self.screenshot_time, "sent_hour": sum(sent > time.time() - 3600 for sent in self.sent_times), "memories_count": len(self.store.memories(settings.channel)), "last_voice": self.last_voice}

    async def close(self):
        await self.stop()
        await self.twitch.close()
        await self.ai.close()
