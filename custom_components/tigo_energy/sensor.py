"""Sensors for Tigo Energy Cloud."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import TigoConfigEntry
from .const import (
    ATTRIBUTION,
    CONFIGURATION_URL,
    DOMAIN,
    INTEGRATION_VERSION,
    MANUFACTURER,
    MODEL,
)
from .coordinator import TigoCoordinator, TigoCoordinatorData
from .models import Module


@dataclass(frozen=True, kw_only=True)
class TigoSensorDescription(SensorEntityDescription):
    """Describe a system sensor and how to read it."""

    value_fn: Callable[[TigoCoordinatorData], StateType | datetime]


SYSTEM_SENSORS: tuple[TigoSensorDescription, ...] = (
    TigoSensorDescription(
        key="current_power",
        translation_key="current_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=lambda data: _snapshot_value(data, "current_power_w"),
    ),
    TigoSensorDescription(
        key="peak_power_today",
        translation_key="peak_power_today",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=lambda data: _snapshot_value(data, "peak_power_today_w"),
    ),
    *tuple(
        TigoSensorDescription(
            key=key,
            translation_key=key,
            device_class=SensorDeviceClass.ENERGY,
            state_class=(
                SensorStateClass.TOTAL
                if key == "energy_lifetime"
                else SensorStateClass.TOTAL_INCREASING
            ),
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            suggested_display_precision=2,
            value_fn=lambda data, field=field: _snapshot_value(data, field),
        )
        for key, field in (
            ("energy_today", "energy_today_kwh"),
            ("energy_week", "energy_week_kwh"),
            ("energy_month", "energy_month_kwh"),
            ("energy_year", "energy_year_kwh"),
            ("energy_lifetime", "energy_lifetime_kwh"),
        )
    ),
    TigoSensorDescription(
        key="reporting_modules",
        translation_key="reporting_modules",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="modules",
        suggested_display_precision=0,
        value_fn=lambda data: _snapshot_value(data, "reporting_modules"),
    ),
    TigoSensorDescription(
        key="last_cloud_update",
        translation_key="last_cloud_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.last_cloud_update,
    ),
    TigoSensorDescription(
        key="cloud_data_age",
        translation_key="cloud_data_age",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=1,
        value_fn=lambda data: data.data_age_minutes,
    ),
    TigoSensorDescription(
        key="account_tier",
        translation_key="account_tier",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: "premium" if data.system_info.has_premium else "basic",
    ),
    TigoSensorDescription(
        key="module_count",
        translation_key="module_count",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="modules",
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=0,
        value_fn=lambda data: len(data.topology.modules),
    ),
    TigoSensorDescription(
        key="polling_interval",
        translation_key="polling_interval",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=0,
        value_fn=lambda data: data.poll_interval_minutes,
    ),
    TigoSensorDescription(
        key="integration_version",
        translation_key="integration_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: INTEGRATION_VERSION,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TigoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up system and module sensors."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        TigoSystemSensor(coordinator, description) for description in SYSTEM_SENSORS
    ]
    known_module_ids: set[str] = set()
    module_metadata: dict[str, tuple[str, str | None, str, str, str]] = {}

    def module_entities(module: Module) -> tuple[TigoModuleSensor, TigoModuleSensor]:
        object_id = _module_object_id(module)
        known_module_ids.add(object_id)
        module_metadata[object_id] = _module_metadata(module)
        return (
            TigoModuleSensor(coordinator, module, "power"),
            TigoModuleSensor(coordinator, module, "energy_today"),
        )

    for module in coordinator.data.topology.modules:
        entities.extend(module_entities(module))
    async_add_entities(entities)

    @callback
    def async_reconcile_modules() -> None:
        """Add newly discovered modules and refresh changed device metadata."""
        new_entities: list[TigoModuleSensor] = []
        device_registry = dr.async_get(hass)
        for module in coordinator.data.topology.modules:
            object_id = _module_object_id(module)
            current_metadata = _module_metadata(module)
            if object_id not in known_module_ids:
                new_entities.extend(module_entities(module))
                continue
            if module_metadata.get(object_id) == current_metadata:
                continue
            module_metadata[object_id] = current_metadata
            device = device_registry.async_get_device(
                identifiers={(DOMAIN, f"{coordinator.system_id}_module_{object_id}")}
            )
            if device is not None:
                # User-assigned names and areas remain authoritative. Updating
                # integration-owned metadata keeps topology changes visible.
                device_registry.async_update_device(
                    device.id,
                    name=_module_label(module),
                    model=str(module.model or "TS4 Optimizer"),
                )
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(async_reconcile_modules))


class TigoSystemSensor(CoordinatorEntity[TigoCoordinator], SensorEntity):
    """A system-level Tigo sensor."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(
        self, coordinator: TigoCoordinator, description: TigoSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.system_id}_{description.key}"
        self._attr_device_info = _system_device_info(coordinator)

    @property
    def native_value(self) -> StateType | datetime:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        attributes: dict[str, Any] = {
            "cloud_sample_time": (
                data.last_cloud_update.isoformat() if data.last_cloud_update else None
            ),
            "cloud_data_delayed": data.is_stale,
            "data_source": "Tigo mobile cloud API",
        }
        if self.entity_description.key == "module_count":
            attributes.update(
                {
                    "inverter_count": data.topology.inverter_count,
                    "mppt_count": data.topology.mppt_count,
                    "string_count": data.topology.string_count,
                }
            )
        if self.entity_description.key == "account_tier":
            attributes["features"] = sorted(data.system_info.features)
        return attributes


