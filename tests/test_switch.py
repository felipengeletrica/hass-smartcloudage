"""Tests for SmartCloudAge output switches."""

from __future__ import annotations

from unittest.mock import Mock

from custom_components.smartcloudage.switch import SmartCloudOutputSwitch


def _switch(hass):
    switch = SmartCloudOutputSwitch(
        hass=hass,
        name="Bancada Output 1",
        output_id=0,
        base_topic="CloudAge/",
        device_id="controller-01",
        alias="Bancada",
    )
    switch.async_write_ha_state = Mock()
    return switch


async def test_turn_on_waits_for_controller_confirmation(hass, mock_mqtt_publish):
    """Publishing an ON command must not optimistically change HA state."""
    switch = _switch(hass)

    await switch.async_turn_on()

    assert switch.is_on is False
    switch.async_write_ha_state.assert_not_called()
    mock_mqtt_publish.assert_awaited_once()


async def test_turn_off_waits_for_controller_confirmation(hass, mock_mqtt_publish):
    """Publishing an OFF command must keep the last controller state."""
    switch = _switch(hass)
    switch.update_state_from_controller(True)
    switch.async_write_ha_state.reset_mock()

    await switch.async_turn_off()

    assert switch.is_on is True
    switch.async_write_ha_state.assert_not_called()
    mock_mqtt_publish.assert_awaited_once()


def test_controller_confirmation_updates_state(hass):
    """Controller MQTT feedback is the source of truth for switch state."""
    switch = _switch(hass)

    switch.update_state_from_controller(True)

    assert switch.is_on is True
    switch.async_write_ha_state.assert_called_once_with()
