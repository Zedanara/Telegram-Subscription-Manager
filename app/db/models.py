import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SubscriptionStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    EXPIRING = "expiring"
    EXPIRED = "expired"
    KICKED = "kicked"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # When this user's last question was forwarded to ADMIN_ID — drives the
    # "Задать вопрос" rate limit (app/domain/rate_limit.py). Null means never
    # asked, which always passes the limit.
    last_question_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="user")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="subscription_status"),
        default=SubscriptionStatus.PENDING,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # How many days-left the most recently *sent* expiration reminder warned
    # about (app/jobs/expiration_warnings.py) — NULL means no reminder sent
    # this cycle. Needed because that job now sends up to three reminders
    # (3/2/1 days left) per cycle, so the ACTIVE->EXPIRING status transition
    # alone can no longer tell "already warned today" apart from "already
    # warned on a different day of the same window". Reset to NULL on every
    # renewal (app/services/payment_service.py) so the next cycle gets all
    # three reminders again.
    last_warning_days_left: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="subscriptions")
    payments: Mapped[list["Payment"]] = relationship(back_populates="subscription")


class Payment(Base):
    __tablename__ = "payments"

    # One payment per provider reference, ever. This is what makes webhook
    # delivery idempotent under concurrency: two simultaneous deliveries of the
    # same Stripe checkout session cannot both insert.
    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_ref", name="uq_payments_provider_provider_ref"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    provider_ref: Mapped[str] = mapped_column(String(255))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    subscription: Mapped["Subscription"] = relationship(back_populates="payments")
