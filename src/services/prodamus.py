"""Prodamus payform: ссылка оплаты и проверка уведомления (54-ФЗ на стороне кассы)."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode

from src.config import settings

logger = logging.getLogger(__name__)

_PHP_KEY = re.compile(r"^([^[]+)((?:\[[^\]]*\])*)$")
_PHP_IDX = re.compile(r"\[([^\]]*)\]")


def is_configured() -> bool:
    return bool(
        settings.PRODAMUS_PAYFORM_URL.strip() and settings.PRODAMUS_SECRET.strip()
    )


def parse_php_form(body: str) -> dict[str, Any]:
    """application/x-www-form-urlencoded → вложенный dict как PHP $_POST."""
    root: dict[str, Any] = {}
    for key, value in parse_qsl(body, keep_blank_values=True):
        _php_assign(root, _php_path(key), value)
    return root


def _php_path(key: str) -> list[str]:
    matched = _PHP_KEY.match(key)
    if not matched:
        return [key]
    parts = [matched.group(1)]
    parts.extend(_PHP_IDX.findall(matched.group(2) or ""))
    return [p for p in parts if p != ""]


def _php_assign(root: dict[str, Any], path: list[str], value: str) -> None:
    cur: Any = root
    for i, key in enumerate(path):
        last = i == len(path) - 1
        nxt_idx = (i + 1 < len(path)) and path[i + 1].isdigit()
        if last:
            if isinstance(cur, list):
                idx = int(key)
                while len(cur) <= idx:
                    cur.append("")
                cur[idx] = value
            else:
                cur[key] = value
            return
        if isinstance(cur, list):
            idx = int(key)
            while len(cur) <= idx:
                cur.append([] if nxt_idx else {})
            if not isinstance(cur[idx], (dict, list)):
                cur[idx] = [] if nxt_idx else {}
            cur = cur[idx]
            continue
        if key not in cur or not isinstance(cur[key], (dict, list)):
            cur[key] = [] if nxt_idx else {}
        cur = cur[key]


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        if value and all(str(k).isdigit() for k in value):
            indexes = sorted(int(k) for k in value)
            if indexes == list(range(len(value))):
                return [_normalize(value[str(i)] if str(i) in value else value[i]) for i in indexes]
        return {str(k): _normalize(value[k]) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if value is None:
        return ""
    return str(value)


def _canonical_json(data: Any) -> list[str]:
    norm = _normalize(data)
    plain = json.dumps(norm, ensure_ascii=False, separators=(",", ":"))
    return [
        plain,
        plain.replace("/", "\\/"),
        json.dumps(norm, ensure_ascii=True, separators=(",", ":")),
    ]


def sign_payload(data: dict[str, Any], secret: str) -> str:
    payload = _canonical_json(data)[0]
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_signature(data: dict[str, Any], signature: str, secret: str) -> bool:
    got = (signature or "").strip().lower()
    if not got:
        return False
    for raw in _canonical_json(data):
        digest = hmac.new(secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()
        if hmac.compare_digest(digest.lower(), got):
            return True
    return False


def _as_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.startswith("{"):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def webhook_payloads_to_verify(payload: dict[str, Any]) -> list[dict[str, Any]]:
    skip = {"signature", "sign"}
    base = {k: v for k, v in payload.items() if k not in skip}
    bodies = [base]
    submit = _as_dict(payload.get("submit"))
    if submit:
        bodies.insert(0, {k: v for k, v in submit.items() if k not in skip})
    return bodies


def webhook_signature_ok(payload: dict[str, Any], signature: str, secret: str) -> bool:
    return any(verify_signature(body, signature, secret) for body in webhook_payloads_to_verify(payload))


def extract_order_fields(payload: dict[str, Any]) -> tuple[str, str]:
    submit = _as_dict(payload.get("submit")) or {}
    candidates = collect_order_ids(payload)
    ours = [i for i in candidates if i.startswith("slot-") or i.startswith("sub-")]
    order_id = (ours[0] if ours else (candidates[0] if candidates else ""))
    status = str(
        payload.get("payment_status")
        or submit.get("payment_status")
        or payload.get("status")
        or submit.get("status")
        or ""
    ).lower()
    return str(order_id), status


def collect_order_ids(payload: dict[str, Any]) -> list[str]:
    found: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in found:
            found.append(text)

    sources = [payload]
    submit = _as_dict(payload.get("submit"))
    if submit:
        sources.append(submit)
    for src in sources:
        for key in ("order_id", "orderId", "order_num", "orderNum"):
            add(src.get(key))
        products = src.get("products")
        if isinstance(products, list):
            for item in products:
                if isinstance(item, dict):
                    add(item.get("sku"))
        extra = str(src.get("customer_extra") or "")
        match = re.search(r"(slot-\d+-\d+|sub-\d+-[a-z]+-\d+)", extra)
        if match:
            add(match.group(1))
    return found


def extract_amount_rub(payload: dict[str, Any]) -> int | None:
    sources = [payload]
    submit = _as_dict(payload.get("submit"))
    if submit:
        sources.append(submit)
    for src in sources:
        for key in ("sum", "order_sum", "amount"):
            raw = src.get(key)
            if raw is None or str(raw).strip() == "":
                continue
            try:
                return int(round(float(str(raw).replace(",", "."))))
            except (TypeError, ValueError):
                continue
        products = src.get("products")
        if isinstance(products, list) and products and isinstance(products[0], dict):
            raw = products[0].get("price")
            if raw is not None and str(raw).strip():
                try:
                    return int(round(float(str(raw).replace(",", "."))))
                except (TypeError, ValueError):
                    continue
    return None


def payment_id_from_payload(payload: dict[str, Any]) -> int | None:
    sources = [payload]
    submit = _as_dict(payload.get("submit"))
    if submit:
        sources.append(submit)
    for src in sources:
        extra = str(src.get("customer_extra") or "")
        match = re.search(r"payment_id=(\d+)", extra)
        if match:
            return int(match.group(1))
    return None


def build_payment_url(
    *,
    order_id: str,
    amount_rub: int,
    description: str,
    customer_phone: str | None = None,
    extra: dict[str, str] | None = None,
) -> str:
    """GET на payform. Подпись в query не ставить: неверный HMAC даёт HTTP 400 (RFC9110)."""
    base = settings.PRODAMUS_PAYFORM_URL.rstrip("/")
    name = " ".join((description or "Услуга").split())[:120]
    extra_note = ""
    if extra:
        extra_note = " ".join(f"{k}={v}" for k, v in extra.items())[:180]
    extra_note = f"{extra_note} order_id={order_id}".strip()
    flat: dict[str, str] = {
        "do": "pay",
        "order_id": order_id,
        "products[0][name]": name,
        "products[0][price]": str(amount_rub),
        "products[0][quantity]": "1",
        "products[0][sku]": order_id,
        "products[0][type]": "service",
        "customer_extra": extra_note or name,
    }
    digits = "".join(ch for ch in (customer_phone or "") if ch.isdigit())
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if len(digits) == 11 and digits.startswith("7"):
        flat["customer_phone"] = digits
    if settings.PUBLIC_BASE_URL.strip():
        root = settings.PUBLIC_BASE_URL.rstrip("/")
        flat["urlSuccess"] = root + "/pay/success"
        flat["urlReturn"] = root + "/pay/return"
    return f"{base}?{urlencode(flat, safe='[]')}"


async def request_refund(order_id: str, amount_rub: int) -> tuple[bool, str]:
    """Возврат в кассе Prodamus. Без ключей — только локальная отмена (тесты/пилот)."""
    if not order_id:
        return True, "no_order"
    if not is_configured():
        return True, "local"
    if amount_rub <= 0:
        return True, "zero"
    import aiohttp

    payload = {
        "do": "refund",
        "order_id": order_id,
        "sum": str(amount_rub),
    }
    payload["signature"] = sign_payload(payload, settings.PRODAMUS_SECRET)
    url = settings.PRODAMUS_PAYFORM_URL.rstrip("/")
    try:
        async with aiohttp.ClientSession() as http:
            async with http.post(url, data=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    logger.warning("prodamus refund http %s: %s", resp.status, body[:300])
                    return False, f"http_{resp.status}"
        return True, "ok"
    except Exception as exc:
        logger.exception("prodamus refund")
        return False, str(exc)[:120]
