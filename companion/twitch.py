import asyncio
import json
import time
from collections import deque

import httpx
import websockets


class TwitchError(RuntimeError):
    pass


class Twitch:
    SCOPES = {"owner": ["channel:bot"], "bot": ["user:read:chat", "user:write:chat"]}

    def __init__(self, store, notify):
        self.store = store
        self.notify = notify
        self.http = httpx.AsyncClient(timeout=20, follow_redirects=False)
        self.locks = {role: asyncio.Lock() for role in self.SCOPES}
        self.pending = {}
        self.oauth_tasks = {}
        self.seen = deque(maxlen=2000)
        self.connected = False

    def client_id(self):
        value = self.store.settings().twitch_client_id
        if not value:
            raise TwitchError("Zuerst die Twitch-App-ID hinterlegen.")
        return value

    async def device_start(self, role: str):
        await self.cancel_auth(role)
        response = await self.http.post("https://id.twitch.tv/oauth2/device", data={"client_id": self.client_id(), "scopes": " ".join(self.SCOPES[role])})
        if response.status_code != 200:
            raise TwitchError("Twitch konnte keinen Freigabecode erstellen. App-ID und öffentlichen App-Typ prüfen.")
        payload = response.json()
        self.pending[role] = {"status": "pending", "user_code": payload["user_code"], "verification_uri": payload["verification_uri"], "expires_at": time.time() + payload["expires_in"]}
        self.oauth_tasks[role] = asyncio.create_task(self._poll_device(role, payload, self.client_id()))
        return self.pending[role]

    async def _poll_device(self, role, payload, client_id):
        interval = max(5, int(payload.get("interval", 5)))
        try:
            while time.time() < self.pending[role]["expires_at"]:
                await asyncio.sleep(interval)
                response = await self.http.post("https://id.twitch.tv/oauth2/token", data={"client_id": client_id, "scopes": " ".join(self.SCOPES[role]), "device_code": payload["device_code"], "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
                if response.status_code == 200:
                    token = response.json()
                    validation = await self._validate(token["access_token"], client_id, self.SCOPES[role])
                    user = await self.http.get("https://api.twitch.tv/helix/users", headers={"Client-ID": client_id, "Authorization": "Bearer " + token["access_token"]})
                    user.raise_for_status()
                    profile = user.json()["data"][0]
                    token.update({key: profile[key] for key in ("id", "login", "display_name", "profile_image_url")})
                    token.update(scopes=validation.get("scopes", []), validated_at=time.time(), expires_at=time.time() + validation.get("expires_in", 0))
                    self.store.put("twitch_" + role, token)
                    settings = self.store.settings()
                    if role == "owner" and not settings.channel:
                        settings.channel = profile["login"]
                        self.store.put("settings", settings.model_dump())
                    self.pending[role] = {"status": "connected"}
                    await self.notify("success", "Twitch verbunden", f'{profile["display_name"]} wurde als {"Kanalbesitzer" if role == "owner" else "Bot"} verbunden.')
                    return
                message = response.json().get("message", response.json().get("error", ""))
                if message == "authorization_pending":
                    continue
                if message == "slow_down":
                    interval += 5
                    continue
                raise TwitchError("Freigabe abgelehnt oder abgelaufen. Bitte einen neuen Code anfordern.")
            raise TwitchError("Der Freigabecode ist abgelaufen.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.pending[role] = {"status": "error", "message": str(exc) if isinstance(exc, TwitchError) else "Twitch-Anmeldung fehlgeschlagen. Verbindung und Freigabe prüfen."}
            await self.notify("error", "Twitch-Anmeldung", self.pending[role]["message"])

    async def cancel_auth(self, role):
        task = self.oauth_tasks.pop(role, None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.pending.pop(role, None)

    async def _validate(self, access, client_id, scopes):
        response = await self.http.get("https://id.twitch.tv/oauth2/validate", headers={"Authorization": "OAuth " + access})
        if response.status_code != 200:
            raise TwitchError("Die Twitch-Anmeldung ist nicht mehr gültig.")
        payload = response.json()
        if payload.get("client_id") != client_id or not set(scopes).issubset(payload.get("scopes", [])):
            raise TwitchError("Twitch-App oder Berechtigungen stimmen nicht überein. Bitte neu anmelden.")
        return payload

    async def token(self, role, force_refresh=False, stale=None):
        async with self.locks[role]:
            token = self.store.get("twitch_" + role)
            if not token:
                raise TwitchError("Bitte den " + ("Kanalbesitzer" if role == "owner" else "Bot-Account") + " bei Twitch anmelden.")
            if force_refresh and stale and token["access_token"] != stale:
                return token["access_token"]
            if not force_refresh and time.time() - token.get("validated_at", 0) > 3500:
                try:
                    validation = await self._validate(token["access_token"], self.client_id(), self.SCOPES[role])
                    token.update(validated_at=time.time(), expires_at=time.time() + validation.get("expires_in", 0))
                    self.store.put("twitch_" + role, token)
                except TwitchError:
                    force_refresh = True
            if force_refresh or token.get("expires_at", 0) <= time.time():
                if not token.get("refresh_token"):
                    raise TwitchError("Twitch-Anmeldung erneuern.")
                data = {"client_id": self.client_id(), "grant_type": "refresh_token", "refresh_token": token["refresh_token"]}
                client_secret = self.store.get("twitch_client_secret")
                if client_secret:
                    data["client_secret"] = client_secret
                response = await self.http.post("https://id.twitch.tv/oauth2/token", data=data)
                if response.status_code != 200:
                    raise TwitchError("Die Twitch-Anmeldung konnte nicht erneuert werden. Bitte neu anmelden.")
                token.update(response.json())
                token.update(expires_at=time.time() + token["expires_in"], validated_at=time.time())
                # Public-device refresh tokens rotate. Persist the replacement atomically.
                self.store.put("twitch_" + role, token)
            return token["access_token"]

    async def request(self, method, path, role="owner", **kwargs):
        access = await self.token(role)
        for attempt in range(2):
            response = await self.http.request(method, "https://api.twitch.tv/helix/" + path, headers={"Authorization": "Bearer " + access, "Client-ID": self.client_id()}, **kwargs)
            if response.status_code == 401 and attempt == 0:
                access = await self.token(role, True, access)
                continue
            if response.status_code == 429:
                raise TwitchError("Twitch begrenzt gerade die Anfragen. Bitte kurz warten.")
            if response.status_code >= 400:
                raise TwitchError(f"Twitch-Anfrage fehlgeschlagen (HTTP {response.status_code}). Berechtigungen und Kanal prüfen.")
            return response.json() if response.content else {}
        raise TwitchError("Twitch-Anmeldung erneuern.")

    async def channel(self):
        settings = self.store.settings()
        users = await self.request("GET", "users", params={"login": settings.channel})
        if not users.get("data"):
            raise TwitchError("Der Twitch-Kanal wurde nicht gefunden.")
        profile = users["data"][0]
        owner = self.store.profiles()["owner"]
        if not owner or owner["id"] != profile["id"]:
            raise TwitchError("Bitte den Besitzer des ausgewählten Kanals anmelden.")
        info = await self.request("GET", "channels", params={"broadcaster_id": profile["id"]})
        live = await self.request("GET", "streams", params={"user_id": profile["id"]})
        meta = info["data"][0] if info.get("data") else {}
        return {"id": profile["id"], "name": profile["display_name"], "login": profile["login"], "title": meta.get("title", ""), "category": meta.get("game_name", ""), "live": bool(live.get("data")), "profile_image_url": profile.get("profile_image_url", "")}

    async def send(self, channel_id, text, *, reply_parent_message_id=None):
        bot = self.store.profiles()["bot"]
        if not bot:
            raise TwitchError("Kein Bot-Account verbunden.")
        message = {"broadcaster_id": channel_id, "sender_id": bot["id"], "message": text}
        if reply_parent_message_id:
            message["reply_parent_message_id"] = reply_parent_message_id
        result = await self.request("POST", "chat/messages", role="bot", json=message)
        sent = (result.get("data") or [{}])[0]
        if not sent.get("is_sent"):
            reason = (sent.get("drop_reason") or {}).get("message", "Nachricht nicht angenommen.")
            raise TwitchError("Twitch hat die Nachricht nicht veröffentlicht: " + reason[:240])
        return sent["message_id"]

    async def chat(self, channel_id, callback):
        retry = 2
        while True:
            socket = None
            try:
                await self.token("bot")
                socket = await websockets.connect("wss://eventsub.wss.twitch.tv/ws?keepalive_timeout_seconds=30", max_size=2**20, open_timeout=15)
                welcome = json.loads(await asyncio.wait_for(socket.recv(), 15))
                session = welcome["payload"]["session"]
                bot = self.store.profiles()["bot"]
                await self.request("POST", "eventsub/subscriptions", role="bot", json={"type": "channel.chat.message", "version": "1", "condition": {"broadcaster_user_id": channel_id, "user_id": bot["id"]}, "transport": {"method": "websocket", "session_id": session["id"]}})
                self.connected = True
                retry = 2
                await self.notify("success", "Chat verbunden", "Neue Nachrichten werden live empfangen.")
                while True:
                    message = json.loads(await asyncio.wait_for(socket.recv(), session.get("keepalive_timeout_seconds", 30) + 10))
                    kind = message["metadata"]["message_type"]
                    if kind == "session_reconnect":
                        url = message["payload"]["session"]["reconnect_url"]
                        if not url.startswith("wss://eventsub.wss.twitch.tv/"):
                            raise TwitchError("Unerwartete Twitch-Verbindungsadresse.")
                        replacement = await websockets.connect(url, max_size=2**20, open_timeout=15)
                        try:
                            new_welcome = json.loads(await asyncio.wait_for(replacement.recv(), 15))
                            session = new_welcome["payload"]["session"]
                        except BaseException:
                            await replacement.close()
                            raise
                        await socket.close()
                        socket = replacement
                    elif kind == "revocation":
                        raise TwitchError("Twitch hat die Chat-Freigabe widerrufen. Bitte den Bot neu anmelden.")
                    elif kind == "notification":
                        message_id = message["metadata"]["message_id"]
                        if message_id not in self.seen:
                            self.seen.append(message_id)
                            await callback(message["payload"]["event"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected = False
                await self.notify("error", "Chat-Verbindung unterbrochen", str(exc) if isinstance(exc, TwitchError) else "Die Verbindung wird erneut hergestellt.")
                await asyncio.sleep(retry)
                retry = min(retry * 2, 60)
            finally:
                self.connected = False
                if socket:
                    await socket.close()

    async def logout(self, role):
        await self.cancel_auth(role)
        token = self.store.get("twitch_" + role)
        if token:
            try:
                await self.http.post("https://id.twitch.tv/oauth2/revoke", data={"client_id": self.client_id(), "token": token["access_token"]})
            except httpx.HTTPError:
                pass
        self.store.delete("twitch_" + role)

    async def close(self):
        for role in self.SCOPES:
            await self.cancel_auth(role)
        await self.http.aclose()
