from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.formatting import RESPONSE_CODES, RESPONSE_LABELS, format_already, format_today_quiet
from app.handlers.common import answer, safe
from app.services.archive import ArchiveService
from app.timeutils import local_day_bounds, utcnow

router = Router(name="today")


@router.message(Command("today"))
async def cmd_today(message: Message, archive: ArchiveService, delivery, state) -> None:
    async with safe(message):
        await state.clear()
        user_id = message.from_user.id
        now = utcnow()
        settings = archive.ensure_user(user_id, now)
        sent = await delivery.deliver_for_user(user_id, now)
        if sent:
            noun = "note" if len(sent) == 1 else "notes"
            await answer(message, f"Opened {len(sent)} {noun}.")
            return
        start, end, _day = local_day_bounds(now, settings.timezone)
        already = archive.repository.deliveries_between(user_id, start, end)
        if already:
            await answer(message, format_already(already))
            return
        waiting = archive.repository.count_sealed(user_id, now)
        await answer(message, format_today_quiet(settings, waiting))


@router.callback_query(F.data.startswith("rv:"))
async def review(query: CallbackQuery, archive: ArchiveService) -> None:
    async with safe(query):
        try:
            _, raw_id, code = (query.data or "").split(":")
            response = RESPONSE_CODES[code]
        except (ValueError, KeyError) as exc:
            from app.errors import UserInputError

            raise UserInputError("That button expired.") from exc
        saved = archive.repository.set_response(
            query.from_user.id, int(raw_id), response, utcnow()
        )
        if not saved:
            from app.errors import NotFoundError

            raise NotFoundError("That rediscovery is not in your archive.")
        await query.answer(f"Marked: {RESPONSE_LABELS[response]}")
