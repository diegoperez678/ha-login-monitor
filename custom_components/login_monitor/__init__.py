"""Login Monitor: fire a bus event on successful Home Assistant logins.

Home Assistant does not emit an event when a login succeeds. The single point
where every authenticated session stamps its source IP is
``AuthManager.async_create_access_token`` (it calls
``async_log_refresh_token_usage``, which sets ``last_used_at`` /
``last_used_ip``). This integration wraps that method, but only fires an event
the first time a given refresh token is seen, i.e. on an actual new login /
new-device session. Every later call for that same refresh token is a routine
access-token refresh (HA mobile apps do this roughly every 30 minutes) and is
not reported, no matter how often the source IP changes (mobile networks
reassign IPv6 prefixes and hand off between WiFi/cellular constantly, so
gating on IP alone fires on nearly every refresh).

Safety: the wrapper forwards ``*args`` / ``**kwargs`` unchanged and returns the
original result first; the event fire is fully guarded. A failure in our code
can therefore never break authentication, the worst case is a missed
notification while logins keep working. The wrapper also stays correct across
reloads, unclean shutdowns, and the case where another integration wraps the
same method on top of ours (see ``async_start`` / ``async_stop``).
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .ban_ip import async_register_ban_service, async_unregister_ban_service
from .const import (
    CONF_GEO_LOOKUP,
    CONF_IGNORE_SYSTEM_TOKENS,
    DEFAULT_GEO_LOOKUP,
    DEFAULT_IGNORE_SYSTEM_TOKENS,
    DOMAIN,
    EVENT_LOGIN,
)
from .failed_login import FailedLoginMonitor
from .geo import async_lookup_geo, is_public_ip

_LOGGER = logging.getLogger(__name__)

# System tokens are minted for internal integrations and are used constantly;
# they are not "logins" in any meaningful sense.
TOKEN_TYPE_SYSTEM = "system"

# Normal logins carry no client_name (that field is only set for named
# long-lived tokens); the identifying value is the OAuth client_id. Map the
# well-known client_ids to friendly labels.
CLIENT_LABELS = {
    "https://home-assistant.io/iOS": "iOS app",
    "https://home-assistant.io/android": "Android app",
}

# Marker set on our wrapper so we can recognise it, and an attribute that
# carries the *true* original underneath it so we never lose the real method.
_WRAP_MARKER = "_login_monitor_wrapped"
_ORIG_ATTR = "_login_monitor_original"


def _get_option(entry: ConfigEntry, key: str, default):
    """Read a value from entry options, falling back to data, then default."""
    if key in entry.options:
        return entry.options[key]
    return entry.data.get(key, default)


class LoginMonitor:
    """Wraps access-token creation and fires an event on new logins."""

    def __init__(
        self,
        hass: HomeAssistant,
        ignore_system_tokens: bool,
        geo_lookup: bool,
    ) -> None:
        """Initialize the monitor."""
        self._hass = hass
        self._ignore_system_tokens = ignore_system_tokens
        self._geo_lookup = geo_lookup
        # Refresh tokens already known at startup, or seen fire once since:
        # these represent already-established sessions, not new logins.
        self._known_token_ids: set[str] = set()
        # The true, unwrapped method. Kept valid for as long as our wrapper may
        # still execute, so a buried wrapper never calls ``None``.
        self._original = None
        self._wrapped = None
        self._active = False

    async def async_start(self) -> None:
        """Seed already-known refresh tokens and install the wrapper."""
        await self._async_seed_known_token_ids()

        manager = self._hass.auth
        current = manager.async_create_access_token
        # Recover the genuine original even if a stale wrapper (ours, from a
        # previous unclean shutdown) is already installed. This takes ownership
        # cleanly instead of stacking a second copy or going inert.
        self._original = getattr(current, _ORIG_ATTR, current)

        @callback
        def _wrapped(*args, **kwargs):
            # Pass through unchanged and return first, never alter auth.
            token = self._original(*args, **kwargs)
            # ``_active`` lets async_stop neutralize this closure even when it
            # cannot be physically removed from the call chain.
            if self._active:
                try:
                    self._handle(args, kwargs)
                except Exception:  # noqa: BLE001 - notifications must never break auth
                    _LOGGER.exception("Login Monitor failed to handle a login")
            return token

        setattr(_wrapped, _WRAP_MARKER, True)
        setattr(_wrapped, _ORIG_ATTR, self._original)
        self._wrapped = _wrapped
        self._active = True
        manager.async_create_access_token = _wrapped
        _LOGGER.debug(
            "Login Monitor active (seeded_tokens=%s)", len(self._known_token_ids)
        )

    @callback
    def async_stop(self) -> None:
        """Neutralize, and cleanly restore the original when possible."""
        if self._wrapped is None:
            # Never installed, or already cleanly restored, nothing to do.
            return

        # Always neutralize first: even if our wrapper is buried under another
        # integration's patch and cannot be removed, it becomes a pure
        # pass-through and fires no further events.
        self._active = False

        manager = self._hass.auth
        if manager.async_create_access_token is self._wrapped:
            # We are still the top of the chain: physically restore.
            manager.async_create_access_token = self._original
            self._original = None
            self._wrapped = None
            return

        # Buried under another patch. Leave our (now inert) wrapper in place and
        # KEEP ``self._original`` valid, so the still-live closure stays a safe
        # pass-through rather than calling ``None``.
        _LOGGER.warning(
            "Login Monitor wrapper is no longer the top of the auth chain "
            "(another integration wrapped the same method); left in place as a "
            "neutralized pass-through until the next restart"
        )

    async def _async_seed_known_token_ids(self) -> None:
        """Record every refresh token that already exists as 'known'.

        These represent sessions that were already logged in before this
        integration started watching, so their next access-token mint is a
        refresh, not a new login.
        """
        for user in await self._hass.auth.async_get_users():
            for token in user.refresh_tokens.values():
                self._known_token_ids.add(token.id)

    @callback
    def _handle(self, args: tuple, kwargs: dict) -> None:
        """Inspect a token-creation call and fire an event when relevant."""
        # Keyword-first extraction so a future signature change (an inserted
        # positional parameter) cannot silently mis-read the IP.
        refresh_token = kwargs.get("refresh_token")
        if refresh_token is None and args:
            refresh_token = args[0]
        remote_ip = kwargs.get("remote_ip")
        if remote_ip is None and len(args) > 1:
            remote_ip = args[1]
        if refresh_token is None:
            return

        if self._ignore_system_tokens and refresh_token.token_type == TOKEN_TYPE_SYSTEM:
            return

        # The refresh token is the actual "this device is trusted" credential.
        # Its access token gets reminted every ~30 min while the session is
        # active; only the *first* mint for a given refresh token is a new
        # login. Everything after that is a routine refresh, regardless of
        # how many times the source IP changes in between.
        is_new_login = refresh_token.id not in self._known_token_ids
        self._known_token_ids.add(refresh_token.id)
        if not is_new_login:
            return

        user = refresh_token.user
        client_id = refresh_token.client_id
        data = {
            "user_name": user.name if user else None,
            "user_id": user.id if user else None,
            "client": self._friendly_client(refresh_token.client_name, client_id),
            "client_id": client_id,
            "client_name": refresh_token.client_name,
            "token_type": refresh_token.token_type,
            "ip_address": remote_ip,
        }

        # Geo lookup is a network call, so it must never run on the auth path.
        # Schedule it as a background task that enriches then fires the event;
        # everything else fires immediately.
        if self._geo_lookup and is_public_ip(remote_ip):
            self._hass.async_create_task(self._async_fire_with_geo(data, remote_ip))
        else:
            self._fire(data)

    @callback
    def _fire(self, data: dict) -> None:
        """Emit the login event on the bus."""
        _LOGGER.debug("Firing %s: %s", EVENT_LOGIN, data)
        self._hass.bus.async_fire(EVENT_LOGIN, data)

    async def _async_fire_with_geo(self, data: dict, ip: str) -> None:
        """Enrich with geolocation (best-effort) then fire, off the auth path."""
        geo = await async_lookup_geo(self._hass, ip)
        self._fire({**data, **geo} if geo else data)

    def _friendly_client(self, client_name: str | None, client_id: str | None):
        """Best-effort human label for the client that authenticated."""
        if client_name:
            return client_name
        if not client_id:
            return None
        if client_id in CLIENT_LABELS:
            return CLIENT_LABELS[client_id]
        cfg = self._hass.config
        instance_urls = {
            url.rstrip("/") for url in (cfg.external_url, cfg.internal_url) if url
        }
        if client_id.rstrip("/") in instance_urls:
            return "Web UI"
        return client_id


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Login Monitor from a config entry."""
    geo_lookup = _get_option(entry, CONF_GEO_LOOKUP, DEFAULT_GEO_LOOKUP)

    monitor = LoginMonitor(
        hass,
        ignore_system_tokens=_get_option(
            entry, CONF_IGNORE_SYSTEM_TOKENS, DEFAULT_IGNORE_SYSTEM_TOKENS
        ),
        geo_lookup=geo_lookup,
    )
    await monitor.async_start()

    failed_monitor = FailedLoginMonitor(hass, geo_lookup=geo_lookup)
    failed_monitor.async_start()

    async_register_ban_service(hass)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "monitor": monitor,
        "failed_monitor": failed_monitor,
    }
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and restore the wrapped methods."""
    stored = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if stored is not None:
        stored["monitor"].async_stop()
        stored["failed_monitor"].async_stop()
    async_unregister_ban_service(hass)
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
