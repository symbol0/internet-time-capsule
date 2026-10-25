from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from app.handlers.common import answer, safe
from app.keyboards.inline import start_keyboard
from app.services.archive import ArchiveService
from app.states import Flow

router = Router(name="start")


WELCOME = (
    "Time Capsule\n\n"
    "Your personal archive. Save something today, and let it come back later.\n\n"
    "/save — archive a note or link\n"
    "/today — open what is due\n"
    "/vault — browse what you saved\n"
    "/search — find a note\n"
    "/seal — lock a note until a date\n"
    "/tags — browse tags\n"
    "/stats — archive summary\n"
    "/export — download your archive\n"
    "/settings — reminders and discovery\n"
    "/delete — remove a note\n\n"
    "Forward a message and its text is saved. File attachments are not stored yet.\n\n"
    "Times display in UTC until you choose a timezone."
)


@router.message(CommandStart())
async def cmd_start(message: Message, archive: ArchiveService, state) -> None:
    async with safe(message):
        await state.clear()
        archive.ensure_user(message.from_user.id)
        await answer(message, WELCOME, reply_markup=start_keyboard())


@router.callback_query(F.data == "go:save")
async def go_save(query: CallbackQuery, state) -> None:
    async with safe(query):
        await state.set_state(Flow.save_text)
        await query.answer()
        if query.message is not None:
            await query.message.answer("Send the note or link to archive.")
