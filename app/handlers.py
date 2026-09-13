import asyncio
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message

import app.keyboards as kb
from app.config import settings
from app.db.models import SubscriptionStatus
from app.db.repositories import PaymentRepository, SubscriptionRepository, UserRepository
from app.domain.pricing import get_current_price
from app.domain.rate_limit import QUESTION_COOLDOWN, minutes_until_allowed, pluralize_minutes_ru
from app.domain.subscription import InvalidTransitionError
from app.domain.time import days_remaining, format_date_ru, pluralize_days_ru, utcnow
from app.services.stripe_service import create_checkout_session

router = Router()

ADMIN_ID = settings.admin_id


class Register(StatesGroup):
    name = State()
    age = State()
    number = State()


class QuestionState(StatesGroup):
    """Состояние для обработки вопросов"""
    waiting_for_question = State()


class ScreenshotState(StatesGroup):
    """Состояние для обработки скриншотов оплаты"""
    waiting_for_screenshot = State()


@router.message(CommandStart())
async def cmd_start(message: Message):
    text = (
        "✨ Привет, я Ирина — персональный стилист и автор канала «Стильный декабрь»\n\n"
        "Если ты здесь, значит тебе интересна тема формирования личного стиля.\n\n"
        "Этот бот — твой личный проводник в мир стиля, уверенности и вдохновения.\n\n"
        "Здесь ты можешь:\n"
        "👗 оформить подписку на закрытый стильный клуб\n"
        "💡 узнать, что входит в доступ\n"
        "💳 получить инструкцию по оплате\n"
        "💬 задать вопрос напрямую\n\n"
        "В закрытом клубе я делюсь:\n"
        "— авторскими подборками образов\n"
        "— капсульными гардеробами на разные случаи\n"
        "— разбором трендов и сочетаний вещей\n"
        "— советами, как перестать тратить деньги на \"висящую\" одежду\n\n"
        "💌 Нажми кнопку ниже, чтобы узнать, что тебя ждёт внутри 👇"
    )
    await message.answer(text, reply_markup=kb.main_menu)


@router.message(Command('help'))
async def cmd_help(message: Message):
    await message.answer('Вы нажали на кнопку помощи')


@router.callback_query(F.data == 'what_is_inside')
async def show_inside_info_callback(callback: CallbackQuery):
    """Показать информацию о содержимом подписки"""
    text = (
        "📦 В подписке ты получаешь:\n\n"
        "💋 Подборки стильных образов — собранные капсулы, идеи нарядов, сочетаемые вещи.\n"
        "🌟 Разборы трендов и советы, как адаптировать их под себя.\n"
        "🧠 Практические мини-гайды — что купить на распродажах, куда инвестировать, как обновить базу.\n"
        "👜 Видео и разборы гардеробов (ежемесячно).\n\n"
        "Всё оформлено в лёгком, вдохновляющем формате — так, чтобы стиль стал естественной частью твоей жизни 💫\n\n"
        "🔄 Подписка действует 30 дней с момента оплаты — не привязана к числу месяца. "
        "Оплатил сегодня — доступ открыт сразу, продлить нужно будет через 30 дней. "
        "За 3 дня до окончания бот сам напомнит. "
        "Проверить, сколько дней осталось, можно в разделе «📅 Моя подписка»."
    )
    await callback.message.edit_text(text, reply_markup=kb.inside_menu)
    await callback.answer()


# Examples are uploaded straight to the server (see media/examples/README.md)
# so content updates never need a code change or rebuild.
EXAMPLES_DIR = Path("/app/media/examples")
_MAX_EXAMPLES = 5
_EXAMPLE_EXTENSIONS = {".jpg": "photo", ".mp4": "video"}

_NO_EXAMPLES_TEXT = (
    "👀 Вот примеры контента из закрытого клуба:\n\n"
    "📸 Здесь ты увидишь стильные подборки, разборы образов и капсульные гардеробы\n\n"
    "💡 В реальной версии здесь будут фото и видео примеры"
)


def _find_example_media(examples_dir: Path) -> list[tuple[str, Path]]:
    """(kind, path) for every example_<n>.<ext> file that exists, in numeric
    order (n = 1..5); within the same n, photo before video. Missing files
    (either index or extension) are skipped silently."""
    items = []
    for index in range(1, _MAX_EXAMPLES + 1):
        for ext, kind in _EXAMPLE_EXTENSIONS.items():
            file_path = examples_dir / f"example_{index}{ext}"
            if file_path.is_file():
                items.append((kind, file_path))
    return items


@router.callback_query(F.data == 'examples')
async def show_examples(callback: CallbackQuery):
    """Показать примеры контента"""
    items = _find_example_media(EXAMPLES_DIR)

    if not items:
        await callback.message.answer(_NO_EXAMPLES_TEXT, reply_markup=kb.back_menu)
        await callback.answer("Примеры отправлены!")
        return

    last_index = len(items) - 1
    for index, (kind, file_path) in enumerate(items):
        media = FSInputFile(file_path)
        markup = kb.back_menu if index == last_index else None
        if kind == "photo":
            await callback.message.answer_photo(media, reply_markup=markup)
        else:
            await callback.message.answer_video(media, reply_markup=markup)

    await callback.answer("Примеры отправлены!")


