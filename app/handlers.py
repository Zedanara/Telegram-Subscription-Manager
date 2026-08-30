from datetime import datetime, timedelta
from decimal import Decimal

from aiogram import F, Router
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart, Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

import app.keyboards as kb
from app.config import settings
from app.db.models import SubscriptionStatus
from app.db.repositories import PaymentRepository, SubscriptionRepository, UserRepository
from app.domain.pricing import get_current_price
from app.domain.subscription import InvalidTransitionError

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
        "💳 Оплати до 5 числа — и получи мгновенный доступ к текущему месяцу.\n"
        "После 5-го — подписка активируется с 1 числа следующего месяца."
    )
    await callback.message.edit_text(text, reply_markup=kb.inside_menu)
    await callback.answer()


@router.callback_query(F.data == 'examples')
async def show_examples(callback: CallbackQuery):
    """Показать примеры контента"""
    text = (
        "👀 Вот примеры контента из закрытого клуба:\n\n"
        "📸 Здесь ты увидишь стильные подборки, разборы образов и капсульные гардеробы\n\n"
        "💡 В реальной версии здесь будут фото и видео примеры"
    )
    await callback.message.answer(text, reply_markup=kb.back_menu)
    await callback.answer("Примеры отправлены!")


@router.callback_query(F.data == 'payment')
async def show_payment(callback: CallbackQuery):
    """Показать информацию об оплате"""
    price = get_current_price()
    text = (
        f"💳 Стоимость участия в закрытом клубе: {price} zł\n\n"
        "После оплаты ты автоматически получаешь доступ в закрытый Telegram-канал.\n\n"
        "💡 Оплата принимается через Stripe или BLIK.\n\n"
        "✨ После оплаты отправь скрин в этот чат, и я активирую доступ вручную в течение дня."
    )
    await callback.message.edit_text(text, reply_markup=kb.get_payment_menu())
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
        provider_ref=f"manual-{message.message_id}",
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
    expires_at = datetime.now() + timedelta(days=30)

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