from datetime import datetime, timezone
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database.base import utcnow
from src.database.models.booking import STATUS_CANCELLED, STATUS_HOLD, STATUS_PAID, Booking
from src.database.models.payment import (
    KIND_OWNER_SUBSCRIPTION,
    KIND_SLOT_PREPAY,
    PAYMENT_PAID,
    PAYMENT_PENDING,
    PAYMENT_REFUND_PENDING,
    PAYMENT_REFUNDED,
    Payment,
)
from src.database.models.studio import TARIFF_PLUS, TARIFF_STARTER, Resource, Studio
from src.services import prodamus, yookassa
from src.services.tariffs import resource_limit_for

logger = logging.getLogger(__name__)


def active_provider() -> str:
    choice = (settings.PAYMENT_PROVIDER or "auto").strip().lower()
    if choice == "yookassa":
        return "yookassa" if yookassa.is_configured() else ""
    if choice == "prodamus":
        return "prodamus" if prodamus.is_configured() else ""
    if yookassa.is_configured():
        return "yookassa"
    if prodamus.is_configured():
        return "prodamus"
    return ""


def is_pay_configured() -> bool:
    return bool(active_provider())


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def amount_matches(payment: Payment, webhook_amount: int | None, *, required: bool) -> bool:
    if webhook_amount is None:
        return not required
    return int(webhook_amount) == int(payment.amount_rub)


async def create_slot_invoice(
    session: AsyncSession,
    booking: Booking,
    amount_rub: int,
) -> Payment:
    existing = (
        await session.execute(
            select(Payment)
            .where(
                Payment.booking_id == booking.id,
                Payment.kind == KIND_SLOT_PREPAY,
                Payment.status == PAYMENT_PENDING,
            )
            .order_by(Payment.id.desc())
        )
    ).scalars().first()
    if existing is not None:
        if existing.amount_rub != amount_rub:
            existing.amount_rub = amount_rub
            await session.commit()
            await session.refresh(existing)
        return existing
    payment = Payment(
        kind=KIND_SLOT_PREPAY,
        booking_id=booking.id,
        studio_id=booking.studio_id,
        amount_rub=amount_rub,
        status=PAYMENT_PENDING,
    )
    session.add(payment)
    await session.flush()
    payment.prodamus_invoice_id = f"slot-{booking.id}-{payment.id}"
    await session.commit()
    await session.refresh(payment)
    return payment


async def create_subscription_invoice(
    session: AsyncSession,
    studio: Studio,
    *,
    tariff: str,
    amount_rub: int,
) -> Payment:
    payment = Payment(
        kind=KIND_OWNER_SUBSCRIPTION,
        studio_id=studio.id,
        amount_rub=amount_rub,
        status=PAYMENT_PENDING,
    )
    session.add(payment)
    await session.flush()
    payment.prodamus_invoice_id = f"sub-{studio.id}-{tariff}-{payment.id}"
    await session.commit()
    await session.refresh(payment)
    return payment


def payment_url(payment: Payment, *, phone: str | None = None, description: str) -> str:
    order_id = payment.prodamus_invoice_id or f"pay-{payment.id}"
    return prodamus.build_payment_url(
        order_id=order_id,
        amount_rub=payment.amount_rub,
        description=description,
        customer_phone=phone,
        extra={"kind": payment.kind, "payment_id": str(payment.id)},
    )


async def create_checkout_url(
    session: AsyncSession,
    payment: Payment,
    *,
    phone: str | None = None,
    description: str,
) -> str:
    if active_provider() == "yookassa":
        order_id = payment.prodamus_invoice_id or f"pay-{payment.id}"
        url, yoo_id = await yookassa.create_payment(
            order_id=order_id,
            amount_rub=payment.amount_rub,
            description=description,
            extra={"kind": payment.kind, "payment_id": str(payment.id)},
        )
        payment.provider = "yookassa"
        payment.provider_payment_id = yoo_id
        await session.commit()
        await session.refresh(payment)
        return url
    payment.provider = payment.provider or "prodamus"
    await session.commit()
    return payment_url(payment, phone=phone, description=description)


async def find_payment_for_yookassa(session: AsyncSession, payload: dict) -> Payment | None:
    yoo_id = yookassa.extract_provider_payment_id(payload)
    if yoo_id:
        stmt = select(Payment).where(Payment.provider_payment_id == yoo_id)
        payment = (await session.execute(stmt)).scalar_one_or_none()
        if payment is not None:
            return payment
    order_id = yookassa.extract_order_id(payload)
    if order_id:
        stmt = select(Payment).where(Payment.prodamus_invoice_id == order_id)
        payment = (await session.execute(stmt)).scalar_one_or_none()
        if payment is not None:
            return payment
    return None


