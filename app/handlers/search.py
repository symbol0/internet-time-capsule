from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from app.formatting import format_search, format_tags
from app.handlers.common import answer, edit_or_send, safe
from app.keyboards.inline import confirm_keyboard, tags_keyboard, vault_keyboard
from app.services.archive import ArchiveService
from app.states import Flow
from app.timeutils import utcnow

router = Router(name="search")


async def show_matches(target, archive: ArchiveService, user_id: int, query: str) -> None:
    now = utcnow()
    settings = archive.ensure_user(user_id, now)
    capsules = archive.search(user_id, query)
    text = format_search(capsules, settings, now)
    keyboard = vault_keyboard(capsules, 0, len(capsules)) if capsules else None
    if isinstance(target, CallbackQuery):
        await edit_or_send(target, text, reply_markup=keyboard)
    else:
        await answer(target, text, reply_markup=keyboard)


@router.message(Command("search"))
async def cmd_search(
    message: Message, command: CommandObject, archive: ArchiveService, state
) -> None:
    async with safe(message):
        await state.clear()
        archive.ensure_user(message.from_user.id)
        if not command.args:
            await state.set_state(Flow.search_query)
            await answer(message, "Send a word, a link, or an id like TC-0042.")
            return
        await show_matches(message, archive, message.from_user.id, command.args)


@router.message(Flow.search_query, F.text, ~F.text.startswith("/"))
async def search_from_prompt(message: Message, archive: ArchiveService, state) -> None:
    async with safe(message):
        await state.clear()
        await show_matches(message, archive, message.from_user.id, message.text or "")


@router.message(Command("tags"))
async def cmd_tags(message: Message, archive: ArchiveService, state) -> None:
    async with safe(message):
        await state.clear()
        archive.ensure_user(message.from_user.id)
        tags = archive.tags(message.from_user.id)
        await answer(message, format_tags(tags), reply_markup=tags_keyboard(tags) if tags else None)


@router.callback_query(F.data.startswith("tagview:"))
async def view_tag(query: CallbackQuery, archive: ArchiveService) -> None:
    async with safe(query):
        tag = archive.repository.get_tag(query.from_user.id, int(query.data.split(":", 1)[1]))
        if tag is None:
            from app.errors import NotFoundError

            raise NotFoundError("That tag is not in your archive.")
        await query.answer()
        await show_matches(query, archive, query.from_user.id, f"#{tag.name}")


@router.callback_query(F.data.startswith("tagx:"))
async def ask_remove_tag(query: CallbackQuery, archive: ArchiveService) -> None:
    async with safe(query):
        tag = archive.repository.get_tag(query.from_user.id, int(query.data.split(":", 1)[1]))
        if tag is None:
            from app.errors import NotFoundError

            raise NotFoundError("That tag is not in your archive.")
        await query.answer()
        await edit_or_send(
            query,
            f"Remove #{tag.name}? The notes stay in the vault.",
            reply_markup=confirm_keyboard(f"tagxok:{tag.id}", "tags:back", yes_label="Remove"),
        )


@router.callback_query(F.data == "tags:back")
async def tags_back(query: CallbackQuery, archive: ArchiveService) -> None:
    async with safe(query):
        tags = archive.tags(query.from_user.id)
        await query.answer()
        await edit_or_send(query, format_tags(tags), reply_markup=tags_keyboard(tags) if tags else None)


@router.callback_query(F.data.startswith("tagxok:"))
async def remove_tag(query: CallbackQuery, archive: ArchiveService) -> None:
    async with safe(query):
        name = archive.delete_tag(query.from_user.id, int(query.data.split(":", 1)[1]))
        tags = archive.tags(query.from_user.id)
        await query.answer("Removed")
        await edit_or_send(
            query,
            f"Removed #{name}.\n\n" + format_tags(tags),
            reply_markup=tags_keyboard(tags) if tags else None,
        )
