"""Typed Tigo cloud models and side-effect-free payload parsers.

The mobile API is undocumented and has changed shape over time.  These
parsers intentionally accept the small set of equivalent wrappers observed in
the web/mobile clients while remaining strict about identity mapping: when a
panel summary provides ``order[]``, that order is authoritative.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .exceptions import TigoDataError

JsonMapping = Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class TigoSystem:
    """A system visible to the authenticated Tigo account."""

    id: int
    name: str
    timezone: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    has_premium: bool | None = None

    @property
    def system_id(self) -> int:
        """Compatibility-friendly explicit system identifier."""

        return self.id


@dataclass(frozen=True, slots=True)
class SystemInfo:
    """Day-specific account and solar-day metadata."""

    system_id: int
    day: date
    timezone: str | None = None
    sunrise: time | None = None
    sunset: time | None = None
    has_premium: bool = False
    features: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Module:
    """Stable metadata for one optimizer/module in the Tigo layout."""

    object_id: str
    label: str
    serial: str | None = None
    model: str | None = None
    inverter_label: str = "Inverter"
    mppt_label: str = "MPPT"
    string_label: str = "String"
    equipment_id: str | None = None
    equipment_index: int | None = None


@dataclass(frozen=True, slots=True)
class Topology:
    """System topology and the identifiers needed to map telemetry."""

    system: TigoSystem
    modules: tuple[Module, ...]
    cca_uids: tuple[str, ...] = ()
    inverter_count: int = 0
    mppt_count: int = 0
    string_count: int = 0
    signature: str = ""

    @property
    def cca_uid(self) -> str | None:
        """Return the primary CCA UID used by summary endpoints."""

        return self.cca_uids[0] if self.cca_uids else None

    @property
    def by_object_id(self) -> dict[str, Module]:
        """Return a fresh lookup so callers cannot mutate this model."""

        return {module.object_id: module for module in self.modules}

    @property
    def by_equipment_id(self) -> dict[str, Module]:
        """Return a fresh equipment-label lookup."""

        return {
            module.equipment_id: module
            for module in self.modules
            if module.equipment_id is not None
        }


@dataclass(frozen=True, slots=True)
class PanelReading:
    """Current power and daily energy for one module."""

    module: Module
    power_w: float | None = None
    sample_time: datetime | None = None
    energy_today_kwh: float | None = None
    energy_sample_time: datetime | None = None

    @property
    def object_id(self) -> str:
        return self.module.object_id


@dataclass(frozen=True, slots=True)
class PanelPowerSummary:
    """Parsed per-module power response."""

    readings: tuple[PanelReading, ...]
    last_update: datetime | None = None


@dataclass(frozen=True, slots=True)
class PanelEnergySummary:
    """Parsed per-module daily-energy response."""

    readings: tuple[PanelReading, ...]
    last_update: datetime | None = None
    total_today_kwh: float | None = None


@dataclass(frozen=True, slots=True)
class ProductionTotals:
    """System-wide values returned by the Tigo homepage endpoint."""

    current_power_w: float | None = None
    energy_today_kwh: float | None = None
    energy_week_kwh: float | None = None
    energy_month_kwh: float | None = None
    energy_year_kwh: float | None = None
    energy_lifetime_kwh: float | None = None
    last_update: datetime | None = None


@dataclass(frozen=True, slots=True)
class PeakPower:
    """Daily peak power and its source timestamp, when supplied."""

    power_w: float | None = None
    sample_time: datetime | None = None


@dataclass(frozen=True, slots=True)
class SystemSnapshot:
    """One coordinator-ready snapshot of system and module production."""

    system_id: int
    current_power_w: float | None
    peak_power_today_w: float | None
    energy_today_kwh: float | None
    energy_week_kwh: float | None
    energy_month_kwh: float | None
    energy_year_kwh: float | None
    energy_lifetime_kwh: float | None
    reporting_modules: int
    last_update: datetime | None
    modules: tuple[PanelReading, ...]


def _mapping(value: Any) -> JsonMapping:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


def _field(data: JsonMapping, *names: str) -> Any:
    """Return the first exact or case-insensitive matching field."""

    for name in names:
        if name in data:
            return data[name]
    folded = {str(key).casefold(): value for key, value in data.items()}
    for name in names:
        if name.casefold() in folded:
            return folded[name.casefold()]
    return None


def _first_not_none(*values: Any) -> Any:
    """Return the first value that is not ``None`` (zero is meaningful)."""

    return next((value for value in values if value is not None), None)


def _container(payload: Any) -> JsonMapping:
    """Unwrap common ``data``/``result`` response envelopes once."""

    root = _mapping(payload)
    for key in ("data", "result"):
        nested = root.get(key)
        if isinstance(nested, Mapping):
            return nested
    return root


def _finite_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


_QUANTITY_RE = re.compile(
    r"^\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*([A-Za-z]+)?\s*$"
)


def _quantity(
    value: Any,
    *,
    target: str,
    default_unit: str,
) -> float | None:
    """Parse a number or ``{value, unit}`` and normalize W/kWh."""

    unit: str | None = None
    raw = value
    if isinstance(value, Mapping):
        raw = _field(value, "value", "amount", "val", "total")
        unit_value = _field(
            value,
            "unit",
            "units",
            "uom",
            "unitOfMeasure",
            "unit_of_measurement",
        )
        unit = str(unit_value) if unit_value is not None else None
    elif isinstance(value, str):
        match = _QUANTITY_RE.match(value)
        if match:
            raw = match.group(1)
            unit = match.group(2)

    number = _finite_float(raw)
    if number is None:
        return None
    normalized_unit = (unit or default_unit).strip().lower().replace(" ", "")

    if target == "w":
        factors = {"w": 1.0, "kw": 1_000.0, "mw": 1_000_000.0}
    elif target == "kwh":
        factors = {"wh": 0.001, "kwh": 1.0, "mwh": 1_000.0}
    else:  # pragma: no cover - internal programming error
        raise ValueError(f"Unsupported target unit: {target}")
    factor = factors.get(normalized_unit)
    return number * factor if factor is not None else None


def _coerce_day(value: date | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as err:
        raise TigoDataError("Tigo request date must be ISO YYYY-MM-DD") from err


def _coerce_timezone(value: str | tzinfo | None) -> tzinfo:
    if isinstance(value, tzinfo):
        return value
    if value:
        try:
            return ZoneInfo(value)
        except ZoneInfoNotFoundError as err:
            raise TigoDataError("Tigo returned an unknown system timezone") from err
    # Callers normally provide the system timezone.  UTC is an explicit,
    # deterministic fallback for accounts that omit it.
    return UTC


def parse_datetime(
    value: Any,
    *,
    day: date | str | None = None,
    system_timezone: str | tzinfo | None = None,
) -> datetime | None:
    """Parse Tigo's ISO, SQL-style, time-only, or epoch timestamps."""

    if value in (None, "", "-") or isinstance(value, bool):
        return None
    zone = _coerce_timezone(system_timezone)
    parsed: datetime | None = None

    if isinstance(value, (int, float)):
        epoch = float(value)
        if epoch > 10_000_000_000:  # milliseconds
            epoch /= 1_000.0
        try:
            return datetime.fromtimestamp(epoch, tz=UTC).astimezone(zone)
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None

    if parsed is None and day is not None:
        date_value = _coerce_day(day)
        try:
            local_time = time.fromisoformat(text)
        except ValueError:
            pass
        else:
            parsed = datetime.combine(date_value, local_time)

    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def _clock_time(value: Any) -> time | None:
    """Parse sunrise/sunset expressed as decimal hour, time, or ISO text."""

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        hour_float = float(value)
        if not 0 <= hour_float < 24:
            return None
        hour = int(hour_float)
        minute_float = (hour_float - hour) * 60
        minute = int(minute_float)
        second = round((minute_float - minute) * 60)
        if second == 60:
            minute += 1
            second = 0
        if minute == 60:
            hour = (hour + 1) % 24
            minute = 0
        return time(hour, minute, second)
    if value is None:
        return None
    text = str(value).strip()
    numeric = _finite_float(text)
    if numeric is not None and ":" not in text and "-" not in text:
        return _clock_time(numeric)
    try:
        return time.fromisoformat(text)
    except ValueError:
        parsed = parse_datetime(text)
        return parsed.timetz().replace(tzinfo=None) if parsed else None


