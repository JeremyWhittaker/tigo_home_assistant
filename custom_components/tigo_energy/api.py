"""Async client for the read-only Tigo Energy mobile cloud API.

The endpoints used here are the JSON endpoints consumed by Tigo's mobile
application.  They are unofficial and may change.  This module deliberately
keeps endpoint details behind a small typed surface so a future API change does
not leak into Home Assistant entities.
"""

from __future__ import annotations

import asyncio
import email.utils
import math
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any

import aiohttp

from .exceptions import (
    TigoAPIError,
    TigoAuthenticationError,
    TigoConnectionError,
    TigoDataError,
    TigoError,
    TigoFeatureUnavailableError,
    TigoRateLimitError,
    TigoServiceUnavailableError,
)
from .models import (
    PanelEnergySummary,
    PanelPowerSummary,
    PanelReading,
    PeakPower,
    ProductionTotals,
    SystemInfo,
    SystemSnapshot,
    TigoSystem,
    Topology,
    build_snapshot,
    parse_datetime,
    parse_homepage,
    parse_panel_energy,
    parse_panel_power,
    parse_peak_power,
    parse_system_info,
    parse_systems,
    parse_topology,
)

DEFAULT_BASE_URL = "https://mapi.tigoenergy.com"
DEFAULT_APP_VERSION = "5.4.7-04"
DEFAULT_TIMEOUT = 30.0
MAX_RETRY_AFTER_SECONDS = 6 * 60 * 60
_EXPIRY_MARGIN = timedelta(days=1)
_MAX_ETAG_CACHE_ENTRIES = 64
_VOLATILE_CACHE_PARAMETERS = frozenset({"date", "resourceid"})


@dataclass(slots=True)
class _CacheEntry:
    etag: str
    payload: Any


def parse_retry_after(
    value: str | None, *, now: datetime | None = None
) -> float | None:
    """Parse Retry-After delta-seconds or an RFC-compliant HTTP date."""

    if not value:
        return None
    candidate = value.strip()
    try:
        seconds = float(candidate)
    except ValueError:
        seconds = None
    if seconds is not None:
        if not math.isfinite(seconds):
            return None
        return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS)
    try:
        target = email.utils.parsedate_to_datetime(candidate)
    except (TypeError, ValueError, OverflowError):
        return None
    if target is None:
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    seconds_until_retry = (target - current).total_seconds()
    if not math.isfinite(seconds_until_retry):
        return None
    return min(max(seconds_until_retry, 0.0), MAX_RETRY_AFTER_SECONDS)


