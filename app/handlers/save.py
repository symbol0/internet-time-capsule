from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.types import CallbackQuery, Message, MessageOriginChannel, MessageOriginChat

from app.errors import UserInputError
from app.formatting import is_locked, saved_prompt, seal_confirmation
from app.handlers.common import FILE_NOTE, answer, edit_or_send, safe
from app.keyboards.inline import draft_keyboard, item_keyboard, schedule_keyboard
from app.services.archive import ArchiveService
from app.states import Flow
from app.timeutils import unlock_from_date, unlock_from_preset, utcnow

router = Router(name="save")


def message_body(message: Message) -> str | None:
    text = message.text or message.caption
    has_file = any(
        (
            message.document,
            message.photo,
            message.video,
            message.audio,
            message.voice,
            message.video_note,
            message.sticker,
        )
    )
    if has_file and text:
        return (
            text.strip()
            + "\n\n(The attached file was not stored. Only this text is in the vault.)"
        )
    if has_file:
        return None
    return text


def origin_label(message: Message) -> str | None:
    origin = message.forward_origin
    if origin is None:
        return None
    sender = getattr(origin, "sender_user", None)
    if sender is not None:
        parts = [sender.first_name, sender.last_name]
        name = " ".join(part for part in parts if part)
        return name or None
    hidden = getattr(origin, "sender_user_name", None)
    if hidden:
        return hidden
    chat = getattr(origin, "chat", None) or getattr(origin, "sender_chat", None)
    if chat is None:
        return None
    return getattr(chat, "title", None) or getattr(chat, "full_name", None)


def forward_source(message: Message) -> tuple[int, int] | None:
    origin = message.forward_origin
    if isinstance(origin, (MessageOriginChannel, MessageOriginChat)):
        return origin.chat.id, origin.message_id
    return None


async def acknowledge(message: Message, archive: ArchiveService, capsule) -> None:
    await answer(message, saved_prompt(capsule), reply_markup=schedule_keyboard(capsule.id))


@router.message(Command("save"))
async def cmd_save(
    message: Message, command: CommandObject, archive: ArchiveService, state
) -> None:
    async with safe(message):
        await state.clear()
        archive.ensure_user(message.from_user.id)
        if not command.args:
            await state.set_state(Flow.save_text)
            await answer(message, "Send the note or link to archive.")
            return
        capsule = archive.save_text(message.from_user.id, command.args)
        await acknowledge(message, archive, capsule)


@router.message(Flow.save_text, F.text, ~F.text.startswith("/"))
async def save_from_prompt(message: Message, archive: ArchiveService, state) -> None:
    async with safe(message):
        capsule = archive.save_text(message.from_user.id, message.text or "")
        await state.clear()
        await acknowledge(message, archive, capsule)


@router.message(StateFilter(None), F.forward_origin)
async def save_forward(message: Message, archive: ArchiveService) -> None:
    async with safe(message):
        archive.ensure_user(message.from_user.id)
        body = message_body(message)
        if not body:
            await answer(message, FILE_NOTE)
            return
        label = origin_label(message)
        if label:
            body = f"Forwarded from {label}:\n\n{body}"
        capsule = archive.save_text(
            message.from_user.id,
            body,
            forwarded=True,
            source=forward_source(message),
        )
        await acknowledge(message, archive, capsule)


@router.message(
    StateFilter(None),
    F.document | F.photo | F.video | F.audio | F.voice | F.video_note | F.sticker,
)
async def reject_file(message: Message, state) -> None:
    async with safe(message):
        body = message_body(message)
        if body is None:
            await answer(message, FILE_NOTE)
            return
        await state.update_data(draft=body)
        await answer(message, "Save the caption in the vault? The file itself is not stored.", reply_markup=draft_keyboard())


@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def offer_save(message: Message, state) -> None:
    async with safe(message):
        await state.update_data(draft=message.text)
        await answer(message, "Save this in the vault?", reply_markup=draft_keyboard())


