import logging
from contextlib import asynccontextmanager

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, LinkPreviewOptions, Message

from app.errors import UserInputError

logger = logging.getLogger(__name__)

PREVIEW = LinkPreviewOptions(is_disabled=True)
FILE_NOTE = (
    "File contents are not stored yet. Send the link, or describe it with /save. "
    "A caption is enough if you want the words kept."
)


def answer(message: Message, text: str, reply_markup=None):
    return message.answer(
        text,
        reply_markup=reply_markup,
        link_preview_options=PREVIEW,
        parse_mode=ParseMode.HTML,
    )


async def edit_or_send(query: CallbackQuery, text: str, reply_markup=None) -> None:
    message = query.message
    if message is not None and hasattr(message, "edit_text"):
        try:
            await message.edit_text(
                text,
                reply_markup=reply_markup,
                link_preview_options=PREVIEW,
                parse_mode=ParseMode.HTML,
            )
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
    if message is not None and hasattr(message, "answer"):
        await message.answer(
            text,
            reply_markup=reply_markup,
            link_preview_options=PREVIEW,
            parse_mode=ParseMode.HTML,
        )


async def present_error(event: Message | CallbackQuery, exc: Exception) -> None:
    text = str(exc) if isinstance(exc, UserInputError) else "Something went wrong. Nothing was changed."
    try:
        if isinstance(event, CallbackQuery):
            await event.answer(text[:180], show_alert=True)
            return
        await answer(event, text)
    except Exception:
        logger.exception("could not report an error")


@asynccontextmanager
async def safe(event: Message | CallbackQuery):
    try:
        yield
    except UserInputError as exc:
        await present_error(event, exc)
    except Exception:
        logger.exception("handler failed")
        await present_error(event, UserInputError("Something went wrong. Nothing was changed."))