@router.callback_query(F.data == 'payment')
async def show_payment(callback: CallbackQuery):
    """Показать информацию об оплате"""
    price = get_current_price()
    text = (
        f"💳 Стоимость подписки: {price} zł за 30 дней\n\n"
        "⚡ Оплати картой или BLIK — доступ откроется автоматически, в течение минуты после оплаты.\n\n"
        "📸 Если способ оплаты не подошёл — можно отправить скрин перевода, и я подтвержу доступ вручную в течение дня."
    )
    await callback.message.edit_text(text, reply_markup=kb.get_payment_menu())
    await callback.answer()


@router.callback_query(F.data == 'my_subscription')
async def show_my_subscription(callback: CallbackQuery):
    """Показать статус подписки пользователя"""
    db_user = await UserRepository.get_by_telegram_id(callback.from_user.id)
    subscription = (
        await SubscriptionRepository.get_active_or_expiring_for_user(db_user.id)
        if db_user is not None
        else None
    )

    if subscription is None:
        text = (
            "У тебя пока нет подписки 🌸\n\n"
            "Загляни в раздел «💳 Оформить подписку» в меню, чтобы получить "
            "доступ в закрытый клуб."
        )
    else:
        days = days_remaining(subscription.expires_at)
        date_str = format_date_ru(subscription.expires_at)
        day_word = pluralize_days_ru(days)
        if subscription.status == SubscriptionStatus.EXPIRING:
            text = (
                f"⏳ Твоя подписка скоро закончится — осталось {days} {day_word} "
                f"(до {date_str}).\n\n"
                "Продли доступ через «💳 Оформить подписку», чтобы не потерять "
                "место в закрытом клубе 💫"
            )
        else:
            text = f"✅ Твоя подписка активна ещё {days} {day_word} (до {date_str})."

    await callback.message.edit_text(text, reply_markup=kb.back_menu)
    await callback.answer()


@router.callback_query(F.data == 'stripe_checkout')
async def stripe_checkout(callback: CallbackQuery):
    """Создать Stripe Checkout Session и отправить пользователю ссылку на оплату"""
    checkout_url = await asyncio.to_thread(create_checkout_session, callback.from_user.id)
    text = (
        "💳 Ссылка на оплату готова — доступны карта и BLIK.\n\n"
        "После оплаты Ирина активирует твой доступ в течение дня 💫"
    )
    await callback.message.answer(text, reply_markup=kb.get_stripe_checkout_keyboard(checkout_url))
    await callback.answer()


@router.callback_query(F.data == 'pay_now')
async def pay_now(callback: CallbackQuery):
    """Кнопка 'Оплатить сейчас' - открывает ссылку на оплату"""
    text = (
        "💸 Для оплаты перейди по ссылке:\n\n"
        "🔗 [Ссылка на оплату будет здесь]\n\n"
        "После оплаты обязательно отправь скриншот подтверждения!"
    )
    await callback.message.answer(text, reply_markup=kb.back_menu)
    await callback.answer()


@router.callback_query(F.data == 'send_screenshot')
async def request_screenshot(callback: CallbackQuery, state: FSMContext):
    """Запросить скриншот оплаты"""
    await state.set_state(ScreenshotState.waiting_for_screenshot)
    text = (
        "📸 Отправь скриншот или фото подтверждения оплаты.\n\n"
        "Я перешлю его Ирине, и она активирует твой доступ в течение дня 💫"
    )
    await callback.message.answer(text, reply_markup=kb.cancel_menu)
    await callback.answer()


@router.message(ScreenshotState.waiting_for_screenshot, F.photo)
async def receive_screenshot(message: Message, state: FSMContext):
    """Получить скриншот от пользователя"""
    user = message.from_user

    db_user = await UserRepository.get_or_create(user.id)
    subscription = await SubscriptionRepository.create(
        user_id=db_user.id, expires_at=None
    )
    await PaymentRepository.create(
        subscription_id=subscription.id,
        provider="manual",
        # message_id is only unique per chat, so it alone would collide
        # across users under the payments (provider, provider_ref) constraint.
        provider_ref=f"manual-{user.id}-{message.message_id}",
        amount=Decimal(get_current_price()),
        currency="PLN",
    )

    caption = (
        f"💳 Новая оплата!\n\n"
        f"👤 От: {user.full_name}\n"
        f"🆔 ID: {user.id}\n"
        f"📱 Username: @{user.username if user.username else 'не указан'}"
    )

    try:
        await message.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=message.photo[-1].file_id,
            caption=caption,
            reply_markup=kb.get_confirm_payment_keyboard(subscription.id)
        )
    except Exception as e:
        print(f"Ошибка отправки админу: {e}")


    await message.answer(
        text="✅ Спасибо! Твой скриншот отправлен Ирине.\n\n"
             "Доступ будет активирован в течение дня. Я пришлю тебе уведомление! 💫",
        reply_markup=kb.main_menu
    )

    await state.clear()


