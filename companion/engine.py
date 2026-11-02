import asyncio
import difflib
import re
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urlparse

from .ai import AI, AIError
from .audio import Audio, obs_screenshot
from .twitch import Twitch, TwitchError
from .mentions import addressed_to_bot, reply_key, REPLY_LIFETIME, DIRECT_REPLY_INTERVAL
from .conversation import combine_request, conversation_followup, memory_candidate


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
        self.analysis_started = 0
        self.analysis_kind = "general"
        self.last_analysis_duration = 0
        self.generation_task = None
        self.model_warmup_task = None
        self.model_warming = False
        self.model_warmup_started = 0
        self.generation_kind = None
        self.preempted_generation = None
        self.wake_conversation = asyncio.Event()
        self.memory_backlog = deque(maxlen=8)
        self.memory_seen = deque(maxlen=100)
        self.last_error = 0
        self.last_observation = 0
        self.last_question = 0
        self.last_owner_text = 0
        self.sent_times = deque(maxlen=200)
        self.recent_sent = deque(maxlen=10)
        self.pending = None
        self.reply_requests = deque(maxlen=12)
        self.handled_replies = deque(maxlen=100)
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
        # New observations continue the conversation; only a verified reset
        # invalidates work already in flight.
        if kind in {"audio", "chat"}:
            self.last_observation = row["time"]
        return row

    async def transcript(self, source, text, *, utterance_id=None, final=True):
        if not self.running or self.paused:
            return
        role = "conversation" if source.id == self.twitch_mix_source else source.role
        subject = self.channel.get("id", "") if role == "streamer" else ""
        extra = {"role": role, "subject_id": subject, "remember": source.remember and role == "streamer" and final, "provisional": not final, "speaker_name": self.channel.get("name", "Streamer") if role == "streamer" else source.name}
        row = next((event for event in self.events if utterance_id and event.get("utterance_id") == utterance_id), None)
        if row:
            self.events.remove(row)
            row.update(text=text[-2000:], time=time.time(), version=row.get("version", 0) + 1, **extra)
            self.events.append(row)
            self.last_observation = row["time"]
        else:
            row = self.event("audio", source.name, text[-2000:], utterance_id=utterance_id, version=1, **extra)
        if role == "streamer":
            self.last_owner_text = time.time()
            settings = self.store.settings()
            address = None
            if final and settings.provider == "local":
                names = [settings.bot_name, *settings.spoken_name_aliases.split(",")[:12]]
                bot = self.store.profiles()["bot"]
                for name in names:
                    name = name.strip()
                    address = addressed_to_bot({"message": {"text": text}}, bot, name) if name else None
                    if address:
                        if name.casefold() != settings.bot_name.casefold():
                            row["canonical_text"] = re.sub(r"(?<!\w)@?" + re.escape(name) + r"(?!\w)", lambda _: settings.bot_name, row["text"], flags=re.IGNORECASE)
                        break
            followup = final and settings.provider == "local" and conversation_followup(self.events, subject, text, spoken=True)
            previous = self.reply_target()
            combined = combine_request(previous, row) if final else None
            if combined:
                row.update(addressed_to_bot=True, address_kind="audio_continuation", speaker_name=self.channel.get("name", "Streamer"))
                combined["speaker_name"] = row["speaker_name"]
                await self.replace_reply(previous, combined)
            elif address or followup:
                row.update(addressed_to_bot=True, address_kind="audio_name" if address else "audio_followup", speaker_name=self.channel.get("name", "Streamer"))
                await self.schedule_reply(row)

    def clear_provisional(self):
        self.events = deque((event for event in self.events if not event.get("provisional")), maxlen=400)
        self.audio.clear_streaming_buffers()

    async def schedule_reply(self, row):
        previous = self.reply_target()
        chat_priority = row["kind"] == "chat" and previous and previous["kind"] == "audio"
        if chat_priority:
            self.reply_requests.appendleft(row)
            self.pending = None
            self.last_analysis = 0
        else:
            self.reply_requests.append(row)
        if self.pending and not self.pending.get("reply_to"):
            self.pending = None
        await self.notify("info", "Ansprache erkannt", f'{row.get("speaker_name", row["name"])}: Der Beitrag wird bevorzugt auf eine Antwort geprüft.')
        if self.reply_target():
            self.wake_conversation.set()
            if self.generation_task and not self.generation_task.done() and (self.generation_kind != "reply" or chat_priority):
                self.preempted_generation = self.generation_task
                self.generation_task.cancel()

    async def replace_reply(self, previous, combined):
        self.finish_reply(previous)
        self.reply_requests = deque((row for row in self.reply_requests if reply_key(row) != reply_key(previous)), maxlen=12)
        self.reply_requests.appendleft(combined)
        self.pending = None
        self.last_analysis = 0
        self.wake_conversation.set()
        if self.generation_task and not self.generation_task.done():
            self.preempted_generation = self.generation_task
            self.generation_task.cancel()
        await self.notify("info", "Nachtrag berücksichtigt", "Die Antwort wird mit der ergänzten Aussage neu formuliert.")

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
                self.events = deque((item for item in self.events if item.get("subject_id") != user_id and item.get("conversation_subject") != user_id), maxlen=400)
                self.reply_requests = deque((item for item in self.reply_requests if item.get("subject_id") != user_id), maxlen=12)
                self.memory_backlog = deque((job for job in self.memory_backlog if job["subject_id"] != user_id), maxlen=8)
                self.revision += 1
                self.pending = None
            await self.notify("info", "Zuschauer-Gedächtnis", f'{name}: {"aktiviert" if enable else "deaktiviert und gelöscht"}.')
            return
        if not text or text.startswith("!") or len(text) > 1000:
            return
        incoming_id = payload.get("message_id", "")
        if incoming_id and (incoming_id in self.handled_replies or any(item.get("message_id") == incoming_id for item in self.reply_requests)):
            return
        address = addressed_to_bot(payload, bot, settings.bot_name)
        if not address and conversation_followup(self.events, user_id, text):
            address = "chat_followup"
        row = self.event("chat", name, text, subject_id=user_id, remember=settings.viewer_memory and self.store.consented(settings.channel, user_id), message_id=incoming_id, addressed_to_bot=bool(address), address_kind=address)
        previous = self.reply_target()
        combined = combine_request(previous, row)
        if combined:
            row.update(addressed_to_bot=True, address_kind="chat_continuation")
            await self.replace_reply(previous, combined)
        elif address:
            await self.schedule_reply(row)
        self.last_owner_text = time.time()

    def reply_target(self, now=None):
        now = time.time() if now is None else now
        while self.reply_requests and (now - self.reply_requests[0]["time"] > REPLY_LIFETIME or reply_key(self.reply_requests[0]) in self.handled_replies):
            self.reply_requests.popleft()
        settings = self.store.settings()
        local = settings.provider != "openai" and urlparse(settings.api_base).hostname in {"127.0.0.1", "localhost", "::1"}
        if not local and not settings.cloud_chat_permission:
            return None
        return self.reply_requests[0] if self.reply_requests else None

    def finish_reply(self, target):
        if target:
            self.handled_replies.append(reply_key(target))

    def reply_still_valid(self, target):
        current = self.reply_target()
        return bool(current and target and reply_key(current) == reply_key(target))

    async def check_channel(self):
        channel = await self.twitch.channel()
        before = self.channel
        self.channel = channel
        self.channel_checked = time.time()
        fields = ("id", "category", "live")
        if before.get("id") and any(before.get(key) != channel.get(key) for key in fields):
            self.revision += 1
            self.pending = None
            self.screenshot = None
            self.events.clear()
            self.reply_requests.clear()
            self.memory_backlog.clear()
            self.last_observation = 0
        return self.channel

    def offline_test_active(self):
        settings = self.store.settings()
        return bool(settings.offline_test and settings.input_mode == "local" and self.channel.get("id") and not self.channel.get("live"))

    def channel_ready(self, now=None):
        now = time.time() if now is None else now
        return bool(self.channel.get("id") and now - self.channel_checked <= 70 and (self.channel.get("live") or self.offline_test_active()))

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
        self.reply_requests.clear()
        self.handled_replies.clear()
        self.memory_backlog.clear()
        self.memory_seen.clear()
        self.wake_conversation.clear()
        self.pending = None
        self.screenshot = None
        self.last_contribution = time.time() - settings.min_interval
        self.last_voice = time.time()
        self.last_analysis = 0
        self.last_owner_text = 0
        self.last_question = 0
        self.twitch_mix_source = None
        try:
            if settings.stt_provider == "local_streaming":
                await self.audio.prepare_streaming()
            if settings.input_mode == "local":
                await self.audio.start_local()
            elif settings.input_mode == "twitch":
                if not self.channel.get("live"):
                    raise ValueError("Für den Twitch-Audioempfang muss der Kanal live sein.")
                await self.audio.start_twitch()
            self.tasks = [asyncio.create_task(self.twitch.chat(self.channel["id"], self.on_chat)), asyncio.create_task(self.monitor()), asyncio.create_task(self.conversation()), asyncio.create_task(self.memory_worker())]
            self.begin_model_warmup()
        except BaseException:
            await self._stop()
            raise
        message = "Vorschläge erscheinen hier." if settings.mode == "preview" else "Automatische Beiträge sind eingeschaltet."
        if self.offline_test_active():
            message += " Offline-Test: Lokales Audio wird verarbeitet; Beiträge können im echten Twitch-Chat erscheinen."
        await self.notify("success", "Begleiter gestartet", message)

    def begin_model_warmup(self):
        if not self.running or self.store.settings().provider != "local" or (self.model_warmup_task and not self.model_warmup_task.done()):
            return
        self.model_warming = True
        self.model_warmup_started = time.time()
        self.model_warmup_task = asyncio.create_task(self.warm_model())
        self.tasks.append(self.model_warmup_task)

    async def warm_model(self):
        try:
            if await self.ai.warmup(self.context()) and self.running:
                await self.notify("info", "Lokales Modell bereit", "Das Gesprächsmodell ist geladen. Aufnahme und Chat werden weiter berücksichtigt.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.running:
                self.paused = True
                await self.notify("error", "Lokales Modell nicht bereit", str(exc) if isinstance(exc, (AIError, ValueError)) else "Das Laden ist fehlgeschlagen. Lokalen KI-Server prüfen und anschließend fortsetzen.")
        finally:
            if self.model_warmup_task is asyncio.current_task():
                self.model_warming = False
                self.model_warmup_started = 0
                self.wake_conversation.set()

    async def stop(self):
        async with self.lifecycle_lock:
            await self._stop()

    async def _stop(self):
        self.running = False
        self.epoch += 1
        self.pending = None
        self.reply_requests.clear()
        self.memory_backlog.clear()
        tasks = self.tasks + list(self.outgoing) + ([self.generation_task] if self.generation_task else [])
        self.clear_provisional()
        self.tasks = []
        current = asyncio.current_task()
        for task in tasks:
            if task is not current:
                task.cancel()
        await asyncio.gather(*(task for task in tasks if task is not current), return_exceptions=True)
        self.model_warming = False
        self.model_warmup_task = None
        self.model_warmup_started = 0
        await self.audio.stop()
        self.screenshot = None
        self.last_voice = 0
        await self.notify("info", "Begleiter gestoppt", "Audioverarbeitung und automatische Beiträge sind beendet.")

    async def monitor(self):
        last_image_attempt = 0
        while self.running:
            try:
                if time.time() - self.channel_checked > 25:
                    await self.check_channel()
                    if not self.channel_ready():
                        self.pending = None
                        self.screenshot = None
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
        target = self.reply_target(now)
        if not self.running or self.paused or self.demo:
            return False, "Begleiter ist angehalten."
        if self.model_warming:
            return False, f"Lokales KI-Modell wird vorbereitet seit {round(now - self.model_warmup_started)} Sekunden."
        if not self.channel_ready(now):
            return False, "Kein bestätigter aktueller Livestream."
        if not self.twitch.connected:
            return False, "Chat-Verbindung wird hergestellt."
        interval = min(settings.min_interval, DIRECT_REPLY_INTERVAL) if target else settings.min_interval
        if now - self.last_contribution < interval:
            return False, "Gesprächspause zwischen Beiträgen."
        if sum(sent > now - 3600 for sent in self.sent_times) >= settings.max_per_hour:
            return False, "Stundenlimit erreicht."
        if now - self.last_observation > 180:
            return False, "Es fehlt aktueller Gesprächskontext."
        if target and target["kind"] == "audio" and (now - self.last_voice < 2 or any(event["kind"] == "audio" and event.get("role") == "streamer" and event.get("provisional") and event["time"] > target["time"] for event in self.events)):
            return False, "Die gesprochene Aussage wird noch ergänzt."
        if not target and now - self.last_voice < 2:
            return False, "Der Streamer spricht gerade."
        if not target and self.last_question and self.last_owner_text <= self.last_question:
            return False, "Die letzte Frage ist noch offen."
        return True, "Eine Ansprache wird bevorzugt beantwortet." if target else "Bereit für einen passenden Beitrag."

    def context(self):
        settings, _, _, external = self.ai.connection()
        bot = self.store.profiles()["bot"] or {}
        now = time.time()
        owner = self.channel.get("id", "owner")
        eligible = {owner} if settings.owner_memory else set()
        target = self.reply_target(now)
        answered = set()
        for event in self.events:
            if event["kind"] == "bot" and event.get("confirmed"):
                answered.update(event.get("reply_request_ids") or [event.get("reply_request_id") or event.get("reply_parent_message_id")])
        rows = []
        for event in self.events:
            if event["time"] < now - 600:
                continue
            if external and not settings.cloud_chat_permission and event["kind"] == "chat":
                continue
            # Only the current request is an answer target. Keep completed and
            # other queued requests in the UI without inviting another answer.
            resolved = event.get("addressed_to_bot") and (not target or reply_key(event) != reply_key(target))
            if resolved and reply_key(event) not in answered:
                continue
            row = dict(event)
            if resolved:
                row.update(addressed_to_bot=False, address_kind=None, resolved=True)
            rows.append(row)
        if settings.provider == "local":
            # The model projection selects a small relevant dialogue below.
            # Retain enough observations here to find prior chat turns among
            # continuously arriving microphone sentences.
            rows = rows[-30:]
            if target and not any(row["id"] == target["id"] for row in rows):
                rows.insert(0, dict(target))
        for event in rows:
            if event.get("remember") and event["kind"] == "chat" and settings.viewer_memory and self.store.consented(settings.channel, event.get("subject_id", "")):
                eligible.add(event["subject_id"])
        memories = [item for item in self.store.memories(settings.channel) if item["subject_id"] in eligible]
        # Audio-source labels identify devices, not people. Keep old stored
        # labels intact while projecting the verified channel-owner identity.
        memories = [{**item, "subject_name": self.channel.get("name") or item.get("subject_name", "")} if item["subject_id"] == owner else item for item in memories]
        if target:
            named_subjects = {target.get("subject_id")}
            request_text = target.get("canonical_text", target["text"])
            for item in memories:
                name = item.get("subject_name", "").strip()
                if name and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", request_text, re.IGNORECASE):
                    named_subjects.add(item["subject_id"])
            memories = [item for item in memories if item["subject_id"] in named_subjects]
        # Select bounded relevant memory rather than appending an unlimited history.
        words = set(re.findall(r"\w{4,}", " ".join(item["text"] for item in rows[-12:]).lower()))
        memories.sort(key=lambda item: len(words.intersection(re.findall(r"\w{4,}", item["fact"].lower()))), reverse=True)
        if settings.provider == "local":
            memories = [{key: item[key] for key in ("id", "subject_id", "subject_name", "fact", "created", "source") if key in item} for item in memories[:3]]
        return {"now": datetime.now(timezone.utc).isoformat(), "channel": {"name": self.channel.get("name"), "title": "Offline-Test mit lokalem Mikrofon" if self.offline_test_active() else self.channel.get("title"), "category": None if self.offline_test_active() else self.channel.get("category")}, "character": {"name": settings.bot_name, "account_login": bot.get("login", ""), "account_display_name": bot.get("display_name", ""), "personality": settings.personality}, "context": rows[-60:], "reply_to": target, "memories": memories[:12], "memory_subjects": list(eligible), "silence_seconds": round(max(0, now - self.last_voice)), "open_question": bool(self.last_question and self.last_owner_text <= self.last_question), "chat_included": not external or settings.cloud_chat_permission}

    async def conversation(self):
        while self.running:
            try:
                if self.pending and self.draft_expired(self.pending):
                    self.pending = None
                allowed, _ = self.can_contribute()
                settings = self.store.settings()
                quiet = time.time() - self.last_voice
                target = self.reply_target()
                analysis_interval = 10 if target else max(25, settings.min_interval / 2)
                if self.pending and settings.mode == "automatic" and allowed:
                    await self.send_pending(self.pending["id"], automatic=True)
                elif allowed and not self.pending and time.time() - self.last_analysis >= analysis_interval and (target or quiet >= settings.silence_after or time.time() - self.last_observation < 25):
                    await self.analyze()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.notify("error", "Gesprächsverarbeitung", str(exc) if isinstance(exc, (AIError, TwitchError, ValueError)) else "Ein Beitrag konnte nicht verarbeitet werden. Es wurde kein Erfolg angenommen.")
                self.last_analysis = time.time()
            try:
                await asyncio.wait_for(self.wake_conversation.wait(), timeout=0.25 if self.reply_target() else 2)
            except asyncio.TimeoutError:
                pass
            self.wake_conversation.clear()

    async def run_model(self, context, screenshot=None, *, kind="general"):
        generation = asyncio.create_task(self.ai.generate(context, screenshot))
        self.generation_task, self.generation_kind = generation, kind
        try:
            result = await generation
            if self.preempted_generation is generation:
                self.last_analysis = 0
                return None
            return result
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            if self.preempted_generation is generation:
                self.last_analysis = 0
                await self.notify("info", "Ansprache hat Vorrang", "Die laufende Berechnung wurde für eine aktuelle Antwort abgebrochen.")
                return None
            if not self.running or self.paused:
                return None
            raise
        finally:
            if self.generation_task is generation:
                self.generation_task, self.generation_kind = None, None
            if self.preempted_generation is generation:
                self.preempted_generation = None

    def defer_memory(self, context):
        target = context.get("reply_to")
        if not target:
            target = next((row for row in reversed(context.get("context", [])) if row.get("remember") and not row.get("provisional") and row.get("subject_id") in context["memory_subjects"] and memory_candidate(row.get("text")) and reply_key(row) not in self.memory_seen), None)
        if self.store.settings().provider != "local" or not target or not target.get("remember") or not memory_candidate(target.get("text")) or target.get("subject_id") not in context["memory_subjects"]:
            return
        if reply_key(target) in self.memory_seen or any(job["id"] == reply_key(target) for job in self.memory_backlog):
            return
        self.memory_seen.append(reply_key(target))
        self.memory_backlog.append({"id": reply_key(target), "subject_id": target["subject_id"], "owner": target["kind"] == "audio" and target.get("role") == "streamer", "channel": self.store.settings().channel, "revision": self.revision, "time": target["time"], "context": {"now": context["now"], "context": [dict(target)], "memories": context["memories"], "memory_subjects": [target["subject_id"]], "memory_only": True}})

    async def process_memory(self):
        if self.model_warming or self.analysis_lock.locked() or not self.memory_backlog or self.reply_target():
            return
        async with self.analysis_lock:
            job = self.memory_backlog.popleft()
            settings = self.store.settings()
            permitted = settings.owner_memory and job["subject_id"] == self.channel.get("id") if job.get("owner") else settings.viewer_memory and self.store.consented(settings.channel, job["subject_id"])
            if not self.running or self.paused or settings.provider != "local" or not permitted or not self.channel_ready() or job["channel"] != settings.channel or job["revision"] != self.revision or time.time() - job["time"] > 600:
                return
            epoch, revision = self.epoch, self.revision
            self.analysis_started, self.analysis_kind = time.time(), "memory"
            try:
                result = await self.run_model(job["context"], kind="memory")
                if self.running and epoch == self.epoch and revision == self.revision:
                    if result is None:
                        self.memory_backlog.appendleft(job)
                    else:
                        self.save_memories(result, job["context"])
            finally:
                self.analysis_started = 0

    async def memory_worker(self):
        while self.running:
            try:
                if not self.paused and not self.reply_target() and not self.pending:
                    await self.process_memory()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.notify("warning", "Gedächtnisprüfung", str(exc) if isinstance(exc, (AIError, ValueError)) else "Eine Notiz konnte nicht geprüft werden.")
            await asyncio.sleep(2)

    @staticmethod
    def draft_expired(draft):
        return time.time() > min(draft["created"] + 90, draft.get("expires", draft["created"] + 90))

    async def analyze(self):
        if self.analysis_lock.locked():
            return
        async with self.analysis_lock:
            allowed, reason = self.can_contribute()
            if not allowed:
                raise ValueError(reason)
            epoch, revision = self.epoch, self.revision
            self.last_analysis = time.time()
            self.analysis_started = self.last_analysis
            self.analysis_kind = "reply" if self.reply_target() else "general"
            expires = self.last_analysis + 90
            try:
                context = self.context()
                target = context["reply_to"]
                if target:
                    expires = min(expires, target["time"] + REPLY_LIFETIME)
                image = self.screenshot if self.store.settings().screenshots_enabled and time.time() - self.screenshot_time < 90 else None
                result = await self.run_model(context, image, kind="reply" if target else "general")
                if result is None:
                    return
                if not self.running or self.paused or epoch != self.epoch:
                    return
                if revision != self.revision:
                    await self.notify("info", "Beitrag verworfen", "Kanal, Kategorie oder freigegebener Kontext wurden geändert.")
                    return
                if time.time() > expires:
                    self.finish_reply(target)
                    await self.notify("warning", "KI-Antwort zu spät", "Die Berechnung dauerte länger als 90 Sekunden. Der Beitrag wurde nicht versendet.")
                    return
                self.save_memories(result, context)
                self.defer_memory(context)
                if not target and self.reply_target():
                    await self.notify("info", "Ansprache hat Vorrang", "Ein allgemeiner Vorschlag macht Platz für eine gezielte Chatantwort.")
                    return
                if result["action"] == "skip":
                    self.finish_reply(target)
                    await self.notify("info", "Hört weiter zu", str(result.get("reason", "Kein passender Anlass.")))
                    return
                text = self.clean_message(result.get("message", ""), allow_repeat=bool(target))
                if not text:
                    self.finish_reply(target)
                    return
                kind = result.get("kind", "comment")
                if kind == "question" and "?" in result.get("message", "") and "?" not in text:
                    kind = "comment"
                self.pending = {"id": uuid.uuid4().hex, "text": text, "kind": kind, "reason": str(result.get("reason", ""))[:300], "created": time.time(), "expires": expires, "epoch": epoch, "revision": revision, "reply_to": target, "demo": False}
                if self.store.settings().mode == "automatic":
                    if self.can_contribute()[0]:
                        await self.send_pending(self.pending["id"], automatic=True)
                    else:
                        await self.notify("info", "Beitrag wartet", "Der Vorschlag wartet auf eine passende Gesprächspause.")
            finally:
                self.last_analysis_duration = time.time() - self.analysis_started
                self.analysis_started = 0

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
            if not evidence or evidence.get("provisional") or not memory_candidate(evidence.get("text")) or subject not in subjects or confidence < 0.9 or not isinstance(fact, str) or evidence.get("subject_id") != subject or not evidence.get("remember"):
                continue
            if evidence["kind"] == "audio" and evidence.get("role") != "streamer":
                continue
            if evidence["kind"] == "chat" and not self.store.consented(settings.channel, subject):
                continue
            self.store.add_memory(settings.channel, subject, evidence.get("speaker_name", evidence["name"]), fact, f'{datetime.fromtimestamp(evidence["time"], timezone.utc).isoformat()} · {evidence["text"][:600]}')

    def clean_message(self, text, *, allow_repeat=False):
        if not isinstance(text, str):
            return ""
        text = " ".join(text.split()).strip('"')
        if not text or text.startswith(("/", ".", "!")) or re.search(r"https?://|www\.", text, re.I):
            return ""
        maximum = self.store.settings().max_message_length
        if len(text) > maximum:
            clipped = text[:maximum - 1]
            endings = [match.end() for match in re.finditer(r"[.!?](?=\s|$)", clipped)]
            if endings and endings[-1] >= maximum / 2:
                text = clipped[:endings[-1]]
            else:
                space = clipped.rfind(" ")
                text = (clipped[:space] if space > maximum / 2 else clipped).rstrip(" ,;:-") + "…"
        if not allow_repeat and any(difflib.SequenceMatcher(None, text.lower(), previous.lower()).ratio() > 0.88 for previous in self.recent_sent):
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
                target = draft.get("reply_to")
                if target and not self.reply_still_valid(target):
                    self.pending = None
                    raise ValueError("Diese Ansprache ist nicht mehr aktuell.")
                if not target and self.reply_target():
                    self.pending = None
                    raise ValueError("Eine direkte Ansprache hat Vorrang.")
                epoch = self.epoch
                settings = self.store.settings()
                if automatic and settings.mode != "automatic":
                    return
                if settings.stream_delay:
                    await asyncio.sleep(settings.stream_delay)
                allowed, reason = self.can_contribute()
                if not allowed:
                    raise ValueError(reason)
                if not self.pending or self.pending["id"] != draft_id or epoch != self.epoch or draft["epoch"] != self.epoch or self.draft_expired(draft) or draft["revision"] != self.revision:
                    self.pending = None
                    raise ValueError("Der Vorschlag ist veraltet. Bitte einen neuen Beitrag erzeugen.")
                # Refresh the live state just before every externally visible message.
                await self.check_channel()
                if not self.can_contribute()[0] or self.epoch != epoch or self.draft_expired(draft) or draft["revision"] != self.revision or not self.pending or self.pending["id"] != draft_id or (target and not self.reply_still_valid(target)) or (not target and self.reply_target()) or (automatic and self.store.settings().mode != "automatic"):
                    raise ValueError("Der Versand wurde abgebrochen.")
                reply_options = {"reply_parent_message_id": target["message_id"]} if target and target.get("message_id") else {}
                message_id = await self.twitch.send(self.channel["id"], draft["text"], **reply_options)
                self.finish_reply(target)
                self.pending = None
                now = time.time()
                self.last_contribution = now
                self.sent_times.append(now)
                self.recent_sent.append(draft["text"])
                self.event("bot", settings.bot_name, draft["text"], message_id=message_id, confirmed=True, reply_parent_message_id=target.get("message_id") if target else None, response_seconds=round(now - target["time"], 2) if target else None, reply_request_id=reply_key(target) if target else None, reply_request_ids=target.get("combined_reply_ids", [reply_key(target)]) if target else [], conversation_subject=target.get("subject_id", "") if target else self.channel.get("id", ""), conversation_question=draft["kind"] == "question")
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
        analyzing = bool(self.analysis_started and self.analysis_lock.locked() and not self.model_warming)
        if analyzing and self.running and not self.paused:
            activity = "prüft Erinnerungen" if self.analysis_kind == "memory" else "formuliert"
            reason = f"KI {activity} seit {round(time.time() - self.analysis_started)} Sekunden."
        return {"running": self.running, "paused": self.paused, "demo": self.demo, "offline_test": self.offline_test_active(), "channel": self.channel, "chat_connected": self.twitch.connected, "reason": reason, "analyzing": analyzing, "analysis_kind": self.analysis_kind, "analysis_seconds": round(time.time() - self.analysis_started, 1) if analyzing else 0, "last_analysis_seconds": round(self.last_analysis_duration, 1), "last_ai_metrics": self.ai.last_metrics, "events": list(self.events)[-100:], "logs": list(self.logs)[:80], "pending": self.pending, "sources": self.audio.snapshot(), "screenshot_time": self.screenshot_time, "sent_hour": sum(sent > time.time() - 3600 for sent in self.sent_times), "memories_count": len(self.store.memories(settings.channel)), "last_voice": self.last_voice}

    async def close(self):
        await self.stop()
        await self.twitch.close()
        await self.ai.close()
