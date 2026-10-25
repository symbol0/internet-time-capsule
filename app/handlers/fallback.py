from aiogram import F, Router
from aiogram.types import Message

from app.handlers.common import answer

router = Router(name="fallback")


@router.message(F.text.startswith("/"))
async def unknown(message: Message, state) -> None:
    await state.clear()
    await answer(message, "Unknown command. /start lists what I can do.")
