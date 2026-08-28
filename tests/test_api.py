"""Fast unit tests for the async Tigo mobile-cloud client."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict, deque
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
    assert parse_retry_after("nonsense", now=now) is None


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
