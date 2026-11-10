"""Tests for read-only digital inputs and float-switch tank levels (beta)."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)

from custom_components.smartcloudage.binary_sensor import SmartCloudAgeInputSensor
from custom_components.smartcloudage.config_flow import DOMAIN
from custom_components.smartcloudage.inputs import (
    channel_active,
    compute_level,
    decode_payload,
    format_level_points,
    input_mask,
    parse_level_points,
)
from custom_components.smartcloudage.sensor import SmartCloudAgeLevelSensor

POINTS = [
    {"channel": 1, "percent": 25},
    {"channel": 2, "percent": 50},
    {"channel": 3, "percent": 75},
    {"channel": 4, "percent": 100},
]


def _input_status(mask: int) -> str:
    """Payload exactly as the firmware publishes it (PROTOCOL.md)."""
    return json.dumps(
        {
            "product": 1,
            "serial": "8C4B14B27524",
            "Wifi_db": "-39",
            "message": json.dumps({"message": "INPUT_STATUS", "Input": {"Inputs": mask}}),
            "uptime": 35,
            "Output": {"Outputs": 0},
        }
    )


def test_input_mask_unwraps_firmware_envelope():
    assert input_mask(decode_payload(_input_status(0b1011))) == 0b1011


def test_input_mask_ignores_other_messages():
    raw = json.dumps({"message": json.dumps({"message": "OUTPUT_STATUS"}), "Output": {"Outputs": 3}})
    assert input_mask(decode_payload(raw)) is None


def test_channel_active_and_invert():
    assert channel_active(0b0100, 3) is True
    assert channel_active(0b0100, 2) is False
    assert channel_active(0b0100, 3, invert=True) is False


def test_parse_and_format_level_points():
    points = parse_level_points(" 3:75 ; 1:25%, 2:50 ")
    assert points == POINTS[:3]
    assert format_level_points(points) == "1:25, 2:50, 3:75"


@pytest.mark.parametrize(
    "text",
    ["", "1-25", "0:25", "17:25", "1:0", "1:101", "1:25, 1:50", "1:25, 2:25", "a:b"],
)
def test_parse_level_points_rejects_invalid(text):
    with pytest.raises(ValueError):
        parse_level_points(text)


@pytest.mark.parametrize(
    ("mask", "level", "consistent"),
    [
        (0b0000, 0, True),
        (0b0001, 25, True),
        (0b0111, 75, True),
        (0b1111, 100, True),
        (0b0101, 75, False),  # input 3 active while input 2 is not
    ],
)
def test_compute_level(mask, level, consistent):
    assert compute_level(mask, POINTS) == (level, consistent)


def test_compute_level_inverted():
    assert compute_level(0b1100, POINTS, invert=True) == (50, True)


def test_level_sensor_attributes():
    sensor = SmartCloudAgeLevelSensor(
        "controller-01", "Bancada", {"id": "abc", "name": "Caixa", "points": POINTS}, False
    )
    sensor.async_write_ha_state = Mock()

    sensor.update_mask(0b0011)

    assert sensor.unique_id == "smartcloudage_controller-01_level_abc"
    assert sensor.native_value == 50
    assert sensor.extra_state_attributes["active_channels"] == [1, 2]
    assert sensor.extra_state_attributes["consistent"] is True


def test_input_sensor_writes_only_on_change():
    sensor = SmartCloudAgeInputSensor("controller-01", "Bancada", 2, False)
    sensor.async_write_ha_state = Mock()

    sensor.update_mask(0b10)
    sensor.update_mask(0b11)
    sensor.update_mask(0b00)

    assert sensor.unique_id == "smartcloudage_controller-01_input_2"
    assert sensor.is_on is False
    assert sensor.async_write_ha_state.call_count == 2


# The MQTT client fixture leaves its periodic housekeeping timer scheduled.
@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_entities_follow_input_status(hass, mqtt_mock):
    """End to end: an INPUT_STATUS message drives inputs and the level."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "devices": [
                {
                    "device_id": "controller-01",
                    "outputs": 16,
                    "alias": "Bancada",
                    "meters": [],
                    "inputs": {"channels": [1, 2], "invert": False},
                    "levels": [{"id": "abc", "name": "Caixa", "points": POINTS}],
                }
            ]
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    input_1 = registry.async_get_entity_id("binary_sensor", DOMAIN, "smartcloudage_controller-01_input_1")
    input_2 = registry.async_get_entity_id("binary_sensor", DOMAIN, "smartcloudage_controller-01_input_2")
    level = registry.async_get_entity_id("sensor", DOMAIN, "smartcloudage_controller-01_level_abc")
    assert input_1 and input_2 and level

    async_fire_mqtt_message(hass, "CloudAge/controller-01/OutTopic/", _input_status(0b0111))
    await hass.async_block_till_done()

    assert hass.states.get(input_1).state == "on"
    assert hass.states.get(input_2).state == "on"
    assert hass.states.get(level).state == "75"
    assert hass.states.get(level).attributes["active_channels"] == [1, 2, 3]


def _options_entry(hass, **device):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "devices": [
                {"device_id": "controller-01", "outputs": 16, "alias": "Bancada", "meters": [], **device}
            ]
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_options_select_inputs(hass):
    entry = _options_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "inputs" in result["menu_options"]

    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "inputs"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"channels": ["4", "1"], "invert": True}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["devices"][0]["inputs"] == {"channels": [1, 4], "invert": True}


async def test_options_add_level_validates_points(hass):
    entry = _options_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "add_level"})

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Caixa", "points": "1:25, 1:50"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"points": "invalid_level_points"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Caixa", "points": "2:50, 1:25"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    level = result["data"]["devices"][0]["levels"][0]
    assert level["name"] == "Caixa"
    assert level["points"] == POINTS[:2]
    assert len(level["id"]) == 8


async def test_options_edit_and_delete_level(hass):
    entry = _options_entry(hass, levels=[{"id": "abc", "name": "Caixa", "points": POINTS}])

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "edit_level"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"level": 0})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Cisterna", "points": "5:50, 6:100"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["devices"][0]["levels"][0] == {
        "id": "abc",
        "name": "Cisterna",
        "points": [{"channel": 5, "percent": 50}, {"channel": 6, "percent": 100}],
    }

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "delete_level"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"level": 0})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"confirm": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["devices"][0]["levels"] == []


async def test_options_level_menu_aborts_without_levels(hass):
    entry = _options_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "edit_level"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_levels_configured"
