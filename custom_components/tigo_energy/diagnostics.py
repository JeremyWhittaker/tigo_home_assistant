"""Diagnostics support for Tigo Energy Cloud."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import TigoConfigEntry

TO_REDACT = {
    "username",
    "password",
    "token",
    "auth",
    "refresh_token",
    "cca_uid",
    "system_id",
    "system_name",
    "time_zone",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TigoConfigEntry
) -> dict[str, Any]:
    """Return privacy-preserving integration diagnostics."""
    coordinator = entry.runtime_data
    data = coordinator.data
    topology = data.topology if data else None
    snapshot = data.snapshot if data else None

    client_diagnostics: dict[str, Any] = {}
    if hasattr(coordinator.client, "diagnostics"):
        value = coordinator.client.diagnostics
        client_diagnostics = value() if callable(value) else value

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_exception_type": (
                type(coordinator.last_exception).__name__
                if coordinator.last_exception
                else None
            ),
            "data_age_minutes": data.data_age_minutes if data else None,
            "is_daylight": data.is_daylight if data else None,
            "is_stale": data.is_stale if data else None,
            "last_cloud_update": (
                data.last_cloud_update.isoformat()
                if data and data.last_cloud_update
                else None
            ),
        },
        "topology": {
            "module_count": len(topology.modules) if topology else 0,
            "inverter_count": getattr(topology, "inverter_count", None),
            "string_count": getattr(topology, "string_count", None),
        },
        "snapshot": {
            "reporting_module_count": (
                getattr(snapshot, "reporting_modules", None) if snapshot else None
            ),
            "has_current_power": bool(
                snapshot and getattr(snapshot, "current_power_w", None) is not None
            ),
            "has_lifetime_energy": bool(
                snapshot and getattr(snapshot, "energy_lifetime_kwh", None) is not None
            ),
        },
        "client": async_redact_data(client_diagnostics, TO_REDACT),
    }