class TigoCloudClient:
    """Small async wrapper around the Tigo mobile cloud endpoints.

    ``session`` is owned by Home Assistant and is never closed here.  Account
    credentials are retained only so the client can obtain a replacement
    bearer token; token and ETag state are memory-only.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        app_version: str = DEFAULT_APP_VERSION,
        request_timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._base_url = base_url.rstrip("/")
        self._app_version = app_version
        self._timeout = aiohttp.ClientTimeout(total=request_timeout)
        self._base_headers = {
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "capacitor://localhost",
            "User-Agent": "TigoEnergyHomeAssistant/0.1",
            "X-App-Version": app_version,
        }
        self._token: str | None = None
        self._token_expires: datetime | None = None
        self._login_lock = asyncio.Lock()
        self._etag_cache: OrderedDict[tuple[Any, ...], _CacheEntry] = OrderedDict()

    def __repr__(self) -> str:
        """Return a representation that cannot expose credentials or tokens."""

        return f"{type(self).__name__}(base_url={self._base_url!r})"

    @property
    def authenticated(self) -> bool:
        """Whether this process currently holds a bearer token."""

        return self._token is not None

    @property
    def token_expires(self) -> datetime | None:
        """Expose expiry metadata, never the token itself."""

        return self._token_expires

    @property
    def diagnostics(self) -> dict[str, Any]:
        """Return process state that is safe for downloadable diagnostics."""

        return {
            "api": "unofficial_mobile_cloud",
            "app_version_header": self._app_version,
            "authenticated": self.authenticated,
            "token_expires": (
                self._token_expires.isoformat() if self._token_expires else None
            ),
            "conditional_cache_entries": len(self._etag_cache),
        }

    async def login(self) -> None:
        """Authenticate explicitly (normal requests also authenticate lazily)."""

        async with self._login_lock:
            await self._login_locked()

    async def _login_locked(self) -> None:
        endpoint = "login"
        payload = await self._request_once(
            "POST",
            "/api/v3/user/login",
            endpoint=endpoint,
            params={"type": 8},
            json_data={"username": self._username, "password": self._password},
            authenticated=False,
            use_etag=False,
            login_request=True,
        )
        user = self._find_login_user(payload)
        token = self._first_value(user, "auth", "token", "access_token", "accessToken")
        if not isinstance(token, str) or not token:
            raise TigoDataError(
                "Tigo login response did not contain a bearer token",
                endpoint=endpoint,
            )
        expires_raw = self._first_value(
            user, "expires", "expires_at", "expiresAt", "expiration"
        )
        self._token = token
        self._token_expires = parse_datetime(expires_raw, system_timezone=UTC)

    @staticmethod
    def _find_login_user(payload: Any) -> Mapping[str, Any]:
        root = payload if isinstance(payload, Mapping) else {}
        data = root.get("data") if isinstance(root.get("data"), Mapping) else root
        user = data.get("user") if isinstance(data.get("user"), Mapping) else data
        return user if isinstance(user, Mapping) else {}

    @staticmethod
    def _first_value(data: Mapping[str, Any], *names: str) -> Any:
        for name in names:
            if name in data:
                return data[name]
        return None

    def _token_needs_refresh(self) -> bool:
        if self._token is None:
            return True
        if self._token_expires is None:
            return False
        current = datetime.now(self._token_expires.tzinfo or UTC)
        return current >= self._token_expires - _EXPIRY_MARGIN

    async def _ensure_authenticated(self) -> None:
        if not self._token_needs_refresh():
            return
        async with self._login_lock:
            if self._token_needs_refresh():
                await self._login_locked()

    async def _relogin_after_rejection(self, rejected_token: str | None) -> None:
        async with self._login_lock:
            # Another concurrent request may already have refreshed it.
            if self._token is not None and self._token != rejected_token:
                return
            await self._login_locked()

    @staticmethod
    def _cache_key(
        method: str,
        path: str,
        params: Mapping[str, Any] | None,
    ) -> tuple[Any, ...]:
        normalized = tuple(
            sorted((str(key), str(value)) for key, value in (params or {}).items())
        )
        return method.upper(), path, normalized

    @staticmethod
    def _cache_family_key(cache_key: tuple[Any, ...]) -> tuple[Any, ...]:
        """Group dated requests whose newest response supersedes older days."""

        method, path, params = cache_key
        stable_params = tuple(
            (key, value)
            for key, value in params
            if key.lower() not in _VOLATILE_CACHE_PARAMETERS
        )
        return method, path, stable_params

    def _remember_response(
        self,
        cache_key: tuple[Any, ...],
        *,
        etag: str,
        payload: Any,
    ) -> None:
        """Store one response while bounding and aging conditional state."""

        family_key = self._cache_family_key(cache_key)
        for existing_key in tuple(self._etag_cache):
            if (
                existing_key != cache_key
                and self._cache_family_key(existing_key) == family_key
            ):
                del self._etag_cache[existing_key]

        self._etag_cache[cache_key] = _CacheEntry(
            etag=etag,
            payload=deepcopy(payload),
        )
        self._etag_cache.move_to_end(cache_key)
        while len(self._etag_cache) > _MAX_ETAG_CACHE_ENTRIES:
            self._etag_cache.popitem(last=False)

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        endpoint: str,
        params: Mapping[str, Any] | None = None,
        json_data: Any = None,
        use_etag: bool = True,
    ) -> Any:
        await self._ensure_authenticated()
        rejected_token = self._token
        try:
            return await self._request_once(
                method,
                path,
                endpoint=endpoint,
                params=params,
                json_data=json_data,
                authenticated=True,
                use_etag=use_etag,
            )
        except TigoAuthenticationError as err:
            if err.status != 401:
                raise
        # A rejected bearer is reissued once and the original request is
        # attempted exactly once more.  A second 401 surfaces to config flow.
        await self._relogin_after_rejection(rejected_token)
        return await self._request_once(
            method,
            path,
            endpoint=endpoint,
            params=params,
            json_data=json_data,
            authenticated=True,
            use_etag=use_etag,
        )

    async def _request_once(
        self,
        method: str,
        path: str,
        *,
        endpoint: str,
        params: Mapping[str, Any] | None,
        json_data: Any,
        authenticated: bool,
        use_etag: bool,
        login_request: bool = False,
    ) -> Any:
        method = method.upper()
        cache_key = self._cache_key(method, path, params)
        cached = (
            self._etag_cache.get(cache_key) if use_etag and method == "GET" else None
        )
        if cached is not None:
            self._etag_cache.move_to_end(cache_key)
        headers = dict(self._base_headers)
        if json_data is not None:
            headers["Content-Type"] = "application/json"
        if authenticated:
            headers["Authorization"] = f"Bearer {self._token}"
        if cached is not None:
            headers["If-None-Match"] = cached.etag

        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                params=dict(params or {}),
                json=json_data,
                headers=headers,
                timeout=self._timeout,
            ) as response:
                status = response.status
                response_headers = response.headers
                if status == 304:
                    if cached is None:
                        raise TigoDataError(
                            "Tigo returned 304 without a cached response",
                            endpoint=endpoint,
                            status=status,
                        )
                    return deepcopy(cached.payload)
                if status == 401 or (login_request and status == 403):
                    raise TigoAuthenticationError(
                        "Tigo authentication was rejected",
                        endpoint=endpoint,
                        status=status,
                    )
                retry_after = parse_retry_after(response_headers.get("Retry-After"))
                if status == 429:
                    raise TigoRateLimitError(
                        "Tigo rate limit reached",
                        endpoint=endpoint,
                        status=status,
                        retry_after=retry_after,
                    )
                if status == 503:
                    raise TigoServiceUnavailableError(
                        "Tigo service is temporarily unavailable",
                        endpoint=endpoint,
                        status=status,
                        retry_after=retry_after,
                    )
                if status in (402, 403, 404, 422):
                    raise TigoFeatureUnavailableError(
                        "Tigo endpoint is unavailable for this system",
                        endpoint=endpoint,
                        status=status,
                    )
                if not 200 <= status < 300:
                    error_type = (
                        TigoServiceUnavailableError if status >= 500 else TigoAPIError
                    )
                    raise error_type(
                        "Tigo returned an unexpected HTTP status",
                        endpoint=endpoint,
                        status=status,
                    )
                try:
                    payload = await response.json(content_type=None)
                except (TypeError, ValueError, UnicodeDecodeError):
                    raise TigoDataError(
                        "Tigo returned a non-JSON response",
                        endpoint=endpoint,
                        status=status,
                    ) from None
                etag = response_headers.get("ETag")
                if use_etag and method == "GET" and etag:
                    self._remember_response(
                        cache_key,
                        etag=etag,
                        payload=payload,
                    )
                return deepcopy(payload)
        except TigoError:
            raise
        except (aiohttp.ClientError, TimeoutError, OSError):
            # aiohttp exceptions often include the full URL.  Suppress that
            # detail so secret-like UIDs in query parameters never reach logs.
            raise TigoConnectionError(
                "Unable to reach the Tigo cloud service",
                endpoint=endpoint,
            ) from None

    async def get_systems(self) -> tuple[TigoSystem, ...]:
        """Return all systems visible to the account."""

        payload = await self._request_json(
            "GET",
            "/api/v3/systems/query",
            endpoint="systems",
            params={"limit": 100, "page": 1, "sort": "-id"},
        )
        return parse_systems(payload)

    async def get_system_info(
        self,
        system_id: int,
        day: date | str,
    ) -> SystemInfo:
        """Return account capabilities and sunrise/sunset for ``day``."""

        day_text = day.isoformat() if isinstance(day, date) else str(day)
        payload = await self._request_json(
            "GET",
            "/api/v3/tigobuild/systeminfo",
            endpoint="system info",
            params={
                "system_id": system_id,
                "date": day_text,
                "resourceId": f"dateinfo-{day_text}",
            },
        )
        return parse_system_info(payload, system_id, day)

    async def get_layout(self, system_id: int) -> Mapping[str, Any]:
        """Return a defensive copy of the raw layout tree."""

        payload = await self._request_json(
            "GET",
            "/api/v3/systems/layout",
            endpoint="system layout",
            params={"id": system_id},
        )
        if not isinstance(payload, Mapping):
            raise TigoDataError(
                "Tigo returned an invalid system layout",
                endpoint="system layout",
            )
        return payload

    # Name retained for discoverability alongside Tigo's endpoint terminology.
    get_system_layout = get_layout

    async def get_equipments(self, system_id: int) -> tuple[Mapping[str, Any], ...]:
        """Return the equipment list in Tigo's original order."""

        payload = await self._request_json(
            "GET",
            "/api/v4/equipments",
            endpoint="equipment",
            params={"systemId": system_id},
        )
        if isinstance(payload, Mapping):
            payload = (
                payload.get("equipments")
                or payload.get("data")
                or payload.get("result")
                or payload.get("items")
                or ()
            )
        if not isinstance(payload, Sequence) or isinstance(
            payload, (str, bytes, bytearray)
        ):
            raise TigoDataError(
                "Tigo returned an invalid equipment list",
                endpoint="equipment",
            )
        return tuple(item for item in payload if isinstance(item, Mapping))

    async def get_build_configuration(self, system_id: int) -> Mapping[str, Any]:
        """Return installer-entered panel ratings from the mobile build model."""

        payload = await self._request_json(
            "GET",
            "/api/v3/tigobuild/config",
            endpoint="build configuration",
            params={"system_id": system_id, "resourceId": "config"},
        )
        if not isinstance(payload, Mapping):
            raise TigoDataError(
                "Tigo returned an invalid build configuration",
                endpoint="build configuration",
            )
        return payload

    async def get_topology(self, system: TigoSystem | int) -> Topology:
        """Fetch and join layout/equipment metadata."""

        system_id = system.id if isinstance(system, TigoSystem) else int(system)

        async def optional_build_configuration() -> Mapping[str, Any]:
            try:
                return await self.get_build_configuration(system_id)
            except TigoFeatureUnavailableError:
                # Capacity is useful context, never a topology prerequisite.
                return {}

        layout, equipments, configuration = await asyncio.gather(
            self.get_layout(system_id),
            self.get_equipments(system_id),
            optional_build_configuration(),
        )
        return parse_topology(system, layout, equipments, configuration)

    async def get_homepage(
        self,
        system_id: int,
        *,
        day: date | str | None = None,
        system_timezone: str | tzinfo | None = None,
    ) -> ProductionTotals:
        """Return authoritative current/day/week/month/year/lifetime values."""

        payload = await self._request_json(
            "GET",
            f"/api/v4/smart/systems/{system_id}/homepage",
            endpoint="homepage",
        )
        return parse_homepage(
            payload,
            day=day,
            system_timezone=system_timezone,
        )

    async def get_panel_summary(
        self,
        system_id: int,
        cca_uid: str,
        day: date | str,
        *,
        metric: str = "pin",
    ) -> Mapping[str, Any]:
        """Return one raw daily panel-summary payload."""

        day_text = day.isoformat() if isinstance(day, date) else str(day)
        payload = await self._request_json(
            "GET",
            "/api/v4/system/summary/summary",
            endpoint="panel summary",
            params={
                "system_id": system_id,
                "date": day_text,
                "temp": metric,
                "uid": cca_uid,
                "resourceId": f"data-{day_text}-{metric}-{cca_uid}",
            },
        )
        if not isinstance(payload, Mapping):
            raise TigoDataError(
                "Tigo returned an invalid panel summary",
                endpoint="panel summary",
            )
        return payload

    async def get_panel_power(
        self,
        system_id: int,
        cca_uid: str,
        day: date | str,
        *,
        topology: Topology,
        system_timezone: str | tzinfo | None = None,
    ) -> PanelPowerSummary:
        """Return latest independently selected module power values."""

        payload = await self.get_panel_summary(
            system_id,
            cca_uid,
            day,
            metric="pin",
        )
        return parse_panel_power(
            payload,
            topology,
            day,
            system_timezone=system_timezone,
        )

    async def get_agg_energy(
        self,
        system_id: int,
        day: date | str,
    ) -> Mapping[str, Any]:
        """Return raw per-object daily energy (Wh)."""

        day_text = day.isoformat() if isinstance(day, date) else str(day)
        payload = await self._request_json(
            "GET",
            "/api/v4/system/summary/aggenergy",
            endpoint="panel energy",
            params={
                "system_id": system_id,
                "date": day_text,
                "temp": "energy",
                "resourceId": f"data-{day_text}-energy",
            },
        )
        if not isinstance(payload, Mapping):
            raise TigoDataError(
                "Tigo returned an invalid energy summary",
                endpoint="panel energy",
            )
        return payload

    async def get_panel_energy(
        self,
        system_id: int,
        day: date | str,
        *,
        topology: Topology,
        system_timezone: str | tzinfo | None = None,
    ) -> PanelEnergySummary:
        """Return per-module daily energy in kWh."""

        payload = await self.get_agg_energy(system_id, day)
        return parse_panel_energy(
            payload,
            topology,
            day,
            system_timezone=system_timezone,
        )

    async def get_agg_power(
        self,
        system_id: int,
        day: date | str,
    ) -> Mapping[str, Any]:
        """Return raw aggregate daily-power statistics."""

        day_text = day.isoformat() if isinstance(day, date) else str(day)
        payload = await self._request_json(
            "GET",
            "/api/v4/system/summary/aggpower",
            endpoint="peak power",
            params={
                "system_id": system_id,
                "date": day_text,
                "temp": "power",
                "resourceId": f"data-{day_text}-power",
            },
        )
        if not isinstance(payload, Mapping):
            raise TigoDataError(
                "Tigo returned an invalid power summary",
                endpoint="peak power",
            )
        return payload

    async def get_peak_power(
        self,
        system_id: int,
        day: date | str,
        *,
        system_timezone: str | tzinfo | None = None,
    ) -> PeakPower:
        """Return today's peak production power."""

        payload = await self.get_agg_power(system_id, day)
        return parse_peak_power(
            payload,
            day=day,
            system_timezone=system_timezone,
        )

    async def get_snapshot(
        self,
        system_id: int,
        day: date | str,
        *,
        topology: Topology,
        cca_uid: str | None = None,
        system_timezone: str | tzinfo | None = None,
        include_panel_energy: bool = True,
        include_peak_power: bool = True,
    ) -> SystemSnapshot:
        """Fetch and combine all dynamic data used by Home Assistant.

        Homepage and module-power failures always surface.  Energy and peak
        endpoints are optional account capabilities: only an explicit
        entitlement/not-found response suppresses those values.  Connection,
        authentication, throttling, service, and malformed-data failures still
        surface so Home Assistant can report the integration unhealthy.
        """

        cca_uids = tuple(
            dict.fromkeys(
                uid
                for uid in ((cca_uid,) if cca_uid else ()) + topology.cca_uids
                if uid
            )
        )
        if not cca_uids:
            raise TigoDataError("Tigo topology does not identify a CCA")

        homepage_task = asyncio.create_task(
            self.get_homepage(
                system_id,
                day=day,
                system_timezone=system_timezone,
            )
        )

        async def all_panel_power() -> PanelPowerSummary:
            summaries = await asyncio.gather(
                *(
                    self.get_panel_power(
                        system_id,
                        uid,
                        day,
                        topology=topology,
                        system_timezone=system_timezone,
                    )
                    for uid in cca_uids
                )
            )
            return self._merge_panel_power(topology, summaries)

        power_task = asyncio.create_task(all_panel_power())

        async def optional_energy() -> PanelEnergySummary | None:
            if not include_panel_energy:
                return None
            try:
                return await self.get_panel_energy(
                    system_id,
                    day,
                    topology=topology,
                    system_timezone=system_timezone,
                )
            except TigoFeatureUnavailableError:
                return None

        async def optional_peak() -> PeakPower | None:
            if not include_peak_power:
                return None
            try:
                return await self.get_peak_power(
                    system_id,
                    day,
                    system_timezone=system_timezone,
                )
            except TigoFeatureUnavailableError:
                return None

        energy_task = asyncio.create_task(optional_energy())
        peak_task = asyncio.create_task(optional_peak())
        production, power, energy, peak = await asyncio.gather(
            homepage_task,
            power_task,
            energy_task,
            peak_task,
        )
        return build_snapshot(topology, production, power, energy, peak)

    @staticmethod
    def _merge_panel_power(
        topology: Topology,
        summaries: Sequence[PanelPowerSummary],
    ) -> PanelPowerSummary:
        """Merge CCA responses using each module's newest valid sample."""

        selected: dict[str, PanelReading] = {}
        for summary in summaries:
            for reading in summary.readings:
                if reading.power_w is None:
                    continue
                current = selected.get(reading.object_id)
                if current is None or (
                    reading.sample_time is not None
                    and (
                        current.sample_time is None
                        or reading.sample_time > current.sample_time
                    )
                ):
                    selected[reading.object_id] = reading

        readings = tuple(
            PanelReading(
                module=module,
                power_w=(
                    selected[module.object_id].power_w
                    if module.object_id in selected
                    else None
                ),
                sample_time=(
                    selected[module.object_id].sample_time
                    if module.object_id in selected
                    else None
                ),
            )
            for module in topology.modules
        )
        updates = [
            summary.last_update
            for summary in summaries
            if summary.last_update is not None
        ]
        return PanelPowerSummary(
            readings=readings,
            last_update=max(updates) if updates else None,
        )

    def clear_response_cache(self) -> None:
        """Clear conditional-response state, primarily for reconfiguration."""

        self._etag_cache.clear()


# Backwards-obvious name for callers that prefer API-specific terminology.
TigoMobileAPI = TigoCloudClient


__all__ = [
    "DEFAULT_APP_VERSION",
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT",
    "MAX_RETRY_AFTER_SECONDS",
    "TigoCloudClient",
    "TigoMobileAPI",
    "parse_retry_after",
]
