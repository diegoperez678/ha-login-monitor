"""Login Monitor: fire a bus event on successful Home Assistant logins.

Home Assistant does not emit an event when a login succeeds. This integration
watches the refresh-token table (a public, supported API) and fires an event
whenever a token's ``last_used_at`` advances, which corresponds to a successful
authenticated access. Each event carries the user, source IP, client, and a
flag indicating whether the IP has been seen before.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_IGNORE_SYSTEM_TOKENS,
    CONF_NEW_IP_ONLY,
    CONF_SCAN_INTERVAL,
    DEFAULT_IGNORE_SYSTEM_TOKENS,
    DEFAULT_NEW_IP_ONLY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_LOGIN,
)

_LOGGER = logging.getLogger(__name__)

# System tokens are minted for internal integrations and are used constantly;
# they are not "logins" in any meaningful sense.
TOKEN_TYPE_SYSTEM = "system"


def _get_option(entry: ConfigEntry, key: str, default):
    """Read a value from entry options, falling back to data, then default."""
    if key in entry.options:
        return entry.options[key]
    return entry.data.get(key, default)


class LoginMonitor:
    """Polls refresh tokens and fires an event on new authenticated access."""

    def __init__(
        self,
        hass: HomeAssistant,
        scan_interval: int,
        new_ip_only: bool,
        ignore_system_tokens: bool,
    ) -> None:
        """Initialize the monitor."""
        self._hass = hass
        self._scan_interval = timedelta(seconds=scan_interval)
        self._new_ip_only = new_ip_only
        self._ignore_system_tokens = ignore_system_tokens
        # token_id -> last_used_at (datetime) already observed.
        self._seen: dict[str, datetime] = {}
        # Every IP we have ever observed, so we can flag genuinely new ones.
        self._known_ips: set[str] = set()
        self._cancel = None

    async def async_start(self) -> None:
        """Seed a baseline (without firing) and start the polling loop."""
        await self._async_collect(fire=False)
        self._cancel = async_track_time_interval(
            self._hass, self._async_tick, self._scan_interval
        )
        _LOGGER.debug(
            "Login Monitor started (interval=%s, new_ip_only=%s, tokens_seeded=%s)",
            self._scan_interval,
            self._new_ip_only,
            len(self._seen),
        )

    @callback
    def async_stop(self) -> None:
        """Stop the polling loop."""
        if self._cancel is not None:
            self._cancel()
            self._cancel = None

    async def _async_tick(self, _now) -> None:
        """Scheduled poll; never let an error kill the interval."""
        try:
            await self._async_collect(fire=True)
        except Exception:  # noqa: BLE001 - defensive: keep the loop alive
            _LOGGER.exception("Login Monitor poll failed")

    async def _async_collect(self, fire: bool) -> None:
        """Scan all refresh tokens and optionally fire events for new usage."""
        users = await self._hass.auth.async_get_users()
        live_token_ids: set[str] = set()

        for user in users:
            for token in user.refresh_tokens.values():
                if self._ignore_system_tokens and token.token_type == TOKEN_TYPE_SYSTEM:
                    continue

                token_id = token.id
                live_token_ids.add(token_id)
                last_used = token.last_used_at
                if last_used is None:
                    continue

                previous = self._seen.get(token_id)
                advanced = previous is None or last_used > previous

                if not advanced:
                    continue

                ip_address = token.last_used_ip
                is_new_ip = ip_address is not None and ip_address not in self._known_ips

                # Record state regardless of whether we notify.
                self._seen[token_id] = last_used
                if ip_address is not None:
                    self._known_ips.add(ip_address)

                if not fire:
                    continue

                if self._new_ip_only and not is_new_ip:
                    continue

                self._fire_event(user, token, ip_address, is_new_ip, last_used)

        # Forget tokens that were revoked so re-created ids can fire again.
        for stale_id in self._seen.keys() - live_token_ids:
            del self._seen[stale_id]

    @callback
    def _fire_event(
        self, user, token, ip_address, is_new_ip: bool, last_used: datetime
    ) -> None:
        """Fire the login event on the bus."""
        data = {
            "user_name": user.name,
            "user_id": user.id,
            "client_name": token.client_name,
            "token_type": token.token_type,
            "ip_address": ip_address,
            "is_new_ip": is_new_ip,
            "last_used_at": last_used.isoformat(),
        }
        _LOGGER.debug("Firing %s: %s", EVENT_LOGIN, data)
        self._hass.bus.async_fire(EVENT_LOGIN, data)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Login Monitor from a config entry."""
    monitor = LoginMonitor(
        hass,
        scan_interval=_get_option(entry, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
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
    """Unload a config entry."""
    monitor: LoginMonitor | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if monitor is not None:
        monitor.async_stop()
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
