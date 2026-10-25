import json
import sqlite3
from datetime import datetime
from pathlib import Path

from app.database.repository import format_public_id
from app.errors import UserInputError
from app.timeutils import dump_dt, validate_timezone

EXPORT_VERSION = 1
_KINDS = {"note", "url", "forward"}
_MODES = {"open", "sealed"}
_DELIVERY_KINDS = {"scheduled", "rediscovery"}
_STATES = {"pending", "sent", "failed"}
_RESPONSES = {None, "useful", "completed", "outdated", "still_relevant"}


def export_payload(repository, user_id: int, now: datetime) -> dict:
    raw = repository.export_user(user_id)
    settings = raw["settings"] or {}
    return {
        "version": EXPORT_VERSION,
        "user_id": user_id,
        "exported_at": dump_dt(now),
        "settings": {
            "timezone": settings.get("timezone", "UTC"),
            "daily_limit": int(settings.get("daily_limit", 1)),
            "discovery_enabled": bool(settings.get("discovery_enabled", 1)),
            "delivery_time": settings.get("delivery_time", "09:00"),
        },
        "capsules": [
            {
                "id": row["id"],
                "public_id": row["public_id"],
                "kind": row["kind"],
                "text_content": row["text_content"],
                "url": row["url"],
                "file_path": row["file_path"],
                "file_name": row["file_name"],
                "created_at": row["created_at"],
                "unlock_at": row["unlock_at"],
                "release_mode": row["release_mode"],
                "source_chat_id": row["source_chat_id"],
                "source_message_id": row["source_message_id"],
            }
            for row in raw["capsules"]
        ],
        "tags": [{"id": row["id"], "name": row["name"]} for row in raw["tags"]],
        "capsule_tags": raw["capsule_tags"],
        "deliveries": [
            {
                "capsule_id": row["capsule_id"],
                "delivered_at": row["delivered_at"],
                "kind": row["kind"],
                "response": row["response"],
                "state": row["state"],
                "responded_at": row["responded_at"],
            }
            for row in raw["deliveries"]
        ],
    }


def dumps_payload(payload: dict) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)


def loads_payload(raw: str | bytes) -> dict:
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UserInputError("That file is not a Time Capsule export.") from exc
    if not isinstance(payload, dict) or payload.get("version") != EXPORT_VERSION:
        raise UserInputError("That export is from a different archive version.")
    if not isinstance(payload.get("user_id"), int):
        raise UserInputError("That export is missing its owner.")
    _validate_payload(payload)
    return payload


def restore_payload(repository, user_id: int, payload: dict, now: datetime) -> int:
    if payload.get("user_id") != user_id:
        raise UserInputError("This export belongs to a different archive.")
    _validate_payload(payload)
    settings = dict(payload.get("settings") or {})
    settings["timezone"] = validate_timezone(str(settings.get("timezone") or "UTC"))
    limit = int(settings.get("daily_limit") or 1)
    if not 1 <= limit <= 5:
        raise UserInputError("Daily limit in the export is invalid.")
    settings["daily_limit"] = limit
    settings["discovery_enabled"] = bool(settings.get("discovery_enabled", True))
    ready = dict(payload)
    ready["settings"] = settings
    return repository.replace_user_archive(user_id, ready, now)


def write_export(repository, user_id: int, now: datetime, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    payload = export_payload(repository, user_id, now)
    path = directory / f"time-capsule-{user_id}-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(dumps_payload(payload), encoding="utf-8")
    _trim_exports(directory, user_id)
    return path


def backup_database(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)


def rotate_daily_backups(source: Path, backup_dir: Path, now: datetime, keep: int = 7) -> Path | None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f"capsule-{now.date().isoformat()}.db"
    created = False
    if not destination.exists():
        backup_database(source, destination)
        created = True
    files = sorted(backup_dir.glob("capsule-*.db"))
    for old in files[:-keep]:
        old.unlink(missing_ok=True)
    return destination if created else None


def _canonical_public_id(public_id: object) -> str | None:
    if not isinstance(public_id, str) or not public_id.startswith("TC-"):
        return None
    number_text = public_id[3:]
    if not number_text.isdigit():
        return None
    number = int(number_text)
    if number < 1 or format_public_id(number) != public_id:
        return None
    return public_id


def _validate_payload(payload: dict) -> None:
    capsules = payload.get("capsules")
    tags = payload.get("tags")
    links = payload.get("capsule_tags")
    deliveries = payload.get("deliveries")
    if not all(isinstance(part, list) for part in (capsules, tags, links, deliveries)):
        raise UserInputError("That export is incomplete.")
    if len(capsules) > 10000:
        raise UserInputError("That export is too large.")
    seen_ids: set[int] = set()
    seen_public: set[str] = set()
    try:
        _validate_rows(capsules, tags, links, deliveries, seen_ids, seen_public)
    except (TypeError, ValueError, KeyError) as exc:
        raise UserInputError("That export is incomplete.") from exc


def _validate_rows(capsules, tags, links, deliveries, seen_ids, seen_public) -> None:
    for row in capsules:
        if not isinstance(row, dict):
            raise UserInputError("That export contains a broken note.")
        capsule_id = row.get("id")
        public_id = row.get("public_id")
        if not isinstance(capsule_id, int) or capsule_id in seen_ids:
            raise UserInputError("That export contains a broken note.")
        if _canonical_public_id(public_id) is None or public_id in seen_public:
            raise UserInputError("That export contains a broken note.")
        text = row.get("text_content")
        if text is not None and not isinstance(text, str):
            raise UserInputError("That export contains a broken note.")
        if row.get("kind") not in _KINDS or row.get("release_mode") not in _MODES:
            raise UserInputError("That export contains a broken note.")
        file_path = row.get("file_path")
        if file_path is not None:
            path = Path(str(file_path))
            if path.is_absolute() or ".." in path.parts:
                raise UserInputError("That export points outside the archive.")
        seen_ids.add(capsule_id)
        seen_public.add(public_id)
    tag_ids: set[int] = set()
    for row in tags:
        if not isinstance(row, dict) or not isinstance(row.get("id"), int) or not isinstance(row.get("name"), str):
            raise UserInputError("That export contains a broken tag.")
        tag_ids.add(row["id"])
    for link in links:
        if int(link["capsule_id"]) not in seen_ids or int(link["tag_id"]) not in tag_ids:
            raise UserInputError("That export contains a broken tag.")
    for row in deliveries:
        if int(row["capsule_id"]) not in seen_ids or row.get("kind") not in _DELIVERY_KINDS:
            raise UserInputError("That export contains a broken delivery.")
        if row.get("state") not in _STATES or row.get("response") not in _RESPONSES:
            raise UserInputError("That export contains a broken delivery.")


def _trim_exports(directory: Path, user_id: int, keep: int = 5) -> None:
    files = sorted(directory.glob(f"time-capsule-{user_id}-*.json"))
    for old in files[:-keep]:
        old.unlink(missing_ok=True)
