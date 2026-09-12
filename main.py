import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.handlers import router
from app.jobs.auto_kick import register as register_auto_kick
from app.jobs.expiration_warnings import register as register_expiration_warnings
from app.web.stripe_webhook import run_webhook_server

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def build_scheduler() -> AsyncIOScheduler:
    """APScheduler backed by the app's own Postgres database.

    A Postgres jobstore (instead of the in-memory default) means scheduled
    jobs and their next-run times survive a container restart rather than
    resetting on every deploy.
    """
    jobstores = {"default": SQLAlchemyJobStore(url=settings.sync_database_url)}
    return AsyncIOScheduler(jobstores=jobstores, timezone="UTC")


async def main():
    bot = Bot(token=settings.bot_token)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    dp.include_router(router)

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Бот запущен")

    scheduler = build_scheduler()
    register_expiration_warnings(scheduler)
    register_auto_kick(scheduler)
    scheduler.start()

    try:
        # Polling, the Stripe webhook server and the scheduler are all
        # long-running and share the loop; if polling or the webhook server
        # dies the whole process should come down rather than keep half the
        # payment flow alive, so only those two are awaited here.
        await asyncio.gather(
            dp.start_polling(bot),
            run_webhook_server(bot),
        )
    finally:
        scheduler.shutdown(wait=False)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот выключен")
