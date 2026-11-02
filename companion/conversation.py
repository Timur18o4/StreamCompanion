"""Small deterministic filters around the local conversation model."""
import re
import time


def acknowledgment(text):
    return bool(re.fullmatch(r"[\W_]*(?:(?:ok(?:ay)?|danke|dankeschön|alles klar|passt|gut|thanks|thx)[\W_]*){1,3}", text.casefold().strip()))


def combine_request(previous, event):
    """Join a short same-person addendum without changing its original UI row."""
    if not previous or previous.get("id") == event.get("id") or previous.get("subject_id") != event.get("subject_id") or not event.get("subject_id") or previous.get("kind") != event.get("kind"):
        return None
    if event.get("kind") == "chat" and event.get("address_kind") not in {None, "chat_followup"}:
        return None
    if event.get("kind") == "audio" and event.get("role") != "streamer":
        return None
    if acknowledgment(event["text"]):
        return None
    start = previous.get("turn_started", previous["time"])
    if not 0 <= event["time"] - previous["time"] <= 8 or event["time"] - start > 20:
        return None
    return {**event, "text": previous["text"][:1000] + "\nNachtrag: " + event["text"][:1000],
            "canonical_text": previous.get("canonical_text", previous["text"])[:1000] + "\nNachtrag: " + event.get("canonical_text", event["text"])[:1000],
            "addressed_to_bot": True, "address_kind": "audio_continuation" if event["kind"] == "audio" else "chat_continuation",
            "turn_started": start, "remember": False,
            "combined_reply_ids": [*previous.get("combined_reply_ids", [previous.get("message_id") or previous["id"]]), event.get("message_id") or event["id"]][-4:]}


def memory_candidate(text):
    """Prefer missing an ambiguous note to storing a question as a fact."""
    if not isinstance(text, str) or len(text.strip()) < 10:
        return False
    value = text.casefold()
    if value.lstrip().startswith(('"', "„", ">")) or re.search(r"\b(sagte|meinte|zitat|said|says)\b.*[\"„]", value):
        return False
    if "?" in value or re.search(r"\b(wenn|falls|vielleicht|eventuell|würde|würden|könnte|könnten|glaube|glauben|denke|denken|vermute|vermuten|vermutlich|wahrscheinlich|anscheinend|if|maybe|perhaps|would|could|probably|apparently|i\s+(?:think|guess|suppose))\b", value):
        return False
    if re.search(r"\b(kannst du|könntest du|was meinst|was hältst|wie findest|weißt du|weisst du|erinnerst du|soll ich|can you|do you|what do you)\b", value):
        return False
    if re.search(r"(?:^|[,.:]\s*)(wer|was|wie|warum|wieso|wann|wo|welche\w*|who|what|why|when|where|how)\b", value):
        return False
    personal = re.search(r"\b(ich|wir|mein(?:e|en|em|er|es)?|unser(?:e|en|em|er|es)?|i|my|we|our)\b", value)
    question = re.search(r"\b(wer|was|wie(?:viel\w*)?|warum|wieso|wann|wo(?:her|hin|mit|für)?|welche\w*|who|what|why|when|where|how)\b", value)
    if personal and question and question.start() < personal.start():
        return False
    assertion = re.search(r"\b(mag|mochte|möchte|möchten|will|wollte|wollen|spiele|spielte|spielen|probiere|probieren|plane|planen|hab|habe|hatte|haben|bin|war|waren|sind|ist|hieß|hiess|sammle|baue|arbeite|entwickle|like|liked|love|loved|prefer|play|played|playing|plan|want|wanted|have|had|am|is|was|were)\b", value)
    return bool(personal and assertion)


def conversation_followup(events, subject, text, *, spoken=False, now=None):
    """Only continue a recent, confirmed exchange with the same participant."""
    if not subject or not text:
        return False
    value = text.casefold().strip()
    if acknowledgment(text):
        return False
    now = time.time() if now is None else now
    previous = next((event for event in reversed(events)
                     if event.get("kind") == "bot" and event.get("confirmed")
                     and event.get("conversation_subject") == subject), None)
    if not previous or not 0 <= now - previous["time"] <= 90:
        return False
    if not spoken or previous.get("conversation_question"):
        return True
    return bool("?" in value or re.search(r"\b(du|dir|dich|dein\w*|warum|wieso|weshalb|welche\w*|kannst|erklär\w*|erzähl\w*)\b", value))
