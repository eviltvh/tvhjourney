"""Mercado Pago: links de pago (Checkout Pro), búsqueda de pagos y reembolsos.

No usa webhooks: el bot pregunta por los pagos de cada link reciente, así que
funciona aunque la laptop no tenga puertos abiertos.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone

import aiohttp

import config

log = logging.getLogger(__name__)

_session: aiohttp.ClientSession | None = None


class MPError(RuntimeError):
    pass


async def _http() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            base_url=config.MP_API_URL + "/",
            headers={
                "Authorization": f"Bearer {config.MP_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            timeout=aiohttp.ClientTimeout(total=20),
        )
    return _session


async def close():
    if _session and not _session.closed:
        await _session.close()


async def _req(method: str, path: str, json=None, params=None, headers=None):
    s = await _http()
    async with s.request(method, path.lstrip("/"), json=json, params=params, headers=headers) as r:
        if r.status >= 300:
            raise MPError(f"{method} {path} → {r.status}: {(await r.text())[:300]}")
        return await r.json()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


async def check() -> str:
    me = await _req("GET", "/users/me")
    return f"{me.get('nickname')} · {me.get('site_id')} · ${config.PRICE_MXN:g} MXN / {config.PASS_DAYS} días"


async def create_checkout(ref: str, back_url: str) -> tuple[str, str]:
    """Crea un link de pago. Devuelve (preference_id, url)."""
    now = datetime.now(timezone.utc)
    until = now + timedelta(days=config.CHECKOUT_DAYS)
    body = {
        "items": [{
            "id": "tvh-journey-pass",
            "title": f"THE TVH JOURNEY · {config.PASS_DAYS} días",
            "description": "Acceso a los videos de THE TVH JOURNEY",
            "category_id": "entertainment",
            "quantity": 1,
            "currency_id": "MXN",
            "unit_price": config.PRICE_MXN,
        }],
        "external_reference": ref,
        "back_urls": {"success": back_url, "pending": back_url, "failure": back_url},
        "auto_return": "approved",
        "payment_methods": {"installments": 1},
        "statement_descriptor": config.STATEMENT_DESCRIPTOR,
        "expires": True,
        "expiration_date_from": _iso(now - timedelta(minutes=5)),
        "expiration_date_to": _iso(until),
        "date_of_expiration": _iso(until),  # vencimiento de la ficha OXXO
    }
    pref = await _req("POST", "/checkout/preferences", json=body)
    return pref["id"], pref["init_point"]


async def payments_for(ref: str) -> list[dict]:
    """Pagos (de cualquier estado) hechos con ese link."""
    data = await _req(
        "GET", "/v1/payments/search",
        params={"external_reference": ref, "sort": "date_created", "criteria": "desc", "limit": "20"},
    )
    return data.get("results", [])


async def refund(payment_id: str):
    await _req(
        "POST", f"/v1/payments/{payment_id}/refunds", json={},
        headers={"X-Idempotency-Key": str(uuid.uuid4())},
    )
