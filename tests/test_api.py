"""Fast unit tests for the async Tigo mobile-cloud client."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict, deque
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp
import pytest

from custom_components.tigo_energy.api import TigoCloudClient, parse_retry_after
from custom_components.tigo_energy.exceptions import (
    TigoAuthenticationError,
    TigoConnectionError,
    TigoDataError,
    TigoRateLimitError,
    TigoServiceUnavailableError,
)
from custom_components.tigo_energy.models import parse_systems, parse_topology

FIXTURES = Path(__file__).parent / "fixtures"
TEST_USER = "user@example.invalid"
TEST_PASSWORD = "TEST-PASSWORD-NOT-A-SECRET"
TEST_TOKEN_1 = "TEST-TOKEN-ONE"
TEST_TOKEN_2 = "TEST-TOKEN-TWO"


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeResponse:
    """Minimal aiohttp response context manager."""

    def __init__(
        self,
        status: int,
        payload: Any = None,
        *,
        headers: dict[str, str] | None = None,
        json_error: Exception | None = None,
    ) -> None:
        self.status = status
        self.payload = payload
        self.headers = headers or {}
        self.json_error = json_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def json(self, *, content_type=None):
        if self.json_error:
            raise self.json_error
        return self.payload


class RaisingContext:
    """Context manager that raises a network error on entry."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def __aenter__(self):
        raise self.error

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeSession:
    """Path-routed scripted session supporting concurrent client calls."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], deque[Any]] = defaultdict(deque)
        self.calls: list[dict[str, Any]] = []

    def add(self, method: str, path: str, *events: Any) -> None:
        self.routes[(method.upper(), path)].extend(events)

    def request(self, method: str, url: str, **kwargs: Any):
        path = urlsplit(url).path
        key = (method.upper(), path)
        self.calls.append(
            {"method": method.upper(), "url": url, "path": path, **kwargs}
        )
        if not self.routes[key]:
            raise AssertionError(f"No fake response for {key}")
        event = self.routes[key].popleft()
        if isinstance(event, Exception):
            return RaisingContext(event)
        return event


def login_payload(
    token: str = TEST_TOKEN_1,
    *,
    expires: str = "2099-01-01T00:00:00+00:00",
) -> dict[str, Any]:
    return {
        "user": {
            "auth": token,
            "expires": expires,
            "refresh_token": "NOT-USED-BY-CLIENT",
        }
    }


def client_with_login(session: FakeSession) -> TigoCloudClient:
    session.add("POST", "/api/v3/user/login", FakeResponse(200, login_payload()))
    return TigoCloudClient(session, TEST_USER, TEST_PASSWORD)  # type: ignore[arg-type]


def run(coroutine):
    return asyncio.run(coroutine)


def test_lazy_login_and_system_discovery_use_mobile_headers() -> None:
    session = FakeSession()
    client = client_with_login(session)
    session.add(
        "GET", "/api/v3/systems/query", FakeResponse(200, fixture("systems.json"))
    )

    systems = run(client.get_systems())

    assert systems == parse_systems(fixture("systems.json"))
    login_call, systems_call = session.calls
    assert login_call["params"] == {"type": 8}
    assert login_call["json"] == {
        "username": TEST_USER,
        "password": TEST_PASSWORD,
    }
    assert "Authorization" not in login_call["headers"]
    assert systems_call["headers"]["Authorization"] == f"Bearer {TEST_TOKEN_1}"
    assert systems_call["headers"]["Origin"] == "capacitor://localhost"
    assert systems_call["headers"]["X-App-Version"]
    assert systems_call["params"] == {"limit": 100, "page": 1, "sort": "-id"}
    assert TEST_TOKEN_1 not in systems_call["url"]
    assert TEST_PASSWORD not in repr(client)
    serialized_diagnostics = json.dumps(client.diagnostics)
    assert TEST_TOKEN_1 not in serialized_diagnostics
    assert TEST_USER not in serialized_diagnostics
    assert TEST_PASSWORD not in serialized_diagnostics


def test_etag_304_returns_defensive_cached_copy() -> None:
    session = FakeSession()
    client = client_with_login(session)
    session.add(
        "GET",
        "/api/v3/systems/layout",
        FakeResponse(200, fixture("layout.json"), headers={"ETag": '"layout-v1"'}),
        FakeResponse(304),
    )

    first = run(client.get_layout(1))
    first["system"]["name"] = "caller mutation"  # type: ignore[index]
    second = run(client.get_layout(1))

    assert second["system"]["name"] == "Example Solar Array"
    get_calls = [call for call in session.calls if call["method"] == "GET"]
    assert "If-None-Match" not in get_calls[0]["headers"]
    assert get_calls[1]["headers"]["If-None-Match"] == '"layout-v1"'


def test_etag_cache_replaces_older_daily_response() -> None:
    session = FakeSession()
    client = client_with_login(session)
    session.add(
        "GET",
        "/api/v3/tigobuild/systeminfo",
        FakeResponse(200, fixture("system_info.json"), headers={"ETag": '"day-1"'}),
        FakeResponse(200, fixture("system_info.json"), headers={"ETag": '"day-2"'}),
        FakeResponse(304),
        FakeResponse(200, fixture("system_info.json"), headers={"ETag": '"day-1b"'}),
    )

    run(client.get_system_info(1, "2026-08-28"))
    run(client.get_system_info(1, "2026-08-29"))
    run(client.get_system_info(1, "2026-08-29"))
    run(client.get_system_info(1, "2026-08-28"))

    get_calls = [call for call in session.calls if call["method"] == "GET"]
    assert get_calls[2]["headers"]["If-None-Match"] == '"day-2"'
    assert "If-None-Match" not in get_calls[3]["headers"]
    assert client.diagnostics["conditional_cache_entries"] == 1


def test_etag_cache_is_lru_bounded() -> None:
    session = FakeSession()
    client = client_with_login(session)
    session.add(
        "GET",
        "/api/v3/systems/layout",
        *(
            FakeResponse(
                200,
                fixture("layout.json"),
                headers={"ETag": f'"layout-{system_id}"'},
            )
            for system_id in range(1, 66)
        ),
        FakeResponse(200, fixture("layout.json"), headers={"ETag": '"layout-1b"'}),
        FakeResponse(304),
    )

    for system_id in range(1, 66):
        run(client.get_layout(system_id))
    run(client.get_layout(1))
    run(client.get_layout(65))

    get_calls = [call for call in session.calls if call["method"] == "GET"]
    assert "If-None-Match" not in get_calls[65]["headers"]
    assert get_calls[66]["headers"]["If-None-Match"] == '"layout-65"'
    assert client.diagnostics["conditional_cache_entries"] == 64


def test_topology_includes_installer_configured_array_capacity() -> None:
    session = FakeSession()
    client = client_with_login(session)
    session.add(
        "GET", "/api/v3/systems/layout", FakeResponse(200, fixture("layout.json"))
    )
    session.add(
        "GET", "/api/v4/equipments", FakeResponse(200, fixture("equipments.json"))
    )
    session.add(
        "GET",
        "/api/v3/tigobuild/config",
        FakeResponse(
            200,
            {
                "system": {
                    "objects": [
                        {"A": 101, "B": 2, "J": 400},
                        {"A": 102, "B": 2, "J": 410},
                    ]
                }
            },
        ),
    )

    topology = run(client.get_topology(1))

    assert topology.rated_power_w == 810.0
    config_call = next(
        call for call in session.calls if call["path"].endswith("/tigobuild/config")
    )
    assert config_call["params"] == {"system_id": 1, "resourceId": "config"}


def test_topology_keeps_capacity_optional_when_build_config_is_unavailable() -> None:
    session = FakeSession()
    client = client_with_login(session)
    session.add(
        "GET", "/api/v3/systems/layout", FakeResponse(200, fixture("layout.json"))
    )
    session.add(
        "GET", "/api/v4/equipments", FakeResponse(200, fixture("equipments.json"))
    )
    session.add("GET", "/api/v3/tigobuild/config", FakeResponse(404))

    topology = run(client.get_topology(1))

    assert topology.rated_power_w is None


def test_401_relogs_in_and_retries_original_request_exactly_once() -> None:
    session = FakeSession()
    session.add(
        "POST",
        "/api/v3/user/login",
        FakeResponse(200, login_payload(TEST_TOKEN_1)),
        FakeResponse(200, login_payload(TEST_TOKEN_2)),
    )
    session.add(
        "GET",
        "/api/v3/systems/query",
        FakeResponse(401),
        FakeResponse(200, fixture("systems.json")),
    )
    client = TigoCloudClient(session, TEST_USER, TEST_PASSWORD)  # type: ignore[arg-type]

    systems = run(client.get_systems())

    assert len(systems) == 1
    login_calls = [call for call in session.calls if call["path"].endswith("/login")]
    systems_calls = [
        call for call in session.calls if call["path"].endswith("/systems/query")
    ]
    assert len(login_calls) == 2
    assert len(systems_calls) == 2
    assert systems_calls[0]["headers"]["Authorization"] == f"Bearer {TEST_TOKEN_1}"
    assert systems_calls[1]["headers"]["Authorization"] == f"Bearer {TEST_TOKEN_2}"


def test_second_401_surfaces_without_a_third_login_or_request() -> None:
    session = FakeSession()
    session.add(
        "POST",
        "/api/v3/user/login",
        FakeResponse(200, login_payload(TEST_TOKEN_1)),
        FakeResponse(200, login_payload(TEST_TOKEN_2)),
    )
    session.add(
        "GET",
        "/api/v3/systems/query",
        FakeResponse(401),
        FakeResponse(401),
    )
    client = TigoCloudClient(session, TEST_USER, TEST_PASSWORD)  # type: ignore[arg-type]

    with pytest.raises(TigoAuthenticationError):
        run(client.get_systems())

    assert len(session.calls) == 4


def test_near_expiry_token_is_replaced_before_the_next_request() -> None:
    session = FakeSession()
    session.add(
        "POST",
        "/api/v3/user/login",
        FakeResponse(
            200,
            login_payload(TEST_TOKEN_1, expires="2000-01-01T00:00:00+00:00"),
        ),
        FakeResponse(200, login_payload(TEST_TOKEN_2)),
    )
    session.add(
        "GET",
        "/api/v3/systems/query",
        FakeResponse(200, fixture("systems.json")),
        FakeResponse(200, fixture("systems.json")),
    )
    client = TigoCloudClient(session, TEST_USER, TEST_PASSWORD)  # type: ignore[arg-type]

    run(client.get_systems())
    run(client.get_systems())

    systems_calls = [
        call for call in session.calls if call["path"].endswith("/systems/query")
    ]
    assert systems_calls[0]["headers"]["Authorization"] == f"Bearer {TEST_TOKEN_1}"
    assert systems_calls[1]["headers"]["Authorization"] == f"Bearer {TEST_TOKEN_2}"


def test_bad_credentials_are_sanitized() -> None:
    session = FakeSession()
    session.add("POST", "/api/v3/user/login", FakeResponse(403, {"password": "echo"}))
    client = TigoCloudClient(session, TEST_USER, TEST_PASSWORD)  # type: ignore[arg-type]

    with pytest.raises(TigoAuthenticationError) as caught:
        run(client.login())

    assert caught.value.status == 403
    assert TEST_USER not in str(caught.value)
    assert TEST_PASSWORD not in str(caught.value)
    assert "echo" not in str(caught.value)


def test_rate_limit_preserves_retry_after_without_sleeping() -> None:
    session = FakeSession()
    client = client_with_login(session)
    session.add(
        "GET",
        "/api/v3/systems/query",
        FakeResponse(429, headers={"Retry-After": "17"}),
    )

    with pytest.raises(TigoRateLimitError) as caught:
        run(client.get_systems())

    assert caught.value.status == 429
    assert caught.value.retry_after == 17.0


def test_service_unavailable_is_retryable_with_or_without_header() -> None:
    session = FakeSession()
    client = client_with_login(session)
    session.add("GET", "/api/v3/systems/query", FakeResponse(503))

    with pytest.raises(TigoServiceUnavailableError) as caught:
        run(client.get_systems())

    assert caught.value.status == 503
    assert caught.value.retry_after is None


def test_retry_after_http_date_is_parsed_against_utc() -> None:
    now = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)

    assert parse_retry_after("Fri, 28 Aug 2026 12:02:30 GMT", now=now) == 150.0
    assert parse_retry_after("Fri, 28 Aug 2026 22:00:00 GMT", now=now) == 21_600.0
    assert parse_retry_after("nonsense", now=now) is None


@pytest.mark.parametrize("value", ["Infinity", "-Infinity", "NaN"])
def test_retry_after_rejects_non_finite_values(value: str) -> None:
    assert parse_retry_after(value) is None


def test_retry_after_clamps_oversized_delta_seconds() -> None:
    assert parse_retry_after("86400000000") == 21_600.0


def test_network_error_does_not_echo_secret_like_query_values() -> None:
    session = FakeSession()
    client = client_with_login(session)
    session.add(
        "GET",
        "/api/v4/system/summary/summary",
        aiohttp.ClientConnectionError("failed URL ?uid=SHOULD-NOT-LEAK"),
    )
    run(client.login())

    with pytest.raises(TigoConnectionError) as caught:
        run(client.get_panel_summary(1, "SHOULD-NOT-LEAK", "2026-08-28"))

    assert "SHOULD-NOT-LEAK" not in str(caught.value)
    assert caught.value.endpoint == "panel summary"
    panel_call = session.calls[-1]
    assert "SHOULD-NOT-LEAK" not in panel_call["url"]
    assert panel_call["params"]["uid"] == "SHOULD-NOT-LEAK"


def test_timeout_becomes_sanitized_connection_error() -> None:
    session = FakeSession()
    client = client_with_login(session)
    session.add("GET", "/api/v3/systems/query", TimeoutError("request timed out"))

    with pytest.raises(TigoConnectionError, match="Unable to reach") as caught:
        run(client.get_systems())

    assert "request timed out" not in str(caught.value)
    assert caught.value.endpoint == "systems"


def test_malformed_json_becomes_typed_data_error() -> None:
    session = FakeSession()
    client = client_with_login(session)
    session.add(
        "GET",
        "/api/v3/systems/query",
        FakeResponse(200, json_error=ValueError("invalid JSON body")),
    )

    with pytest.raises(TigoDataError, match="non-JSON") as caught:
        run(client.get_systems())

    assert "invalid JSON body" not in str(caught.value)
    assert caught.value.endpoint == "systems"


def test_snapshot_tolerates_only_explicitly_unavailable_optional_endpoints() -> None:
    session = FakeSession()
    client = client_with_login(session)
    run(client.login())
    parsed_topology = parse_topology(
        parse_systems(fixture("systems.json"))[0],
        fixture("layout.json"),
        fixture("equipments.json"),
    )
    session.add(
        "GET",
        "/api/v4/smart/systems/1/homepage",
        FakeResponse(200, fixture("homepage.json")),
    )
    session.add(
        "GET",
        "/api/v4/system/summary/summary",
        FakeResponse(200, fixture("panel_power.json")),
    )
    session.add("GET", "/api/v4/system/summary/aggenergy", FakeResponse(404))
    session.add("GET", "/api/v4/system/summary/aggpower", FakeResponse(403))

    snapshot = run(
        client.get_snapshot(
            1,
            "2026-08-28",
            topology=parsed_topology,
            system_timezone="America/Phoenix",
        )
    )

    assert snapshot.current_power_w == 2400.0
    assert snapshot.peak_power_today_w is None
    assert snapshot.energy_lifetime_kwh == 24500.125
    assert all(reading.energy_today_kwh is None for reading in snapshot.modules)


def test_snapshot_merges_newest_valid_power_from_every_cca() -> None:
    session = FakeSession()
    client = client_with_login(session)
    run(client.login())
    parsed_topology = replace(
        parse_topology(
            parse_systems(fixture("systems.json"))[0],
            fixture("layout.json"),
            fixture("equipments.json"),
        ),
        cca_uids=("CCA-A", "CCA-B"),
    )
    session.add(
        "GET",
        "/api/v4/smart/systems/1/homepage",
        FakeResponse(200, fixture("homepage.json")),
    )
    session.add(
        "GET",
        "/api/v4/system/summary/summary",
        FakeResponse(
            200,
            {
                "lastData": "2026-08-28 10:30:00",
                "dataset": [
                    {
                        "order": [101, 102],
                        "data": [
                            {"t": "10:00", "d": [100, 200]},
                            {"t": "10:30", "d": ["-", 210]},
                        ],
                    }
                ],
            },
        ),
        FakeResponse(
            200,
            {
                "lastData": "2026-08-28 10:45:00",
                "dataset": [
                    {
                        "order": [101, 102],
                        "data": [
                            {"t": "10:15", "d": [110, "-"]},
                            {"t": "10:45", "d": ["-", "-"]},
                        ],
                    }
                ],
            },
        ),
    )

    snapshot = run(
        client.get_snapshot(
            1,
            "2026-08-28",
            topology=parsed_topology,
            cca_uid="CCA-A",
            system_timezone="America/Phoenix",
            include_panel_energy=False,
            include_peak_power=False,
        )
    )

    power_by_object_id = {
        reading.object_id: reading.power_w for reading in snapshot.modules
    }
    assert power_by_object_id == {"101": 110.0, "102": 210.0}
    assert [
        call["params"]["uid"]
        for call in session.calls
        if call["path"] == "/api/v4/system/summary/summary"
    ] == ["CCA-A", "CCA-B"]
    assert snapshot.reporting_modules == 2


def test_snapshot_does_not_hide_optional_endpoint_connectivity_failure() -> None:
    session = FakeSession()
    client = client_with_login(session)
    run(client.login())
    parsed_topology = parse_topology(
        parse_systems(fixture("systems.json"))[0],
        fixture("layout.json"),
        fixture("equipments.json"),
    )
    session.add(
        "GET",
        "/api/v4/smart/systems/1/homepage",
        FakeResponse(200, fixture("homepage.json")),
    )
    session.add(
        "GET",
        "/api/v4/system/summary/summary",
        FakeResponse(200, fixture("panel_power.json")),
    )
    session.add("GET", "/api/v4/system/summary/aggenergy", FakeResponse(503))
    session.add(
        "GET",
        "/api/v4/system/summary/aggpower",
        FakeResponse(200, fixture("aggpower.json")),
    )

    with pytest.raises(TigoServiceUnavailableError):
        run(
            client.get_snapshot(
                1,
                "2026-08-28",
                topology=parsed_topology,
                system_timezone="America/Phoenix",
            )
        )
