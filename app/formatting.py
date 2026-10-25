from datetime import datetime
from html import escape

from app.models import Capsule, Delivery, Settings, Stats
from app.services.rediscovery import MIN_AGE_DAYS
from app.timeutils import to_local

RESPONSE_CODES = {
    "c": "completed",
    "s": "still_relevant",
    "u": "useful",
    "o": "outdated",
}
RESPONSE_LABELS = {
    "completed": "Started it",
    "still_relevant": "Still interested",
    "useful": "Useful",
    "outdated": "Not relevant anymore",
}
KIND_LABELS = {
    "scheduled": "Opened on schedule",
    "rediscovery": "Recovered from your past",
}


def is_locked(capsule: Capsule, now: datetime) -> bool:
    return (
        capsule.release_mode == "sealed"
        and capsule.unlock_at is not None
        and capsule.unlock_at > now
    )


def format_when(moment: datetime, tzname: str) -> str:
    local = to_local(moment, tzname)
    return local.strftime("%d %b %Y, %H:%M")


def age_phrase(created: datetime, now: datetime, tzname: str = "UTC") -> str:
    days = (to_local(now, tzname).date() - to_local(created, tzname).date()).days
    if days <= 0:
        return "Earlier today, you saved this."
    if days == 1:
        return "Yesterday, you saved this."
    if days < 45:
        return f"{days} days ago, you saved this."
    if days < 75:
        return "About a month ago, you saved this."
    months = max(2, days // 30)
    if months < 24:
        return f"About {months} months ago, you saved this."
    return f"About {months // 12} years ago, you saved this."


def quote(text: str | None, limit: int = 3500) -> str:
    body = (text or "").strip()
    if len(body) > limit:
        body = body[: limit - 1].rstrip() + "…"
    return f"<blockquote>{escape(body)}</blockquote>"


def saved_prompt(capsule: Capsule) -> str:
    lines = [
        "Sealed in the Vault",
        "",
        f"Saved as <code>{escape(capsule.public_id)}</code>",
    ]
    if capsule.tags:
        lines.append(escape(" ".join(f"#{tag}" for tag in capsule.tags)))
    lines.extend(["", quote(capsule.text_content, limit=500), "", "Choose when to rediscover it."])
    return "\n".join(lines)


def seal_confirmation(capsule: Capsule, settings: Settings) -> str:
    if capsule.release_mode != "sealed" or capsule.unlock_at is None:
        return (
            f"<code>{escape(capsule.public_id)}</code> stays in the vault.\n\n"
            f"It can resurface after it is {MIN_AGE_DAYS} days old."
        )
    when = format_when(capsule.unlock_at, settings.timezone)
    return (
        f"<code>{escape(capsule.public_id)}</code> is sealed until {escape(when)}.\n\n"
        "It will not show up before that date. Telegram still keeps this chat, "
        "but the vault hides the note until then."
    )


def capsule_summary(capsule: Capsule, settings: Settings, now: datetime) -> str:
    when = format_when(capsule.created_at, settings.timezone)
    if is_locked(capsule, now) and capsule.unlock_at is not None:
        unlock = format_when(capsule.unlock_at, settings.timezone)
        state = f"Sealed until {unlock}"
    elif capsule.release_mode == "sealed":
        state = "Waiting to open"
    else:
        state = "Open"
    tags = f" · {escape(' '.join('#' + tag for tag in capsule.tags))}" if capsule.tags else ""
    return f"<code>{escape(capsule.public_id)}</code> · {escape(when)} · {escape(state)}{tags}"


def capsule_detail(capsule: Capsule, settings: Settings, now: datetime) -> str:
    summary = capsule_summary(capsule, settings, now)
    if is_locked(capsule, now):
        return summary + "\n\nThe note stays hidden until the seal date."
    lines = [summary, "", quote(capsule.text_content)]
    if capsule.url and capsule.kind != "url":
        lines.extend(["", escape(capsule.url)])
    return "\n".join(lines)


def delivery_text(capsule: Capsule, kind: str, now: datetime, tzname: str) -> str:
    if kind == "scheduled":
        lead = "Opened on schedule"
        detail = "You asked to see this today."
    else:
        lead = "Recovered from your past"
        detail = age_phrase(capsule.created_at, now, tzname)
    lines = [
        lead,
        "",
        f"<code>{escape(capsule.public_id)}</code>",
        escape(detail),
        "",
        quote(capsule.text_content),
    ]
    if capsule.tags:
        lines.extend(["", escape(" ".join(f"#{tag}" for tag in capsule.tags))])
    lines.extend(["", "What happened to this?"])
    return "\n".join(lines)


def delete_prompt(capsule: Capsule, now: datetime) -> str:
    if is_locked(capsule, now):
        return (
            f"Delete sealed note <code>{escape(capsule.public_id)}</code> permanently? "
            "This cannot be undone."
        )
    preview = escape((capsule.text_content or "")[:180])
    return (
        f"Delete <code>{escape(capsule.public_id)}</code> permanently?\n\n"
        f"{preview}\n\nThis cannot be undone."
    )


def format_vault(capsules: list[Capsule], total: int, offset: int, settings: Settings, now: datetime) -> str:
    if total == 0:
        return "The vault is empty.\n\nSave a note with /save, or forward a message here."
    start = offset + 1
    end = offset + len(capsules)
    lines = [f"Vault · {total} saved", "", f"Showing {start}–{end} of the newest.", ""]
    lines.extend(capsule_summary(capsule, settings, now) for capsule in capsules)
    lines.extend(["", "Open one to seal it, tag it, or delete it."])
    return "\n".join(lines)


def format_search(capsules: list[Capsule], settings: Settings, now: datetime) -> str:
    if not capsules:
        return "Nothing in your vault matches that."
    lines = [f"{len(capsules)} match{'es' if len(capsules) != 1 else ''}.", ""]
    lines.extend(capsule_summary(capsule, settings, now) for capsule in capsules)
    return "\n".join(lines)


def format_tags(tags) -> str:
    if not tags:
        return "No tags yet.\n\nAdd one while saving, like #ideas, or from an open note."
    lines = ["Tags", ""]
    for tag in tags:
        noun = "note" if tag.count == 1 else "notes"
        lines.append(f"#{escape(tag.name)} · {tag.count} {noun}")
    lines.extend(["", "Open a tag to see its notes, or remove the tag. The notes stay."])
    return "\n".join(lines)


def format_stats(stats: Stats, settings: Settings, now: datetime) -> str:
    if stats.oldest_at is None:
        age = "No notes yet"
    else:
        days = max(0, (to_local(now, settings.timezone).date() - to_local(stats.oldest_at, settings.timezone).date()).days)
        age = f"{days} days since the oldest note"
    lines = [
        "Archive",
        "",
        age,
        f"{stats.capsule_count} saved · {stats.open_count} open · {stats.sealed_count} sealed",
        f"{stats.tag_count} tags",
        f"{stats.rediscovery_count} rediscoveries · {stats.scheduled_count} scheduled openings",
    ]
    if stats.responses:
        parts = [
            f"{RESPONSE_LABELS.get(key, key)} {count}"
            for key, count in sorted(stats.responses.items())
        ]
        lines.append("Responses: " + ", ".join(parts))
    lines.append(
        f"Discovery {'on' if settings.discovery_enabled else 'off'} · "
        f"{settings.daily_limit} a day at {settings.delivery_time} ({settings.timezone})"
    )
    return "\n".join(lines)


def format_settings(settings: Settings) -> str:
    discovery = "on" if settings.discovery_enabled else "off"
    return (
        "Settings\n\n"
        f"Timezone: {escape(settings.timezone)}\n"
        f"Rediscovery: {discovery}\n"
        f"Daily limit: {settings.daily_limit}\n"
        f"Delivery time: {escape(settings.delivery_time)}\n\n"
        f"Open notes can come back after {MIN_AGE_DAYS} days. "
        "A sealed note waits for its date, and that date always wins."
    )


def format_today_quiet(settings: Settings, sealed_waiting: int) -> str:
    lines = ["Nothing new is due."]
    if settings.discovery_enabled:
        lines.append(
            f"Open notes resurface after {MIN_AGE_DAYS} days, oldest first, "
            f"up to {settings.daily_limit} a day."
        )
        lines.append(f"The daily pass is at {settings.delivery_time} ({settings.timezone}).")
    else:
        lines.append("Rediscovery is off. Sealed notes still open on their date.")
    if sealed_waiting:
        noun = "note is" if sealed_waiting == 1 else "notes are"
        lines.append(f"{sealed_waiting} sealed {noun} still waiting.")
    return "\n".join(lines)


def format_already(deliveries: list[Delivery]) -> str:
    lines = ["Already opened today.", ""]
    for delivery in deliveries:
        label = KIND_LABELS.get(delivery.kind, delivery.kind)
        public = delivery.public_id or str(delivery.capsule_id)
        lines.append(f"<code>{escape(public)}</code> · {escape(label)}")
    return "\n".join(lines)
