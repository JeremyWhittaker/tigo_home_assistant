"""Diagnostic binary sensors for Tigo Energy Cloud."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import TigoConfigEntry
from .const import ATTRIBUTION
from .coordinator import TigoCoordinator
from .sensor import _system_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TigoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up cloud health sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        (
            TigoCloudConnectedBinarySensor(coordinator),
            TigoDataStaleBinarySensor(coordinator),
        )
    )


class _TigoDiagnosticBinarySensor(
    CoordinatorEntity[TigoCoordinator], BinarySensorEntity
):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: TigoCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.system_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = _system_device_info(coordinator)

    @property
    def available(self) -> bool:
        """Health entities must remain visible when updates fail."""
        return True


class TigoCloudConnectedBinarySensor(_TigoDiagnosticBinarySensor):
    """Whether the latest API update succeeded."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: TigoCoordinator) -> None:
        super().__init__(coordinator, "cloud_connected")

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success


class TigoDataStaleBinarySensor(_TigoDiagnosticBinarySensor):
    """Whether source samples are unexpectedly old during daylight."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: TigoCoordinator) -> None:
        super().__init__(coordinator, "data_stale")

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        return data.is_stale if data else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        data = self.coordinator.data
        return {
            "daylight": data.is_daylight if data else None,
            "age_minutes": data.data_age_minutes if data else None,
        }