def parse_systems(payload: Any) -> tuple[TigoSystem, ...]:
    """Parse the systems query response."""

    root = _mapping(payload)
    candidates: Any = _field(root, "systems")
    if candidates is None:
        envelope = _container(root)
        candidates = _field(envelope, "systems", "items", "rows")
        if candidates is None and isinstance(root.get("data"), Sequence):
            candidates = root["data"]
    if (
        candidates is None
        and isinstance(payload, Sequence)
        and not isinstance(payload, (str, bytes, bytearray))
    ):
        candidates = payload

    systems: list[TigoSystem] = []
    for item in _sequence(candidates):
        data = _mapping(item)
        identifier = _finite_float(_field(data, "id", "system_id", "systemId"))
        if identifier is None:
            continue
        location = _mapping(_field(data, "location", "site"))
        name = _field(data, "name", "label", "system_name", "systemName")
        tz_name = _field(data, "timezone", "timeZone", "tz") or _field(
            location, "timezone", "timeZone", "tz"
        )
        systems.append(
            TigoSystem(
                id=int(identifier),
                name=str(name or f"Tigo System {int(identifier)}"),
                timezone=str(tz_name) if tz_name else None,
                latitude=_finite_float(
                    _first_not_none(
                        _field(data, "latitude", "lat"),
                        _field(location, "latitude", "lat"),
                    )
                ),
                longitude=_finite_float(
                    _first_not_none(
                        _field(data, "longitude", "lon", "lng"),
                        _field(location, "longitude", "lon", "lng"),
                    )
                ),
                has_premium=(
                    bool(_field(data, "has_premium", "hasPremium"))
                    if _field(data, "has_premium", "hasPremium") is not None
                    else None
                ),
            )
        )
    return tuple(systems)


