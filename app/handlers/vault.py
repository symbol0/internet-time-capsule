from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from app.formatting import capsule_detail, delete_prompt, format_vault
from app.handlers.common import answer, edit_or_send, safe
from app.keyboards.inline import PAGE_SIZE, confirm_keyboard, item_keyboard, schedule_keyboard, vault_keyboard
from app.services.archive import ArchiveService
from app.timeutils import unlock_from_date, utcnow

router = Router(name="vault")


async def show_vault(target, archive: ArchiveService, user_id: int, offset: int) -> None:
    now = utcnow()
    settings = archive.ensure_user(user_id, now)
    total = archive.count(user_id)
    if offset < 0 or (total and offset >= total):
        offset = 0
    capsules = archive.vault(user_id, limit=PAGE_SIZE, offset=offset)
    text = format_vault(capsules, total, offset, settings, now)
    keyboard = vault_keyboard(capsules, offset, total) if total else None
    if isinstance(target, CallbackQuery):
        await edit_or_send(target, text, reply_markup=keyboard)
    else:
        await answer(target, text, reply_markup=keyboard)


@router.message(Command("vault"))
async def cmd_vault(message: Message, archive: ArchiveService, state) -> None:
    async with safe(message):
        await state.clear()
        await show_vault(message, archive, message.from_user.id, 0)


@router.callback_query(F.data.startswith("vault:"))
async def page_vault(query: CallbackQuery, archive: ArchiveService) -> None:
    async with safe(query):
        offset = int(query.data.split(":", 1)[1])
        await query.answer()
        await show_vault(query, archive, query.from_user.id, offset)


@router.callback_query(F.data.startswith("item:"))
async def open_item(query: CallbackQuery, archive: ArchiveService) -> None:
    async with safe(query):
        capsule_id = int(query.data.split(":", 1)[1])
        now = utcnow()
        settings = archive.ensure_user(query.from_user.id, now)
        capsule = archive.get(query.from_user.id, capsule_id)
        await query.answer()
        await edit_or_send(
            query,
            capsule_detail(capsule, settings, now),
            reply_markup=item_keyboard(capsule, now),
        )


@router.message(Command("seal"))
async def cmd_seal(
    message: Message, command: CommandObject, archive: ArchiveService, state
) -> None:
    async with safe(message):
        await state.clear()
        if not command.args:
            await answer(message, "Which note? Example: /seal TC-0042 or /seal TC-0042 2027-04-01")
            return
        raw_id, _, raw_date = command.args.partition(" ")
        now = utcnow()
        settings = archive.ensure_user(message.from_user.id, now)
        capsule = archive.get_public(message.from_user.id, raw_id)
        if not raw_date:
            await answer(
                message,
                f"Choose when <code>{capsule.public_id}</code> should open.",
                reply_markup=schedule_keyboard(capsule.id),
            )
            return
        unlock_at = unlock_from_date(raw_date, settings.timezone, settings.delivery_time, now)
        capsule = archive.seal(message.from_user.id, capsule.id, unlock_at, now)
        from app.formatting import seal_confirmation

        await answer(message, seal_confirmation(capsule, settings))


@router.message(Command("delete"))
async def cmd_delete(message: Message, command: CommandObject, archive: ArchiveService, state) -> None:
    async with safe(message):
        await state.clear()
        if not command.args:
            await answer(message, "Delete which note? Example: /delete TC-0042")
            return
        now = utcnow()
        settings = archive.ensure_user(message.from_user.id, now)
        capsule = archive.get_public(message.from_user.id, command.args)
        await answer(
            message,
            delete_prompt(capsule, now),
            reply_markup=confirm_keyboard(f"delok:{capsule.id}", f"delno:{capsule.id}"),
        )


@router.callback_query(F.data.startswith("del:"))
async def ask_delete(query: CallbackQuery, archive: ArchiveService) -> None:
    async with safe(query):
        capsule = archive.get(query.from_user.id, int(query.data.split(":", 1)[1]))
        await query.answer()
        await edit_or_send(
            query,
            delete_prompt(capsule, utcnow()),
            reply_markup=confirm_keyboard(f"delok:{capsule.id}", f"item:{capsule.id}", yes_label="Delete"),
        )


@router.callback_query(F.data.startswith("delok:"))
async def confirm_delete(query: CallbackQuery, archive: ArchiveService) -> None:
    async with safe(query):
        capsule_id = int(query.data.split(":", 1)[1])
        removed = archive.delete(query.from_user.id, capsule_id)
        await query.answer("Deleted")
        await edit_or_send(query, f"<code>{removed.public_id}</code> is deleted, including any local file.")


@router.callback_query(F.data.startswith("delno:"))
async def cancel_delete(query: CallbackQuery) -> None:
    async with safe(query):
        await query.answer("Kept")
        await edit_or_send(query, "Kept.")


@router.callback_query(F.data.startswith("unseal:"))
async def ask_unseal(query: CallbackQuery, archive: ArchiveService) -> None:
    async with safe(query):
        capsule = archive.get(query.from_user.id, int(query.data.split(":", 1)[1]))
        await query.answer()
        await edit_or_send(
            query,
            f"Unseal <code>{capsule.public_id}</code> now? The note becomes visible in the vault.",
            reply_markup=confirm_keyboard(f"unok:{capsule.id}", f"item:{capsule.id}", yes_label="Unseal"),
        )


@router.callback_query(F.data.startswith("unok:"))
async def confirm_unseal(query: CallbackQuery, archive: ArchiveService) -> None:
    async with safe(query):
        now = utcnow()
        settings = archive.ensure_user(query.from_user.id, now)
        capsule = archive.unseal(query.from_user.id, int(query.data.split(":", 1)[1]))
        await query.answer("Unsealed")
        await edit_or_send(
            query,
            capsule_detail(capsule, settings, now),
            reply_markup=item_keyboard(capsule, now),
        )
