from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.errors import UserInputError


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def dump_dt(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def to_local(moment: datetime, tzname: str) -> datetime:
    try:
        zone = ZoneInfo(tzname)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(zone)


def validate_timezone(name: str) -> str:
    cleaned = name.strip()
    try:
        ZoneInfo(cleaned)
    except ZoneInfoNotFoundError as exc:
        raise UserInputError("Unknown timezone. Try Asia/Dhaka or UTC.") from exc
    return cleaned


def split_time(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError) as exc:
        raise UserInputError("Use a time like 09:00.") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise UserInputError("Use a time like 09:00.")
    return hour, minute


def normalize_delivery_time(value: str) -> str:
    hour, minute = split_time(value.strip())
    return f"{hour:02d}:{minute:02d}"


def local_day_bounds(moment: datetime, tzname: str) -> tuple[datetime, datetime, str]:
    local = to_local(moment, tzname)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
        start_local.date().isoformat(),
    )


def unlock_from_preset(now: datetime, preset: str) -> datetime | None:
    if preset == "open":
        return None
    days = {"1d": 1, "30d": 30, "1y": 365}.get(preset)
    if days is None:
        raise UserInputError("Unknown schedule.")
    return (now + timedelta(days=days)).replace(microsecond=0)


def unlock_from_date(date_text: str, tzname: str, delivery_time: str, now: datetime) -> datetime:
    try:
        day = datetime.strptime(date_text.strip(), "%Y-%m-%d")
    except ValueError as exc:
        raise UserInputError("Use a date like 2027-04-01.") from exc
    hour, minute = split_time(delivery_time)
    try:
        zone = ZoneInfo(tzname)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    local = day.replace(hour=hour, minute=minute, tzinfo=zone)
    current = to_local(now, tzname)
    if local <= current:
        raise UserInputError("Pick a date in the future.")
    return local.astimezone(timezone.utc).replace(microsecond=0)