def parse_system_info(payload: Any, system_id: int, day: date | str) -> SystemInfo:
    """Parse Basic/Premium flags and day-specific solar metadata."""

    day_value = _coerce_day(day)
    root = _container(payload)
    day_data = _mapping(root.get(day_value.isoformat()))
    if not day_data:
        day_data = _mapping(_field(root, "day", "dayInfo", "dateInfo"))

    timezone_name = _field(day_data, "timezone", "timeZone", "tz") or _field(
        root, "timezone", "timeZone", "tz"
    )
    raw_features = _field(root, "features") or ()
    features: set[str] = set()
    if isinstance(raw_features, Mapping):
        features.update(str(key) for key, enabled in raw_features.items() if enabled)
    else:
        for feature in _sequence(raw_features):
            if isinstance(feature, Mapping):
                feature = _field(feature, "name", "key", "feature")
            if feature is not None:
                features.add(str(feature))

    return SystemInfo(
        system_id=system_id,
        day=day_value,
        timezone=str(timezone_name) if timezone_name else None,
        sunrise=_clock_time(_field(day_data, "sunrise", "sunRise")),
        sunset=_clock_time(_field(day_data, "sunset", "sunSet")),
        has_premium=bool(_field(root, "has_premium", "hasPremium") or False),
        features=frozenset(features),
    )


def enrich_system(system: TigoSystem, info: SystemInfo) -> TigoSystem:
    """Return a system enriched with the day-specific info response."""

    return replace(
        system,
        timezone=info.timezone or system.timezone,
        has_premium=info.has_premium,
    )


def _normalize_serial(value: Any) -> str:
    return "".join(char for char in str(value or "").casefold() if char.isalnum())


def _layout_root(layout: Any) -> JsonMapping:
    root = _container(layout)
    return _mapping(root.get("system")) or root


def _flatten_layout(layout: Any) -> tuple[list[dict[str, Any]], int, int, int]:
    root = _layout_root(layout)
    flattened: list[dict[str, Any]] = []
    inverter_count = mppt_count = string_count = 0
    for inverter_index, inverter_raw in enumerate(_sequence(root.get("inverters"))):
        inverter = _mapping(inverter_raw)
        inverter_count += 1
        inverter_label = str(
            _field(inverter, "label", "name") or f"Inverter {inverter_index + 1}"
        )
        for mppt_index, mppt_raw in enumerate(_sequence(inverter.get("mppts"))):
            mppt = _mapping(mppt_raw)
            mppt_count += 1
            mppt_label = str(_field(mppt, "label", "name") or f"MPPT {mppt_index + 1}")
            for string_index, string_raw in enumerate(_sequence(mppt.get("strings"))):
                string_data = _mapping(string_raw)
                string_count += 1
                string_label = str(
                    _field(string_data, "label", "name") or f"String {string_index + 1}"
                )
                for panel_index, panel_raw in enumerate(
                    _sequence(_field(string_data, "panels", "modules"))
                ):
                    panel = _mapping(panel_raw)
                    flattened.append(
                        {
                            "object_id": _field(panel, "object_id", "objectId", "id"),
                            "label": _field(panel, "label", "name", "equipmentId")
                            or f"Module {panel_index + 1}",
                            "serial": _field(
                                panel, "serial", "equipmentSerial", "serialNumber"
                            ),
                            "model": _field(panel, "type", "model", "equipmentModel"),
                            "inverter_label": inverter_label,
                            "mppt_label": mppt_label,
                            "string_label": string_label,
                        }
                    )
    return flattened, inverter_count, mppt_count, string_count


