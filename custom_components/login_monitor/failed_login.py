"""Fire a bus event on failed / invalid-auth login attempts.

Home Assistant *does* surface failed logins, but only as a hardcoded persistent
notification (``Login attempt failed``) that carries the source IP and nothing
else: no bus event, no geolocation. The single point where every invalid-auth
request is funnelled is ``homeassistant.components.http.ban.process_wrong_login``.
This monitor wraps that module-level function so it can fire an enriched event
(with approximate location) in real time whenever a login fails.

Safety mirrors the successful-login wrapper: the real ``process_wrong_login`` is
awaited first and its result returned unchanged; our event fire is fully guarded
so a bug here can never affect Home Assistant's failed-login handling or the IP
ban logic, the worst case is a missed notification.

Noise control: a public instance is hit constantly by bots, so we fire only the
*first* time each distinct IP fails (dedup by IP for the life of the process),
matching the "new IP only" behaviour of the successful-login path.
"""

from __future__ import annotations

import logging

from homeassistant.components.http import ban as http_ban
from homeassistant.core import HomeAssistant, callback

from .const import EVENT_FAILED_LOGIN
from .geo import async_lookup_geo, is_public_ip

_LOGGER = logging.getLogger(__name__)

# Marker set on our wrapper so we can recognise it, and an attribute that
# carries the *true* original underneath it so we never lose the real function.
_WRAP_MARKER = "_login_monitor_failed_wrapped"
_ORIG_ATTR = "_login_monitor_failed_original"


class FailedLoginMonitor:
    """Wraps ``process_wrong_login`` and fires an event on new failed-login IPs."""

    def __init__(self, hass: HomeAssistant, geo_lookup: bool) -> None:
        """Initialize the monitor."""
        self._hass = hass
        self._geo_lookup = geo_lookup
        self._known_failed_ips: set[str] = set()
        # The true, unwrapped function. Kept valid for as long as our wrapper may
        # still execute, so a buried wrapper never awaits ``None``.
        self._original = None
        self._wrapped = None
        self._active = False

    @callback
    def async_start(self) -> None:
        """Install the wrapper around ``process_wrong_login``."""
        current = http_ban.process_wrong_login
        # Recover the genuine original even if a stale wrapper (ours, from a
        # previous unclean reload) is already installed, take ownership cleanly
        # instead of stacking a second copy.
        self._original = getattr(current, _ORIG_ATTR, current)

        async def _wrapped(request):
            # Run HA's real failed-login handling FIRST and unchanged.
            result = await self._original(request)
            # ``_active`` lets async_stop neutralize this closure even when it
            # cannot be physically removed from the call chain.
            if self._active:
                try:
                    self._handle(request)
                except Exception:  # noqa: BLE001 - notifications must never break auth
                    _LOGGER.exception("Login Monitor failed to handle a failed login")
            return result

        setattr(_wrapped, _WRAP_MARKER, True)
        setattr(_wrapped, _ORIG_ATTR, self._original)
        self._wrapped = _wrapped
        self._active = True
        http_ban.process_wrong_login = _wrapped
        _LOGGER.debug("Login Monitor failed-login watch active")

    @callback
    def async_stop(self) -> None:
        """Neutralize, and cleanly restore the original when possible."""
        if self._wrapped is None:
            # Never installed, or already cleanly restored, nothing to do.
            return

        # Always neutralize first: even if our wrapper is buried under another
        # patch and cannot be removed, it becomes a pure pass-through.
        self._active = False

        if http_ban.process_wrong_login is self._wrapped:
            # We are still the top of the chain: physically restore.
            http_ban.process_wrong_login = self._original
            self._original = None
            self._wrapped = None
            return

        # Buried under another patch. Leave our (now inert) wrapper in place and
        # KEEP ``self._original`` valid, so the still-live closure stays a safe
        # pass-through rather than awaiting ``None``.
        _LOGGER.warning(
            "Login Monitor failed-login wrapper is no longer the top of the chain "
            "(another integration wrapped process_wrong_login); left in place as a "
            "neutralized pass-through until the next restart"
        )

    @callback
    def _handle(self, request) -> None:
        """Inspect a failed-login request and fire an event on new IPs."""
        remote_ip = getattr(request, "remote", None)
        if not remote_ip:
            return

        # Once per new IP: keeps bots on a public instance from spamming.
        if remote_ip in self._known_failed_ips:
            return
        self._known_failed_ips.add(remote_ip)

        data = {"ip_address": remote_ip, "is_new_ip": True}

        # Geo lookup is a network call, so it must never run on the auth path.
        # Schedule it as a background task that enriches then fires the event;
        # everything else fires immediately.
        if self._geo_lookup and is_public_ip(remote_ip):
            self._hass.async_create_task(self._async_fire_with_geo(data, remote_ip))
        else:
            self._fire(data)

    @callback
    def _fire(self, data: dict) -> None:
        """Emit the failed-login event on the bus."""
        _LOGGER.debug("Firing %s: %s", EVENT_FAILED_LOGIN, data)
        self._hass.bus.async_fire(EVENT_FAILED_LOGIN, data)

    async def _async_fire_with_geo(self, data: dict, ip: str) -> None:
        """Enrich with geolocation (best-effort) then fire, off the auth path."""
        geo = await async_lookup_geo(self._hass, ip)
        self._fire({**data, **geo} if geo else data)
