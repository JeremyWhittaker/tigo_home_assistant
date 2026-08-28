"""Home Assistant registry and entity tests for Tigo Energy Cloud."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.components.sensor import ATTR_STATE_CLASS, SensorStateClass
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tigo_energy.const import DOMAIN, INTEGRATION_VERSION


def entity_id_for(
    registry: er.EntityRegistry,
    platform: str,
    unique_id: str,
) -> str:
    """Resolve an entity through its stable integration unique ID."""

    entity_id = registry.async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity_id is not None
    return entity_id


def state_for(hass: HomeAssistant, entity_id: str) -> State:
    """Return an existing state with a useful assertion on failure."""

    state = hass.states.get(entity_id)
    assert state is not None
    return state


def fresh_day_data(system_info_factory, snapshot_factory):
    """Return metadata and a snapshot that are fresh regardless of test time."""

    now = datetime.now(UTC)
    local_day = now.astimezone(ZoneInfo("America/Phoenix")).date()
    info = system_info_factory(day=local_day, sunrise=time.min, sunset=time.max)
    return info, snapshot_factory(last_update=now - timedelta(minutes=10))


async def setup_integration(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fake_client: MagicMock,
    topology,
    info,
    snapshot,
) -> None:
    """Set up the real platforms with an injected cloud client."""

    fake_client.get_topology.return_value = topology
    fake_client.get_system_info.return_value = info
    fake_client.get_snapshot.return_value = snapshot
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.tigo_energy.coordinator.async_get_clientsession",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.tigo_energy.coordinator.TigoCloudClient",
            return_value=fake_client,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_setup_registers_stable_entities_and_correct_state_metadata(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_cloud_client: MagicMock,
    tigo_topology,
    system_info_factory,
    snapshot_factory,
) -> None:
    info, snapshot = fresh_day_data(system_info_factory, snapshot_factory)
    await setup_integration(
        hass,
        config_entry,
        fake_cloud_client,
        tigo_topology,
        info,
        snapshot,
    )
    registry = er.async_get(hass)
    entry_entities = er.async_entries_for_config_entry(registry, config_entry.entry_id)

    # 14 system sensors + 2 sensors for each of 2 modules + 2 health sensors.
    assert len(entry_entities) == 20
    assert len({entity.unique_id for entity in entry_entities}) == 20

    current_id = entity_id_for(registry, "sensor", "1_current_power")
    current = state_for(hass, current_id)
    assert current.state == "2400.0"
    assert current.attributes[ATTR_DEVICE_CLASS] == "power"
    assert current.attributes[ATTR_STATE_CLASS] == SensorStateClass.MEASUREMENT
    assert current.attributes[ATTR_UNIT_OF_MEASUREMENT] == UnitOfPower.WATT
    assert current.attributes["cloud_data_delayed"] is False

    lifetime_id = entity_id_for(registry, "sensor", "1_energy_lifetime")
    lifetime = state_for(hass, lifetime_id)
    assert lifetime.state == "24500.125"
    assert lifetime.attributes[ATTR_STATE_CLASS] == SensorStateClass.TOTAL
    assert lifetime.attributes[ATTR_UNIT_OF_MEASUREMENT] == UnitOfEnergy.KILO_WATT_HOUR

    today_id = entity_id_for(registry, "sensor", "1_energy_today")
    assert (
        state_for(hass, today_id).attributes[ATTR_STATE_CLASS]
        == SensorStateClass.TOTAL_INCREASING
    )

    account_id = entity_id_for(registry, "sensor", "1_account_tier")
    account = state_for(hass, account_id)
    assert account.state == "basic"
    assert registry.async_get(account_id).entity_category is EntityCategory.DIAGNOSTIC
    assert account.attributes["features"] == ["Display.energy", "Display.pin"]

    module_count_id = entity_id_for(registry, "sensor", "1_module_count")
    module_count = state_for(hass, module_count_id)
    assert module_count.state == "2"
    assert module_count.attributes["inverter_count"] == 1
    assert module_count.attributes["mppt_count"] == 1
    assert module_count.attributes["string_count"] == 1

    interval_id = entity_id_for(registry, "sensor", "1_polling_interval")
    version_id = entity_id_for(registry, "sensor", "1_integration_version")
    assert state_for(hass, interval_id).state == "5.0"
    assert state_for(hass, version_id).state == INTEGRATION_VERSION

    module_power_id = entity_id_for(registry, "sensor", "1_module_101_power")
    module_energy_id = entity_id_for(registry, "sensor", "1_module_101_energy_today")
    missing_energy_id = entity_id_for(registry, "sensor", "1_module_102_energy_today")
    module_power = state_for(hass, module_power_id)
    assert module_power.state == "110.0"
    assert module_power.attributes[ATTR_STATE_CLASS] == SensorStateClass.MEASUREMENT
    assert module_power.attributes["string_label"] == "String A"
    assert state_for(hass, module_energy_id).state == "1.2"
    assert (
        state_for(hass, module_energy_id).attributes[ATTR_STATE_CLASS]
        == SensorStateClass.TOTAL_INCREASING
    )
    assert state_for(hass, missing_energy_id).state == STATE_UNAVAILABLE

    connected_id = entity_id_for(registry, "binary_sensor", "1_cloud_connected")
    stale_id = entity_id_for(registry, "binary_sensor", "1_data_stale")
    assert state_for(hass, connected_id).state == STATE_ON
    assert state_for(hass, stale_id).state == STATE_OFF


@pytest.mark.asyncio
async def test_module_devices_are_linked_through_system_device(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_cloud_client: MagicMock,
    tigo_topology,
    system_info_factory,
    snapshot_factory,
) -> None:
    info, snapshot = fresh_day_data(system_info_factory, snapshot_factory)
    await setup_integration(
        hass,
        config_entry,
        fake_cloud_client,
        tigo_topology,
        info,
        snapshot,
    )
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    system_device = devices.async_get_device(identifiers={(DOMAIN, "1")})
    module_device = devices.async_get_device(identifiers={(DOMAIN, "1_module_101")})

    assert system_device is not None
    assert system_device.name == "Example Solar Array"
    assert system_device.manufacturer == "Tigo Energy"
    assert system_device.configuration_url.endswith("sysid=1")
    assert module_device is not None
    assert module_device.name == "A1"
    assert module_device.via_device_id == system_device.id
    module_power_id = entity_id_for(entities, "sensor", "1_module_101_power")
    assert entities.async_get(module_power_id).device_id == module_device.id


@pytest.mark.asyncio
async def test_stale_data_disables_module_power_but_preserves_daily_energy(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_cloud_client: MagicMock,
    tigo_topology,
    system_info_factory,
    snapshot_factory,
) -> None:
    info, snapshot = fresh_day_data(system_info_factory, snapshot_factory)
    await setup_integration(
        hass,
        config_entry,
        fake_cloud_client,
        tigo_topology,
        info,
        snapshot,
    )
    coordinator = config_entry.runtime_data
    coordinator.async_set_updated_data(replace(coordinator.data, is_stale=True))
    await hass.async_block_till_done()
    registry = er.async_get(hass)

    assert (
        state_for(hass, entity_id_for(registry, "sensor", "1_module_101_power")).state
        == STATE_UNAVAILABLE
    )
    assert (
        state_for(
            hass,
            entity_id_for(registry, "sensor", "1_module_101_energy_today"),
        ).state
        == "1.2"
    )
    assert (
        state_for(hass, entity_id_for(registry, "binary_sensor", "1_data_stale")).state
        == STATE_ON
    )


@pytest.mark.asyncio
async def test_update_failure_keeps_health_entities_visible(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_cloud_client: MagicMock,
    tigo_topology,
    system_info_factory,
    snapshot_factory,
) -> None:
    info, snapshot = fresh_day_data(system_info_factory, snapshot_factory)
    await setup_integration(
        hass,
        config_entry,
        fake_cloud_client,
        tigo_topology,
        info,
        snapshot,
    )
    coordinator = config_entry.runtime_data
    coordinator.async_set_update_error(UpdateFailed("offline"))
    await hass.async_block_till_done()
    registry = er.async_get(hass)

    connected_id = entity_id_for(registry, "binary_sensor", "1_cloud_connected")
    stale_id = entity_id_for(registry, "binary_sensor", "1_data_stale")
    module_id = entity_id_for(registry, "sensor", "1_module_101_power")
    assert state_for(hass, connected_id).state == STATE_OFF
    assert state_for(hass, connected_id).state != STATE_UNAVAILABLE
    assert state_for(hass, stale_id).state != STATE_UNAVAILABLE
    assert state_for(hass, module_id).state == STATE_UNAVAILABLE
