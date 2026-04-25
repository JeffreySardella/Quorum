from __future__ import annotations

import tomllib
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Models(BaseModel):
    vision: str = "mistral-small3.2:latest"
    text: str = "gemma4:31b"


class Whisper(BaseModel):
    """Transcript-signal settings. Two backends supported.

    * `backend = "faster-whisper"` (default) — pip-installable, auto-downloads
      the model on first run, CPU-only on AMD Windows.
    * `backend = "whisper.cpp"` — GPU-accelerated via the Vulkan build, but
      requires a manual download of the binary and model file.
    """

    backend: str = "faster-whisper"

    # faster-whisper settings
    model_size: str = "small"         # tiny | base | small | medium | large-v3 | distil-large-v3
    compute_type: str = "auto"        # auto | int8 | int8_float16 | float16 | float32

    # whisper.cpp settings (only used when backend = "whisper.cpp")
    binary: Path | None = None
    model: Path | None = None

    language: str = "auto"

    @field_validator("binary", "model", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v


class Thresholds(BaseModel):
    auto_apply: float = 0.85
    review_floor: float = 0.30


class Extract(BaseModel):
    keyframe_count: int = 6
    audio_seconds: int = 30
    cache_dir: Path = Path(".quorum-cache")


class Paths(BaseModel):
    review_queue: Path = Path("review.jsonl")


class SignalToggles(BaseModel):
    filename: bool = True
    vision: bool = True
    transcript: bool = True          # default on — faster-whisper auto-installs
    ocr: bool = False
    fingerprint: bool = False
    opensub: bool = False


class WatchInbox(BaseModel):
    path: Path
    mode: str = "auto"           # auto | home-videos | photos
    dest: Path = Path(".")


class WatchPlex(BaseModel):
    enabled: bool = False
    url: str = "http://127.0.0.1:32400"
    token: str = ""
    library_ids: list[int] = Field(default_factory=list)


class Watch(BaseModel):
    poll_interval: int = 30
    inboxes: list[WatchInbox] = Field(default_factory=list)
    plex: WatchPlex = Field(default_factory=WatchPlex)


class Web(BaseModel):
    port: int = 8080
    auth_user: str = ""
    auth_password: str = ""


class Faces(BaseModel):
    distance_threshold: float = 0.55
    min_cluster_size: int = 2


class Events(BaseModel):
    gap_hours: float = 2.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cpu_only: bool = False
    db_path: Path = Path("quorum.db")

    tmdb_api_key: str = ""
    ollama_url: str = "http://127.0.0.1:11434"

    models: Models = Field(default_factory=Models)
    whisper: Whisper = Field(default_factory=Whisper)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    extract: Extract = Field(default_factory=Extract)
    paths: Paths = Field(default_factory=Paths)
    signals: SignalToggles = Field(default_factory=SignalToggles)
    watch: Watch = Field(default_factory=Watch)
    web: Web = Field(default_factory=Web)
    faces: Faces = Field(default_factory=Faces)
    events: Events = Field(default_factory=Events)


def load_settings(config_path: Path | None = None) -> Settings:
    load_dotenv(override=False)
    data: dict = {}
    if config_path and config_path.exists():
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    return Settings(**data)
