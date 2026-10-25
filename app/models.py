from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Settings:
    user_id: int
    timezone: str
    daily_limit: int
    discovery_enabled: bool
    delivery_time: str
    last_discovery_date: str | None
    created_at: datetime


@dataclass(frozen=True)
class Capsule:
    id: int
    public_id: str
    user_id: int
    kind: str
    text_content: str | None
    url: str | None
    file_path: str | None
    file_name: str | None
    created_at: datetime
    unlock_at: datetime | None
    release_mode: str
    status: str
    tags: tuple[str, ...] = ()
    source_chat_id: int | None = None
    source_message_id: int | None = None


@dataclass(frozen=True)
class Tag:
    id: int
    user_id: int
    name: str
    count: int


@dataclass(frozen=True)
class Delivery:
    id: int
    capsule_id: int
    user_id: int
    delivered_at: datetime
    kind: str
    response: str | None
    state: str
    public_id: str | None = None
    responded_at: datetime | None = None


@dataclass(frozen=True)
class Stats:
    capsule_count: int
    open_count: int
    sealed_count: int
    tag_count: int
    rediscovery_count: int
    scheduled_count: int
    oldest_at: datetime | None
    responses: dict[str, int]
