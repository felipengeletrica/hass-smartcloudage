"""Options flow steps for air conditioners."""

from __future__ import annotations

from homeassistant.data_entry_flow import FlowResultType, InvalidData
import pytest
import voluptuous as vol
from pytest_homeassistant_custom_component.common import MockConfigEntry

from unittest.mock import AsyncMock, patch

from custom_components.smartcloudage.air_conditioner import IR_NO_SEGMENTS, IR_SEGMENTS
from custom_components.smartcloudage.config_flow import DOMAIN


@pytest.fixture(autouse=True)
def firmware_probe():
    """The controller answers the IR status query with a segment-capable firmware."""
    with patch(
        "custom_components.smartcloudage.air_conditioner.IrSender.async_probe",
        new_callable=AsyncMock,
        return_value=IR_SEGMENTS,
    ) as probe:
        yield probe


def _entry(hass, air_conditioners=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "devices": [
                {
                    "device_id": "controller-01",
                    "outputs": 16,
                    "alias": "Bancada",
                    "meters": [],
                    "air_conditioners": air_conditioners or [],
                }
            ]
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_add_air_conditioner_with_model_step(hass):
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "add_ac" in result["menu_options"]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_ac"}
    )
    assert result["step_id"] == "ac"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Sala", "brand": "LG", "ir_output": "26"}
    )
    assert result["step_id"] == "ac_model"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"model": "AKB75215403"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    (unit,) = result["data"]["devices"][0]["air_conditioners"]
    assert unit["name"] == "Sala"
    assert unit["brand"] == "LG"
    assert unit["model"] == "AKB75215403"
    assert unit["ir_output"] == 26
    assert len(unit["id"]) == 8


async def test_single_model_brand_skips_model_step(hass):
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_ac"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Quarto", "brand": "Samsung", "ir_output": "23"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    (unit,) = result["data"]["devices"][0]["air_conditioners"]
    assert unit["model"] == "AR"
    assert unit["ir_output"] == 23


async def test_delete_air_conditioner(hass):
    unit = {"id": "a1b2c3d4", "name": "Sala", "brand": "Samsung", "model": "AR", "ir_output": 0}
    entry = _entry(hass, [unit])
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "delete_ac"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"air_conditioner": 0}
    )
    assert result["step_id"] == "confirm_delete_ac"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirm": True}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["devices"][0]["air_conditioners"] == []
    # The stored entry is not modified until Home Assistant saves the new options.
    assert entry.data["devices"][0]["air_conditioners"] == [unit]


async def test_new_air_conditioner_must_choose_brand_and_channel(hass):
    """Brand and PWM channel have no preset value: the installer picks both."""
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_ac"}
    )
    for field in ("brand", "ir_output"):
        marker = next(key for key in result["data_schema"].schema if key == field)
        assert marker.default is vol.UNDEFINED

    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {"name": "Sala", "brand": "LG"}
        )


async def test_old_firmware_blocks_registration(hass, firmware_probe):
    firmware_probe.return_value = IR_NO_SEGMENTS
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_ac"}
    )
    user_input = {"name": "Sala", "brand": "Samsung", "ir_output": "26"}
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "firmware_no_segments"}

    # The installer can still register it, e.g. before updating the firmware.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**user_input, "skip_firmware_check": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_delete_removes_the_air_conditioner_device(hass):
    from homeassistant.helpers import device_registry as dr

    unit = {"id": "a1b2c3d4", "name": "Sala", "brand": "Samsung", "model": "AR", "ir_output": 26}
    entry = _entry(hass, [unit])
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "controller-01_ac_a1b2c3d4")}
    )
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "delete_ac"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"air_conditioner": 0})
    await hass.config_entries.options.async_configure(result["flow_id"], {"confirm": True})

    assert registry.async_get(device.id) is None
