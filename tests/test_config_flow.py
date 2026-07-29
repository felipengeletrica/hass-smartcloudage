"""Tests for the SmartCloudAge config flow."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.smartcloudage.config_flow import DOMAIN


async def test_user_flow_without_meter(hass):
    """A controller can be configured without a pulse meter."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "device_id": "controller-01",
            "outputs": 4,
            "alias": "Bancada",
            "configure_meter": False,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Bancada"
    assert result["data"]["devices"][0]["meters"] == []


async def test_meter_rejects_non_positive_factor(hass):
    """A zero conversion factor is invalid."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "device_id": "controller-02",
            "outputs": 4,
            "alias": "Predial",
            "configure_meter": True,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "channel": 9,
            "name": "Energia",
            "type": "energy",
            "factor": 0,
            "offset": 0,
            "unit": "kWh",
            "add_another": False,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"factor": "factor_must_be_positive"}


async def test_meter_rejects_duplicate_channel(hass):
    """Two meters cannot consume the same physical input channel."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "device_id": "controller-03",
            "outputs": 4,
            "alias": "Predial",
            "configure_meter": True,
        },
    )
    meter = {
        "channel": 9,
        "name": "AP 203",
        "type": "gas",
        "factor": 0.01,
        "offset": 5.502,
        "unit": "m³",
        "add_another": True,
    }
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], meter
    )
    assert result["step_id"] == "meter"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**meter, "name": "AP 303", "add_another": False}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"channel": "channel_already_configured"}
