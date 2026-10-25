import asyncio
from datetime import timedelta
from types import SimpleNamespace

from app.services.rediscovery import (
    MIN_AGE_DAYS,
    choose_rediscoveries,
    discovery_due,
    select_rediscoveries,
)
from app.services.scheduler import DeliveryService


def _item(item_id, created, status="active"):
    return SimpleNamespace(id=item_id, created_at=created, status=status)


def test_choose_rediscoveries_prefers_the_oldest_within_the_limit(now):
    items = [
        _item(2, now - timedelta(days=10)),
        _item(1, now - timedelta(days=40)),
        _item(3, now - timedelta(days=20), status="deleted"),
    ]
    chosen = choose_rediscoveries(items, delivered_ids={2}, daily_limit=1)
    assert [item.id for item in chosen] == [1]
    assert choose_rediscoveries(items, delivered_ids=set(), daily_limit=0) == []


def test_future_seal_never_surfaces_early(archive, repo, now):
    capsule = archive.save_text(1, "Open me next year", now=now)
    archive.seal(1, capsule.id, now + timedelta(days=365), now)
    assert repo.due_scheduled(now + timedelta(days=30)) == []
    assert repo.eligible_items(1, now + timedelta(days=30), MIN_AGE_DAYS) == []
    assert repo.due_scheduled(now + timedelta(days=365))[0].id == capsule.id


def test_only_old_open_notes_are_eligible_and_the_oldest_is_sent(archive, repo, now):
    archive.save_text(1, "too new", now=now - timedelta(days=29))
    oldest = archive.save_text(1, "oldest idea", now=now - timedelta(days=80))
    newer = archive.save_text(1, "also old", now=now - timedelta(days=40))
    archive.save_text(2, "someone else", now=now - timedelta(days=100))
    eligible = repo.eligible_items(1, now, MIN_AGE_DAYS)
    assert [item.id for item in eligible] == [oldest.id, newer.id]
    settings = archive.update_settings(1, daily_limit=1)
    chosen = select_rediscoveries(repo, settings, now)
    assert [item.id for item in chosen] == [oldest.id]


def test_recent_delivery_is_skipped_and_an_old_one_can_return(archive, repo, now):
    quiet = archive.save_text(1, "quiet", now=now - timedelta(days=50))
    repeat = archive.save_text(1, "repeat", now=now - timedelta(days=120))
    recent = repo.claim_delivery(1, quiet.id, "rediscovery", now - timedelta(days=2))
    repo.mark_delivery(recent, "sent", 1)
    old = repo.claim_delivery(1, repeat.id, "rediscovery", now - timedelta(days=100))
    repo.mark_delivery(old, "sent", 2)
    settings = archive.ensure_user(1, now)
    chosen = select_rediscoveries(repo, settings, now)
    assert [item.id for item in chosen] == [repeat.id]


def test_discovery_clock_uses_the_user_timezone(archive, now):
    archive.ensure_user(1, now)
    settings = archive.update_settings(
        1, timezone="Asia/Dhaka", delivery_time="09:00", discovery_enabled=True
    )
    before = now.replace(hour=2, minute=59)
    at_nine = now.replace(hour=3, minute=0)
    assert discovery_due(settings, before) is False
    assert discovery_due(settings, at_nine) is True
    archive.repository.set_last_discovery_date(1, "2026-10-25")
    done = archive.repository.get_settings(1)
    assert discovery_due(done, at_nine) is False


def test_scheduled_delivery_retries_then_does_not_repeat(archive, repo, now):
    capsule = archive.save_text(1, "see you tomorrow", now=now)
    archive.seal(1, capsule.id, now + timedelta(days=1), now)
    later = now + timedelta(days=1, minutes=5)

    class Sender:
        def __init__(self):
            self.failures = 1
            self.sent = []

        async def __call__(self, outgoing):
            if self.failures:
                self.failures -= 1
                raise RuntimeError("telegram unavailable")
            self.sent.append(outgoing)
            return 50

    sender = Sender()
    delivery = DeliveryService(repo, sender)

    async def attempt():
        return (
            await delivery.deliver_due(later),
            await delivery.deliver_due(later),
            await delivery.deliver_due(later),
        )

    first, second, third = asyncio.run(attempt())
    assert first == []
    assert [item.capsule.id for item in second] == [capsule.id]
    assert second[0].kind == "scheduled"
    assert third == []
    opened = repo.get(1, capsule.id)
    assert opened is not None
    assert opened.release_mode == "open"


def test_manual_today_can_run_before_the_daily_clock_without_duplicates(archive, repo, now):
    capsule = archive.save_text(1, "resurface me", now=now - timedelta(days=45))
    archive.update_settings(1, delivery_time="23:00", daily_limit=1)

    class Sender:
        def __init__(self):
            self.sent = []

        async def __call__(self, outgoing):
            self.sent.append(outgoing.kind)
            return len(self.sent)

    sender = Sender()
    delivery = DeliveryService(repo, sender)

    async def attempt():
        skipped = await delivery.deliver_due(now)
        opened = await delivery.deliver_for_user(1, now)
        repeated = await delivery.deliver_for_user(1, now)
        return skipped, opened, repeated

    skipped, opened, repeated = asyncio.run(attempt())
    assert skipped == []
    assert [item.capsule.id for item in opened] == [capsule.id]
    assert sender.sent == ["rediscovery"]
    assert repeated == []


def test_sealed_item_is_not_rediscovered_before_its_scheduled_send(archive, repo, now):
    capsule = archive.save_text(1, "wait", now=now - timedelta(days=400))
    unlock = now - timedelta(minutes=1)
    repo.update_release(1, capsule.id, "sealed", unlock)
    settings = archive.ensure_user(1, now)
    assert select_rediscoveries(repo, settings, now) == []
    due = repo.due_scheduled(now)
    assert [item.id for item in due] == [capsule.id]
