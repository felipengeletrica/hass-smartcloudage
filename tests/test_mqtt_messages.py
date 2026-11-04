"""MQTT message handling of the sensor and switch platforms."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from custom_components.smartcloudage import sensor, switch

DEVICE = "controller-01"
# Firmware 2.0.5 reply to command 32 READ: the status object is not escaped.
IR_STATUS = (
    '{"product":1,"protocol":"0.4.3","serial":"47","device":"controller-01","response":{"type":3,'
    '"command":32,"status":"{"initialized":1,"busy":0,"last_segments":4,"tx_counter":2}"},'
    '"signature":"controller-01"}'
)
KEEPALIVE = json.dumps(
    {"product": 1, "firmware": "2.0.5", "device": DEVICE, "Wifi_db": "-49", "message": "KeepAlive", "uptime": 61}
)


async def _setup(module, hass):
    entry = Mock(entry_id="e1", options={}, data={"devices": [{"device_id": DEVICE, "outputs": 2, "meters": []}]})
    added = []
    with patch.object(module.mqtt, "async_subscribe", new_callable=AsyncMock, return_value=Mock()) as sub:
        await module.async_setup_entry(hass, entry, added.extend)
    for entity in added:
        entity.hass = hass
        entity.async_write_ha_state = Mock()
    return added, sub.call_args.args[2]


def _msg(payload):
    return SimpleNamespace(topic=f"CloudAge/{DEVICE}/OutTopic/", payload=payload.encode())


async def test_controller_without_meters_reports_rssi_and_uptime(hass):
    entities, callback = await _setup(sensor, hass)

    await callback(_msg(KEEPALIVE))

    values = {type(e).__name__: e.native_value for e in entities}
    assert values["SmartCloudAgeRSSISensor"] == -49
    assert values["SmartCloudAgeUptimeSensor"] == 61


async def test_ir_status_reply_is_not_logged_as_invalid(hass, caplog):
    _, sensor_callback = await _setup(sensor, hass)
    _, switch_callback = await _setup(switch, hass)

    with caplog.at_level(logging.WARNING):
        await sensor_callback(_msg(IR_STATUS))
        await switch_callback(_msg(IR_STATUS))

    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
