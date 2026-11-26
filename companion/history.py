"""Incremental local chat-log index. Historical messages never become live events."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time

from .conversation import memory_candidate

EXTENSIONS = {".json", ".jsonl", ".ndjson"}
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "_internal", "__pycache__"}
STOPWORDS = set("spiele spielen spielte gespielt mag gerne lieber gern aber alle alles auch aus bei bin bist bitte das dass dein deine dem den der des dich die dir doch dort du ein eine einem einen einer er es etwas für gehabt habe haben hast hat hatte hatten heute hier ich ihr ihre im in ist ja kann kannst kennt könntest mal mein meine mich mir mit noch nun nur oder schon sein seit sich sie sind so und uns unser vom von vor war waren was welche welches welchem welchen wer wie wieder wir wird wissen wo zu zum zur erinner erinnerst erinnerung damals gestern letzte letzten letztes letztem vorher vorhin gesagt the a an and are as at be been can did do does for from had has have how i in is it me my of on or our said that the their them there this to us was what when where which who with you your".split())


def roots(settings):
    return [Path(os.path.expandvars(os.path.expanduser(value))).absolute() for value in settings.history_paths]


def in_scope(path, settings):
    path = Path(path)
    for root in roots(settings):
        if path == root:
            return True
        if root.is_dir():
            try:
                relative = path.relative_to(root)
                if settings.history_recursive or len(relative.parts) == 1:
                    return True
            except ValueError:
                pass
    return False


def parse_record(data):
    if not isinstance(data, dict) or str(data.get("event") or "chat").casefold() != "chat":
        return None
    platform = str(data.get("platform") or data.get("source") or "").casefold()
    if platform not in {"twitch", "youtube", "kick", "tiktok", "cng"}:
        return None
    name = data.get("username") or data.get("userName") or data.get("displayName")
    text = data.get("message", data.get("content"))
    if not isinstance(name, str) or not isinstance(text, str) or not name.strip() or not text.strip() or len(text) > 4000:
        return None
    raw_time = data.get("timestamp") or data.get("createdAt") or data.get("time")
    try:
        if isinstance(raw_time, (int, float)) or isinstance(raw_time, str) and re.fullmatch(r"\d+(?:\.\d+)?", raw_time):
            stamp = float(raw_time)
            if stamp > 10**11:
                stamp /= 1000
        else:
            parsed = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            stamp = parsed.timestamp()
        if not 946684800 <= stamp <= time.time() + 300:
            return None
    except (ValueError, TypeError, OverflowError):
        return None
    user_id = str(data.get("userId") or data.get("user_id") or "").strip()
    if len(user_id) > 256:
        return None
    subject = user_id if platform == "twitch" and user_id else f"{platform}:{user_id}" if user_id else f"{platform}:name:{name.casefold()}"
    message_id = str(data.get("id") or data.get("messageId") or data.get("chatId") or "")
    fingerprint = platform + ":" + message_id if message_id else hashlib.sha256(json.dumps([platform, subject, stamp, text], ensure_ascii=False).encode()).hexdigest()
    return {"platform": platform, "subject": subject, "username": name[:100], "text": text, "stamp": stamp, "verified": int(bool(user_id)), "fingerprint": fingerprint}


def platform_of(subject):
    return subject.split(":", 1)[0] if ":" in subject else "twitch"


class ChatHistory:
    def __init__(self, store):
        self.store = store
        self.path = store.root / "chat-history.sqlite3"
        self.lock = asyncio.Lock()
        self.abort = threading.Event()
        self.task = None
        self.names_cache = None
        self.wake = asyncio.Event()
        self.state = {"indexing": False, "files": 0, "records": 0, "skipped": 0, "errors": [], "last_scan": 0}
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS records (
                    id INTEGER PRIMARY KEY, channel TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    platform TEXT NOT NULL, subject TEXT NOT NULL, username TEXT NOT NULL,
                    text TEXT NOT NULL, stamp REAL NOT NULL, verified INTEGER NOT NULL,
                    UNIQUE(channel,fingerprint));
                CREATE INDEX IF NOT EXISTS records_person ON records(channel,subject,stamp);
                CREATE TABLE IF NOT EXISTS origins (
                    channel TEXT NOT NULL, path TEXT NOT NULL, offset INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL, PRIMARY KEY(channel,path,offset));
                CREATE INDEX IF NOT EXISTS origins_fingerprint ON origins(channel,fingerprint);
                CREATE TABLE IF NOT EXISTS files (
                    channel TEXT NOT NULL, path TEXT NOT NULL, offset INTEGER NOT NULL,
                    file_id TEXT NOT NULL, prefix TEXT NOT NULL, prefix_length INTEGER NOT NULL, mtime INTEGER NOT NULL,
                    PRIMARY KEY(channel,path));
                CREATE VIRTUAL TABLE IF NOT EXISTS fulltext USING fts5(text,username,content='records',content_rowid='id',tokenize='unicode61');
                CREATE TRIGGER IF NOT EXISTS history_insert AFTER INSERT ON records BEGIN
                    INSERT INTO fulltext(rowid,text,username) VALUES(new.id,new.text,new.username); END;
                CREATE TRIGGER IF NOT EXISTS history_delete AFTER DELETE ON records BEGIN
                    INSERT INTO fulltext(fulltext,rowid,text,username) VALUES('delete',old.id,old.text,old.username); END;
            """)
            if "prefix_length" not in {row["name"] for row in db.execute("PRAGMA table_info(files)")}:
                db.execute("ALTER TABLE files ADD COLUMN prefix_length INTEGER NOT NULL DEFAULT 512")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def policy(self, settings):
        blocks = [dict(row) for row in self.store.db.execute("SELECT user_id,name FROM consent WHERE channel=? AND enabled=0", (settings.channel,))]
        cutoffs = [dict(row) for row in self.store.db.execute("SELECT subject_id,name,cutoff FROM history_forget WHERE channel=?", (settings.channel,))]
        return blocks, cutoffs

    @staticmethod
    def excluded(row, policy):
        blocks, cutoffs = policy
        for block in blocks:
            if row["subject"] == block["user_id"] or row["platform"] == platform_of(block["user_id"]) and row["username"].casefold() == block["name"].casefold():
                return True
        for cutoff in cutoffs:
            same = cutoff["subject_id"] == "*" or row["subject"] == cutoff["subject_id"] or row["platform"] == platform_of(cutoff["subject_id"]) and row["username"].casefold() == cutoff["name"]
            if same and row["stamp"] <= cutoff["cutoff"]:
                return True
        return False

    def purge(self):
        settings = self.store.settings()
        policy = self.policy(settings)
        self.state["records"] = self.purge_index(settings.channel, policy)

    def purge_index(self, channel, policy):
        with self.connect() as db:
            rows = db.execute("SELECT id,subject,platform,username,stamp,fingerprint FROM records WHERE channel=?", (channel,)).fetchall() if any(policy) else []
            remove = [(r["id"],) for r in rows if self.excluded(r, policy)]
            db.executemany("DELETE FROM records WHERE id=?", remove)
            db.execute("DELETE FROM origins WHERE channel=? AND fingerprint NOT IN (SELECT fingerprint FROM records WHERE channel=?)", (channel, channel))
            return db.execute("SELECT COUNT(*) FROM records WHERE channel=?", (channel,)).fetchone()[0]

    def discover(self, settings):
        found, errors = set(), []
        for root in roots(settings):
            if not root.exists():
                errors.append(f"Pfad nicht gefunden: {root}")
                continue
            if root.is_symlink() or getattr(root, "is_junction", lambda: False)():
                errors.append(f"Verknuepfte Ordner werden nicht verfolgt: {root}")
                continue
            if root.is_file():
                if root.suffix.casefold() in EXTENSIONS:
                    found.add(str(root))
                else:
                    errors.append(f"Nicht unterstuetztes Logformat: {root.name}")
                continue
            for base, directories, files in os.walk(root, followlinks=False, onerror=lambda e: errors.append(str(e))):
                directories[:] = [d for d in directories if d not in SKIP_DIRS and not (Path(base) / d).is_symlink() and not getattr(Path(base) / d, "is_junction", lambda: False)()]
                for name in files:
                    path = Path(base) / name
                    if path.suffix.casefold() in EXTENSIONS and not path.is_symlink():
                        found.add(str(path))
                if not settings.history_recursive:
                    directories.clear()
                if len(found) > 10000 or self.abort.is_set():
                    errors.append("Die Dateisuche wurde begrenzt. Bitte einen kleineren Logordner waehlen.")
                    return sorted(found)[:10000], errors
        return sorted(found), errors

    def index(self, settings, policy, rebuild=False):
        paths, errors = self.discover(settings)
        if self.abort.is_set():
            return dict(self.state)
        skipped = added = consumed = 0
        pending = False
        with self.connect() as db:
            if rebuild:
                for table in ("origins", "files", "records"):
                    db.execute(f"DELETE FROM {table} WHERE channel=?", (settings.channel,))
            known = db.execute("SELECT path FROM files WHERE channel=?", (settings.channel,)).fetchall()
            for row in known:
                if row["path"] not in paths:
                    db.execute("DELETE FROM origins WHERE channel=? AND path=?", (settings.channel, row["path"]))
                    db.execute("DELETE FROM files WHERE channel=? AND path=?", (settings.channel, row["path"]))
            for value in paths:
                if consumed >= 25000:
                    pending = True
                    break
                if self.abort.is_set():
                    break
                try:
                    path = Path(value)
                    stat = path.stat()
                    identity = f"{stat.st_dev}:{stat.st_ino}"
                    previous = db.execute("SELECT * FROM files WHERE channel=? AND path=?", (settings.channel, value)).fetchone()
                    with path.open("rb") as stream:
                        first = stream.read(512)
                        prefix = hashlib.sha256(first).hexdigest()
                        offset = previous["offset"] if previous else 0
                        # Prefix changes on small append-only files until 512 bytes exist.
                        changed = previous and (stat.st_size < offset or previous["file_id"] != identity or stat.st_size == offset and previous["mtime"] != stat.st_mtime_ns or previous["prefix"] != hashlib.sha256(first[:previous["prefix_length"]]).hexdigest())
                        if changed:
                            db.execute("DELETE FROM origins WHERE channel=? AND path=?", (settings.channel, value))
                            offset = 0
                        stream.seek(offset)
                        count = 0
                        while stream.tell() < stat.st_size and count < 5000 and consumed < 25000 and not self.abort.is_set():
                            position = stream.tell()
                            line = stream.readline(65537)
                            if not line.endswith(b"\n"):
                                if len(line) <= 65536:
                                    try:
                                        json.loads(line.decode("utf-8-sig"))
                                    except (ValueError, UnicodeError):
                                        stream.seek(position)
                                        break  # Retry incomplete writes without skipping them.
                                else:
                                    while line and not line.endswith(b"\n") and not self.abort.is_set():
                                        line = stream.readline(65537)
                                    skipped += 1
                                    offset = stream.tell()
                                    continue
                            offset = stream.tell()
                            count += 1
                            consumed += 1
                            try:
                                record = parse_record(json.loads(line.decode("utf-8-sig")))
                            except (ValueError, UnicodeError):
                                record = None
                            if not record or self.excluded(record, policy):
                                skipped += 1
                                continue
                            values = [record[k] for k in ("fingerprint", "platform", "subject", "username", "text", "stamp", "verified")]
                            cursor = db.execute("INSERT OR IGNORE INTO records(channel,fingerprint,platform,subject,username,text,stamp,verified) VALUES (?,?,?,?,?,?,?,?)", [settings.channel, *values])
                            added += cursor.rowcount
                            db.execute("INSERT OR REPLACE INTO origins VALUES (?,?,?,?)", (settings.channel, value, position, record["fingerprint"]))
                        db.execute("INSERT OR REPLACE INTO files(channel,path,offset,file_id,prefix,prefix_length,mtime) VALUES (?,?,?,?,?,?,?)", (settings.channel, value, offset, identity, prefix, len(first), stat.st_mtime_ns))
                except (OSError, ValueError) as exc:
                    errors.append(f"{value}: {exc}")
            db.execute("DELETE FROM records WHERE channel=? AND fingerprint NOT IN (SELECT fingerprint FROM origins WHERE channel=?)", (settings.channel, settings.channel))
            total = db.execute("SELECT COUNT(*) FROM records WHERE channel=?", (settings.channel,)).fetchone()[0]
        return {"pending": pending, "files": len(paths), "records": total, "added": added, "skipped": skipped, "errors": errors[:8], "last_scan": time.time()}

    async def scan(self, rebuild=False):
        async with self.lock:
            settings = self.store.settings()
            if not settings.history_enabled:
                return self.snapshot()
            self.abort = threading.Event()
            self.state["indexing"] = True
            task = asyncio.create_task(asyncio.to_thread(self.index, settings, self.policy(settings), rebuild))
            try:
                self.state.update(await asyncio.shield(task))
                # Honour opt-outs that arrived while an import was in progress.
                current = self.store.settings()
                self.state["records"] = await asyncio.to_thread(self.purge_index, current.channel, self.policy(current))
            except asyncio.CancelledError:
                self.abort.set()
                await task
                raise
            finally:
                self.state["indexing"] = False
            return self.snapshot()

    def snapshot(self):
        return {**self.state, "enabled": self.store.settings().history_enabled}

    async def worker(self):
        while True:
            try:
                if self.store.settings().history_enabled:
                    await self.scan()
            except asyncio.CancelledError:
                raise
            except (OSError, sqlite3.Error) as exc:
                self.state["errors"] = [f"Historienindex nicht verfuegbar: {exc}"]
            try:
                await asyncio.wait_for(self.wake.wait(), 30)
            except asyncio.TimeoutError:
                pass
            self.wake.clear()

    def start(self):
        if not self.task:
            self.task = asyncio.create_task(self.worker())

    async def close(self):
        self.abort.set()
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    def search(self, query, *, subjects=None, personal=False, owner="", limit=20, before=None):
        settings = self.store.settings()
        if not settings.history_enabled or not settings.history_paths:
            return []
        policy = self.policy(settings)
        tokens = list(dict.fromkeys(t.casefold() for t in re.findall(r"\w{3,}", query[:1000]) if t.casefold() not in STOPWORDS))[:12]
        match = " OR ".join('"' + token + '"*' for token in tokens)
        where, params = ["r.channel=?"], [settings.channel]
        if subjects is not None:
            if not subjects:
                return []
            where.append("r.subject IN (" + ",".join("?" for _ in subjects) + ")")
            params.extend(subjects)
        if personal:
            where.append("r.verified=1")
        if before is not None:
            where.append("r.stamp<?")
            params.append(before)
        join, order = "", "r.stamp DESC"
        if match:
            join = "JOIN fulltext ON fulltext.rowid=r.id"
            where.append("fulltext MATCH ?")
            params.append(match)
            order = "bm25(fulltext),r.stamp DESC"
        with self.connect() as db:
            rows = db.execute(f"SELECT r.* FROM records r {join} WHERE {' AND '.join(where)} ORDER BY {order} LIMIT 200", params).fetchall()
            results = []
            bot = self.store.profiles()["bot"] or {}
            for raw in rows:
                row = dict(raw)
                if self.excluded(row, policy):
                    continue
                if row["platform"] == "twitch" and (row["subject"] == bot.get("id") or row["username"].casefold() == bot.get("login", "").casefold()):
                    continue
                if personal and not (settings.owner_memory if row["subject"] == owner else self.store.viewer_memory_allowed(settings.channel, row["subject"])):
                    continue
                origins = db.execute("SELECT path,offset FROM origins WHERE channel=? AND fingerprint=?", (settings.channel, row["fingerprint"])).fetchall()
                origin = next((o for o in origins if in_scope(o["path"], settings) and Path(o["path"]).exists()), None)
                if origin:
                    results.append({"id": row["id"], "subject_id": row["subject"], "name": row["username"], "platform": row["platform"], "text": row["text"], "timestamp": row["stamp"], "identity_verified": bool(row["verified"]), "file": Path(origin["path"]).name, "offset": origin["offset"]})
                if len(results) >= limit:
                    break
        return results

    def context(self, query, subject, owner, exclude=()):
        if not subject:
            return []
        subjects = {subject}
        cache_key = (self.store.settings().channel, self.state["last_scan"])
        if not self.names_cache or self.names_cache[0] != cache_key:
            with self.connect() as db:
                names = [dict(row) for row in db.execute("SELECT DISTINCT subject,username FROM records WHERE channel=? AND verified=1 LIMIT 2000", (cache_key[0],)).fetchall()]
            self.names_cache = (cache_key, names)
        for row in self.names_cache[1]:
            if len(row["username"]) >= 3 and re.search(r"(?<!\w)" + re.escape(row["username"]) + r"(?!\w)", query, re.I):
                subjects.add(row["subject"])
        hits = self.search(query, subjects=subjects, personal=True, owner=owner, limit=8, before=time.time() - 30)
        if not hits and re.search(r"\b(erinner\w*|damals|gestern|letzt\w*|vorher|vorhin|nochmal|früher|erwähnt)\b", query, re.I):
            hits = [r for r in self.search("", subjects=subjects, personal=True, owner=owner, limit=20, before=time.time() - 30) if memory_candidate(r["text"])]
        hits = [row for row in hits if (row["subject_id"], row["text"]) not in exclude][:2]
        return [{"name": row["name"], "platform": row["platform"], "date": datetime.fromtimestamp(row["timestamp"], timezone.utc).isoformat(), "text": row["text"][:450], "source": f"{row['file']} · Byte {row['offset']}"} for row in hits]
