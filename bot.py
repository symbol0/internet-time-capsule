import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from app.database.repository import Repository
from app.formatting import delivery_text
from app.handlers import build_router
from app.handlers.common import PREVIEW
from app.handlers.middleware import AllowlistMiddleware
from app.keyboards.inline import review_keyboard
from app.services.archive import ArchiveService
from app.services.scheduler import DeliveryService, Outgoing, Scheduler
from app.timeutils import utcnow
from config import load_config

logger = logging.getLogger("timecapsule")

COMMANDS = [
    BotCommand(command="start", description="Set up your archive"),
    BotCommand(command="save", description="Save a note or link"),
    BotCommand(command="today", description="Open what is due today"),
    BotCommand(command="vault", description="Browse saved notes"),
    BotCommand(command="search", description="Search notes and metadata"),
    BotCommand(command="seal", description="Lock a note until a date"),
    BotCommand(command="tags", description="Browse and remove tags"),
    BotCommand(command="stats", description="Archive age and counts"),
    BotCommand(command="export", description="Download your archive"),
    BotCommand(command="import", description="Restore an export"),
    BotCommand(command="settings", description="Reminders and discovery"),
    BotCommand(command="delete", description="Delete a note permanently"),
]


async def run() -> None:
    config = load_config()
    repository = Repository(config.database_path)
    repository.migrate()
    archive = ArchiveService(repository, config.storage_dir)
    bot = Bot(config.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    async def sender(outgoing: Outgoing) -> int:
        settings = repository.get_settings(outgoing.user_id)
        text = delivery_text(outgoing.capsule, outgoing.kind, utcnow(), settings.timezone)
        sent = await bot.send_message(
            outgoing.user_id,
            text,
            reply_markup=review_keyboard(outgoing.delivery_id),
            link_preview_options=PREVIEW,
        )
        return sent.message_id

    delivery = DeliveryService(repository, sender, config.backup_dir)
    scheduler = Scheduler(delivery, config.poll_seconds)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.update.middleware(AllowlistMiddleware(config.allowed_user_ids))
    dispatcher.include_router(build_router())
    logger.info("Archive database: %s", config.database_path)
    if config.allowed_user_ids:
        logger.info("Private archive for %s account(s).", len(config.allowed_user_ids))
    else:
        logger.info("Each Telegram account gets its own vault.")
    task = asyncio.create_task(scheduler.run())
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await bot.set_my_commands(COMMANDS)
        await dispatcher.start_polling(
            bot,
            allowed_updates=["message", "callback_query"],
            close_bot_session=False,
            archive=archive,
            delivery=delivery,
            config=config,
        )
    finally:
        scheduler.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await bot.session.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        raise


if __name__ == "__main__":
    main()
