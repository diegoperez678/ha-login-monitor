"""Manual IP ban service for the Login Monitor integration.

Calls straight into Home Assistant's own ``ip_bans.yaml`` mechanism
(``homeassistant.components.http.ban``) — the same one HA's automatic
threshold-based banning uses. This bypasses the failed-attempt counter
entirely, so a ban only ever happens when explicitly requested, never
automatically.

Requires ``ip_ban_enabled: true`` in the ``http:`` config; without it, HA
never installs the ban-enforcement middleware, so there is nothing to add a
ban to. Un-banning is not exposed by this service — HA itself only supports
that via manually editing ``ip_bans.yaml`` and restarting.
"""

from __future__ import annotations

import logging
from ipaddress import ip_address

import voluptuous as vol
from homeassistant.components.http.ban import KEY_BAN_MANAGER
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import ATTR_IP_ADDRESS, DOMAIN, SERVICE_BAN_IP

_LOGGER = logging.getLogger(__name__)

SERVICE_BAN_IP_SCHEMA = vol.Schema({vol.Required(ATTR_IP_ADDRESS): cv.string})


async def _async_handle_ban_ip(hass: HomeAssistant, call: ServiceCall) -> None:
    """Ban a single IP address via HA's own ip_bans.yaml mechanism."""
    raw_ip = call.data[ATTR_IP_ADDRESS]
    try:
        remote_addr = ip_address(raw_ip)
    except ValueError as err:
        raise HomeAssistantError(f"'{raw_ip}' is not a valid IP address") from err

    ban_manager = hass.http.app.get(KEY_BAN_MANAGER)
    if ban_manager is None:
        raise HomeAssistantError(
            "Cannot ban IP: ip_ban_enabled is not active in the http: config "
            "(requires a full HA restart after enabling it)"
        )

    await ban_manager.async_add_ban(remote_addr)
    _LOGGER.warning("Login Monitor manually banned IP %s", remote_addr)


def async_register_ban_service(hass: HomeAssistant) -> None:
    """Register the login_monitor.ban_ip service (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_BAN_IP):
        return

    async def _handle(call: ServiceCall) -> None:
        await _async_handle_ban_ip(hass, call)

    hass.services.async_register(
        DOMAIN, SERVICE_BAN_IP, _handle, schema=SERVICE_BAN_IP_SCHEMA
    )


def async_unregister_ban_service(hass: HomeAssistant) -> None:
    """Remove the login_monitor.ban_ip service."""
    hass.services.async_remove(DOMAIN, SERVICE_BAN_IP)
