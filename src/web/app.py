"""HTTP: webhook Prodamus, iCal, лендинг, health. Не веб-кабинет."""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlencode

from aiohttp import web
from sqlalchemy import select

from src.config import PROJECT_ROOT, settings
from src.database.models.booking import STATUS_BLOCKED, STATUS_PAID, Booking
from src.database.models.payment import PAYMENT_REFUND_PENDING, PAYMENT_REFUNDED
from src.services import prodamus, yookassa
from src.services.ical import build_calendar, feed_token_ok
from src.services.payments import (
    amount_matches,
    apply_paid_order,
    find_payment_for_webhook,
    find_payment_for_yookassa,
)
from src.services.studios import get_studio_by_slug, list_active_resources

logger = logging.getLogger(__name__)

LANDING_DIR = PROJECT_ROOT / "landing"
LANDING_PATH = LANDING_DIR / "index.html"


def _form_to_dict(data: dict) -> dict:
    return {str(k): (v[0] if isinstance(v, list) and v else v) for k, v in data.items()}


DEFAULT_BOT_USERNAME = "Studio_book_bot"


def _landing_username(request: web.Request | None = None) -> str:
    if request is not None:
        cached = str(request.app.get("bot_username") or "").strip().lstrip("@")
        if cached:
            return cached
    configured = settings.BOT_USERNAME.strip().lstrip("@")
    return configured or DEFAULT_BOT_USERNAME


def _render_landing(path: Path, bot_username: str | None = None) -> str:
    html = path.read_text(encoding="utf-8") if path.exists() else "<p>studio-book</p>"
    username = (bot_username or DEFAULT_BOT_USERNAME).strip().lstrip("@") or DEFAULT_BOT_USERNAME
    html = html.replace("{{BOT_USERNAME}}", username)
    html = html.replace("{{BOT_LINK}}", f"https://t.me/{username}")
    html = html.replace("{{TARIFF_STARTER_RUB}}", str(settings.TARIFF_STARTER_RUB))
    html = html.replace("{{TARIFF_PLUS_RUB}}", str(settings.TARIFF_PLUS_RUB))
    html = html.replace("{{FREE_BOOKINGS_PER_MONTH}}", str(settings.FREE_BOOKINGS_PER_MONTH))
    return html


