import re
from datetime import datetime
from pathlib import Path

from app.database.repository import Repository, normalize_public_id
from app.errors import NotFoundError, UserInputError
from app.models import Capsule, Settings, Stats, Tag
from app.timeutils import utcnow

MAX_TEXT = 4000
URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
TAG_RE = re.compile(r"(?<!\w)#([A-Za-z][A-Za-z0-9_-]{0,29})")
TAG_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,29}$")


def classify_text(text: str, *, forwarded: bool) -> tuple[str, str | None]:
    match = URL_RE.search(text)
    url = match.group(0).rstrip(").,]>") if match else None
    if forwarded:
        return "forward", url
    if url and text.strip() == url:
        return "url", url
    return "note", url


def extract_tags(text: str) -> list[str]:
    seen: list[str] = []
    for raw in TAG_RE.findall(text):
        name = raw.lower()
        if name not in seen:
            seen.append(name)
        if len(seen) == 8:
            break
    return seen


def normalize_tag(raw: str) -> str:
    name = raw.strip().lstrip("#").lower()
    if not TAG_NAME.fullmatch(name):
        raise UserInputError("Use a tag like #ideas. Letters, numbers, and hyphens only.")
    return name


class ArchiveService:
    def __init__(self, repository: Repository, storage_root):
        self.repository = repository
        self.storage_root = storage_root

    def ensure_user(self, user_id: int, now: datetime | None = None) -> Settings:
        return self.repository.ensure_settings(user_id, now or utcnow())

    def save_text(
        self,
        user_id: int,
        text: str,
        *,
        forwarded: bool = False,
        source: tuple[int, int] | None = None,
        now: datetime | None = None,
    ) -> Capsule:
        moment = now or utcnow()
        cleaned = text.strip()
        if not cleaned:
            raise UserInputError("Write a note or paste a link.")
        if len(cleaned) > MAX_TEXT:
            raise UserInputError("That note is too long. Keep it under 4000 characters.")
        kind, url = classify_text(cleaned, forwarded=forwarded)
        source_chat_id, source_message_id = source if source else (None, None)
        return self.repository.insert_capsule(
            user_id=user_id,
            kind=kind,
            text_content=cleaned,
            url=url,
            created_at=moment,
            tags=extract_tags(cleaned),
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            now=moment,
        )

    def get(self, user_id: int, capsule_id: int) -> Capsule:
        capsule = self.repository.get(user_id, capsule_id)
        if capsule is None:
            raise NotFoundError("No capsule with that id in your vault.")
        return capsule

    def get_public(self, user_id: int, raw_id: str) -> Capsule:
        public_id = normalize_public_id(raw_id)
        capsule = self.repository.get_by_public_id(user_id, public_id)
        if capsule is None:
            raise NotFoundError("No capsule with that id in your vault.")
        return capsule

    def vault(self, user_id: int, *, limit: int, offset: int) -> list[Capsule]:
        return self.repository.list_capsules(user_id, limit=limit, offset=offset)

    def count(self, user_id: int) -> int:
        return self.repository.count_capsules(user_id)

    def search(self, user_id: int, query: str) -> list[Capsule]:
        cleaned = query.strip()
        if not cleaned:
            raise UserInputError("Tell me what to search for.")
        if cleaned.startswith("#") and " " not in cleaned:
            return self.repository.capsules_by_tag(user_id, normalize_tag(cleaned))
        if len(cleaned) > 200:
            raise UserInputError("Search for something shorter.")
        return self.repository.search(user_id, cleaned)

    def seal(self, user_id: int, capsule_id: int, unlock_at: datetime, now: datetime) -> Capsule:
        if unlock_at <= now:
            raise UserInputError("That time has already passed.")
        return self.repository.update_release(user_id, capsule_id, "sealed", unlock_at)

    def unseal(self, user_id: int, capsule_id: int) -> Capsule:
        return self.repository.update_release(user_id, capsule_id, "open", None)

    def add_tag(self, user_id: int, capsule_id: int, raw_tag: str) -> Capsule:
        return self.repository.add_tag(user_id, capsule_id, normalize_tag(raw_tag))

    def tags(self, user_id: int) -> list[Tag]:
        return self.repository.list_tags(user_id)

    def delete_tag(self, user_id: int, tag_id: int) -> str:
        return self.repository.delete_tag(user_id, tag_id)

    def delete(self, user_id: int, capsule_id: int) -> Capsule:
        capsule = self.get(user_id, capsule_id)
        removed = self.repository.delete_capsule(user_id, capsule.id)
        delete_attachment(self.storage_root, removed.file_path)
        return removed

    def stats(self, user_id: int) -> Stats:
        return self.repository.stats(user_id)

    def update_settings(self, user_id: int, **fields) -> Settings:
        return self.repository.update_settings(user_id, **fields)


def delete_attachment(storage_root, relative: str | None) -> None:
    if not relative:
        return
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise UserInputError("Invalid attachment path.")
    root = Path(storage_root).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise UserInputError("Invalid attachment path.")
    if target.is_file():
        target.unlink()