@router.callback_query(F.data == "draft:save")
async def confirm_draft(query: CallbackQuery, archive: ArchiveService, state) -> None:
    async with safe(query):
        data = await state.get_data()
        text = data.get("draft")
        if not text:
            raise UserInputError("That draft expired. Send it again.")
        archive.ensure_user(query.from_user.id)
        capsule = archive.save_text(query.from_user.id, text)
        await state.update_data(draft=None)
        await query.answer("Saved")
        await edit_or_send(query, saved_prompt(capsule), reply_markup=schedule_keyboard(capsule.id))


@router.callback_query(F.data == "draft:drop")
async def drop_draft(query: CallbackQuery, state) -> None:
    async with safe(query):
        await state.update_data(draft=None)
        await query.answer("Left it")
        await edit_or_send(query, "Left it out of the vault.")


@router.callback_query(F.data.startswith("sealmenu:"))
async def seal_menu(query: CallbackQuery, archive: ArchiveService) -> None:
    async with safe(query):
        capsule_id = int(query.data.split(":", 1)[1])
        capsule = archive.get(query.from_user.id, capsule_id)
        now = utcnow()
        if is_locked(capsule, now):
            text = (
                f"Choose a new date for <code>{capsule.public_id}</code>. "
                "The note stays hidden."
            )
        else:
            text = saved_prompt(capsule)
        await query.answer()
        await edit_or_send(query, text, reply_markup=schedule_keyboard(capsule.id))


@router.callback_query(F.data.startswith("seal:"))
async def apply_seal(query: CallbackQuery, archive: ArchiveService, state) -> None:
    async with safe(query):
        _, raw_id, preset = query.data.split(":")
        capsule_id = int(raw_id)
        user_id = query.from_user.id
        now = utcnow()
        settings = archive.ensure_user(user_id, now)
        if preset == "pick":
            await state.set_state(Flow.seal_date)
            await state.update_data(capsule_id=capsule_id)
            await query.answer()
            await edit_or_send(query, "Send the date as YYYY-MM-DD. It opens at your delivery time.")
            return
        unlock_at = unlock_from_preset(now, preset)
        if unlock_at is None:
            capsule = archive.unseal(user_id, capsule_id)
        else:
            capsule = archive.seal(user_id, capsule_id, unlock_at, now)
        await query.answer("Saved")
        await edit_or_send(
            query,
            seal_confirmation(capsule, settings),
            reply_markup=item_keyboard(capsule, now),
        )


@router.message(Flow.seal_date, F.text, ~F.text.startswith("/"))
async def seal_custom_date(message: Message, archive: ArchiveService, state) -> None:
    async with safe(message):
        data = await state.get_data()
        capsule_id = int(data["capsule_id"])
        user_id = message.from_user.id
        now = utcnow()
        settings = archive.ensure_user(user_id, now)
        unlock_at = unlock_from_date(
            message.text or "", settings.timezone, settings.delivery_time, now
        )
        capsule = archive.seal(user_id, capsule_id, unlock_at, now)
        await state.clear()
        await answer(
            message,
            seal_confirmation(capsule, settings),
            reply_markup=item_keyboard(capsule, now),
        )


@router.callback_query(F.data.startswith("tag:"))
async def ask_tag(query: CallbackQuery, archive: ArchiveService, state) -> None:
    async with safe(query):
        capsule_id = int(query.data.split(":", 1)[1])
        capsule = archive.get(query.from_user.id, capsule_id)
        await state.set_state(Flow.tag_name)
        await state.update_data(capsule_id=capsule.id)
        await query.answer()
        await edit_or_send(query, f"Send a tag for <code>{capsule.public_id}</code>, like ideas.")


@router.message(Flow.tag_name, F.text, ~F.text.startswith("/"))
async def save_tag(message: Message, archive: ArchiveService, state) -> None:
    async with safe(message):
        data = await state.get_data()
        capsule = archive.add_tag(message.from_user.id, int(data["capsule_id"]), message.text or "")
        await state.clear()
        tags = " ".join(f"#{tag}" for tag in capsule.tags)
        await answer(message, f"<code>{capsule.public_id}</code> tagged {tags}.")
