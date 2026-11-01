from typing import Literal

from pydantic import BaseModel, Field, field_validator


Role = Literal["streamer", "conversation", "game", "ignore"]


class AudioSource(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=80)
    role: Role = "streamer"
    enabled: bool = True
    device: int | None = None
    device_name: str = Field(default="", max_length=256)
    remember: bool = False


class Settings(BaseModel):
    channel: str = Field(default="", max_length=40)
    twitch_client_id: str = Field(default="", max_length=128)
    bot_name: str = Field(default="Milo", min_length=1, max_length=32)
    personality: str = Field(default="Locker, aufmerksam und knapp. Interessiert an Entscheidungen im Spiel und an Geschichten aus der Community. Gelegentlich trockener Humor, respektvoll und ohne ständige Fragen.", max_length=3000)
    provider: Literal["local", "openai", "compatible"] = "local"
    api_base: str = "http://127.0.0.1:11434/v1"
    text_model: str = ""
    transcription_model: str = "gpt-live-transcribe"
    stt_provider: Literal["openai_realtime", "local_whisper"] = "openai_realtime"
    whisper_model: str = Field(default="small", max_length=100)
    mode: Literal["preview", "automatic"] = "preview"
    min_interval: int = Field(default=90, ge=20, le=1800)
    silence_after: int = Field(default=50, ge=15, le=600)
    max_per_hour: int = Field(default=20, ge=1, le=120)
    stream_delay: int = Field(default=0, ge=0, le=180)
    max_message_length: int = Field(default=240, ge=40, le=450)
    owner_memory: bool = True
    viewer_memory: bool = False
    cloud_chat_permission: bool = False
    screenshots_enabled: bool = False
    screenshot_source: Literal["obs", "companion", "manual"] = "obs"
    screenshot_interval: int = Field(default=30, ge=10, le=300)
    obs_url: str = "ws://127.0.0.1:4455"
    input_mode: Literal["local", "companion", "twitch"] = "local"
    sources: list[AudioSource] = Field(default_factory=lambda: [AudioSource(id="microphone", name="Mikrofon", remember=True)])

    @field_validator("channel")
    @classmethod
    def clean_channel(cls, value: str) -> str:
        value = value.strip().lower().removeprefix("https://www.twitch.tv/").removeprefix("https://twitch.tv/").strip("/")
        if value and (not value.replace("_", "").isalnum() or not value.isascii()):
            raise ValueError("Bitte einen Twitch-Kanalnamen eingeben.")
        return value

    @field_validator("api_base")
    @classmethod
    def check_base(cls, value: str) -> str:
        from urllib.parse import urlparse

        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Ungültige KI-Serveradresse.")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Externe KI-Server benötigen HTTPS.")
        return value.rstrip("/")

    @field_validator("sources")
    @classmethod
    def unique_sources(cls, sources: list[AudioSource]) -> list[AudioSource]:
        if len(sources) > 8 or len({s.id for s in sources}) != len(sources):
            raise ValueError("Maximal acht Quellen mit eindeutigen Kennungen verwenden.")
        return sources


