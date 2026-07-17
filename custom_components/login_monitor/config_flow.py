"""Config flow for the Login Monitor integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

from .const import (
    CONF_GEO_LOOKUP,
    CONF_IGNORE_SYSTEM_TOKENS,
    DEFAULT_GEO_LOOKUP,
    DEFAULT_IGNORE_SYSTEM_TOKENS,
    DOMAIN,
)


class LoginMonitorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup. Single instance only."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the user step."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title="Login Monitor", data={})
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return LoginMonitorOptionsFlow()


class LoginMonitorOptionsFlow(OptionsFlow):
    """Handle Login Monitor options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_IGNORE_SYSTEM_TOKENS,
                    default=options.get(
                        CONF_IGNORE_SYSTEM_TOKENS, DEFAULT_IGNORE_SYSTEM_TOKENS
                    ),
                ): bool,
                vol.Required(
                    CONF_GEO_LOOKUP,
                    default=options.get(CONF_GEO_LOOKUP, DEFAULT_GEO_LOOKUP),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
