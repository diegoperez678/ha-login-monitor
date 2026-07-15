"""Login Monitor: fire a bus event on successful Home Assistant logins.

Home Assistant does not emit an event when a login succeeds. The single point
where every authenticated session stamps its source IP is
``AuthManager.async_create_access_token`` (it calls
``async_log_refresh_token_usage``, which sets ``last_used_at`` /
``last_used_ip``). This integration wraps that method so it can fire an event
in real time whenever an access token is minted — i.e. on login, on new-device
sessions, and on token refreshes.

Safety: the wrapper forwards ``*args`` / ``**kwargs`` unchanged and returns the
original result first; the event fire is fully guarded. A failure in our code
can therefore never break authentication — the worst case is a missed
notification while logins keep working.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import (
    CONF_IGNORE_SYSTEM_TOKENS,
    CONF_NEW_IP_ONLY,
    DEFAULT_IGNORE_SYSTEM_TOKENS,
    DEFAULT_NEW_IP_ONLY,
    DOMAIN,
    EVENT_LOGIN,
)

_LOGGER = logging.getLogger(__name__)

# System tokens are minted for internal integrations and are used constantly;
# they are not "logins" in any meaningful sense.
TOKEN_TYPE_SYSTEM = "system"

# Marker set on our wrapper so we never double-wrap or restore the wrong method.
_WRAP_MARKER = "_login_monitor_wrapped"


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
        new_ip_only: bool,
        ignore_system_tokens: bool,
    ) -> None:
        """Initialize the monitor."""
        self._hass = hass
        self._new_ip_only = new_ip_only
        self._ignore_system_tokens = ignore_system_tokens
        self._known_ips: set[str] = set()
        self._original = None

    async def async_start(self) -> None:
        """Seed known IPs from history and install the wrapper."""
        await self._async_seed_known_ips()

        manager = self._hass.auth
        current = manager.async_create_access_token
        if getattr(current, _WRAP_MARKER, False):
            # Already wrapped (e.g. a previous entry did not clean up). Leave it.
            _LOGGER.debug("async_create_access_token already wrapped; skipping")
            return

        self._original = current

        @callback
        def _wrapped(*args, **kwargs):
            # Pass through unchanged and return first — never alter auth.
            token = self._original(*args, **kwargs)
            try:
                self._handle(args, kwargs)
            except Exception:  # noqa: BLE001 - notifications must never break auth
                _LOGGER.exception("Login Monitor failed to handle a login")
            return token

        setattr(_wrapped, _WRAP_MARKER, True)
        manager.async_create_access_token = _wrapped
        _LOGGER.debug(
            "Login Monitor active (new_ip_only=%s, seeded_ips=%s)",
            self._new_ip_only,
            len(self._known_ips),
        )

    @callback
    def async_stop(self) -> None:
        """Restore the original method."""
        if self._original is None:
            return
        manager = self._hass.auth
        # Only restore if the currently installed method is ours.
        if getattr(manager.async_create_access_token, _WRAP_MARKER, False):
            manager.async_create_access_token = self._original
        self._original = None

    async def _async_seed_known_ips(self) -> None:
        """Record every IP already in the token history as 'known'."""
        for user in await self._hass.auth.async_get_users():
            for token in user.refresh_tokens.values():
                if token.last_used_ip:
                    self._known_ips.add(token.last_used_ip)

    @callback
    def _handle(self, args: tuple, kwargs: dict) -> None:
        """Inspect a token-creation call and fire an event when relevant."""
        refresh_token = kwargs.get("refresh_token") or (args[0] if args else None)
        remote_ip = kwargs.get("remote_ip") or (args[1] if len(args) > 1 else None)
        if refresh_token is None:
            return

        if self._ignore_system_tokens and refresh_token.token_type == TOKEN_TYPE_SYSTEM:
            return

        is_new_ip = remote_ip is not None and remote_ip not in self._known_ips
        if remote_ip is not None:
            self._known_ips.add(remote_ip)

        if self._new_ip_only and not is_new_ip:
            return

        user = refresh_token.user
        data = {
            "user_name": user.name if user else None,
            "user_id": user.id if user else None,
            "client_name": refresh_token.client_name,
            "token_type": refresh_token.token_type,
            "ip_address": remote_ip,
            "is_new_ip": is_new_ip,
        }
        _LOGGER.debug("Firing %s: %s", EVENT_LOGIN, data)
        self._hass.bus.async_fire(EVENT_LOGIN, data)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Login Monitor from a config entry."""
    monitor = LoginMonitor(
        hass,
        new_ip_only=_get_option(entry, CONF_NEW_IP_ONLY, DEFAULT_NEW_IP_ONLY),
        ignore_system_tokens=_get_option(
            entry, CONF_IGNORE_SYSTEM_TOKENS, DEFAULT_IGNORE_SYSTEM_TOKENS
        ),
    )
    await monitor.async_start()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = monitor
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and restore the original auth method."""
    monitor: LoginMonitor | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if monitor is not None:
        monitor.async_stop()
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
