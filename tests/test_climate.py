"""Tests for IR-controlled air conditioners."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.components.climate import ClimateEntityFeature, HVACMode
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.smartcloudage import air_conditioner
from custom_components.smartcloudage.air_conditioner import (
    IR_NO_RESPONSE,
    IR_NO_SEGMENTS,
    IR_SEGMENTS,
    IR_UNSUPPORTED,
    AirConditioner,
    IrSender,
)
from custom_components.smartcloudage.climate import SmartCloudAgeClimate
from custom_components.smartcloudage.hvac import (
    AcBrand,
    AcCommandState,
    AcFanSpeed,
    AcMode,
    build_payload,
)
from custom_components.smartcloudage.switch import build_ac_option_switches


def _ack(status: int):
    return SimpleNamespace(
        topic="CloudAge/controller-01/OutTopic/response",
        payload=json.dumps({"response": {"type": 2, "command": 32, "status": status}}),
    )


@pytest.fixture
def mqtt_subscribe():
    with patch(
        "custom_components.smartcloudage.air_conditioner.mqtt.async_subscribe",
        new_callable=AsyncMock,
        return_value=Mock(),
    ) as subscribe:
        yield subscribe


@pytest.fixture
def ack_publish(mqtt_subscribe):
    """Publish mock that answers every IR command with the given status."""
    status = {"value": 1}

    async def publish(hass, topic, payload, qos, retain):
        callback = mqtt_subscribe.call_args.args[2]
        if status["value"] is not None:
            await callback(_ack(status["value"]))

    with patch(
        "custom_components.smartcloudage.air_conditioner.mqtt.async_publish",
        new_callable=AsyncMock,
        side_effect=publish,
    ) as mock:
        mock.status = status
        yield mock


def _unit(hass, brand="Samsung", model="AR", **extra):
    config = {"id": "a1b2c3d4", "name": "Sala", "brand": brand, "model": model, **extra}
    return AirConditioner(IrSender(hass, "controller-01"), "Bancada", config)


def _climate(unit):
    entity = SmartCloudAgeClimate(unit)
    entity.async_write_ha_state = Mock()
    return entity


async def test_set_temperature_sends_full_state(hass, ack_publish):
    unit = _unit(hass, ir_output=26)
    climate = _climate(unit)

    await climate.async_set_hvac_mode(HVACMode.COOL)
    await climate.async_set_temperature(temperature=21)

    topic, raw = ack_publish.call_args.args[1:3]
    message = json.loads(raw)
    assert topic == "CloudAge/controller-01"
    assert message["command"] == 32 and message["type"] == 1
    expected = AcCommandState(
        AcBrand.SAMSUNG, "AR", True, AcMode.COOL, 21, AcFanSpeed.AUTO, gpio=26, light=True
    )
    assert message["payload"] == build_payload(expected)
    assert climate.hvac_mode == HVACMode.COOL
    assert climate.target_temperature == 21


async def test_refused_command_keeps_previous_state(hass, ack_publish):
    climate = _climate(_unit(hass))
    ack_publish.status["value"] = 0

    with pytest.raises(HomeAssistantError):
        await climate.async_set_hvac_mode(HVACMode.HEAT)

    assert climate.hvac_mode == HVACMode.OFF


async def test_missing_ack_times_out(hass, ack_publish):
    climate = _climate(_unit(hass))
    ack_publish.status["value"] = None

    with patch.object(air_conditioner, "ACK_TIMEOUT_S", 0.01), pytest.raises(HomeAssistantError):
        await climate.async_turn_on()

    assert climate.hvac_mode == HVACMode.OFF


async def test_preset_turns_other_flags_off(hass, ack_publish):
    unit = _unit(hass)
    climate = _climate(unit)
    await climate.async_set_hvac_mode(HVACMode.COOL)

    await climate.async_set_preset_mode("boost")
    assert climate.preset_mode == "boost"
    await climate.async_set_preset_mode("eco")
    assert climate.preset_mode == "eco"
    assert unit.state.turbo is False
    await climate.async_set_preset_mode("none")
    assert climate.preset_mode == "none"


def test_modes_follow_what_the_protocol_distinguishes(hass):
    # Hitachi RAR-8P2 has no auto mode and no swing control.
    hitachi = _climate(_unit(hass, "Hitachi", "RAR-8P2"))
    assert HVACMode.AUTO not in hitachi.hvac_modes
    assert not hitachi.supported_features & ClimateEntityFeature.SWING_MODE
    assert hitachi.preset_modes is None

    # Samsung sends Min and Low as the same fan speed: only one is offered.
    samsung = _climate(_unit(hass))
    assert "min" not in samsung.fan_modes and "low" in samsung.fan_modes
    assert samsung.swing_modes == ["off", "on"]
    assert samsung.supported_features & ClimateEntityFeature.SWING_HORIZONTAL_MODE

    # Gree has vane positions.
    gree = _climate(_unit(hass, "Gree", "YAW1F"))
    assert {"highest", "middle", "lowest"} <= set(gree.swing_modes)
    assert gree.min_temp == 16 and gree.max_temp == 30


def test_option_switches_only_for_supported_features(hass):
    switches = build_ac_option_switches(
        [_unit(hass), _unit(hass, "LG", "AKB75215403"), _unit(hass, "Daikin", "ARC433")]
    )
    assert [s.translation_key for s in switches] == ["ac_display", "ac_clean"]


async def test_display_switch_sends_light_flag(hass, ack_publish):
    unit = _unit(hass)
    (display,) = build_ac_option_switches([unit])
    display.async_write_ha_state = Mock()

    await display.async_turn_off()

    assert display.is_on is False
    assert unit.state.light is False
    ack_publish.assert_awaited_once()


def test_unknown_model_falls_back_to_default(hass):
    unit = _unit(hass, "Gree", "XYZ")
    assert unit.model == "YAW1F"


# The MQTT mock leaves its periodic housekeeping timer behind.
@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_entry_setup_creates_climate_entity(hass, mqtt_mock):
    """The A/C becomes its own device with a climate entity and option switches."""
    from homeassistant.helpers import device_registry as dr, entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain="smartcloudage",
        data={
            "devices": [
                {
                    "device_id": "controller-01",
                    "outputs": 4,
                    "alias": "Bancada",
                    "meters": [],
                    "air_conditioners": [
                        {"id": "a1b2c3d4", "name": "Sala", "brand": "Gree", "model": "YAW1F", "ir_output": 0}
                    ],
                }
            ]
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_id = er.async_get(hass).async_get_entity_id(
        "climate", "smartcloudage", "smartcloudage_controller-01_ac_a1b2c3d4_climate"
    )
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "off"
    assert state.attributes["brand"] == "Gree"
    assert "highest" in state.attributes["swing_modes"]
    device = dr.async_get(hass).async_get_device(
        identifiers={("smartcloudage", "controller-01_ac_a1b2c3d4")}
    )
    assert device.name == "Sala" and device.model == "YAW1F"
    controller = dr.async_get(hass).async_get_device(identifiers={("smartcloudage", "controller-01")})
    assert device.via_device_id == controller.id
    assert er.async_get(hass).async_get_entity_id(
        "switch", "smartcloudage", "smartcloudage_controller-01_ac_a1b2c3d4_display"
    )

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_pwm2_sends_its_gpio_explicitly(hass, ack_publish):
    """Configs saved before 1.3.0 stored PWM2 as 0; the command carries GPIO 23."""
    climate = _climate(_unit(hass, ir_output=0))

    await climate.async_turn_on()

    message = json.loads(ack_publish.call_args.args[2])
    assert message["payload"]["gpio"] == 23


# Replies of firmware 2.0.5 / 2.0.4d to command 32 READ: the status object is
# quoted without escaping, so the message is not valid JSON.
STATUS_205 = (
    '{"product":1,"protocol":"1.0","serial":"7","device":"controller-01","response":{"type":3,'
    '"command":32,"status":"{"initialized":1,"busy":0,"last_status":0,"last_mode":1,"last_gpio":23,'
    '"last_frequency":38000,"last_frame_gap_us":30000,"last_items":0,"last_segments":4,'
    '"last_symbols":0,"queue_waiting":0,"tx_counter":3,"error_counter":0}"},"signature":"controller-01"}'
)
STATUS_204D = STATUS_205.replace('"last_segments":4,', "")
NO_COMMAND = '{"product":1,"response":{"type":3,"command":32,"status":0},"signature":"controller-01"}'


@pytest.mark.parametrize(
    ("reply", "expected"),
    [(STATUS_205, IR_SEGMENTS), (STATUS_204D, IR_NO_SEGMENTS), (NO_COMMAND, IR_UNSUPPORTED), (None, IR_NO_RESPONSE)],
)
async def test_firmware_probe(hass, mqtt_subscribe, reply, expected):
    async def publish(hass, topic, payload, qos, retain):
        assert json.loads(payload) == {"command": 32, "type": 2, "signature": "controller-01", "payload": {}}
        if reply is not None:
            await mqtt_subscribe.call_args.args[2](SimpleNamespace(topic="CloudAge/controller-01/OutTopic", payload=reply.encode()))

    with patch(
        "custom_components.smartcloudage.air_conditioner.mqtt.async_publish", side_effect=publish
    ), patch.object(air_conditioner, "ACK_TIMEOUT_S", 0.01):
        assert await IrSender(hass, "controller-01").async_probe() == expected


def test_brand_change_removes_unsupported_option_switch(hass):
    """Samsung has a display switch; after changing to LG it must not linger."""
    from homeassistant.helpers import entity_registry as er

    from custom_components.smartcloudage.switch import remove_stale_ac_option_switches

    registry = er.async_get(hass)
    registry.async_get_or_create(
        "switch", "smartcloudage", "smartcloudage_controller-01_ac_a1b2c3d4_display"
    )
    lg = _unit(hass, "LG", "GE6711AR2853M")

    remove_stale_ac_option_switches(hass, [lg], build_ac_option_switches([lg]))

    assert registry.async_get_entity_id(
        "switch", "smartcloudage", "smartcloudage_controller-01_ac_a1b2c3d4_display"
    ) is None
