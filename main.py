import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import settings
from app.handlers import router
from app.web.stripe_webhook import run_webhook_server

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def main():
    bot = Bot(token=settings.bot_token)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    dp.include_router(router)

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Бот запущен")

    # Polling and the Stripe webhook server are both long-running; neither may
    # block the other, so they share the loop. If either one dies the whole
    # process should come down rather than keep half the payment flow alive.
    await asyncio.gather(
        dp.start_polling(bot),
        run_webhook_server(bot),
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот выключен")
