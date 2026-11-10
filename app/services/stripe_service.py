import stripe

from app.config import settings

stripe.api_key = settings.stripe_secret_key


def create_checkout_session(telegram_id: int, price: int) -> str:
    """Create a one-time Stripe Checkout Session for `price` (whole złoty, as
    read from get_current_price() by the caller — the same reading the user
    is shown) and return its hosted URL. Does not touch the database — the
    webhook reads client_reference_id to identify the payer."""
    price_grosze = price * 100

    session = stripe.checkout.Session.create(
        payment_method_types=["card", "blik"],
        mode="payment",
        line_items=[
            {
                "price_data": {
                    "currency": "pln",
                    "unit_amount": price_grosze,
                    "product_data": {
                        "name": "DressCode by Irina — monthly access",
                    },
                },
                "quantity": 1,
            }
        ],
        client_reference_id=str(telegram_id),
        success_url=f"https://t.me/{settings.bot_username}?start=payment_success",
        cancel_url=f"https://t.me/{settings.bot_username}?start=payment_cancelled",
    )
    return session.url
