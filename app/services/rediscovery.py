from datetime import datetime

from app.models import Settings
from app.timeutils import local_day_bounds, split_time, to_local

MIN_AGE_DAYS = 30
COOLDOWN_DAYS = 90


def choose_rediscoveries(eligible_items, delivered_ids, daily_limit):
    """Pick the oldest eligible notes, up to the daily limit.

    Scheduled unlocks are not part of this list. The query that builds
    `eligible_items` has already dropped sealed capsules whose date has not
    arrived, and anything still waiting on its scheduled delivery.
    """
    if daily_limit <= 0:
        return []
    candidates = [
        item
        for item in eligible_items
        if item.id not in delivered_ids and item.status == "active"
    ]
    candidates.sort(key=lambda item: item.created_at)
    return candidates[:daily_limit]


def discovery_due(settings: Settings, now: datetime) -> bool:
    if not settings.discovery_enabled:
        return False
    local = to_local(now, settings.timezone)
    if settings.last_discovery_date == local.date().isoformat():
        return False
    hour, minute = split_time(settings.delivery_time)
    return (local.hour, local.minute) >= (hour, minute)


def select_rediscoveries(repository, settings: Settings, now: datetime) -> list:
    if not settings.discovery_enabled:
        return []
    start, end, _day = local_day_bounds(now, settings.timezone)
    already = repository.count_rediscovery_between(settings.user_id, start, end)
    remaining = settings.daily_limit - already
    eligible = repository.eligible_items(settings.user_id, now, MIN_AGE_DAYS)
    delivered = repository.recent_delivery_ids(settings.user_id, now, COOLDOWN_DAYS)
    return choose_rediscoveries(eligible, delivered, remaining)