async def health(_request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def landing(request: web.Request) -> web.Response:
    return web.Response(
        text=_render_landing(LANDING_PATH, _landing_username(request)),
        content_type="text/html",
        charset="utf-8",
    )


async def robots_txt(_request: web.Request) -> web.Response:
    path = LANDING_DIR / "robots.txt"
    text = path.read_text(encoding="utf-8") if path.exists() else "User-agent: *\nAllow: /\n"
    return web.Response(text=text, content_type="text/plain", charset="utf-8")


async def offer_page(request: web.Request) -> web.Response:
    path = LANDING_DIR / "offer.html"
    if not path.exists():
        raise web.HTTPNotFound()
    return web.Response(
        text=_render_landing(path, _landing_username(request)),
        content_type="text/html",
        charset="utf-8",
    )


async def offer_pdf(_request: web.Request) -> web.StreamResponse:
    path = LANDING_DIR / "offer.pdf"
    if not path.exists():
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Content-Type": "application/pdf"})


async def pay_stub(request: web.Request) -> web.Response:
    return web.Response(
        text="Оплата принята платёжной системой. Вернитесь в Telegram — бронь подтвердится автоматически.",
        content_type="text/plain",
        charset="utf-8",
    )


async def ical_feed(request: web.Request) -> web.Response:
    slug = request.match_info["slug"]
    token = request.match_info.get("token") or ""
    if not feed_token_ok(slug, token):
        raise web.HTTPNotFound()
    session_maker = request.app["session_maker"]
    async with session_maker() as session:
        studio = await get_studio_by_slug(session, slug)
        if studio is None:
            raise web.HTTPNotFound()
        resources = await list_active_resources(session, studio.id)
        if not resources:
            raise web.HTTPNotFound()
        rows = (
            await session.execute(
                select(Booking).where(
                    Booking.studio_id == studio.id,
                    Booking.status.in_((STATUS_PAID, STATUS_BLOCKED)),
                )
            )
        ).scalars().all()
        body = build_calendar(studio, resources, list(rows))
    return web.Response(
        text=body,
        content_type="text/calendar",
        charset="utf-8",
        headers={"Content-Disposition": f'attachment; filename="{slug}.ics"'},
    )


async def _notify_after_paid(bot, session, payment) -> None:
    if payment.kind == "slot_prepay" and payment.booking_id:
        from src.database.models.studio import Resource, Studio
        from src.keyboards.inline import client_booking_keyboard
        from src.services.formatters import booking_summary

        booking = await session.get(Booking, payment.booking_id)
        if not booking:
            return
        studio = await session.get(Studio, booking.studio_id)
        resource = await session.get(Resource, booking.resource_id)
        if not (studio and resource):
            return
        if payment.status in (PAYMENT_REFUNDED, PAYMENT_REFUND_PENDING):
            text = (
                "Оплата пришла, но слот уже занят. Деньги возвращаем.\n"
                + booking_summary(booking, studio, resource)
            )
        else:
            text = "✅ Оплата получена.\n" + booking_summary(booking, studio, resource)
        try:
            await bot.send_message(
                booking.client_telegram_id,
                text,
                reply_markup=client_booking_keyboard(booking.id),
            )
            await bot.send_message(studio.owner_telegram_id, text)
        except Exception:
            logger.exception("notify after payment")
        if payment.status in (PAYMENT_REFUNDED, PAYMENT_REFUND_PENDING):
            for admin_id in settings.admin_ids:
                try:
                    await bot.send_message(
                        admin_id,
                        f"Касса: поздняя оплата payment={payment.id} booking={booking.id} "
                        f"status={payment.status}",
                    )
                except Exception:
                    logger.exception("admin late payment alert")
        return
    if payment.kind == "owner_subscription" and payment.studio_id:
        from src.database.models.studio import Studio

        studio = await session.get(Studio, payment.studio_id)
        if not studio:
            return
        try:
            await bot.send_message(
                studio.owner_telegram_id,
                f"✅ Подписка оплачена. Тариф: {studio.tariff}.",
            )
        except Exception:
            logger.exception("notify subscription")


async def yookassa_webhook(request: web.Request) -> web.Response:
    if not yookassa.is_configured():
        logger.warning("yookassa webhook while not configured")
        raise web.HTTPForbidden()
    try:
        loaded = await request.json()
    except Exception:
        raise web.HTTPBadRequest()
    payload = loaded if isinstance(loaded, dict) else {}
    if not yookassa.is_succeeded_event(payload):
        return web.Response(text="ignored")

    yoo_id = yookassa.extract_provider_payment_id(payload)
    if not await yookassa.payment_is_succeeded(yoo_id):
        logger.warning("yookassa webhook: payment not succeeded id=%s", yoo_id)
        raise web.HTTPForbidden()

    session_maker = request.app["session_maker"]
    bot = request.app["bot"]
    async with session_maker() as session:
        found = await find_payment_for_yookassa(session, payload)
        if found is None:
            logger.info(
                "yookassa webhook: unknown order=%s yoo_id=%s",
                yookassa.extract_order_id(payload),
                yoo_id,
            )
            return web.Response(text="unknown", status=404)
        if not amount_matches(found, yookassa.extract_amount_rub(payload), required=True):
            logger.warning(
                "yookassa webhook: amount mismatch payment=%s got=%s expected=%s",
                found.id,
                yookassa.extract_amount_rub(payload),
                found.amount_rub,
            )
            raise web.HTTPBadRequest()
        invoice = found.prodamus_invoice_id or f"pay-{found.id}"
        payment = await apply_paid_order(session, invoice)
        if payment is None:
            return web.Response(text="unknown", status=404)
        await _notify_after_paid(bot, session, payment)
    return web.Response(text="ok")


async def prodamus_webhook(request: web.Request) -> web.Response:
    content_type = (request.content_type or "").lower()
    payload: dict = {}
    if "json" in content_type:
        loaded = await request.json()
        payload = loaded if isinstance(loaded, dict) else {}
    else:
        post = await request.post()
        encoded = urlencode(
            [(str(k), v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else str(v))
             for k, v in post.items()]
        )
        payload = prodamus.parse_php_form(encoded)
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest()

    signature = (
        request.headers.get("Sign")
        or request.headers.get("X-Signature")
        or str(payload.get("signature") or payload.get("sign") or "")
    )
    if not prodamus.is_configured():
        logger.warning("prodamus webhook while not configured")
        raise web.HTTPForbidden()
    if not prodamus.webhook_signature_ok(
        payload, signature, settings.PRODAMUS_SECRET
    ):
        logger.warning(
            "prodamus webhook: bad signature keys=%s has_sign=%s",
            sorted(str(k) for k in payload.keys()),
            bool(signature),
        )
        raise web.HTTPForbidden()

    order_id, status = prodamus.extract_order_fields(payload)
    if status and status not in {"success", "paid", "ok", "1"}:
        return web.Response(text="ignored")

    session_maker = request.app["session_maker"]
    bot = request.app["bot"]
    async with session_maker() as session:
        found = await find_payment_for_webhook(session, payload)
        if found is None:
            logger.info(
                "prodamus webhook: unknown order extracted=%s ids=%s extra=%s keys=%s",
                order_id,
                prodamus.collect_order_ids(payload),
                str(payload.get("customer_extra") or "")[:120],
                sorted(str(k) for k in payload.keys()),
            )
            return web.Response(text="unknown", status=404)
        if not amount_matches(found, prodamus.extract_amount_rub(payload), required=False):
            logger.warning(
                "prodamus webhook: amount mismatch payment=%s got=%s expected=%s",
                found.id,
                prodamus.extract_amount_rub(payload),
                found.amount_rub,
            )
            raise web.HTTPBadRequest()
        invoice = found.prodamus_invoice_id or f"pay-{found.id}"
        payment = await apply_paid_order(session, invoice)
        if payment is None:
            return web.Response(text="unknown", status=404)
        await _notify_after_paid(bot, session, payment)
    return web.Response(text="ok")


def create_web_app(bot, session_maker) -> web.Application:
    app = web.Application()
    app["bot"] = bot
    app["session_maker"] = session_maker
    app.router.add_get("/health", health)
    app.router.add_get("/", landing)
    app.router.add_get("/robots.txt", robots_txt)
    app.router.add_get("/offer", offer_page)
    app.router.add_get("/offer/", offer_page)
    app.router.add_get("/offer.pdf", offer_pdf)
    app.router.add_get("/pay/success", pay_stub)
    app.router.add_get("/pay/return", pay_stub)
    app.router.add_get("/ical/{slug}/{token}.ics", ical_feed)
    app.router.add_post("/prodamus/webhook", prodamus_webhook)
    app.router.add_post("/yookassa/webhook", yookassa_webhook)
    return app


async def start_http(bot, session_maker) -> web.AppRunner | None:
    if settings.HTTP_PORT <= 0:
        logger.info("HTTP_PORT=0 — веб (лендинг/webhook/iCal) выключен")
        return None
    app = create_web_app(bot, session_maker)
    if bot is not None:
        from src.utils.qr_code import resolve_bot_username

        try:
            username = await resolve_bot_username(bot, settings.BOT_USERNAME)
            app["bot_username"] = username
            logger.info("Лендинг и QR: https://t.me/%s", username)
        except Exception:
            logger.exception("Не удалось получить username бота для лендинга")
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.HTTP_PORT)
    await site.start()
    logger.info("HTTP слушает 0.0.0.0:%s", settings.HTTP_PORT)
    return runner
