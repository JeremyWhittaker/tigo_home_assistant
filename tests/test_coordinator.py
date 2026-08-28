"""Home Assistant coordinator behavior tests for Tigo Energy Cloud."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import MagicMock, patch

import pytest
from freezegun import freeze_time
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tigo_energy.const import (
    CONF_NIGHT_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_STALE_AFTER,
)
from custom_components.tigo_energy.coordinator import TigoCoordinator, _is_daylight
from custom_components.tigo_energy.exceptions import (
    TigoAuthenticationError,
    TigoConnectionError,
    TigoRateLimitError,
)


def make_coordinator(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fake_client: MagicMock,
) -> TigoCoordinator:
    """Construct a coordinator without opening a real aiohttp session."""

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
        return TigoCoordinator(hass, entry)


@pytest.mark.asyncio
@freeze_time("2026-08-28 18:00:00+00:00")
async def test_daylight_update_computes_age_and_caches_static_data(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_cloud_client: MagicMock,
    tigo_topology,
    system_info_factory,
    snapshot_factory,
) -> None:
    info = system_info_factory(day=date(2026, 8, 28))
    snapshot = snapshot_factory(last_update=datetime(2026, 8, 28, 17, 50, tzinfo=UTC))
    fake_cloud_client.get_topology.return_value = tigo_topology
    fake_cloud_client.get_system_info.return_value = info
    fake_cloud_client.get_snapshot.return_value = snapshot
    coordinator = make_coordinator(hass, config_entry, fake_cloud_client)

    first = await coordinator._async_update_data()
    second = await coordinator._async_update_data()

    assert first.is_daylight is True
    assert first.is_stale is False
    assert first.data_age_minutes == 10.0
    assert first.poll_interval_minutes == 5.0
    assert coordinator.update_interval == timedelta(seconds=300)
    assert second.topology is first.topology
    assert fake_cloud_client.get_topology.await_count == 1
    assert fake_cloud_client.get_system_info.await_count == 1
    assert fake_cloud_client.get_snapshot.await_count == 2
    snapshot_call = fake_cloud_client.get_snapshot.await_args
    assert snapshot_call.args == (1, date(2026, 8, 28))
    assert snapshot_call.kwargs["topology"] is tigo_topology
    assert snapshot_call.kwargs["cca_uid"] == "REDACTED-CCA"
    assert str(snapshot_call.kwargs["system_timezone"]) == "America/Phoenix"


@pytest.mark.asyncio
@freeze_time("2026-08-29 06:00:00+00:00")
async def test_night_update_uses_slow_interval_without_stale_problem(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_cloud_client: MagicMock,
    tigo_topology,
    system_info_factory,
    snapshot_factory,
) -> None:
    # 06:00 UTC is 23:00 on the preceding Phoenix local day.
    local_day = date(2026, 8, 28)
    fake_cloud_client.get_topology.return_value = tigo_topology
    fake_cloud_client.get_system_info.return_value = system_info_factory(day=local_day)
    fake_cloud_client.get_snapshot.return_value = snapshot_factory(
        last_update=datetime(2026, 8, 29, 2, 0, tzinfo=UTC)
    )
    coordinator = make_coordinator(hass, config_entry, fake_cloud_client)

    result = await coordinator._async_update_data()

    assert result.is_daylight is False
    assert result.is_stale is False
    assert result.data_age_minutes == 240.0
    assert result.poll_interval_minutes == 30.0
    assert coordinator.update_interval == timedelta(seconds=1800)
    fake_cloud_client.get_system_info.assert_awaited_once_with(1, local_day)


@pytest.mark.asyncio
@freeze_time("2026-08-28 18:00:00+00:00")
async def test_old_daylight_sample_is_marked_stale(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_cloud_client: MagicMock,
    tigo_topology,
    system_info_factory,
    snapshot_factory,
) -> None:
    fake_cloud_client.get_topology.return_value = tigo_topology
    fake_cloud_client.get_system_info.return_value = system_info_factory(
        day=date(2026, 8, 28)
    )
    fake_cloud_client.get_snapshot.return_value = snapshot_factory(
        last_update=datetime(2026, 8, 28, 17, 0, tzinfo=UTC)
    )
    coordinator = make_coordinator(hass, config_entry, fake_cloud_client)

    result = await coordinator._async_update_data()

    assert result.is_daylight is True
    assert result.is_stale is True
    assert result.data_age_minutes == 60.0


@pytest.mark.asyncio
@freeze_time("2026-08-28 18:00:00+00:00")
async def test_options_control_day_night_and_stale_thresholds(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_cloud_client: MagicMock,
    tigo_topology,
    system_info_factory,
    snapshot_factory,
) -> None:
    object.__setattr__(
        config_entry,
        "options",
        {
            CONF_SCAN_INTERVAL: 240,
            CONF_NIGHT_SCAN_INTERVAL: 1200,
            CONF_STALE_AFTER: 3600,
        },
    )
    fake_cloud_client.get_topology.return_value = tigo_topology
    fake_cloud_client.get_system_info.return_value = system_info_factory(
        day=date(2026, 8, 28)
    )
    fake_cloud_client.get_snapshot.return_value = snapshot_factory(
        last_update=datetime(2026, 8, 28, 17, 0, tzinfo=UTC)
    )
    coordinator = make_coordinator(hass, config_entry, fake_cloud_client)

    result = await coordinator._async_update_data()

    assert result.is_stale is False
    assert result.poll_interval_minutes == 4.0


@pytest.mark.asyncio
@freeze_time("2026-08-28 18:00:00+00:00")
async def test_topology_refreshes_after_twenty_four_hours(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_cloud_client: MagicMock,
    tigo_topology,
    system_info_factory,
    snapshot_factory,
) -> None:
    fake_cloud_client.get_topology.return_value = tigo_topology
    fake_cloud_client.get_system_info.return_value = system_info_factory(
        day=date(2026, 8, 28)
    )
    fake_cloud_client.get_snapshot.return_value = snapshot_factory()
    coordinator = make_coordinator(hass, config_entry, fake_cloud_client)
    await coordinator._async_update_data()
    assert coordinator._topology_refreshed_at is not None
    coordinator._topology_refreshed_at -= timedelta(hours=25)

    await coordinator._async_update_data()

    assert fake_cloud_client.get_topology.await_count == 2
    # Day metadata remains cached independently from the topology.
    assert fake_cloud_client.get_system_info.await_count == 1


@pytest.mark.asyncio
async def test_authentication_error_requests_config_entry_reauth(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_cloud_client: MagicMock,
) -> None:
    fake_cloud_client.get_topology.side_effect = TigoAuthenticationError("rejected")
    coordinator = make_coordinator(hass, config_entry, fake_cloud_client)

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
@freeze_time("2026-08-28 18:00:00+00:00")
async def test_retry_after_extends_interval_and_surfaces_update_failure(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_cloud_client: MagicMock,
) -> None:
    fake_cloud_client.get_topology.side_effect = TigoRateLimitError(
        "rate limited",
        status=429,
        retry_after=900,
    )
    coordinator = make_coordinator(hass, config_entry, fake_cloud_client)

    with pytest.raises(UpdateFailed, match="Temporary Tigo cloud error"):
        await coordinator._async_update_data()

    assert coordinator.update_interval == timedelta(seconds=900)


@pytest.mark.asyncio
async def test_failed_refresh_advances_retained_freshness_and_keeps_telemetry(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_cloud_client: MagicMock,
    tigo_topology,
    system_info_factory,
    snapshot_factory,
) -> None:
    snapshot = snapshot_factory(last_update=datetime(2026, 8, 28, 17, 50, tzinfo=UTC))
    fake_cloud_client.get_topology.return_value = tigo_topology
    fake_cloud_client.get_system_info.return_value = system_info_factory(
        day=date(2026, 8, 28)
    )
    fake_cloud_client.get_snapshot.return_value = snapshot
    coordinator = make_coordinator(hass, config_entry, fake_cloud_client)

    with freeze_time("2026-08-28 18:00:00+00:00"):
        await coordinator.async_refresh()

    retained = coordinator.data
    assert retained is not None
    assert coordinator.last_update_success is True

    fake_cloud_client.get_snapshot.side_effect = TigoConnectionError("offline")
    with freeze_time("2026-08-28 18:30:00+00:00"):
        await coordinator.async_refresh()

    first_failure = coordinator.data
    assert first_failure is not None
    assert coordinator.last_update_success is False
    assert isinstance(coordinator.last_exception, UpdateFailed)
    assert first_failure.snapshot is snapshot
    assert first_failure.fetched_at == retained.fetched_at
    assert first_failure.last_cloud_update == retained.last_cloud_update
    assert first_failure.data_age_minutes == 40.0
    assert first_failure.is_daylight is True
    assert first_failure.is_stale is False

    with freeze_time("2026-08-28 18:40:00+00:00"):
        await coordinator.async_refresh()

    second_failure = coordinator.data
    assert second_failure is not None
    assert coordinator.last_update_success is False
    assert second_failure.snapshot is snapshot
    assert second_failure.fetched_at == retained.fetched_at
    assert second_failure.data_age_minutes == 50.0
    assert second_failure.is_daylight is True
    assert second_failure.is_stale is True


@pytest.mark.asyncio
async def test_retry_after_respects_night_baseline_and_updates_retained_metadata(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_cloud_client: MagicMock,
    tigo_topology,
    system_info_factory,
    snapshot_factory,
) -> None:
    snapshot = snapshot_factory(last_update=datetime(2026, 8, 28, 17, 50, tzinfo=UTC))
    fake_cloud_client.get_topology.return_value = tigo_topology
    fake_cloud_client.get_system_info.return_value = system_info_factory(
        day=date(2026, 8, 28)
    )
    fake_cloud_client.get_snapshot.return_value = snapshot
    coordinator = make_coordinator(hass, config_entry, fake_cloud_client)

    with freeze_time("2026-08-28 18:00:00+00:00"):
        await coordinator.async_refresh()

    fake_cloud_client.get_system_info.return_value = system_info_factory(
        day=date(2026, 8, 28)
    )
    fake_cloud_client.get_snapshot.side_effect = TigoRateLimitError(
        "rate limited",
        status=429,
        retry_after=900,
    )
    with freeze_time("2026-08-29 06:00:00+00:00"):
        await coordinator.async_refresh()

    retained = coordinator.data
    assert retained is not None
    assert coordinator.last_update_success is False
    assert retained.snapshot is snapshot
    assert retained.data_age_minutes == 730.0
    assert retained.is_daylight is False
    assert retained.is_stale is False
    assert retained.poll_interval_minutes == 30.0
    assert coordinator.update_interval == timedelta(seconds=1800)


@pytest.mark.asyncio
async def test_non_retryable_api_error_surfaces_update_failure(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_cloud_client: MagicMock,
) -> None:
    fake_cloud_client.get_topology.side_effect = TigoConnectionError("offline")
    coordinator = make_coordinator(hass, config_entry, fake_cloud_client)

    with pytest.raises(UpdateFailed, match="Error communicating with Tigo cloud"):
        await coordinator._async_update_data()


def test_daylight_helper_honors_boundaries_and_fallbacks(system_info_factory) -> None:
    info = system_info_factory(
        day=date(2026, 8, 28),
        sunrise=time(6, 0),
        sunset=time(18, 0),
    )

    assert _is_daylight(datetime(2026, 8, 28, 6, 0), info) is True
    assert _is_daylight(datetime(2026, 8, 28, 18, 0), info) is True
    assert _is_daylight(datetime(2026, 8, 28, 5, 59), info) is False
    assert _is_daylight(datetime(2026, 8, 28, 18, 1), info) is False
