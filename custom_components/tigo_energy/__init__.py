"""Tigo Energy Cloud integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .coordinator import TigoCoordinator

    type TigoConfigEntry = ConfigEntry[TigoCoordinator]
else:
    # Keeping package import lightweight lets the pure API/parser test suite
    # run without bootstrapping all of Home Assistant. HA imports the runtime
    # modules inside setup below.
    TigoConfigEntry = Any


async def async_setup_entry(hass: HomeAssistant, entry: TigoConfigEntry) -> bool:
    """Set up Tigo Energy Cloud from a config entry."""
    from .const import PLATFORMS
    from .coordinator import TigoCoordinator

    coordinator = TigoCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TigoConfigEntry) -> bool:
    """Unload a Tigo Energy Cloud config entry."""
    from .const import PLATFORMS

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: TigoConfigEntry) -> None:
    """Reload an entry after its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