async def find_payment_for_webhook(session: AsyncSession, payload: dict) -> Payment | None:
    """Prodamus часто кладёт свой UUID в order_id; наш номер — slot-/sub- или sku."""
    from src.services.prodamus import collect_order_ids, payment_id_from_payload

    for order_id in collect_order_ids(payload):
        stmt = select(Payment).where(Payment.prodamus_invoice_id == order_id)
        payment = (await session.execute(stmt)).scalar_one_or_none()
        if payment is not None:
            return payment
    payment_id = payment_id_from_payload(payload)
    if payment_id:
        payment = await session.get(Payment, payment_id)
        if payment is not None:
            return payment
    return None


async def _fulfill_slot_booking(session: AsyncSession, payment: Payment, booking: Booking) -> str:
    """paid | restored | refunded | refund_pending."""
    if booking.status == STATUS_HOLD:
        booking.status = STATUS_PAID
        booking.hold_expires_at = None
        return "paid"
    if booking.status == STATUS_PAID:
        return "paid"
    if booking.status != STATUS_CANCELLED:
        return "paid"

    from src.services.slots import has_overlap

    resource = await session.get(Resource, booking.resource_id)
    can_restore = False
    if resource is not None:
        can_restore = not await has_overlap(
            session,
            resource=resource,
            starts_at=booking.starts_at,
            ends_at=booking.ends_at,
            exclude_id=booking.id,
        )
    if can_restore:
        booking.status = STATUS_PAID
        booking.hold_expires_at = None
        booking.cancel_reason = None
        logger.info("late payment restored booking=%s payment=%s", booking.id, payment.id)
        return "restored"

    ok, detail = await _request_provider_refund(payment, payment.amount_rub)
    await apply_refund(session, payment, payment.amount_rub, commit=False, remote_ok=ok)
    logger.warning(
        "late payment auto-refund payment=%s booking=%s ok=%s %s",
        payment.id,
        booking.id,
        ok,
        detail,
    )
    return "refunded" if ok else "refund_pending"


async def _request_provider_refund(payment: Payment, amount_rub: int) -> tuple[bool, str]:
    if payment.provider == "yookassa" or (payment.provider_payment_id and yookassa.is_configured()):
        return await yookassa.request_refund(payment.provider_payment_id or "", amount_rub)
    order_id = payment.provider_payment_id or payment.prodamus_invoice_id or ""
    return await prodamus.request_refund(order_id, amount_rub)


async def apply_paid_order(session: AsyncSession, order_id: str) -> Payment | None:
    """Идемпотентно: повторный webhook не меняет уже paid/refunded."""
    stmt = select(Payment).where(Payment.prodamus_invoice_id == order_id)
    payment = (await session.execute(stmt)).scalar_one_or_none()
    if payment is None:
        return None
    if payment.status in (PAYMENT_PAID, PAYMENT_REFUNDED, PAYMENT_REFUND_PENDING):
        return payment

    payment.status = PAYMENT_PAID
    payment.paid_at = utcnow()

    if payment.kind == KIND_SLOT_PREPAY and payment.booking_id:
        booking = await session.get(Booking, payment.booking_id)
        if booking is not None:
            await _fulfill_slot_booking(session, payment, booking)

    if payment.kind == KIND_OWNER_SUBSCRIPTION and payment.studio_id:
        studio = await session.get(Studio, payment.studio_id)
        if studio:
            tariff = TARIFF_PLUS if payment.amount_rub >= settings.TARIFF_PLUS_RUB else TARIFF_STARTER
            if payment.prodamus_invoice_id and "-plus-" in payment.prodamus_invoice_id:
                tariff = TARIFF_PLUS
            elif payment.prodamus_invoice_id and "-starter-" in payment.prodamus_invoice_id:
                tariff = TARIFF_STARTER
            studio.tariff = tariff
            studio.resource_limit = resource_limit_for(tariff)
            from datetime import timedelta

            base = _as_utc(studio.subscription_until)
            now = utcnow()
            start = base if base and base > now else now
            studio.subscription_until = start + timedelta(days=30)

    await session.commit()
    await session.refresh(payment)
    return payment


async def apply_refund(
    session: AsyncSession,
    payment: Payment,
    amount_rub: int,
    *,
    commit: bool = True,
    remote_ok: bool = True,
) -> Payment:
    if payment.status == PAYMENT_REFUNDED:
        return payment
    if remote_ok:
        payment.status = PAYMENT_REFUNDED
        payment.refunded_at = utcnow()
    else:
        payment.status = PAYMENT_REFUND_PENDING
    payment.refund_amount_rub = amount_rub
    if commit:
        await session.commit()
        await session.refresh(payment)
    return payment