def _equipment_items(payload: Any) -> Sequence[Any]:
    if isinstance(payload, Sequence) and not isinstance(
        payload, (str, bytes, bytearray)
    ):
        return payload
    root = _container(payload)
    return _sequence(_field(root, "equipments", "items", "rows"))


def _is_gateway(equipment_type: str) -> bool:
    return any(word in equipment_type for word in ("unit", "cca", "gateway"))


def _is_module(equipment_type: str) -> bool:
    if not equipment_type:
        return True
    return any(
        word in equipment_type
        for word in ("panel", "module", "optimizer", "mlpe", "ts4")
    )


def _topology_system(system: TigoSystem | int, layout: Any) -> TigoSystem:
    if isinstance(system, TigoSystem):
        base = system
    else:
        base = TigoSystem(id=int(system), name=f"Tigo System {int(system)}")
    root = _layout_root(layout)
    location = _mapping(_field(root, "location", "site"))
    latitude = _finite_float(
        _first_not_none(
            _field(root, "latitude", "lat"),
            _field(location, "latitude", "lat"),
        )
    )
    longitude = _finite_float(
        _first_not_none(
            _field(root, "longitude", "lon", "lng"),
            _field(location, "longitude", "lon", "lng"),
        )
    )
    return replace(
        base,
        name=str(_field(root, "name", "label") or base.name),
        timezone=(
            str(_field(root, "timezone", "timeZone", "tz"))
            if _field(root, "timezone", "timeZone", "tz")
            else base.timezone
        ),
        latitude=latitude if latitude is not None else base.latitude,
        longitude=longitude if longitude is not None else base.longitude,
    )


