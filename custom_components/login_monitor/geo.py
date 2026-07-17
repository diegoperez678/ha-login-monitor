"""Approximate IP geolocation via a keyless provider (shared, best-effort).

Used by both the successful-login and failed-login monitors. The lookup is a
network call, so callers must run it off the auth path; a failed/slow lookup
simply returns ``None`` and the caller fires its event without location.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import GEO_PROVIDER_URL, GEO_TIMEOUT

_LOGGER = logging.getLogger(__name__)


def is_public_ip(ip: str | None) -> bool:
    """True only for routable public IPs (skip geo lookups for LAN/loopback)."""
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (addr.is_private or addr.is_loopback or addr.is_link_local)


async def async_lookup_geo(hass: HomeAssistant, ip: str) -> dict | None:
    """Look up approximate location for an IP via a keyless provider."""
    session = async_get_clientsession(hass)
    try:
        async with asyncio.timeout(GEO_TIMEOUT):
            resp = await session.get(GEO_PROVIDER_URL.format(ip=ip))
            payload = await resp.json(content_type=None)
    except Exception:  # noqa: BLE001 - geo is optional, never fatal (incl. timeout)
        _LOGGER.debug("Geo lookup failed for %s", ip, exc_info=True)
        return None

    if not isinstance(payload, dict) or not payload.get("success"):
        return None

    city = payload.get("city") or None
    region = payload.get("region") or None
    country = payload.get("country") or None
    country_code = payload.get("country_code") or None
    location = ", ".join(part for part in (city, region, country) if part) or None
    return {
        "city": city,
        "region": region,
        "country": country,
        "country_code": country_code,
        "location": location,
    }
