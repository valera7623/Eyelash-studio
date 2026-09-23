from datetime import date, datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo

from src.database.models.booking import STATUS_CANCELLED, STATUS_HOLD, STATUS_PAID, STATUS_PENDING, Booking
from src.database.models.payment import PAYMENT_PAID
from src.database.models.studio import TARIFF_FREE, TARIFF_STARTER, Resource, Studio
from src.database.models.user import User
from src.services.payments import apply_paid_order, create_slot_invoice
from src.services.prodamus import sign_payload, verify_signature
from src.services.slots import (
    add_window,
    available_slots,
    create_hold,
    create_request,
    confirm_request,
    expire_holds,
    generate_slots_for_day,
)
from src.config import settings
from src.services.tariffs import can_create_booking
from src.utils.slug import slugify


def _future_day(days: int = 21):
    return datetime.now(ZoneInfo("Europe/Moscow")).date() + timedelta(days=days)


def _slot_start() -> datetime:
    return datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


async def _seed_resource(session, *, slug="demo-studio", telegram_id=1001) -> Resource:
    owner = User(
        telegram_id=telegram_id,
        username="owner",
        first_name="Анна",
        language_code="ru",
    )
    session.add(owner)
    await session.flush()

    studio = Studio(
        slug=slug,
        name="Демо студия",
        owner_id=owner.id,
        owner_telegram_id=owner.telegram_id,
        timezone="Europe/Moscow",
    )
    session.add(studio)
    await session.flush()

    resource = Resource(
        studio_id=studio.id,
        name="Циклорама",
        duration_min=60,
        slot_step_min=60,
        min_duration_min=60,
        buffer_min=0,
        timezone="Europe/Moscow",
        work_start=time(10, 0),
        work_end=time(12, 0),
        weekdays="1,2,3,4,5,6,7",
        price_rub=1000,
    )
    session.add(resource)
    await session.flush()
    return resource


async def test_two_active_holds_same_slot_rejected(session):
    resource = await _seed_resource(session)
    start = _slot_start()
    end = start + timedelta(hours=1)

    first = await create_hold(
        session,
        resource=resource,
        starts_at=start,
        ends_at=end,
        client_telegram_id=2001,
        client_name="Клиент А",
        client_phone="+79990000001",
        client_user_id=None,
    )
    assert first is not None

    second = await create_hold(
        session,
        resource=resource,
        starts_at=start,
        ends_at=end,
        client_telegram_id=2002,
        client_name="Клиент Б",
        client_phone="+79990000002",
        client_user_id=None,
    )
    assert second is None


async def test_cancelled_slot_can_be_rebooked(session):
    resource = await _seed_resource(session)
    start = _slot_start()
    end = start + timedelta(hours=1)

    first = Booking(
        resource_id=resource.id,
        studio_id=resource.studio_id,
        client_telegram_id=2001,
        client_name="Клиент А",
        starts_at=start,
        ends_at=end,
        status=STATUS_CANCELLED,
    )
    session.add(first)
    await session.commit()

    second = await create_hold(
        session,
        resource=resource,
        starts_at=start,
        ends_at=end,
        client_telegram_id=2002,
        client_name="Клиент Б",
        client_phone=None,
        client_user_id=None,
    )
    assert second is not None


