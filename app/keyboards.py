from aiogram.types import (ReplyKeyboardMarkup, KeyboardButton,
                           InlineKeyboardMarkup, InlineKeyboardButton)

from app.domain.pricing import get_current_price

# ========== ОБЫЧНЫЕ КЛАВИАТУРЫ (если нужны) ==========

main = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='💳 Подписка и доступ')],
                                     [KeyboardButton(text='📦 Что внутри')],
                                     [KeyboardButton(text='💬 Отзывы участниц')],
                                     [KeyboardButton(text='📖 Инструкция по оплате')],
                                     [KeyboardButton(text='Контакты'),
                                     KeyboardButton(text='📞 Связаться с Ириной')]],
                            resize_keyboard=True,
                            input_field_placeholder='Выберите пункт меню...')


# ========== INLINE КЛАВИАТУРЫ ==========

# Главное меню (стартовое)
main_menu = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text='📦 Что внутри', callback_data='what_is_inside')],
    [InlineKeyboardButton(text='💳 Оформить подписку', callback_data='payment')],
    [InlineKeyboardButton(text='💬 Задать вопрос Ирине', callback_data='ask_question')]
])

# Меню раздела "Что внутри"
inside_menu = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text='💳 Оплатить подписку', callback_data='payment')],
    [InlineKeyboardButton(text='👀 Посмотреть примеры контента', callback_data='examples')],
    [InlineKeyboardButton(text='↩️ Назад в меню', callback_data='main_menu')]
])


# Меню раздела "Оплата"
def get_payment_menu() -> InlineKeyboardMarkup:
    price = get_current_price()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f'💳 Оплатить картой / BLIK ({price} zł)',
            callback_data='stripe_checkout'
        )],
        [InlineKeyboardButton(text='📩 Отправить скрин оплаты', callback_data='send_screenshot')],
        [InlineKeyboardButton(text='↩️ Назад в меню', callback_data='main_menu')]
    ])

# Кнопка со ссылкой на готовую Stripe Checkout Session
def get_stripe_checkout_keyboard(checkout_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='💳 Перейти к оплате', url=checkout_url)],
        [InlineKeyboardButton(text='↩️ Назад в меню', callback_data='main_menu')]
    ])

# Кнопка подтверждения оплаты (для админа)
def get_confirm_payment_keyboard(subscription_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text='✅ Confirm payment',
            callback_data=f'confirm_payment:{subscription_id}'
        )]
    ])

# Простая кнопка "Назад"
back_menu = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text='↩️ Назад в меню', callback_data='main_menu')]
])

# Кнопка отмены (для состояний)
cancel_menu = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text='❌ Отменить', callback_data='main_menu')]
])

# Старая клавиатура для совместимости
catalog = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text='📦 Что внутри', callback_data='what_is_inside')]
])