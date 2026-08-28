"""Config flow for Tigo Energy Cloud."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TigoCloudClient
from .const import (
    CONF_NIGHT_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_STALE_AFTER,
    CONF_SYSTEM_ID,
    CONF_SYSTEM_NAME,
    CONF_TIME_ZONE,
    DEFAULT_NIGHT_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STALE_AFTER,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .exceptions import TigoApiError, TigoAuthError


def _system_id(system: Any) -> int:
    """Return a system identifier across API payload/model versions."""
    value = getattr(system, "system_id", None)
    if value is None:
        value = system.id
    return int(value)


def _system_name(system: Any) -> str:
    """Return a human-readable system name."""
    return str(getattr(system, "name", None) or f"Tigo system {_system_id(system)}")


def _system_timezone(system: Any) -> str:
    """Return the system timezone with a safe UTC fallback."""
    return str(
        getattr(system, "timezone", None)
        or getattr(system, "time_zone", None)
        or "UTC"
    )


class TigoEnergyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure a Tigo cloud system."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._credentials: dict[str, str] = {}
        self._systems: list[Any] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect credentials and discover systems."""
        errors: dict[str, str] = {}
        if user_input is not None:
            username = str(user_input[CONF_USERNAME]).strip()
            password = str(user_input[CONF_PASSWORD])
            try:
                systems = await self._async_get_systems(username, password)
            except TigoAuthError:
                errors["base"] = "invalid_auth"
            except TigoApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # Home Assistant requires an unknown fallback.
                errors["base"] = "unknown"
            else:
                if not systems:
                    errors["base"] = "no_systems"
                else:
                    self._credentials = {
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                    }
                    self._systems = systems
                    if len(systems) == 1:
                        return await self._async_create_system_entry(systems[0])
                    return await self.async_step_system()

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    async def async_step_system(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select one system when the account contains several."""
        if not self._systems or not self._credentials:
            return self.async_abort(reason="discovery_expired")

        choices = {
            str(_system_id(system)): _system_name(system) for system in self._systems
        }
        if user_input is not None:
            selected_id = int(user_input[CONF_SYSTEM_ID])
            selected = next(
                system for system in self._systems if _system_id(system) == selected_id
            )
            return await self._async_create_system_entry(selected)

        return self.async_show_form(
            step_id="system",
            data_schema=vol.Schema({vol.Required(CONF_SYSTEM_ID): vol.In(choices)}),
        )

    async def _async_get_systems(self, username: str, password: str) -> list[Any]:
        client = TigoCloudClient(
            async_get_clientsession(self.hass),
            username,
            password,
        )
        return list(await client.get_systems())

    async def _async_create_system_entry(self, system: Any) -> ConfigFlowResult:
        system_id = _system_id(system)
        await self.async_set_unique_id(str(system_id))
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=_system_name(system),
            data={
                **self._credentials,
                CONF_SYSTEM_ID: system_id,
                CONF_SYSTEM_NAME: _system_name(system),
                CONF_TIME_ZONE: _system_timezone(system),
            },
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start credential renewal for an existing system."""
        self._credentials = {
            CONF_USERNAME: str(entry_data.get(CONF_USERNAME, "")),
            CONF_PASSWORD: "",
        }
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate and save replacement credentials."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            username = str(user_input[CONF_USERNAME]).strip()
            password = str(user_input[CONF_PASSWORD])
            try:
                systems = await self._async_get_systems(username, password)
            except TigoAuthError:
                errors["base"] = "invalid_auth"
            except TigoApiError:
                errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"
            else:
                configured_id = int(entry.data[CONF_SYSTEM_ID])
                if not any(_system_id(system) == configured_id for system in systems):
                    errors["base"] = "system_not_found"
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={
                            CONF_USERNAME: username,
                            CONF_PASSWORD: password,
                        },
                    )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_USERNAME,
                    default=self._credentials.get(CONF_USERNAME, ""),
                ): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry: Any) -> OptionsFlow:
        """Return the options flow."""
        return TigoOptionsFlow()


class TigoOptionsFlow(OptionsFlow):
    """Configure polling and stale-data thresholds."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage integration options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(MIN_SCAN_INTERVAL, MAX_SCAN_INTERVAL),
                ),
                vol.Required(
                    CONF_NIGHT_SCAN_INTERVAL,
                    default=options.get(
                        CONF_NIGHT_SCAN_INTERVAL, DEFAULT_NIGHT_SCAN_INTERVAL
                    ),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(MIN_SCAN_INTERVAL, MAX_SCAN_INTERVAL),
                ),
                vol.Required(
                    CONF_STALE_AFTER,
                    default=options.get(CONF_STALE_AFTER, DEFAULT_STALE_AFTER),
                ): vol.All(vol.Coerce(int), vol.Range(900, 21600)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
