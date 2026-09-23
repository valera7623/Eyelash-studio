"""ЮKassa API v3: платёж, проверка webhook, возврат."""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

from src.config import settings

logger = logging.getLogger(__name__)

YOOKASSA_API = "https://api.yookassa.ru/v3"
_TIMEOUT = aiohttp.ClientTimeout(total=20, connect=8)


def is_configured() -> bool:
    return bool(settings.YOOKASSA_SHOP_ID.strip() and settings.YOOKASSA_SECRET_KEY.strip())


def _auth() -> aiohttp.BasicAuth:
    return aiohttp.BasicAuth(settings.YOOKASSA_SHOP_ID.strip(), settings.YOOKASSA_SECRET_KEY.strip())


def build_payment_payload(
    *,
    order_id: str,
    amount_rub: int,
    description: str,
    return_url: str,
    extra: dict[str, str] | None = None,
) -> dict[str, Any]:
    major = f"{int(amount_rub):.2f}"
    metadata = {"order_id": order_id}
    if extra:
        metadata.update({k: str(v)[:100] for k, v in extra.items()})
    return {
        "amount": {"value": major, "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": return_url},
        "description": " ".join((description or "Услуга").split())[:128],
        "metadata": metadata,
    }


def extract_order_id(payload: dict[str, Any]) -> str:
    obj = payload.get("object") if isinstance(payload.get("object"), dict) else {}
    meta = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
    return str(meta.get("order_id") or "").strip()


def extract_provider_payment_id(payload: dict[str, Any]) -> str:
    obj = payload.get("object") if isinstance(payload.get("object"), dict) else {}
    return str(obj.get("id") or "").strip()


def extract_amount_rub(payload: dict[str, Any]) -> int | None:
    obj = payload.get("object") if isinstance(payload.get("object"), dict) else {}
    amount = obj.get("amount") if isinstance(obj.get("amount"), dict) else {}
    raw = amount.get("value")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(round(float(str(raw).replace(",", "."))))
    except (TypeError, ValueError):
        return None


def is_succeeded_event(payload: dict[str, Any]) -> bool:
    event = str(payload.get("event") or "")
    obj = payload.get("object") if isinstance(payload.get("object"), dict) else {}
    status = str(obj.get("status") or "")
    return event == "payment.succeeded" or status == "succeeded"


async def create_payment(
    *,
    order_id: str,
    amount_rub: int,
    description: str,
    extra: dict[str, str] | None = None,
) -> tuple[str, str]:
    if not is_configured():
        raise RuntimeError("YooKassa is not configured")
    root = settings.PUBLIC_BASE_URL.rstrip("/") if settings.PUBLIC_BASE_URL.strip() else ""
    return_url = (root + "/pay/success") if root else "https://studiobook.com.ru/pay/success"
    payload = build_payment_payload(
        order_id=order_id,
        amount_rub=amount_rub,
        description=description,
        return_url=return_url,
        extra=extra,
    )
    headers = {"Idempotence-Key": order_id[:64], "Content-Type": "application/json"}
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as http:
        async with http.post(
            f"{YOOKASSA_API}/payments",
            json=payload,
            headers=headers,
            auth=_auth(),
        ) as resp:
            body = await resp.text()
            if resp.status >= 400:
                logger.warning("yookassa create http %s: %s", resp.status, body[:400])
                raise RuntimeError(f"yookassa_http_{resp.status}")
            try:
                data = json.loads(body)
            except json.JSONDecodeError as exc:
                raise RuntimeError("yookassa_bad_json") from exc
    confirmation = data.get("confirmation") if isinstance(data, dict) else {}
    url = str((confirmation or {}).get("confirmation_url") or "")
    yoo_id = str((data or {}).get("id") or "")
    if not url or not yoo_id:
        raise RuntimeError("yookassa_no_confirmation_url")
    return url, yoo_id


async def payment_is_succeeded(provider_payment_id: str) -> bool:
    if not is_configured() or not provider_payment_id:
        return False
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as http:
        async with http.get(
            f"{YOOKASSA_API}/payments/{provider_payment_id}",
            auth=_auth(),
        ) as resp:
            if resp.status >= 400:
                logger.warning("yookassa get payment http %s", resp.status)
                return False
            data = await resp.json(content_type=None)
    return isinstance(data, dict) and str(data.get("status") or "") == "succeeded"


async def request_refund(provider_payment_id: str, amount_rub: int) -> tuple[bool, str]:
    if not provider_payment_id:
        return True, "no_order"
    if not is_configured():
        return True, "local"
    if amount_rub <= 0:
        return True, "zero"
    payload = {
        "payment_id": provider_payment_id,
        "amount": {"value": f"{int(amount_rub):.2f}", "currency": "RUB"},
    }
    headers = {
        "Idempotence-Key": f"refund-{provider_payment_id}-{amount_rub}"[:64],
        "Content-Type": "application/json",
    }
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as http:
            async with http.post(
                f"{YOOKASSA_API}/refunds",
                json=payload,
                headers=headers,
                auth=_auth(),
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    logger.warning("yookassa refund http %s: %s", resp.status, body[:300])
                    return False, f"http_{resp.status}"
        return True, "ok"
    except Exception as exc:
        logger.exception("yookassa refund")
        return False, str(exc)[:120]