def parse_topology(
    system: TigoSystem | int,
    layout: Any,
    equipments: Any,
) -> Topology:
    """Join layout metadata with equipment identities without guessing order[]."""

    system_model = _topology_system(system, layout)
    layout_modules, inverter_count, mppt_count, string_count = _flatten_layout(layout)
    by_serial = {
        _normalize_serial(item.get("serial")): item
        for item in layout_modules
        if item.get("serial")
    }
    by_label = {
        str(item.get("label") or "").casefold(): item
        for item in layout_modules
        if item.get("label")
    }

    equipment_modules: list[tuple[int, JsonMapping]] = []
    cca_uids: list[str] = []
    signature_rows: list[list[str | None]] = []
    for index, equipment_raw in enumerate(_equipment_items(equipments)):
        equipment = _mapping(equipment_raw)
        equipment_type = str(
            _field(equipment, "equipmentType", "type", "kind") or ""
        ).casefold()
        equipment_id_raw = _field(equipment, "equipmentId", "label", "name", "id")
        serial_raw = _field(
            equipment, "equipmentSerial", "serial", "serialNumber", "uid"
        )
        signature_rows.append(
            [
                str(equipment_id_raw) if equipment_id_raw is not None else None,
                _normalize_serial(serial_raw) or None,
                equipment_type or None,
            ]
        )
        if _is_gateway(equipment_type):
            if serial_raw:
                cca_uids.append(str(serial_raw))
            continue
        if _is_module(equipment_type):
            equipment_modules.append((index, equipment))

    matched_layout_ids: set[int] = set()
    modules: list[Module] = []
    for panel_position, (equipment_index, equipment) in enumerate(equipment_modules):
        equipment_id_raw = _field(equipment, "equipmentId", "label", "name", "id")
        equipment_id = str(equipment_id_raw or f"module-{panel_position + 1}")
        serial_raw = _field(equipment, "equipmentSerial", "serial", "serialNumber")
        matched = by_serial.get(_normalize_serial(serial_raw)) if serial_raw else None
        if matched is None:
            matched = by_label.get(equipment_id.casefold())
        if matched is None and panel_position < len(layout_modules):
            candidate = layout_modules[panel_position]
            if id(candidate) not in matched_layout_ids:
                matched = candidate
        if matched is not None:
            matched_layout_ids.add(id(matched))

        object_raw = (
            matched.get("object_id")
            if matched is not None
            else _field(equipment, "object_id", "objectId", "id")
        )
        object_id = str(object_raw if object_raw is not None else equipment_id)
        modules.append(
            Module(
                object_id=object_id,
                label=str(matched.get("label") if matched else equipment_id),
                serial=(
                    str(serial_raw)
                    if serial_raw is not None
                    else str(matched.get("serial"))
                    if matched and matched.get("serial") is not None
                    else None
                ),
                model=(
                    str(
                        _field(equipment, "equipmentModel", "model", "type")
                        or (matched.get("model") if matched else "")
                    )
                    or None
                ),
                inverter_label=str(
                    matched.get("inverter_label", "Inverter") if matched else "Inverter"
                ),
                mppt_label=str(
                    matched.get("mppt_label", "MPPT") if matched else "MPPT"
                ),
                string_label=str(
                    matched.get("string_label", "String") if matched else "String"
                ),
                equipment_id=equipment_id,
                equipment_index=equipment_index,
            )
        )

    # Layout-only modules still map correctly when summary order[] uses their
    # object IDs, even if Tigo omitted them from /equipments temporarily.
    for layout_module in layout_modules:
        if id(layout_module) in matched_layout_ids:
            continue
        object_raw = layout_module.get("object_id") or layout_module.get("label")
        if object_raw is None:
            continue
        modules.append(
            Module(
                object_id=str(object_raw),
                label=str(layout_module.get("label") or object_raw),
                serial=(
                    str(layout_module["serial"])
                    if layout_module.get("serial") is not None
                    else None
                ),
                model=(
                    str(layout_module["model"])
                    if layout_module.get("model") is not None
                    else None
                ),
                inverter_label=str(layout_module.get("inverter_label", "Inverter")),
                mppt_label=str(layout_module.get("mppt_label", "MPPT")),
                string_label=str(layout_module.get("string_label", "String")),
                equipment_id=str(layout_module.get("label") or object_raw),
            )
        )

    identifiers = [module.object_id for module in modules]
    if len(identifiers) != len(set(identifiers)):
        raise TigoDataError("Tigo topology contains duplicate module identifiers")

    signature = hashlib.sha256(
        json.dumps(signature_rows, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    return Topology(
        system=system_model,
        modules=tuple(modules),
        cca_uids=tuple(dict.fromkeys(cca_uids)),
        inverter_count=inverter_count,
        mppt_count=mppt_count,
        string_count=string_count,
        signature=signature,
    )


def _dataset_block(payload: Any) -> tuple[JsonMapping, JsonMapping]:
    root = _container(payload)
    dataset = _field(root, "dataset")
    if isinstance(dataset, Mapping):
        return root, dataset
    sequence = _sequence(dataset)
    return root, _mapping(sequence[0]) if sequence else {}


def _ordered_module_columns(
    block: JsonMapping, topology: Topology
) -> dict[int, Module]:
    order = _field(block, "order", "orders")
    if order is not None:
        object_lookup = topology.by_object_id
        equipment_lookup = topology.by_equipment_id
        columns: dict[int, Module] = {}
        for index, identifier in enumerate(_sequence(order)):
            key = str(identifier)
            module = object_lookup.get(key) or equipment_lookup.get(key)
            if module is not None:
                columns[index] = module
        if not columns and _sequence(order):
            raise TigoDataError("Tigo panel order does not match the system topology")
        return columns
    # Older payloads did not always expose order[].  In that case use module
    # order only; never replace an explicit, unmatched order with this fallback.
    return {index: module for index, module in enumerate(topology.modules)}


def parse_panel_power(
    payload: Any,
    topology: Topology,
    day: date | str,
    *,
    system_timezone: str | tzinfo | None = None,
) -> PanelPowerSummary:
    """Parse latest valid power independently for every order[] column."""

    day_value = _coerce_day(day)
    root, block = _dataset_block(payload)
    columns = _ordered_module_columns(block, topology)
    latest: dict[str, tuple[datetime, float]] = {}
    rows = _sequence(_field(block, "data", "rows", "values"))
    for row_raw in rows:
        row = _mapping(row_raw)
        row_time = parse_datetime(
            _field(row, "t", "time", "timestamp"),
            day=day_value,
            system_timezone=system_timezone,
        )
        if row_time is None:
            continue
        values = _sequence(_field(row, "d", "data", "values"))
        for index, module in columns.items():
            if index >= len(values) or values[index] in (None, "", "-"):
                continue
            power = _quantity(values[index], target="w", default_unit="W")
            if power is None:
                continue
            previous = latest.get(module.object_id)
            if previous is None or row_time >= previous[0]:
                latest[module.object_id] = (row_time, power)

    readings = tuple(
        PanelReading(
            module=module,
            power_w=(
                latest[module.object_id][1] if module.object_id in latest else None
            ),
            sample_time=(
                latest[module.object_id][0] if module.object_id in latest else None
            ),
        )
        for module in topology.modules
    )
    last_update = parse_datetime(
        _field(root, "lastData", "last_data", "lastUpdate", "minLastTime"),
        day=day_value,
        system_timezone=system_timezone,
    )
    if last_update is None:
        last_update = _latest_datetime(reading.sample_time for reading in readings)
    return PanelPowerSummary(readings=readings, last_update=last_update)


def _timestamp_for_identifier(
    value: Any,
    identifiers: Iterable[str],
    *,
    day: date,
    system_timezone: str | tzinfo | None,
) -> datetime | None:
    if isinstance(value, Mapping):
        for identifier in identifiers:
            if identifier in value:
                return parse_datetime(
                    value[identifier], day=day, system_timezone=system_timezone
                )
        return None
    return parse_datetime(value, day=day, system_timezone=system_timezone)


def parse_panel_energy(
    payload: Any,
    topology: Topology,
    day: date | str,
    *,
    system_timezone: str | tzinfo | None = None,
) -> PanelEnergySummary:
    """Parse per-module daily Wh as kWh; absent keys remain ``None``."""

    day_value = _coerce_day(day)
    root = _container(payload)
    dataset = _mapping(_field(root, "dataset", "energy"))
    last_data = _field(root, "datasetLastData", "lastData", "lastUpdate")
    readings: list[PanelReading] = []
    for module in topology.modules:
        identifiers = tuple(
            identifier
            for identifier in (module.object_id, module.equipment_id)
            if identifier is not None
        )
        raw: Any = None
        found = False
        for identifier in identifiers:
            if identifier in dataset:
                raw = dataset[identifier]
                found = True
                break
            # Some JSON decoders preserve numeric keys only in synthetic tests.
            numeric_identifier = _finite_float(identifier)
            if numeric_identifier is not None and int(numeric_identifier) in dataset:
                raw = dataset[int(numeric_identifier)]
                found = True
                break
        energy = _quantity(raw, target="kwh", default_unit="Wh") if found else None
        sample_time = _timestamp_for_identifier(
            last_data,
            identifiers,
            day=day_value,
            system_timezone=system_timezone,
        )
        readings.append(
            PanelReading(
                module=module,
                energy_today_kwh=energy,
                energy_sample_time=sample_time if found else None,
            )
        )

    stats = _mapping(_field(root, "dailyStats", "stats"))
    total = _quantity(
        _field(stats, "total_agg_energy", "totalEnergy", "energy"),
        target="kwh",
        default_unit="Wh",
    )
    overall_update = _timestamp_for_identifier(
        last_data,
        (),
        day=day_value,
        system_timezone=system_timezone,
    )
    if overall_update is None:
        overall_update = _latest_datetime(
            reading.energy_sample_time for reading in readings
        )
    return PanelEnergySummary(
        readings=tuple(readings),
        last_update=overall_update,
        total_today_kwh=total,
    )


def _production_container(payload: Any) -> tuple[JsonMapping, JsonMapping]:
    root = _container(payload)
    production = _mapping(
        _field(root, "energyProduction", "energy_production", "production")
    )
    return root, production or root


def parse_homepage(
    payload: Any,
    *,
    day: date | str | None = None,
    system_timezone: str | tzinfo | None = None,
) -> ProductionTotals:
    """Parse authoritative system production totals from the homepage."""

    root, production = _production_container(payload)
    return ProductionTotals(
        current_power_w=_quantity(
            _field(production, "now", "current", "currentPower", "power"),
            target="w",
            default_unit="W",
        ),
        energy_today_kwh=_quantity(
            _field(production, "day", "today", "daily"),
            target="kwh",
            default_unit="kWh",
        ),
        energy_week_kwh=_quantity(
            _field(production, "week", "weekly"),
            target="kwh",
            default_unit="kWh",
        ),
        energy_month_kwh=_quantity(
            _field(production, "month", "monthly"),
            target="kwh",
            default_unit="kWh",
        ),
        energy_year_kwh=_quantity(
            _field(production, "year", "yearly", "ytd"),
            target="kwh",
            default_unit="kWh",
        ),
        energy_lifetime_kwh=_quantity(
            _field(production, "lifetime", "lifeTime", "total"),
            target="kwh",
            default_unit="kWh",
        ),
        last_update=parse_datetime(
            _field(root, "minLastTime", "lastData", "lastUpdate", "timestamp")
            or _field(production, "minLastTime", "lastData", "lastUpdate"),
            day=day,
            system_timezone=system_timezone,
        ),
    )


def parse_peak_power(
    payload: Any,
    *,
    day: date | str | None = None,
    system_timezone: str | tzinfo | None = None,
) -> PeakPower:
    """Parse ``dayMax`` from the aggregate-power response."""

    root = _container(payload)
    stats = _mapping(_field(root, "dailyStats", "stats"))
    value = _field(root, "dayMax", "day_max", "peak", "max")
    if value is None:
        value = _field(stats, "dayMax", "day_max", "peak", "max")
    timestamp_value = _field(
        root, "dayMaxTime", "day_max_time", "maxTime", "lastData", "lastUpdate"
    ) or _field(stats, "dayMaxTime", "day_max_time", "maxTime")
    return PeakPower(
        power_w=_quantity(value, target="w", default_unit="W"),
        sample_time=parse_datetime(
            timestamp_value, day=day, system_timezone=system_timezone
        ),
    )


def _latest_datetime(values: Iterable[datetime | None]) -> datetime | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def build_snapshot(
    topology: Topology,
    production: ProductionTotals,
    power: PanelPowerSummary,
    energy: PanelEnergySummary | None = None,
    peak: PeakPower | None = None,
) -> SystemSnapshot:
    """Merge typed endpoint results without manufacturing missing values."""

    power_by_id = {reading.object_id: reading for reading in power.readings}
    energy_by_id = (
        {reading.object_id: reading for reading in energy.readings} if energy else {}
    )
    modules: list[PanelReading] = []
    for module in topology.modules:
        power_reading = power_by_id.get(module.object_id)
        energy_reading = energy_by_id.get(module.object_id)
        modules.append(
            PanelReading(
                module=module,
                power_w=power_reading.power_w if power_reading else None,
                sample_time=power_reading.sample_time if power_reading else None,
                energy_today_kwh=(
                    energy_reading.energy_today_kwh if energy_reading else None
                ),
                energy_sample_time=(
                    energy_reading.energy_sample_time if energy_reading else None
                ),
            )
        )

    current_power = production.current_power_w
    if current_power is None:
        available_power = [
            reading.power_w for reading in modules if reading.power_w is not None
        ]
        current_power = sum(available_power) if available_power else None
    energy_today = production.energy_today_kwh
    if energy_today is None and energy is not None:
        energy_today = energy.total_today_kwh

    return SystemSnapshot(
        system_id=topology.system.id,
        current_power_w=current_power,
        peak_power_today_w=peak.power_w if peak else None,
        energy_today_kwh=energy_today,
        energy_week_kwh=production.energy_week_kwh,
        energy_month_kwh=production.energy_month_kwh,
        energy_year_kwh=production.energy_year_kwh,
        energy_lifetime_kwh=production.energy_lifetime_kwh,
        reporting_modules=sum(reading.power_w is not None for reading in modules),
        # minLastTime is the homepage's deliberate system-freshness marker.
        # Only fall back to per-panel time when the endpoint omits it.
        last_update=production.last_update or power.last_update,
        modules=tuple(modules),
    )


__all__ = [
    "Module",
    "PanelEnergySummary",
    "PanelPowerSummary",
    "PanelReading",
    "PeakPower",
    "ProductionTotals",
    "SystemInfo",
    "SystemSnapshot",
    "TigoSystem",
    "Topology",
    "build_snapshot",
    "enrich_system",
    "parse_datetime",
    "parse_homepage",
    "parse_panel_energy",
    "parse_panel_power",
    "parse_peak_power",
    "parse_system_info",
    "parse_systems",
    "parse_topology",
]
