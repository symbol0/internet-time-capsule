import logging
from dataclasses import dataclass
from datetime import datetime

from app.models import Capsule
from app.services.export import rotate_daily_backups
from app.services.rediscovery import discovery_due, select_rediscoveries
from app.timeutils import local_day_bounds

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Outgoing:
    user_id: int
    capsule: Capsule
    kind: str
    delivery_id: int


class DeliveryService:
    """Sends scheduled unlocks and rediscoveries. A send is recorded only after it succeeds."""

    def __init__(self, repository, sender, backup_dir=None):
        self.repository = repository
        self.sender = sender
        self.backup_dir = backup_dir
        self._sending = None

    async def deliver_due(self, now: datetime) -> list[Outgoing]:
        async with self._lock():
            self.repository.fail_stale_pending(now)
            sent: list[Outgoing] = []
            for capsule in self.repository.due_scheduled(now):
                outgoing = await self._send_one(capsule, "scheduled", now)
                if outgoing is not None:
                    sent.append(outgoing)
            for settings in self.repository.all_settings():
                if not discovery_due(settings, now):
                    continue
                items = select_rediscoveries(self.repository, settings, now)
                if not items:
                    _day = local_day_bounds(now, settings.timezone)[2]
                    self.repository.set_last_discovery_date(settings.user_id, _day)
                    continue
                all_ok = True
                for capsule in items:
                    outgoing = await self._send_one(capsule, "rediscovery", now)
                    if outgoing is None:
                        all_ok = False
                        break
                    sent.append(outgoing)
                if all_ok:
                    _day = local_day_bounds(now, settings.timezone)[2]
                    self.repository.set_last_discovery_date(settings.user_id, _day)
            return sent

    async def deliver_for_user(self, user_id: int, now: datetime) -> list[Outgoing]:
        """Manual /today. Scheduled capsules always go out. Rediscovery ignores the clock."""
        async with self._lock():
            self.repository.fail_stale_pending(now)
            sent: list[Outgoing] = []
            for capsule in self.repository.due_scheduled(now, user_id=user_id):
                outgoing = await self._send_one(capsule, "scheduled", now)
                if outgoing is not None:
                    sent.append(outgoing)
            settings = self.repository.get_settings(user_id)
            for capsule in select_rediscoveries(self.repository, settings, now):
                outgoing = await self._send_one(capsule, "rediscovery", now)
                if outgoing is None:
                    break
                sent.append(outgoing)
            return sent

    def backup_if_needed(self, now: datetime):
        if self.backup_dir is None:
            return None
        return rotate_daily_backups(self.repository.path, self.backup_dir, now)

    async def _send_one(self, capsule: Capsule, kind: str, now: datetime) -> Outgoing | None:
        delivery_id = self.repository.claim_delivery(capsule.user_id, capsule.id, kind, now)
        if delivery_id is None:
            return None
        outgoing = Outgoing(
            user_id=capsule.user_id,
            capsule=capsule,
            kind=kind,
            delivery_id=delivery_id,
        )
        try:
            message_id = await self.sender(outgoing)
        except Exception:
            logger.exception("delivery failed for %s", capsule.public_id)
            self.repository.mark_delivery(delivery_id, "failed")
            return None
        self.repository.mark_delivery(delivery_id, "sent", int(message_id) if message_id else None)
        if kind == "scheduled":
            self.repository.update_release(capsule.user_id, capsule.id, "open", None)
        logger.info("delivered %s (%s) to %s", capsule.public_id, kind, capsule.user_id)
        return outgoing

    def _lock(self):
        import asyncio

        if self._sending is None:
            self._sending = asyncio.Lock()
        return self._sending


class Scheduler:
    def __init__(self, delivery: DeliveryService, poll_seconds: int = 30):
        self.delivery = delivery
        self.poll_seconds = poll_seconds
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True

    async def run(self) -> None:
        import asyncio

        from app.timeutils import utcnow

        while not self._stopped:
            now = utcnow()
            try:
                await self.delivery.deliver_due(now)
                self.delivery.backup_if_needed(now)
            except Exception:
                logger.exception("scheduler pass failed")
            await asyncio.sleep(self.poll_seconds)