async def test_hold_ttl_expires(session):
    resource = await _seed_resource(session)
    start = _slot_start()
    booking = Booking(
        resource_id=resource.id,
        studio_id=resource.studio_id,
        client_telegram_id=2001,
        client_name="Клиент А",
        starts_at=start,
        ends_at=start + timedelta(hours=1),
        status=STATUS_HOLD,
        hold_expires_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    session.add(booking)
    await session.commit()
    n = await expire_holds(session)
    assert len(n) == 1
    await session.refresh(booking)
    assert booking.status == STATUS_CANCELLED
    again = await create_hold(
        session,
        resource=resource,
        starts_at=start,
        ends_at=start + timedelta(hours=1),
        client_telegram_id=2003,
        client_name="Другой",
        client_phone=None,
        client_user_id=None,
    )
    assert again is not None


async def test_generate_hourly_slots_moscow():
    resource = Resource(
        studio_id=1,
        name="Зал",
        duration_min=60,
        slot_step_min=60,
        timezone="Europe/Moscow",
    )
    day = date(2026, 9, 1)
    tz = ZoneInfo("Europe/Moscow")
    windows = [
        (
            datetime(2026, 9, 1, 10, 0, tzinfo=tz),
            datetime(2026, 9, 1, 12, 0, tzinfo=tz),
        )
    ]
    slots = generate_slots_for_day(resource, day, windows=windows)
    assert len(slots) == 2
    local0 = slots[0].starts_at.astimezone(ZoneInfo("Europe/Moscow"))
    assert local0.hour == 10
    assert generate_slots_for_day(resource, day) == []


async def test_available_slots_skip_hold(session):
    resource = await _seed_resource(session)
    day = _future_day()
    tz = ZoneInfo("Europe/Moscow")
    start_local = datetime(day.year, day.month, day.day, 10, 0, tzinfo=tz)
    await add_window(session, resource, start_local, start_local + timedelta(hours=2))
    await create_hold(
        session,
        resource=resource,
        starts_at=start_local,
        ends_at=start_local + timedelta(hours=1),
        client_telegram_id=1,
        client_name="A",
        client_phone=None,
        client_user_id=None,
    )
    free = await available_slots(session, resource, day)
    hours = {s.starts_at.astimezone(tz).hour for s in free}
    assert 10 not in hours
    assert 11 in hours


async def test_free_monthly_limit(session):
    resource = await _seed_resource(session)
    studio = await session.get(Studio, resource.studio_id)
    studio.tariff = TARIFF_FREE
    for i in range(settings.FREE_BOOKINGS_PER_MONTH):
        session.add(
            Booking(
                resource_id=resource.id,
                studio_id=studio.id,
                client_telegram_id=3000 + i,
                client_name="x",
                starts_at=_slot_start() + timedelta(days=i),
                ends_at=_slot_start() + timedelta(days=i, hours=1),
                status=STATUS_PAID,
            )
        )
    await session.commit()
    ok, reason = await can_create_booking(session, studio)
    assert ok is False
    assert "лимит" in reason.lower()
    studio.tariff = TARIFF_STARTER
    ok, _ = await can_create_booking(session, studio)
    assert ok is True


async def test_webhook_idempotent(session):
    resource = await _seed_resource(session, slug="pay-studio", telegram_id=5001)
    booking = await create_hold(
        session,
        resource=resource,
        starts_at=_slot_start(),
        ends_at=_slot_start() + timedelta(hours=1),
        client_telegram_id=9,
        client_name="Клиент",
        client_phone="+79991112233",
        client_user_id=None,
    )
    payment = await create_slot_invoice(session, booking, 1000)
    order_id = payment.prodamus_invoice_id
    first = await apply_paid_order(session, order_id)
    second = await apply_paid_order(session, order_id)
    assert first.status == PAYMENT_PAID
    assert second.id == first.id
    await session.refresh(booking)
    assert booking.status == STATUS_PAID


async def test_prodamus_signature_roundtrip():
    secret = "test-secret"
    data = {"order_id": "slot-1-2", "sum": "1000"}
    sig = sign_payload(data, secret)
    assert verify_signature(data, sig, secret)
    assert not verify_signature(data, "deadbeef", secret)


def test_prodamus_php_form_nests_products():
    from src.services.prodamus import extract_order_fields, parse_php_form, webhook_signature_ok

    body = (
        "order_id=slot-2-2&payment_status=success"
        "&products[0][name]=Hall&products[0][price]=50&products[0][quantity]=1"
        "&submit[order_id]=slot-2-2&submit[payment_status]=success"
    )
    parsed = parse_php_form(body)
    assert parsed["order_id"] == "slot-2-2"
    assert parsed["products"][0]["name"] == "Hall"
    assert parsed["products"][0]["price"] == "50"
    order_id, status = extract_order_fields(parsed)
    assert order_id == "slot-2-2"
    assert status == "success"
    nested = {"order_id": "slot-2-2", "payment_status": "success", "sum": "50.00"}
    sig = sign_payload(nested, "s3cret")
    assert webhook_signature_ok({"submit": nested, "order_id": "slot-2-2"}, sig, "s3cret")


def test_collect_order_ids_prefers_slot_sku():
    from src.services.prodamus import collect_order_ids, extract_order_fields

    payload = {
        "order_id": "01a057b2-5ac2-7a22-8ff5-dc8815433382",
        "payment_status": "success",
        "products": [{"sku": "slot-3-3", "name": "Hall", "price": "50"}],
        "customer_extra": "kind=slot_prepay payment_id=3 order_id=slot-3-3",
    }
    ids = collect_order_ids(payload)
    assert "slot-3-3" in ids
    order_id, status = extract_order_fields(payload)
    assert order_id == "slot-3-3"
    assert status == "success"


def test_payment_url_has_no_query_signature(monkeypatch):
    from src.services import prodamus as prodamus_mod

    monkeypatch.setattr(prodamus_mod.settings, "PRODAMUS_PAYFORM_URL", "https://demo.payform.ru")
    monkeypatch.setattr(prodamus_mod.settings, "PUBLIC_BASE_URL", "https://studiobook.com.ru")
    url = prodamus_mod.build_payment_url(
        order_id="slot-1-2",
        amount_rub=100,
        description="Зал / 1 час",
        customer_phone="+79991234567",
        extra={"kind": "slot_prepay", "payment_id": "2"},
    )
    assert url.startswith("https://demo.payform.ru?")
    assert "signature=" not in url
    assert "urlNotification=" not in url
    assert "do=pay" in url
    assert "products[0][price]=100" in url
    assert "customer_phone=79991234567" in url
    assert "urlSuccess=" in url
    assert "products[0][sku]=slot-1-2" in url
    assert "order_id=slot-1-2" in url


def test_yookassa_payload_and_succeeded_event():
    from src.services.yookassa import (
        build_payment_payload,
        extract_order_id,
        extract_provider_payment_id,
        is_succeeded_event,
    )

    payload = build_payment_payload(
        order_id="slot-1-2",
        amount_rub=490,
        description="Старт",
        return_url="https://studiobook.com.ru/pay/success",
        extra={"kind": "slot_prepay"},
    )
    assert payload["amount"]["value"] == "490.00"
    assert payload["capture"] is True
    assert payload["metadata"]["order_id"] == "slot-1-2"
    assert payload["metadata"]["kind"] == "slot_prepay"

    event = {
        "event": "payment.succeeded",
        "object": {
            "id": "yo-abc",
            "status": "succeeded",
            "metadata": {"order_id": "slot-1-2"},
        },
    }
    assert is_succeeded_event(event)
    assert extract_order_id(event) == "slot-1-2"
    assert extract_provider_payment_id(event) == "yo-abc"
    from src.services.yookassa import extract_amount_rub

    event["object"]["amount"] = {"value": "490.00", "currency": "RUB"}
    assert extract_amount_rub(event) == 490


def test_active_provider_prefers_yookassa(monkeypatch):
    from src.services import payments as payments_mod
    from src.services import prodamus as prodamus_mod
    from src.services import yookassa as yookassa_mod

    monkeypatch.setattr(yookassa_mod, "is_configured", lambda: True)
    monkeypatch.setattr(prodamus_mod, "is_configured", lambda: True)
    monkeypatch.setattr(payments_mod.settings, "PAYMENT_PROVIDER", "auto")
    assert payments_mod.active_provider() == "yookassa"
    monkeypatch.setattr(payments_mod.settings, "PAYMENT_PROVIDER", "prodamus")
    assert payments_mod.active_provider() == "prodamus"


def test_slugify_russian():
    assert slugify("Циклорама Свет") == "ciklorama-svet"


async def test_hold_ttl_uses_studio_minutes(session):
    from src.database.base import utcnow

    resource = await _seed_resource(session, slug="ttl-studio", telegram_id=6001)
    studio = await session.get(Studio, resource.studio_id)
    studio.hold_ttl_minutes = 60
    await session.commit()
    booking = await create_hold(
        session,
        resource=resource,
        starts_at=_slot_start(),
        ends_at=_slot_start() + timedelta(hours=1),
        client_telegram_id=1,
        client_name="A",
        client_phone=None,
        client_user_id=None,
        studio=studio,
    )
    assert booking is not None
    exp = booking.hold_expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    delta = exp - utcnow()
    assert timedelta(minutes=59) <= delta <= timedelta(minutes=61)


async def test_overlapping_durations_rejected(session):
    resource = await _seed_resource(session, slug="overlap", telegram_id=6002)
    start = datetime(2026, 9, 1, 10, 0, tzinfo=ZoneInfo("Europe/Moscow")).astimezone(timezone.utc)
    first = await create_hold(
        session,
        resource=resource,
        starts_at=start,
        ends_at=start + timedelta(hours=2),
        client_telegram_id=1,
        client_name="A",
        client_phone=None,
        client_user_id=None,
    )
    assert first is not None
    second = await create_hold(
        session,
        resource=resource,
        starts_at=start + timedelta(hours=1),
        ends_at=start + timedelta(hours=2),
        client_telegram_id=2,
        client_name="B",
        client_phone=None,
        client_user_id=None,
    )
    assert second is None


async def test_buffer_blocks_neighbor_start(session):
    resource = await _seed_resource(session, slug="buffer", telegram_id=6003)
    resource.buffer_min = 10
    await session.commit()
    tz = ZoneInfo("Europe/Moscow")
    day = _future_day()
    start_local = datetime(day.year, day.month, day.day, 10, 0, tzinfo=tz)
    await add_window(session, resource, start_local, start_local + timedelta(hours=4))
    await create_hold(
        session,
        resource=resource,
        starts_at=start_local,
        ends_at=start_local + timedelta(hours=1),
        client_telegram_id=1,
        client_name="A",
        client_phone=None,
        client_user_id=None,
    )
    free = await available_slots(session, resource, day, 60)
    hours = {s.starts_at.astimezone(tz).hour for s in free}
    assert 10 not in hours
    assert 11 not in hours
    assert 12 in hours


async def test_night_tariff_moscow():
    from src.services.slots import quote_price_rub

    resource = Resource(
        studio_id=1,
        name="Зал",
        duration_min=60,
        timezone="Europe/Moscow",
        work_start=time(10, 0),
        work_end=time(23, 0),
        price_rub=2000,
        night_price_rub=3000,
        night_start=time(22, 0),
        min_duration_min=60,
        hour_markup_percent=50,
    )
    day = datetime(2026, 9, 2, 22, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    assert quote_price_rub(resource, day, 60) == 3000
    day_noon = datetime(2026, 9, 2, 12, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    assert quote_price_rub(resource, day_noon, 60) == 2000
    saturday = datetime(2026, 9, 5, 12, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    resource.weekend_price_rub = 2500
    assert quote_price_rub(resource, saturday, 60) == 2500


async def test_one_hour_markup_when_min_two():
    from src.services.slots import allowed_durations, quote_price_rub

    resource = Resource(
        studio_id=1,
        name="Циклорама",
        duration_min=120,
        min_duration_min=120,
        slot_step_min=60,
        timezone="Europe/Moscow",
        price_rub=2200,
        hour_markup_percent=50,
    )
    assert 60 in allowed_durations(resource)
    assert 120 in allowed_durations(resource)
    noon = datetime(2026, 9, 2, 12, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    assert quote_price_rub(resource, noon, 60) == 3300
    assert quote_price_rub(resource, noon, 120) == 4400


async def test_prepay_percent_and_cancel_refund():
    from src.database.models.studio import Studio
    from src.services.cancellations import refund_for_cancel
    from src.services.slots import prepay_amount_rub

    studio = Studio(
        slug="x",
        name="x",
        owner_id=1,
        owner_telegram_id=1,
        prepay_percent=50,
        cancel_free_hours=72,
        late_cancel_retain_percent=50,
    )
    assert prepay_amount_rub(studio, 1000) == 500
    booking = Booking(
        resource_id=1,
        studio_id=1,
        client_telegram_id=1,
        client_name="A",
        starts_at=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
        ends_at=datetime(2026, 10, 1, 13, 0, tzinfo=timezone.utc),
        status=STATUS_PAID,
    )
    far = datetime(2026, 9, 1, tzinfo=timezone.utc)
    amount, reason = refund_for_cancel(studio, booking, 500, by="client", now=far)
    assert amount == 500 and reason == "free_cancel"
    late = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)
    amount, reason = refund_for_cancel(studio, booking, 500, by="client", now=late)
    assert amount == 250 and reason == "late_cancel"
    amount, reason = refund_for_cancel(studio, booking, 500, by="owner", now=late)
    assert amount == 500 and reason == "owner_cancel"


async def test_client_cancel_marks_refunded(session):
    from src.database.models.payment import PAYMENT_REFUNDED
    from src.services.cancellations import cancel_booking
    from src.services.payments import apply_paid_order, create_slot_invoice

    resource = await _seed_resource(session, slug="cx-studio", telegram_id=7001)
    studio = await session.get(Studio, resource.studio_id)
    studio.cancel_free_hours = 72
    await session.commit()
    start = datetime.now(timezone.utc) + timedelta(days=5)
    booking = await create_hold(
        session,
        resource=resource,
        starts_at=start,
        ends_at=start + timedelta(hours=1),
        client_telegram_id=9,
        client_name="Клиент",
        client_phone=None,
        client_user_id=None,
        quoted_price_rub=1000,
        prepay_amount_rub=1000,
        studio=studio,
    )
    payment = await create_slot_invoice(session, booking, 1000)
    await apply_paid_order(session, payment.prodamus_invoice_id)
    await session.refresh(booking)
    result = await cancel_booking(session, booking, studio, by="client")
    assert result.ok
    assert result.refund_rub == 1000
    await session.refresh(payment)
    assert payment.status == PAYMENT_REFUNDED
    await session.refresh(booking)
    assert booking.status == STATUS_CANCELLED


async def test_block_hides_slot(session):
    from src.services.slots import create_block

    resource = await _seed_resource(session, slug="block-studio", telegram_id=8001)
    tz = ZoneInfo("Europe/Moscow")
    day = _future_day()
    start_local = datetime(day.year, day.month, day.day, 10, 0, tzinfo=tz)
    await add_window(session, resource, start_local, start_local + timedelta(hours=2))
    block = await create_block(
        session,
        resource=resource,
        starts_at=start_local,
        ends_at=start_local + timedelta(hours=1),
        owner_telegram_id=8001,
    )
    assert block is not None
    free = await available_slots(session, resource, day, 60)
    hours = {s.starts_at.astimezone(tz).hour for s in free}
    assert 10 not in hours
    assert 11 in hours


async def test_reminders_24h_and_2h(session):
    from src.database.base import utcnow
    from src.services.jobs import collect_due_reminders

    resource = await _seed_resource(session, slug="rem-studio", telegram_id=9001)
    now = utcnow()
    far = Booking(
        resource_id=resource.id,
        studio_id=resource.studio_id,
        client_telegram_id=1,
        client_name="A",
        starts_at=now + timedelta(hours=10),
        ends_at=now + timedelta(hours=11),
        status=STATUS_PAID,
    )
    near = Booking(
        resource_id=resource.id,
        studio_id=resource.studio_id,
        client_telegram_id=2,
        client_name="B",
        starts_at=now + timedelta(hours=1, minutes=30),
        ends_at=now + timedelta(hours=2, minutes=30),
        status=STATUS_PAID,
    )
    session.add_all([far, near])
    await session.commit()
    due = await collect_due_reminders(session, now)
    kinds = {b.client_name: k for b, k in due}
    assert kinds["A"] == "24h"
    assert kinds["B"] == "2h"


async def test_refund_webhook_idempotent(session):
    from src.database.models.payment import PAYMENT_REFUNDED
    from src.services.payments import apply_paid_order, apply_refund, create_slot_invoice

    resource = await _seed_resource(session, slug="ref-studio", telegram_id=9101)
    booking = await create_hold(
        session,
        resource=resource,
        starts_at=_slot_start(),
        ends_at=_slot_start() + timedelta(hours=1),
        client_telegram_id=9,
        client_name="Клиент",
        client_phone=None,
        client_user_id=None,
    )
    payment = await create_slot_invoice(session, booking, 1000)
    await apply_paid_order(session, payment.prodamus_invoice_id)
    first = await apply_refund(session, payment, 1000)
    second = await apply_refund(session, payment, 1000)
    assert first.status == PAYMENT_REFUNDED
    assert second.id == first.id
    again = await apply_paid_order(session, payment.prodamus_invoice_id)
    assert again.status == PAYMENT_REFUNDED


async def test_request_outside_window_then_one_confirm(session):
    resource = await _seed_resource(session, slug="req-studio", telegram_id=9201)
    start = datetime.now(timezone.utc) + timedelta(days=3)
    end = start + timedelta(hours=1)
    first = await create_request(
        session,
        resource=resource,
        starts_at=start,
        ends_at=end,
        client_telegram_id=1,
        client_name="Анна",
        client_phone="+79990000000",
        client_user_id=None,
    )
    second = await create_request(
        session,
        resource=resource,
        starts_at=start,
        ends_at=end,
        client_telegram_id=2,
        client_name="Оля",
        client_phone="+79990000001",
        client_user_id=None,
    )
    assert first is not None and first.status == STATUS_PENDING
    assert second is not None and second.status == STATUS_PENDING
    confirmed = await confirm_request(session, first)
    assert confirmed is not None
    assert confirmed.status in {STATUS_HOLD, STATUS_PAID}
    blocked = await confirm_request(session, second)
    assert blocked is None
