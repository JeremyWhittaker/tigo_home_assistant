"""Data coordinator for Tigo Energy Cloud."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MAX_RETRY_AFTER_SECONDS, TigoCloudClient
from .const import (
    CONF_NIGHT_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_STALE_AFTER,
    CONF_SYSTEM_ID,
    CONF_TIME_ZONE,
    DEFAULT_NIGHT_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STALE_AFTER,
    DOMAIN,
    LOGGER,
    TOPOLOGY_REFRESH_INTERVAL,
)
from .exceptions import TigoApiError, TigoAuthError, TigoRetryableError
from .models import SystemInfo, SystemSnapshot, Topology


@dataclass(frozen=True, slots=True)
class TigoCoordinatorData:
    """One coherent set of topology, telemetry, and freshness metadata."""

    topology: Topology
    system_info: SystemInfo
    snapshot: SystemSnapshot
    fetched_at: datetime
    last_cloud_update: datetime | None
    data_age_minutes: float | None
    is_daylight: bool
    is_stale: bool
    poll_interval_minutes: float


class TigoCoordinator(DataUpdateCoordinator[TigoCoordinatorData]):
    """Poll Tigo while keeping cloud connectivity and data age distinct."""

    def __init__(self, hass: HomeAssistant, entry: Any) -> None:
        self.config_entry = entry
        self.system_id = int(entry.data[CONF_SYSTEM_ID])
        self.client = TigoCloudClient(
            async_get_clientsession(hass),
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
        )
        try:
            self.time_zone = ZoneInfo(str(entry.data.get(CONF_TIME_ZONE, "UTC")))
        except ZoneInfoNotFoundError:
            self.time_zone = ZoneInfo("UTC")
        self._topology: Topology | None = None
        self._topology_refreshed_at: datetime | None = None
        self._system_info: SystemInfo | None = None
        self._notify_failure_freshness = False
        self._day_interval = int(
            entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        self._night_interval = int(
            entry.options.get(CONF_NIGHT_SCAN_INTERVAL, DEFAULT_NIGHT_SCAN_INTERVAL)
        )
        self._stale_after = int(
            entry.options.get(CONF_STALE_AFTER, DEFAULT_STALE_AFTER)
        )
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{self.system_id}",
            update_interval=timedelta(seconds=self._day_interval),
        )

    async def _async_update_data(self) -> TigoCoordinatorData:
        """Fetch topology when needed and refresh all dynamic telemetry."""
        now = datetime.now(UTC)
        try:
            topology = await self._async_topology(now)
            local_day = now.astimezone(self.time_zone).date()
            system_info = await self._async_system_info(local_day)
            snapshot = await self.client.get_snapshot(
                self.system_id,
                local_day,
                topology=topology,
                cca_uid=topology.cca_uid,
                system_timezone=self.time_zone,
            )
        except TigoAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except TigoRetryableError as err:
            self._advance_retained_freshness(now, retry_after=err.retry_after)
            raise UpdateFailed(f"Temporary Tigo cloud error: {err}") from err
        except TigoApiError as err:
            self._advance_retained_freshness(now)
            raise UpdateFailed(f"Error communicating with Tigo cloud: {err}") from err

        last_update = _snapshot_last_update(snapshot)
        if last_update is not None and last_update.tzinfo is None:
            last_update = last_update.replace(tzinfo=self.time_zone).astimezone(UTC)
        elif last_update is not None:
            last_update = last_update.astimezone(UTC)

        age_minutes = (
            max(0.0, (now - last_update).total_seconds() / 60) if last_update else None
        )
        is_daylight = _is_daylight(now.astimezone(self.time_zone), system_info)
        is_stale = bool(
            is_daylight
            and (age_minutes is None or age_minutes * 60 > self._stale_after)
        )
        self.update_interval = timedelta(
            seconds=self._day_interval if is_daylight else self._night_interval
        )
        return TigoCoordinatorData(
            topology=topology,
            system_info=system_info,
            snapshot=snapshot,
            fetched_at=now,
            last_cloud_update=last_update,
            data_age_minutes=round(age_minutes, 1) if age_minutes is not None else None,
            is_daylight=is_daylight,
            is_stale=is_stale,
            poll_interval_minutes=self.update_interval.total_seconds() / 60,
        )

    def _advance_retained_freshness(
        self,
        now: datetime,
        *,
        retry_after: float | None = None,
    ) -> None:
        """Advance time-derived metadata while retaining last good telemetry."""
        retained = self.data
        system_info = (
            retained.system_info if retained is not None else self._system_info
        )
        is_daylight = _is_daylight(now.astimezone(self.time_zone), system_info)
        baseline = self._day_interval if is_daylight else self._night_interval
        requested_delay = retry_after if retry_after is not None else 0.0
        if not math.isfinite(requested_delay):
            requested_delay = 0.0
        requested_delay = min(max(requested_delay, 0.0), MAX_RETRY_AFTER_SECONDS)
        self.update_interval = timedelta(seconds=max(baseline, requested_delay))

        if retained is None:
            return

        last_update = retained.last_cloud_update
        age_minutes = (
            max(0.0, (now - last_update).total_seconds() / 60)
            if last_update is not None
            else None
        )
        is_stale = bool(
            is_daylight
            and (age_minutes is None or age_minutes * 60 > self._stale_after)
        )
        updated = replace(
            retained,
            data_age_minutes=(
                round(age_minutes, 1) if age_minutes is not None else None
            ),
            is_daylight=is_daylight,
            is_stale=is_stale,
            poll_interval_minutes=self.update_interval.total_seconds() / 60,
        )
        self._notify_failure_freshness = (
            not self.last_update_success and updated != retained
        )
        self.data = updated

    @callback
    def _async_refresh_finished(self) -> None:
        """Publish time-derived changes during consecutive failed refreshes."""
        if not self._notify_failure_freshness:
            return
        self._notify_failure_freshness = False
        self.async_update_listeners()

    async def _async_topology(self, now: datetime) -> Topology:
        if (
            self._topology is None
            or self._topology_refreshed_at is None
            or now - self._topology_refreshed_at >= TOPOLOGY_REFRESH_INTERVAL
        ):
            self._topology = await self.client.get_topology(self.system_id)
            self._topology_refreshed_at = now
            system_timezone = getattr(self._topology.system, "timezone", None)
            if system_timezone:
                try:
                    self.time_zone = ZoneInfo(str(system_timezone))
                except ZoneInfoNotFoundError:
                    LOGGER.warning(
                        "Tigo returned an unknown timezone; keeping %s",
                        self.time_zone,
                    )
        return self._topology

    async def _async_system_info(self, local_day: date) -> SystemInfo:
        """Refresh solar-day metadata once for each system-local date."""
        if self._system_info is None or self._system_info.day != local_day:
            self._system_info = await self.client.get_system_info(
                self.system_id, local_day
            )
            if self._system_info.timezone:
                try:
                    self.time_zone = ZoneInfo(self._system_info.timezone)
                except ZoneInfoNotFoundError:
                    LOGGER.warning(
                        "Tigo returned an unknown system-info timezone; keeping %s",
                        self.time_zone,
                    )
        return self._system_info


def _snapshot_last_update(snapshot: Any) -> datetime | None:
    """Find the newest authoritative cloud timestamp in a snapshot."""
    value = getattr(snapshot, "last_update", None) or getattr(
        snapshot, "last_cloud_update", None
    )
    if isinstance(value, datetime):
        return value
    readings = getattr(snapshot, "modules", ()) or ()
    timestamps = [
        timestamp
        for reading in readings
        if isinstance((timestamp := getattr(reading, "sample_time", None)), datetime)
    ]
    return max(timestamps, default=None)


def _is_daylight(local_now: datetime, system_info: Any) -> bool:
    """Use Tigo sunrise/sunset metadata, with a conservative local fallback."""
    sunrise = _as_local_time(getattr(system_info, "sunrise", None), local_now.date())
    sunset = _as_local_time(getattr(system_info, "sunset", None), local_now.date())
    sunrise = sunrise or time(5, 0)
    sunset = sunset or time(21, 0)
    return sunrise <= local_now.timetz().replace(tzinfo=None) <= sunset


def _as_local_time(value: Any, today: date) -> time | None:
    if isinstance(value, time):
        return value.replace(tzinfo=None)
    if isinstance(value, datetime):
        return value.timetz().replace(tzinfo=None)
    if isinstance(value, str):
        candidate = value.strip()
        try:
            if "T" in candidate:
                return datetime.fromisoformat(candidate.replace("Z", "+00:00")).time()
            return time.fromisoformat(candidate)
        except ValueError:
            return None
    return None
