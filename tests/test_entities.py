"""Home Assistant registry and entity tests for Tigo Energy Cloud."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from freezegun import freeze_time
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
from custom_components.tigo_energy.exceptions import TigoConnectionError
from custom_components.tigo_energy.models import PanelReading


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
    rated_topology = replace(
        tigo_topology,
        modules=tuple(
            replace(module, rated_power_w=400.0) for module in tigo_topology.modules
        ),
        rated_power_w=800.0,
    )
    await setup_integration(
        hass,
        config_entry,
        fake_cloud_client,
        rated_topology,
        info,
        snapshot,
    )
    registry = er.async_get(hass)
    entry_entities = er.async_entries_for_config_entry(registry, config_entry.entry_id)

    # 15 system sensors + 2 sensors for each of 2 modules + 2 health sensors.
    assert len(entry_entities) == 21
    assert len({entity.unique_id for entity in entry_entities}) == 21

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

    capacity_id = entity_id_for(registry, "sensor", "1_rated_array_power")
    capacity = state_for(hass, capacity_id)
    assert capacity.state == "800.0"
    assert capacity.attributes[ATTR_DEVICE_CLASS] == "power"
    assert capacity.attributes[ATTR_UNIT_OF_MEASUREMENT] == UnitOfPower.WATT
    assert capacity.attributes["rated_modules"] == 2
    assert registry.async_get(capacity_id).entity_category is EntityCategory.DIAGNOSTIC

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
async def test_topology_refresh_adds_new_module_entities_once(
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
    new_module = replace(
        tigo_topology.modules[0],
        object_id="103",
        label="B1",
        serial="SANITIZED-MODULE-103",
        equipment_id="B1",
    )
    changed_topology = replace(
        tigo_topology,
        modules=(*tigo_topology.modules, new_module),
        signature="topology-with-module-103",
    )
    changed_snapshot = replace(
        snapshot,
        modules=(
            *snapshot.modules,
            PanelReading(
                module=new_module,
                power_w=105.0,
                sample_time=snapshot.last_update,
                energy_today_kwh=1.1,
                energy_sample_time=snapshot.last_update,
            ),
        ),
        reporting_modules=3,
    )

    coordinator.async_set_updated_data(
        replace(
            coordinator.data,
            topology=changed_topology,
            snapshot=changed_snapshot,
        )
    )
    await hass.async_block_till_done()
    registry = er.async_get(hass)

    assert (
        state_for(
            hass,
            entity_id_for(registry, "sensor", "1_module_103_power"),
        ).state
        == "105.0"
    )
    assert (
        state_for(
            hass,
            entity_id_for(registry, "sensor", "1_module_103_energy_today"),
        ).state
        == "1.1"
    )
    assert len(er.async_entries_for_config_entry(registry, config_entry.entry_id)) == 23

    coordinator.async_set_updated_data(coordinator.data)
    await hass.async_block_till_done()
    assert len(er.async_entries_for_config_entry(registry, config_entry.entry_id)) == 23


@pytest.mark.asyncio
async def test_topology_refresh_updates_existing_module_metadata(
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
    original = tigo_topology.by_object_id["101"]
    renamed = replace(
        original,
        label="North roof A1",
        model="TS4-A-O",
        string_label="North string",
    )
    changed_topology = replace(
        tigo_topology,
        modules=tuple(
            renamed if module.object_id == "101" else module
            for module in tigo_topology.modules
        ),
        signature="renamed-module-101",
    )

    coordinator.async_set_updated_data(
        replace(coordinator.data, topology=changed_topology)
    )
    await hass.async_block_till_done()
    entities = er.async_get(hass)
    module_power = state_for(
        hass,
        entity_id_for(entities, "sensor", "1_module_101_power"),
    )
    assert module_power.attributes["panel_label"] == "North roof A1"
    assert module_power.attributes["string_label"] == "North string"

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "1_module_101")})
    assert device is not None
    assert device.name == "North roof A1"
    assert device.model == "TS4-A-O"


@pytest.mark.asyncio
async def test_module_energy_uses_its_own_sample_timestamp(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_cloud_client: MagicMock,
    tigo_topology,
    system_info_factory,
    snapshot_factory,
) -> None:
    info, snapshot = fresh_day_data(system_info_factory, snapshot_factory)
    power_time = datetime(2026, 8, 28, 18, 0, tzinfo=UTC)
    energy_time = datetime(2026, 8, 28, 18, 15, tzinfo=UTC)
    changed_readings = tuple(
        replace(
            reading,
            sample_time=power_time,
            energy_sample_time=energy_time,
        )
        if reading.object_id == "101"
        else reading
        for reading in snapshot.modules
    )
    snapshot = replace(snapshot, modules=changed_readings)
    await setup_integration(
        hass,
        config_entry,
        fake_cloud_client,
        tigo_topology,
        info,
        snapshot,
    )
    registry = er.async_get(hass)

    power = state_for(
        hass,
        entity_id_for(registry, "sensor", "1_module_101_power"),
    )
    energy = state_for(
        hass,
        entity_id_for(registry, "sensor", "1_module_101_energy_today"),
    )
    assert power.attributes["sample_time"] == power_time.isoformat()
    assert energy.attributes["sample_time"] == energy_time.isoformat()


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


@pytest.mark.asyncio
async def test_consecutive_failures_publish_advancing_age_and_stale_state(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_cloud_client: MagicMock,
    tigo_topology,
    system_info_factory,
    snapshot_factory,
) -> None:
    snapshot = snapshot_factory(last_update=datetime(2026, 8, 28, 17, 50, tzinfo=UTC))
    info = system_info_factory(day=datetime(2026, 8, 28, tzinfo=UTC).date())
    with freeze_time("2026-08-28 18:00:00+00:00"):
        await setup_integration(
            hass,
            config_entry,
            fake_cloud_client,
            tigo_topology,
            info,
            snapshot,
        )

    coordinator = config_entry.runtime_data
    fake_cloud_client.get_snapshot.side_effect = TigoConnectionError("offline")
    registry = er.async_get(hass)
    connected_id = entity_id_for(registry, "binary_sensor", "1_cloud_connected")
    stale_id = entity_id_for(registry, "binary_sensor", "1_data_stale")
    age_id = entity_id_for(registry, "sensor", "1_cloud_data_age")

    with freeze_time("2026-08-28 18:30:00+00:00"):
        await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert state_for(hass, connected_id).state == STATE_OFF
    assert state_for(hass, stale_id).state == STATE_OFF
    assert state_for(hass, age_id).state == "40.0"

    with freeze_time("2026-08-28 18:40:00+00:00"):
        await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert state_for(hass, connected_id).state == STATE_OFF
    assert state_for(hass, stale_id).state == STATE_ON
    assert state_for(hass, stale_id).attributes["age_minutes"] == 50.0
    assert state_for(hass, age_id).state == "50.0"
