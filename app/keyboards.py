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
            text=f'💸 Оплатить {price} zł',
            url=f'https://buy.stripe.com/твоя-ссылка-{price}zl'
        )],
        [InlineKeyboardButton(text='📩 Отправить скрин оплаты', callback_data='send_screenshot')],
        [InlineKeyboardButton(text='↩️ Назад в меню', callback_data='main_menu')]
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