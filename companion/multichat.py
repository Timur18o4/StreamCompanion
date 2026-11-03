"""Read the local CNG MultiChat SSE stream without Twitch authentication."""
import asyncio
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import time
import re
import uuid

import httpx


def normalize_message(data):
    if not isinstance(data, dict):
        return None
    kind = str(data.get("event") or data.get("type") or "chat").lower()
    if kind not in {"chat", "chat_message", "message"}:
        return None
    platform = str(data.get("platform") or data.get("source") or "").strip().lower()
    if platform not in {"twitch", "youtube", "kick", "tiktok", "cng"}:
        return None
    text = data.get("message", data.get("content"))
    if isinstance(text, dict):
        text = text.get("text")
    name = data.get("username") or data.get("userName") or data.get("displayName")
    if not isinstance(text, str) or not isinstance(name, str) or not text.strip() or len(text) > 1000 or not name.strip():
        return None
    # Names alone are not sufficiently stable for permanent person memories.
    raw_user = str(data.get("userId") or data.get("user_id") or "").strip()
    subject = raw_user if platform == "twitch" and raw_user else f"{platform}:{raw_user}" if raw_user else f"{platform}:name:{name.casefold()}"
    raw_id = str(data.get("id") or data.get("messageId") or data.get("chatId") or "").strip()
    timestamp = data.get("timestamp") or data.get("createdAt") or data.get("time")
    if timestamp:
        try:
            stamp = float(timestamp) if isinstance(timestamp, (int, float)) or isinstance(timestamp, str) and re.fullmatch(r"\d+(?:\.\d+)?", timestamp) else datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")).timestamp()
            if stamp > 10**11:
                stamp /= 1000
            if time.time() - stamp > 180 or stamp - time.time() > 60:
                return None
        except (TypeError, ValueError, OverflowError):
            return None
    key = f"{platform}:{raw_id}" if raw_id else hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest() + ":" + str(int(time.time() / 3))
    return {"chatter_user_id": subject, "chatter_user_name": name[:80], "message_id": key,
            "message": {"text": text}, "platform": platform, "source_user_id": raw_user,
            "source_message_id": raw_id if platform == "twitch" else None,
            "memory_identity_verified": bool(raw_user), "source_login": str(data.get("login") or name).casefold()}


class MultiChat:
    def __init__(self, store, notify):
        self.store, self.notify = store, notify
        self.connected = False
        self.last_packet = 0
        self.seen = deque(maxlen=2000)
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(25, connect=5), follow_redirects=False, trust_env=False)

    async def listen(self, callback):
        retry = 1
        while True:
            try:
                async with self.http.stream("GET", self.store.settings().multichat_url, headers={"Accept": "text/event-stream", "Last-Event-ID": ""}) as response:
                    response.raise_for_status()
                    if "text/event-stream" not in response.headers.get("content-type", ""):
                        raise ValueError("Der MultiChat-Endpunkt liefert keinen Ereignisstrom.")
                    self.connected = True
                    self.last_packet = time.time()
                    retry = 1
                    await self.notify("success", "MultiChat verbunden", "Chatnachrichten werden aus dem lokalen Ereignisstrom empfangen.")
                    data, length = [], 0
                    async for line in response.aiter_lines():
                        self.last_packet = time.time()
                        if not line:
                            if data:
                                try:
                                    message = normalize_message(json.loads("\n".join(data)))
                                except (ValueError, TypeError):
                                    message = None
                                if message and message["message_id"] not in self.seen:
                                    self.seen.append(message["message_id"])
                                    await callback(message)
                            data, length = [], 0
                        elif line.startswith("data:"):
                            length += len(line)
                            if length > 65536:
                                raise ValueError("Ein MultiChat-Ereignis ist zu gross.")
                            data.append(line[5:].lstrip(" "))
                    raise ConnectionError("Der MultiChat-Ereignisstrom wurde beendet.")
            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, ValueError, ConnectionError):
                self.connected = False
                await self.notify("warning", "MultiChat unterbrochen", "Die lokale Verbindung wird automatisch erneut aufgebaut.")
                await asyncio.sleep(retry)
                retry = min(15, retry * 2)
            finally:
                self.connected = False

    async def close(self):
        await self.http.aclose()
