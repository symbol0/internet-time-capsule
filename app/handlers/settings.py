from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, Message

from app.formatting import format_settings, format_stats
from app.handlers.common import answer, edit_or_send, safe
from app.keyboards.inline import confirm_keyboard, settings_keyboard, timezone_keyboard
from app.services.archive import ArchiveService
from app.services.export import loads_payload, restore_payload, write_export
from app.states import Flow
from app.timeutils import normalize_delivery_time, utcnow, validate_timezone

router = Router(name="settings")

MAX_IMPORT_BYTES = 1_500_000


@router.message(Command("settings"))
async def cmd_settings(message: Message, archive: ArchiveService, state) -> None:
    async with safe(message):
        await state.clear()
        settings = archive.ensure_user(message.from_user.id)
        await answer(message, format_settings(settings), reply_markup=settings_keyboard(settings))


@router.message(Command("stats"))
async def cmd_stats(message: Message, archive: ArchiveService, state) -> None:
    async with safe(message):
        await state.clear()
        now = utcnow()
        settings = archive.ensure_user(message.from_user.id, now)
        await answer(message, format_stats(archive.stats(message.from_user.id), settings, now))


@router.callback_query(F.data.startswith("set:"))
async def change_setting(query: CallbackQuery, archive: ArchiveService, state) -> None:
    async with safe(query):
        parts = (query.data or "").split(":", 2)
        if len(parts) < 3:
            from app.errors import UserInputError

            raise UserInputError("That button expired.")
        kind, value = parts[1], parts[2]
        user_id = query.from_user.id
        now = utcnow()
        archive.ensure_user(user_id, now)
        if kind == "tz" and value == "menu":
            await query.answer()
            await edit_or_send(query, "Choose a timezone.", reply_markup=timezone_keyboard())
            return
        if kind == "tz" and value == "ask":
            await state.set_state(Flow.timezone)
            await query.answer()
            if query.message is not None and hasattr(query.message, "answer"):
                await query.message.answer("Send a timezone name, like Asia/Dhaka.")
            return
        if kind == "tz":
            archive.update_settings(user_id, timezone=validate_timezone(value))
        elif kind == "disc":
            archive.update_settings(user_id, discovery_enabled=value == "1")
        elif kind == "limit":
            limit = int(value)
            if limit not in {1, 3, 5}:
                from app.errors import UserInputError

                raise UserInputError("Choose 1, 3, or 5.")
            archive.update_settings(user_id, daily_limit=limit)
        elif kind == "time":
            archive.update_settings(user_id, delivery_time=normalize_delivery_time(value))
        else:
            from app.errors import UserInputError

            raise UserInputError("That button expired.")
        settings = archive.ensure_user(user_id, now)
        await query.answer("Saved")
        await edit_or_send(query, format_settings(settings), reply_markup=settings_keyboard(settings))


@router.message(Flow.timezone, F.text, ~F.text.startswith("/"))
async def timezone_from_text(message: Message, archive: ArchiveService, state) -> None:
    async with safe(message):
        archive.update_settings(message.from_user.id, timezone=validate_timezone(message.text or ""))
        await state.clear()
        settings = archive.ensure_user(message.from_user.id)
        await answer(message, format_settings(settings), reply_markup=settings_keyboard(settings))


@router.message(Command("export"))
async def cmd_export(message: Message, archive: ArchiveService, state, config) -> None:
    async with safe(message):
        await state.clear()
        user_id = message.from_user.id
        archive.ensure_user(user_id)
        path = write_export(archive.repository, user_id, utcnow(), config.exports_dir)
        await message.answer_document(
            FSInputFile(path),
            caption=(
                "Your archive backup. It includes notes, tags, and rediscovery history. "
                "File bytes are not included, because those are not stored yet."
            ),
        )


@router.message(Command("import"))
async def cmd_import(message: Message, state) -> None:
    async with safe(message):
        await state.set_state(Flow.import_file)
        await answer(message, "Send the JSON file from /export. Restoring replaces this archive.")


@router.message(Flow.import_file, F.document)
async def receive_import(message: Message, state, config) -> None:
    async with safe(message):
        document = message.document
        if document is None:
            return
        if document.file_size and document.file_size > MAX_IMPORT_BYTES:
            raise _too_large()
        filename = (document.file_name or "").lower()
        if not filename.endswith(".json"):
            from app.errors import UserInputError

            raise UserInputError("Send the JSON file from /export.")
        path = Path(config.exports_dir) / f"incoming-{message.from_user.id}.json"
        await message.bot.download(document, destination=path)
        if path.stat().st_size > MAX_IMPORT_BYTES:
            path.unlink(missing_ok=True)
            raise _too_large()
        payload = loads_payload(path.read_text(encoding="utf-8"))
        if payload["user_id"] != message.from_user.id:
            path.unlink(missing_ok=True)
            from app.errors import UserInputError

            raise UserInputError("This export belongs to a different archive.")
        await state.set_state(Flow.import_confirm)
        await state.update_data(import_path=str(path))
        count = len(payload["capsules"])
        await answer(
            message,
            f"Replace your archive with {count} notes? This cannot be undone.",
            reply_markup=confirm_keyboard("imp:yes", "imp:no", yes_label="Replace"),
        )


@router.callback_query(Flow.import_confirm, F.data == "imp:yes")
async def confirm_import(query: CallbackQuery, archive: ArchiveService, state) -> None:
    async with safe(query):
        data = await state.get_data()
        path = Path(str(data.get("import_path") or ""))
        if not path.is_file():
            from app.errors import UserInputError

            raise UserInputError("That import expired. Send /import again.")
        payload = loads_payload(path.read_text(encoding="utf-8"))
        count = restore_payload(archive.repository, query.from_user.id, payload, utcnow())
        path.unlink(missing_ok=True)
        await state.clear()
        await query.answer("Restored")
        await edit_or_send(query, f"Restored {count} notes.")


@router.callback_query(Flow.import_confirm, F.data == "imp:no")
async def cancel_import(query: CallbackQuery, state) -> None:
    async with safe(query):
        data = await state.get_data()
        path = Path(str(data.get("import_path") or ""))
        if path.is_file():
            path.unlink(missing_ok=True)
        await state.clear()
        await query.answer("Cancelled")
        await edit_or_send(query, "Import cancelled. The vault was not changed.")


def _too_large():
    from app.errors import UserInputError

    return UserInputError("That export is too large.")