@router.callback_query(F.data.startswith('confirm_payment:'))
async def confirm_payment(callback: CallbackQuery):
    """Админ подтверждает оплату и активирует подписку"""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer()
        return

    subscription_id = int(callback.data.split(':', 1)[1])
    expires_at = utcnow() + timedelta(days=30)

    try:
        subscription = await SubscriptionRepository.update_status(
            subscription_id, SubscriptionStatus.ACTIVE, expires_at=expires_at
        )
    except InvalidTransitionError:
        await callback.answer("Эта оплата уже обработана.", show_alert=True)
        return

    subscriber = await UserRepository.get_by_id(subscription.user_id)
    if subscriber is not None:
        try:
            await callback.bot.send_message(
                chat_id=subscriber.telegram_id,
                text="🎉 Твоя оплата подтверждена! Доступ в закрытый клуб активен 30 дней.\n\n"
                     "Ирина добавит тебя в канал в течение дня 💫"
            )
        except Exception as e:
            print(f"Ошибка отправки подтверждения пользователю: {e}")

    await callback.message.edit_caption(
        caption=(callback.message.caption or "") + "\n\n✅ Оплата подтверждена",
        reply_markup=None
    )
    await callback.answer("Подписка активирована")


@router.message(ScreenshotState.waiting_for_screenshot)
async def wrong_screenshot_format(message: Message):
    """Если отправлено не фото"""
    await message.answer(
        text="❌ Пожалуйста, отправь именно фото или скриншот оплаты.",
        reply_markup=kb.cancel_menu
    )


@router.callback_query(F.data == 'ask_question')
async def ask_question(callback: CallbackQuery, state: FSMContext):
    """Начать задавать вопрос"""
    await state.set_state(QuestionState.waiting_for_question)
    text = (
        "💬 Ты можешь написать мне напрямую — я помогу разобраться с оплатой, "
        "доступом или просто расскажу, подходит ли тебе участие 🌸\n\n"
        "✉️ Напиши сообщение ниже — я отвечу лично."
    )
    await callback.message.edit_text(text, reply_markup=kb.cancel_menu)
    await callback.answer()


@router.message(QuestionState.waiting_for_question)
async def receive_question(message: Message, state: FSMContext):
    """Получить вопрос от пользователя"""
    user = message.from_user

    db_user = await UserRepository.get_or_create(user.id)
    minutes_left = minutes_until_allowed(db_user.last_question_at, QUESTION_COOLDOWN)
    if minutes_left > 0:
        minute_word = pluralize_minutes_ru(minutes_left)
        await message.answer(
            text=(
                "Ты уже отправил(а) вопрос недавно — Ирина ответит в ближайшее время 💫\n\n"
                f"Следующий вопрос можно будет отправить через {minutes_left} {minute_word}."
            ),
            reply_markup=kb.main_menu
        )
        await state.clear()
        return

    admin_message = (
        f"💬 Новый вопрос!\n\n"
        f"👤 От: {user.full_name}\n"
        f"🆔 ID: {user.id}\n"
        f"📱 Username: @{user.username if user.username else 'не указан'}\n\n"
        f"❓ Вопрос:\n{message.text}"
    )

    try:
        await message.bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_message
        )
    except Exception as e:
        print(f"Ошибка отправки админу: {e}")

    await UserRepository.set_last_question_at(db_user.id, utcnow())

    await message.answer(
        text="✅ Спасибо за вопрос! Ирина получила твоё сообщение и ответит в ближайшее время 💌",
        reply_markup=kb.main_menu
    )

    await state.clear()


@router.callback_query(F.data == 'main_menu')
async def back_to_main_menu(callback: CallbackQuery, state: FSMContext):
    """Возврат в главное меню"""
    
    await state.clear()
    
    text = (
        "✨ Привет, я Ирина — персональный стилист и автор канала «Стильный декабрь»\n\n"
        "Если ты здесь, значит тебе интересна тема формирования личного стиля.\n\n"
        "Этот бот — твой личный проводник в мир стиля, уверенности и вдохновения.\n\n"
        "Здесь ты можешь:\n"
        "👗 оформить подписку на закрытый стильный клуб\n"
        "💡 узнать, что входит в доступ\n"
        "💳 получить инструкцию по оплате\n"
        "💬 задать вопрос напрямую\n\n"
        "В закрытом клубе я делюсь:\n"
        "— авторскими подборками образов\n"
        "— капсульными гардеробами на разные случаи\n"
        "— разбором трендов и сочетаний вещей\n"
        "— советами, как перестать тратить деньги на \"висящую\" одежду\n\n"
        "💌 Нажми кнопку ниже, чтобы узнать, что тебя ждёт внутри 👇"
    )
    await callback.message.edit_text(text, reply_markup=kb.main_menu)
    await callback.answer()