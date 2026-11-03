import json
import os
import secrets
import sqlite3
import time
from pathlib import Path

from cryptography.fernet import Fernet

from .models import Settings


class Store:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        try:
            root.chmod(0o700)
        except OSError:
            pass
        keyfile = root / "encryption.key"
        if not keyfile.exists():
            self._private_write(keyfile, Fernet.generate_key())
        self.cipher = Fernet(keyfile.read_bytes())
        access = root / "access.key"
        if not access.exists():
            self._private_write(access, secrets.token_urlsafe(32).encode())
        self.access_key = access.read_text().strip()
        self.db = sqlite3.connect(root / "companion.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value BLOB NOT NULL);
          CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY, channel TEXT NOT NULL, subject_id TEXT NOT NULL,
            subject_name TEXT NOT NULL, fact TEXT NOT NULL, source TEXT NOT NULL,
            created REAL NOT NULL, last_used REAL NOT NULL DEFAULT 0);
          CREATE TABLE IF NOT EXISTS consent (
            channel TEXT NOT NULL, user_id TEXT NOT NULL, name TEXT NOT NULL,
            enabled INTEGER NOT NULL, PRIMARY KEY(channel,user_id));
        """)
        self.db.commit()

    @staticmethod
    def _private_write(path: Path, content: bytes):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)

    def get(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        return json.loads(self.cipher.decrypt(row["value"]))

    def put(self, key: str, value):
        encrypted = self.cipher.encrypt(json.dumps(value, ensure_ascii=False).encode())
        self.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, encrypted))
        self.db.commit()

    def delete(self, key: str):
        self.db.execute("DELETE FROM kv WHERE key=?", (key,))
        self.db.commit()

    def settings(self) -> Settings:
        data = self.get("settings", {})
        if not data.get("twitch_client_id"):
            data["twitch_client_id"] = os.getenv("STREAM_COMPANION_TWITCH_CLIENT_ID", "")
        return Settings.model_validate(data)

    def profiles(self):
        result = {}
        for role in ("owner", "bot"):
            token = self.get("twitch_" + role)
            result[role] = None if not token else {key: token.get(key) for key in ("id", "login", "display_name", "profile_image_url", "scopes")}
        return result

    def consented(self, channel: str, user_id: str) -> bool:
        row = self.db.execute("SELECT enabled FROM consent WHERE channel=? AND user_id=?", (channel, user_id)).fetchone()
        return bool(row and row[0])

    def viewer_memory_allowed(self, channel: str, user_id: str) -> bool:
        settings = self.settings()
        if not settings.viewer_memory or not user_id:
            return False
        row = self.db.execute("SELECT enabled FROM consent WHERE channel=? AND user_id=?", (channel, user_id)).fetchone()
        # Explicit opt-outs remain binding in automatic mode, including after restart.
        return bool(row[0]) if row else settings.viewer_memory_mode == "automatic"

    def consent(self, channel: str, user_id: str, name: str, enabled: bool):
        self.db.execute("INSERT OR REPLACE INTO consent VALUES (?,?,?,?)", (channel, user_id, name, int(enabled)))
        if not enabled:
            self.db.execute("DELETE FROM memories WHERE channel=? AND subject_id=?", (channel, user_id))
        self.db.commit()

    def add_memory(self, channel: str, subject: str, name: str, fact: str, source: str):
        fact = fact.strip()[:600]
        if not fact:
            return
        if self.db.execute("SELECT 1 FROM memories WHERE channel=? AND subject_id=? AND fact=?", (channel, subject, fact)).fetchone():
            return
        self.db.execute("INSERT INTO memories(channel,subject_id,subject_name,fact,source,created) VALUES (?,?,?,?,?,?)", (channel, subject, name, fact, source[:1000], time.time()))
        self.db.commit()

    def memories(self, channel: str, limit=100):
        rows = self.db.execute("SELECT * FROM memories WHERE channel=? ORDER BY created DESC LIMIT ?", (channel, limit)).fetchall()
        return [dict(row) for row in rows]

    def remove_memory(self, memory_id: int, channel: str):
        self.db.execute("DELETE FROM memories WHERE id=? AND channel=?", (memory_id, channel))
        self.db.commit()

    def resolve_local_owner(self, channel: str, subject: str, name: str):
        """Keep speech-only notes when a Twitch bot later resolves the channel ID."""
        previous = "local:" + channel
        if subject == previous:
            return
        self.db.execute("DELETE FROM memories WHERE channel=? AND subject_id=? AND fact IN (SELECT fact FROM memories WHERE channel=? AND subject_id=?)", (channel, previous, channel, subject))
        self.db.execute("UPDATE memories SET subject_id=?,subject_name=? WHERE channel=? AND subject_id=?", (subject, name, channel, previous))
        self.db.commit()

    def forget_channel(self, channel: str):
        self.db.execute("DELETE FROM memories WHERE channel=?", (channel,))
        # Clearing notes must never silently re-enable a viewer who opted out.
        self.db.execute("DELETE FROM consent WHERE channel=? AND enabled=1", (channel,))
        self.db.commit()

    def close(self):
        self.db.close()
