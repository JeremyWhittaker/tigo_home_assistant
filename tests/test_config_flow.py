"""Home Assistant framework tests for Tigo config and options flows."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tigo_energy.const import (
    CONF_NIGHT_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_STALE_AFTER,
    CONF_SYSTEM_ID,
    CONF_SYSTEM_NAME,
    CONF_TIME_ZONE,
    DOMAIN,
)
from custom_components.tigo_energy.exceptions import (
    TigoAuthenticationError,
    TigoConnectionError,
)
from custom_components.tigo_energy.models import TigoSystem

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")
TEST_USERNAME = "user@example.invalid"
TEST_PASSWORD = "TEST-PASSWORD-NOT-A-SECRET"


@pytest.fixture(autouse=True)
def mock_integration_setup():
    """Prevent config-flow entry creation from starting a real cloud setup."""

    with (
        patch(
            "custom_components.tigo_energy.async_setup_entry",
            new=AsyncMock(return_value=True),
        ) as setup,
        patch(
            "custom_components.tigo_energy.config_flow.async_get_clientsession",
            return_value=MagicMock(),
        ),
    ):
        yield setup


async def _start_user_flow(hass: HomeAssistant) -> dict:
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )


async def _submit_credentials(hass: HomeAssistant, flow_id: str) -> dict:
    return await hass.config_entries.flow.async_configure(
        flow_id,
        {
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: TEST_PASSWORD,
        },
    )


@pytest.mark.asyncio
async def test_single_system_creates_entry(hass: HomeAssistant) -> None:
    system = TigoSystem(
        id=1,
        name="Example Solar Array",
        timezone="America/Phoenix",
    )
    with patch(
        "custom_components.tigo_energy.config_flow.TigoCloudClient"
    ) as client_cls:
        client_cls.return_value.get_systems = AsyncMock(return_value=(system,))
        initial = await _start_user_flow(hass)
        result = await _submit_credentials(hass, initial["flow_id"])
        await hass.async_block_till_done()

    assert initial["type"] is FlowResultType.FORM
    assert initial["step_id"] == "user"
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Example Solar Array"
    assert result["data"] == {
        CONF_USERNAME: TEST_USERNAME,
        CONF_PASSWORD: TEST_PASSWORD,
        CONF_SYSTEM_ID: 1,
        CONF_SYSTEM_NAME: "Example Solar Array",
        CONF_TIME_ZONE: "America/Phoenix",
    }
    assert result["result"].unique_id == "1"


@pytest.mark.asyncio
async def test_multiple_systems_require_explicit_selection(hass: HomeAssistant) -> None:
    systems = (
        TigoSystem(id=1, name="Array One", timezone="America/Phoenix"),
        TigoSystem(id=2, name="Array Two", timezone="America/Denver"),
    )
    with patch(
        "custom_components.tigo_energy.config_flow.TigoCloudClient"
    ) as client_cls:
        client_cls.return_value.get_systems = AsyncMock(return_value=systems)
        initial = await _start_user_flow(hass)
        chooser = await _submit_credentials(hass, initial["flow_id"])
        result = await hass.config_entries.flow.async_configure(
            chooser["flow_id"],
            {CONF_SYSTEM_ID: "2"},
        )
        await hass.async_block_till_done()

    assert chooser["type"] is FlowResultType.FORM
    assert chooser["step_id"] == "system"
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Array Two"
    assert result["data"][CONF_SYSTEM_ID] == 2
    assert result["data"][CONF_TIME_ZONE] == "America/Denver"


@pytest.mark.asyncio
async def test_duplicate_system_aborts(hass: HomeAssistant) -> None:
    existing = MockConfigEntry(domain=DOMAIN, unique_id="1", data={CONF_SYSTEM_ID: 1})
    existing.add_to_hass(hass)
    system = TigoSystem(id=1, name="Example Solar Array")
    with patch(
        "custom_components.tigo_energy.config_flow.TigoCloudClient"
    ) as client_cls:
        client_cls.return_value.get_systems = AsyncMock(return_value=(system,))
        initial = await _start_user_flow(hass)
        result = await _submit_credentials(hass, initial["flow_id"])

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected_error"),
    [
        (TigoAuthenticationError("rejected"), "invalid_auth"),
        (TigoConnectionError("offline"), "cannot_connect"),
        ((), "no_systems"),
    ],
)
async def test_credential_errors_stay_in_user_form(
    hass: HomeAssistant,
    outcome: Exception | tuple,
    expected_error: str,
) -> None:
    with patch(
        "custom_components.tigo_energy.config_flow.TigoCloudClient"
    ) as client_cls:
        if isinstance(outcome, Exception):
            client_cls.return_value.get_systems = AsyncMock(side_effect=outcome)
        else:
            client_cls.return_value.get_systems = AsyncMock(return_value=outcome)
        initial = await _start_user_flow(hass)
        result = await _submit_credentials(hass, initial["flow_id"])

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": expected_error}


@pytest.mark.asyncio
async def test_reauthentication_updates_only_credentials(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
) -> None:
    config_entry.add_to_hass(hass)
    replacement_username = "replacement@example.invalid"
    replacement_password = "TEST-REPLACEMENT-PASSWORD"
    system = TigoSystem(id=1, name="Example Solar Array")
    with (
        patch(
            "custom_components.tigo_energy.config_flow.TigoCloudClient"
        ) as client_cls,
        patch.object(hass.config_entries, "async_reload", new=AsyncMock()),
    ):
        client_cls.return_value.get_systems = AsyncMock(return_value=(system,))
        initial = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": SOURCE_REAUTH,
                "entry_id": config_entry.entry_id,
                "unique_id": config_entry.unique_id,
            },
            data=config_entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            initial["flow_id"],
            {
                CONF_USERNAME: replacement_username,
                CONF_PASSWORD: replacement_password,
            },
        )

    assert initial["type"] is FlowResultType.FORM
    assert initial["step_id"] == "reauth_confirm"
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_USERNAME] == replacement_username
    assert config_entry.data[CONF_PASSWORD] == replacement_password
    assert config_entry.data[CONF_SYSTEM_ID] == 1


@pytest.mark.asyncio
async def test_reauthentication_rejects_account_without_configured_system(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
) -> None:
    config_entry.add_to_hass(hass)
    with patch(
        "custom_components.tigo_energy.config_flow.TigoCloudClient"
    ) as client_cls:
        client_cls.return_value.get_systems = AsyncMock(
            return_value=(TigoSystem(id=2, name="Different Array"),)
        )
        initial = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": SOURCE_REAUTH,
                "entry_id": config_entry.entry_id,
                "unique_id": config_entry.unique_id,
            },
            data=config_entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            initial["flow_id"],
            {
                CONF_USERNAME: TEST_USERNAME,
                CONF_PASSWORD: TEST_PASSWORD,
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "system_not_found"}


@pytest.mark.asyncio
async def test_options_flow_saves_valid_polling_values(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
) -> None:
    config_entry.add_to_hass(hass)
    initial = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        initial["flow_id"],
        {
            CONF_SCAN_INTERVAL: 240,
            CONF_NIGHT_SCAN_INTERVAL: 1200,
            CONF_STALE_AFTER: 3600,
        },
    )

    assert initial["type"] is FlowResultType.FORM
    assert initial["step_id"] == "init"
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {
        CONF_SCAN_INTERVAL: 240,
        CONF_NIGHT_SCAN_INTERVAL: 1200,
        CONF_STALE_AFTER: 3600,
    }


@pytest.mark.asyncio
async def test_options_flow_enforces_minimum_scan_interval(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
) -> None:
    config_entry.add_to_hass(hass)
    initial = await hass.config_entries.options.async_init(config_entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            initial["flow_id"],
            {
                CONF_SCAN_INTERVAL: 60,
                CONF_NIGHT_SCAN_INTERVAL: 1200,
                CONF_STALE_AFTER: 3600,
            },
        )
