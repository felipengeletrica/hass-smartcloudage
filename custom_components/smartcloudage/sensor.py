"""Pulse counter sensors for SmartCloudAge controllers."""

from __future__ import annotations

import json
import logging
from typing import Any

from homeassistant.components import mqtt
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass

DOMAIN = "smartcloudage"
_LOGGER = logging.getLogger(__name__)

DEVICE_CLASSES = {
    "water": SensorDeviceClass.WATER,
    "gas": SensorDeviceClass.GAS,
    "energy": SensorDeviceClass.ENERGY,
}


async def async_setup_entry(hass, entry, async_add_entities):
    """Create configured pulse counter entities."""
    devices = entry.options.get("devices", entry.data.get("devices", []))
    entities_by_device: dict[str, dict[int, SmartCloudAgePulseSensor]] = {}
    entities = []

    for device in devices:
        device_id = device.get("device_id")
        alias = device.get("alias") or device_id
        if not device_id:
            continue
        channel_entities = entities_by_device.setdefault(device_id, {})
        for meter in device.get("meters", []):
            channel = int(meter["channel"])
            entity = SmartCloudAgePulseSensor(device_id, alias, meter)
            channel_entities[channel] = entity
            entities.append(entity)

    async_add_entities(entities)

    async def message_received(msg):
        try:
            raw = msg.payload.decode("utf-8") if isinstance(msg.payload, bytes) else msg.payload
            data = json.loads(raw)
            if str(data.get("message", "")).upper() != "PULSE_SENSOR":
                return

            device_id = data.get("device") or msg.topic.split("/")[1]
            configured = entities_by_device.get(device_id)
            if not configured:
                return

            for pulse in data.get("Pulses", []):
                channel = int(pulse.get("Sensor", 0))
                entity = configured.get(channel)
                if entity is None:
                    continue
                lsb = int(pulse.get("lsb", 0)) & 0xFFFF
                msb = int(pulse.get("msb", 0)) & 0xFFFF
                entity.update_pulses((msb << 16) | lsb)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as err:
            _LOGGER.warning("Invalid SmartCloudAge pulse payload on %s: %s", msg.topic, err)

    for device_id in entities_by_device:
        unsubscribe = await mqtt.async_subscribe(
            hass,
            f"CloudAge/{device_id}/OutTopic/#",
            message_received,
            0,
        )
        entry.async_on_unload(unsubscribe)


class SmartCloudAgePulseSensor(SensorEntity):
    """Accumulated pulse meter exposed as a native HA sensor."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_should_poll = False

    def __init__(self, device_id: str, alias: str, meter: dict[str, Any]) -> None:
        self._device_id = device_id
        self._channel = int(meter["channel"])
        self._factor = float(meter.get("factor", 1.0))
        self._offset = float(meter.get("offset", 0.0))
        self._attr_name = meter.get("name") or f"{alias} Sensor {self._channel}"
        self._attr_unique_id = f"smartcloudage_{device_id}_pulse_{self._channel}"
        self._attr_native_unit_of_measurement = meter.get("unit") or "pulses"
        self._attr_device_class = DEVICE_CLASSES.get(meter.get("type"))
        self._attr_native_value = None
        self._raw_pulses = None
        self._alias = alias

    @property
    def extra_state_attributes(self):
        """Expose the raw counter and conversion settings."""
        return {
            "raw_pulses": self._raw_pulses,
            "pulse_factor": self._factor,
            "offset": self._offset,
            "channel": self._channel,
        }

    @property
    def device_info(self):
        """Link the sensor to its SmartCloudAge controller."""
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": f"SmartCloudAge {self._alias}",
            "manufacturer": "SmartCloudAge",
            "model": "MQTT Controller",
        }

    def update_pulses(self, raw_pulses: int) -> None:
        """Apply the configured conversion factor and update HA."""
        self._raw_pulses = raw_pulses
        self._attr_native_value = round(
            raw_pulses * self._factor + self._offset, 9
        )
        self.async_write_ha_state()
