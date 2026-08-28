"""Shared Home Assistant fixtures for the Tigo Energy integration tests."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tigo_energy.api import TigoCloudClient
from custom_components.tigo_energy.const import (
    CONF_SYSTEM_ID,
    CONF_SYSTEM_NAME,
    CONF_TIME_ZONE,
    DOMAIN,
)
from custom_components.tigo_energy.models import (
    PanelReading,
    SystemInfo,
    SystemSnapshot,
    TigoSystem,
    Topology,
    parse_systems,
    parse_topology,
)

FIXTURES = Path(__file__).parent / "fixtures"
TEST_USERNAME = "user@example.invalid"
TEST_PASSWORD = "TEST-PASSWORD-NOT-A-SECRET"


def load_fixture(name: str) -> Any:
    """Load one sanitized fixture payload."""

    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow Home Assistant to load this repository's custom component."""


@pytest.fixture
def tigo_system() -> TigoSystem:
    """Return the sanitized example system."""

    return parse_systems(load_fixture("systems.json"))[0]


@pytest.fixture
def tigo_topology(tigo_system: TigoSystem) -> Topology:
    """Return a two-module topology with deliberately non-visual API order."""

    return parse_topology(
        tigo_system,
        load_fixture("layout.json"),
        load_fixture("equipments.json"),
    )


@pytest.fixture
def system_info_factory():
    """Build day-specific Tigo system metadata."""

    def factory(
        *,
        day: date,
        sunrise: time = time(5, 45),
        sunset: time = time(18, 45),
    ) -> SystemInfo:
        return SystemInfo(
            system_id=1,
            day=day,
            timezone="America/Phoenix",
            sunrise=sunrise,
            sunset=sunset,
            has_premium=False,
            features=frozenset({"Display.energy", "Display.pin"}),
        )

    return factory


@pytest.fixture
def snapshot_factory(tigo_topology: Topology):
    """Build coordinator snapshots with controllable freshness/missing data."""

    def factory(
        *,
        last_update: datetime | None = None,
        include_second_energy: bool = False,
    ) -> SystemSnapshot:
        update = last_update or datetime(2026, 8, 28, 17, 50, tzinfo=UTC)
        readings = tuple(
            PanelReading(
                module=module,
                power_w=95.0 if module.object_id == "102" else 110.0,
                sample_time=update,
                energy_today_kwh=(
                    1.15
                    if module.object_id == "102" and include_second_energy
                    else 1.2
                    if module.object_id == "101"
                    else None
                ),
                energy_sample_time=(
                    update
                    if module.object_id == "101" or include_second_energy
                    else None
                ),
            )
            for module in tigo_topology.modules
        )
        return SystemSnapshot(
            system_id=1,
            current_power_w=2400.0,
            peak_power_today_w=5750.0,
            energy_today_kwh=12.5,
            energy_week_kwh=72.25,
            energy_month_kwh=310.75,
            energy_year_kwh=4200.5,
            energy_lifetime_kwh=24500.125,
            reporting_modules=2,
            last_update=update,
            modules=readings,
        )

    return factory


@pytest.fixture
def fake_cloud_client() -> MagicMock:
    """Return a strict async fake for the cloud client surface."""

    client = MagicMock(spec=TigoCloudClient)
    client.get_systems = AsyncMock()
    client.get_topology = AsyncMock()
    client.get_system_info = AsyncMock()
    client.get_snapshot = AsyncMock()
    return client


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a configured but not-yet-added Tigo entry."""

    return MockConfigEntry(
        domain=DOMAIN,
        title="Example Solar Array",
        unique_id="1",
        data={
            "username": TEST_USERNAME,
            "password": TEST_PASSWORD,
            CONF_SYSTEM_ID: 1,
            CONF_SYSTEM_NAME: "Example Solar Array",
            CONF_TIME_ZONE: "America/Phoenix",
        },
    )