class TigoModuleSensor(CoordinatorEntity[TigoCoordinator], SensorEntity):
    """Power or daily energy for one optimizer/module."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: TigoCoordinator, module: Module, kind: str) -> None:
        super().__init__(coordinator)
        self._object_id = _module_object_id(module)
        self._fallback_module = module
        self.kind = kind
        self._attr_unique_id = (
            f"{coordinator.system_id}_module_{self._object_id}_{kind}"
        )
        self._attr_translation_key = (
            "module_power" if kind == "power" else "module_energy_today"
        )
        self._attr_device_class = (
            SensorDeviceClass.POWER if kind == "power" else SensorDeviceClass.ENERGY
        )
        self._attr_state_class = (
            SensorStateClass.MEASUREMENT
            if kind == "power"
            else SensorStateClass.TOTAL_INCREASING
        )
        self._attr_native_unit_of_measurement = (
            UnitOfPower.WATT if kind == "power" else UnitOfEnergy.KILO_WATT_HOUR
        )
        self._attr_suggested_display_precision = 0 if kind == "power" else 2
        self._attr_device_info = _module_device_info(coordinator, module)

    @property
    def module(self) -> Module:
        """Return current topology metadata while retaining a removal fallback."""
        return self.coordinator.data.topology.by_object_id.get(
            self._object_id, self._fallback_module
        )

    @property
    def available(self) -> bool:
        reading = _module_reading(self.coordinator.data, self._object_id)
        value = _reading_value(reading, self.kind)
        if self.kind == "power" and self.coordinator.data.is_stale:
            return False
        return super().available and value is not None

    @property
    def native_value(self) -> StateType:
        reading = _module_reading(self.coordinator.data, self._object_id)
        return _reading_value(reading, self.kind)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        module = self.module
        reading = _module_reading(self.coordinator.data, self._object_id)
        timestamp_field = (
            "sample_time" if self.kind == "power" else "energy_sample_time"
        )
        sample_time = getattr(reading, timestamp_field, None) if reading else None
        return {
            "panel_label": _module_label(module),
            "inverter_label": module.inverter_label,
            "mppt_label": module.mppt_label,
            "string_label": module.string_label,
            "sample_time": (
                sample_time.isoformat()
                if isinstance(sample_time, datetime)
                else sample_time
            ),
        }


def _snapshot_value(data: TigoCoordinatorData, field: str) -> StateType:
    return getattr(data.snapshot, field, None)


def _module_reading(data: TigoCoordinatorData, object_id: str) -> Any | None:
    readings = getattr(data.snapshot, "modules", ()) or ()
    return next(
        (
            reading
            for reading in readings
            if str(getattr(reading, "object_id", "")) == object_id
        ),
        None,
    )


def _reading_value(reading: Any | None, kind: str) -> StateType:
    if reading is None:
        return None
    return getattr(
        reading,
        "power_w" if kind == "power" else "energy_today_kwh",
        None,
    )


def _module_object_id(module: Any) -> str:
    return str(module.object_id)


def _module_label(module: Any) -> str:
    return str(getattr(module, "label", None) or f"Module {_module_object_id(module)}")


def _module_metadata(module: Module) -> tuple[str, str | None, str, str, str]:
    """Return integration-owned metadata used to detect topology changes."""
    return (
        _module_label(module),
        module.model,
        module.inverter_label,
        module.mppt_label,
        module.string_label,
    )


def _system_device_info(coordinator: TigoCoordinator) -> DeviceInfo:
    system = coordinator.data.topology.system
    return DeviceInfo(
        identifiers={(DOMAIN, str(coordinator.system_id))},
        name=str(getattr(system, "name", None) or coordinator.config_entry.title),
        manufacturer=MANUFACTURER,
        model=MODEL,
        configuration_url=CONFIGURATION_URL.format(coordinator.system_id),
    )


def _module_device_info(coordinator: TigoCoordinator, module: Any) -> DeviceInfo:
    object_id = _module_object_id(module)
    info = DeviceInfo(
        identifiers={(DOMAIN, f"{coordinator.system_id}_module_{object_id}")},
        name=_module_label(module),
        manufacturer=MANUFACTURER,
        model=str(getattr(module, "model", None) or "TS4 Optimizer"),
        via_device=(DOMAIN, str(coordinator.system_id)),
    )
    string_label = getattr(module, "string_label", None)
    if string_label:
        info["suggested_area"] = str(string_label)
    return info
