"""Unit tests for the side-effect-free Tigo payload parsers."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import date, time
from pathlib import Path

import pytest

from custom_components.tigo_energy.exceptions import TigoDataError
from custom_components.tigo_energy.models import (
    build_snapshot,
    parse_homepage,
    parse_panel_energy,
    parse_panel_power,
    parse_peak_power,
    parse_system_info,
    parse_systems,
    parse_topology,
)

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str):
    """Load a sanitized JSON fixture."""

    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def topology():
    systems = parse_systems(fixture("systems.json"))
    return parse_topology(
        systems[0],
        fixture("layout.json"),
        fixture("equipments.json"),
    )


def test_parse_systems_preserves_zero_coordinates() -> None:
    systems = parse_systems(fixture("systems.json"))

    assert len(systems) == 1
    assert systems[0].id == 1
    assert systems[0].name == "Example Solar Array"
    assert systems[0].timezone == "America/Phoenix"
    assert systems[0].latitude == 0.0
    assert systems[0].longitude == 0.0


def test_system_info_parses_capabilities_and_solar_day() -> None:
    info = parse_system_info(fixture("system_info.json"), 1, "2026-08-28")

    assert info.day == date(2026, 8, 28)
    assert info.timezone == "America/Phoenix"
    assert info.sunrise == time(5, 45)
    assert info.sunset == time(18, 45)
    assert info.has_premium is False
    assert info.features == frozenset({"Display.energy", "Display.pin"})


def test_topology_joins_non_alphabetic_equipment_order_by_serial() -> None:
    parsed = topology()
    modules = {module.object_id: module for module in parsed.modules}

    assert parsed.system.id == 1
    assert parsed.cca_uid == "REDACTED-CCA"
    assert parsed.inverter_count == 1
    assert parsed.mppt_count == 1
    assert parsed.string_count == 1
    assert tuple(module.object_id for module in parsed.modules) == ("102", "101")
    assert modules["101"].equipment_id == "A1"
    assert modules["101"].label == "A1"
    assert modules["101"].string_label == "String A"
    assert len(parsed.signature) == 64
    assert "REDACTED" not in parsed.signature


def test_models_are_frozen() -> None:
    parsed = topology()

    with pytest.raises(FrozenInstanceError):
        parsed.modules[0].label = "changed"  # type: ignore[misc]


def test_panel_power_uses_order_and_scans_each_module_independently() -> None:
    summary = parse_panel_power(
        fixture("panel_power.json"),
        topology(),
        "2026-08-28",
        system_timezone="America/Phoenix",
    )
    readings = {reading.object_id: reading for reading in summary.readings}

    # order[] is [102, 101], intentionally unlike the layout's visual order.
    assert readings["101"].power_w == 110.0
    assert readings["101"].sample_time.isoformat() == "2026-08-28T10:15:00-07:00"
    assert readings["102"].power_w == 95.0
    assert readings["102"].sample_time.isoformat() == "2026-08-28T10:30:00-07:00"
    # The all-missing 23:59 row must not erase either panel's latest sample.
    assert summary.last_update.isoformat() == "2026-08-28T10:30:00-07:00"


def test_explicit_unmatched_panel_order_never_falls_back_positionally() -> None:
    payload = fixture("panel_power.json")
    payload["dataset"][0]["order"] = ["unknown-one", "unknown-two"]

    with pytest.raises(TigoDataError, match="order"):
        parse_panel_power(payload, topology(), "2026-08-28")


def test_panel_energy_converts_wh_and_keeps_missing_module_unavailable() -> None:
    summary = parse_panel_energy(
        fixture("panel_energy.json"),
        topology(),
        "2026-08-28",
        system_timezone="America/Phoenix",
    )
    readings = {reading.object_id: reading for reading in summary.readings}

    assert readings["101"].energy_today_kwh == 1.2
    assert readings["101"].energy_sample_time.isoformat() == "2026-08-28T10:15:00-07:00"
    assert readings["102"].energy_today_kwh is None
    assert readings["102"].energy_sample_time is None
    assert summary.total_today_kwh == 2.35


def test_homepage_normalizes_unitless_power_and_wh_energy_totals() -> None:
    production = parse_homepage(
        fixture("homepage.json"),
        day="2026-08-28",
        system_timezone="America/Phoenix",
    )

    assert production.current_power_w == 2400.0
    assert production.energy_today_kwh == 12.5
    assert production.energy_week_kwh == 72.25
    assert production.energy_month_kwh == 310.75
    assert production.energy_year_kwh == 4200.5
    assert production.energy_lifetime_kwh == 24500.125
    assert production.last_update.isoformat() == "2026-08-28T10:20:00-07:00"


def test_quantity_parser_honors_explicit_wh_units() -> None:
    payload = {
        "energyProduction": {
            "day": {"value": 2500, "unit": "Wh"},
            "lifetime": "2.5 MWh",
        }
    }

    production = parse_homepage(payload)

    assert production.energy_today_kwh == 2.5
    assert production.energy_lifetime_kwh == 2500.0


def test_build_snapshot_merges_without_local_lifetime_accumulation() -> None:
    parsed_topology = topology()
    production = parse_homepage(
        fixture("homepage.json"),
        day="2026-08-28",
        system_timezone="America/Phoenix",
    )
    power = parse_panel_power(
        fixture("panel_power.json"),
        parsed_topology,
        "2026-08-28",
        system_timezone="America/Phoenix",
    )
    energy = parse_panel_energy(
        fixture("panel_energy.json"),
        parsed_topology,
        "2026-08-28",
        system_timezone="America/Phoenix",
    )
    peak = parse_peak_power(
        fixture("aggpower.json"),
        day="2026-08-28",
        system_timezone="America/Phoenix",
    )

    snapshot = build_snapshot(parsed_topology, production, power, energy, peak)

    assert snapshot.current_power_w == 2400.0
    assert snapshot.peak_power_today_w == 5750.0
    assert snapshot.energy_lifetime_kwh == 24500.125
    assert snapshot.reporting_modules == 2
    assert snapshot.last_update == production.last_update
    module_values = {reading.object_id: reading for reading in snapshot.modules}
    assert module_values["101"].power_w == 110.0
    assert module_values["101"].energy_today_kwh == 1.2
    assert module_values["102"].energy_today_kwh is None


def test_current_power_falls_back_to_sum_only_when_homepage_omits_now() -> None:
    parsed_topology = topology()
    production = parse_homepage({"energyProduction": {"day": 1000.0}})
    power = parse_panel_power(
        fixture("panel_power.json"),
        parsed_topology,
        "2026-08-28",
        system_timezone="America/Phoenix",
    )

    snapshot = build_snapshot(parsed_topology, production, power)

    assert snapshot.current_power_w == 205.0
    assert snapshot.energy_today_kwh == 1.0
