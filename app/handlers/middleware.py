from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message

from app.services.access import is_user_allowed


class AllowlistMiddleware(BaseMiddleware):
    def __init__(self, allowed_user_ids: frozenset[int]):
        self.allowed_user_ids = allowed_user_ids

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is None:
            return None
        if not is_user_allowed(user.id, self.allowed_user_ids):
            if isinstance(event, Message):
                await event.answer("This Time Capsule is private.")
            elif isinstance(event, CallbackQuery):
                await event.answer("This Time Capsule is private.", show_alert=True)
            return None
        chat = None
        if isinstance(event, Message):
            chat = event.chat
        elif isinstance(event, CallbackQuery) and event.message is not None:
            chat = getattr(event.message, "chat", None)
        if chat is not None and chat.type != ChatType.PRIVATE:
            return None
        return await handler(event, data)
