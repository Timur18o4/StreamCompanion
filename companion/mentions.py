"""Recognize explicit references to this bot without guessing pronoun identity."""
import re

REPLY_LIFETIME = 180
DIRECT_REPLY_INTERVAL = 20


def addressed_to_bot(payload, bot, character_name):
    bot = bot or {}
    bot_id = bot.get("id")
    reply = payload.get("reply") or {}
    if bot_id and reply.get("parent_user_id") == bot_id:
        return "reply"
    message = payload.get("message") or {}
    for fragment in message.get("fragments") or []:
        if bot_id and (fragment.get("mention") or {}).get("user_id") == bot_id:
            return "mention"
    names = {bot.get("login", ""), bot.get("display_name", ""), character_name}
    for name in names:
        if not isinstance(name, str):
            continue
        name = name.strip().lstrip("@")
        if name and re.search(r"(?<!\w)@?" + re.escape(name) + r"(?!\w)", message.get("text", ""), re.IGNORECASE):
            return "name"
    return None


def reply_key(event):
    return event.get("message_id") or event["id"]
