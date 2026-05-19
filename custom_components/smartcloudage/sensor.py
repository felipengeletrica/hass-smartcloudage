import json
import logging

from homeassistant.components import mqtt
from homeassistant.components.sensor import SensorEntity, SensorStateClass

_LOGGER = logging.getLogger(__name__)

DOMAIN = "smartcloudage"
HARDCODED_TOPIC_PREFIX = "CloudAge/"
DEFAULT_PULSE_MULTIPLIER = 1.0
DEFAULT_PULSE_UNIT = "pulses"


def _decode_payload(payload):
    """Decode MQTT payload and unwrap double-encoded message when needed."""
    raw = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    data = json.loads(raw)

    inner = data.get("message")
    if isinstance(inner, str):
        try:
            inner_obj = json.loads(inner)
            data = {**data, **inner_obj}
        except Exception:
            pass

    return data


def _pulse_total(lsb, msb):
    """Build 64-bit pulse counter from firmware lsb/msb fields."""
    return (int(msb) << 32) | int(lsb)


def _safe_float(value, default=DEFAULT_PULSE_MULTIPLIER):
    """Convert configuration values to float without breaking setup."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _channel_multiplier(device_conf, pulse_id):
    """Return multiplier for one pulse channel.

    Supports a single device-level multiplier and an optional future per-channel
    list/map named pulse_multipliers. The UI currently exposes the device-level
    multiplier because it is easier to maintain in Home Assistant options.
    """
    per_channel = device_conf.get("pulse_multipliers")

    if isinstance(per_channel, list) and pulse_id < len(per_channel):
        return _safe_float(per_channel[pulse_id])

    if isinstance(per_channel, dict):
        return _safe_float(
            per_channel.get(str(pulse_id), per_channel.get(str(pulse_id + 1))),
            _safe_float(device_conf.get("pulse_multiplier", DEFAULT_PULSE_MULTIPLIER)),
        )

    return _safe_float(device_conf.get("pulse_multiplier", DEFAULT_PULSE_MULTIPLIER))


async def async_setup_entry(hass, entry, async_add_entities):
    try:
        devices = entry.options.get("devices")
        if devices is None:
            devices = entry.data.get("devices", [])
    except Exception as err:
        _LOGGER.error("Erro ao carregar devices para sensores de pulso: %s", err)
        devices = []

    entities = []
    entities_by_device = {}

    for device_conf in devices:
        device_id = device_conf.get("device_id")
        alias = device_conf.get("alias") or device_id
        pulse_channels = int(device_conf.get("pulses", 16))
        pulse_unit = device_conf.get("pulse_unit") or DEFAULT_PULSE_UNIT

        entities_by_device.setdefault(device_id, {})

        for pulse_id in range(pulse_channels):
            multiplier = _channel_multiplier(device_conf, pulse_id)
            entity = SmartCloudPulseSensor(
                name=f"{alias} Pulso {pulse_id + 1}",
                pulse_id=pulse_id,
                device_id=device_id,
                alias=alias,
                multiplier=multiplier,
                unit=pulse_unit,
            )
            entities.append(entity)
            entities_by_device[device_id][pulse_id] = entity

    async_add_entities(entities)

    async def message_received(msg):
        try:
            topic_parts = msg.topic.split("/")
            if len(topic_parts) < 2:
                return

            device_id = topic_parts[1]
            if device_id not in entities_by_device:
                return

            data = _decode_payload(msg.payload)
            msg_type = str(data.get("message", "")).upper()

            if msg_type != "PULSE_SENSOR":
                return

            pulses = data.get("Pulses")
            if not isinstance(pulses, list):
                return

            for item in pulses:
                if not isinstance(item, dict):
                    continue

                sensor_index = int(item.get("Sensor"))
                entity = entities_by_device[device_id].get(sensor_index)
                if entity is None:
                    continue

                lsb = item.get("lsb", 0)
                msb = item.get("msb", 0)
                total = _pulse_total(lsb, msb)

                entity.update_from_mqtt(total, lsb, msb)

        except Exception as err:
            _LOGGER.error(
                "Erro processando pulsos MQTT: %s Payload: %s", err, msg.payload
            )

    for device_id in entities_by_device.keys():
        await mqtt.async_subscribe(hass, f"+/{device_id}/OutTopic/#", message_received, 0)
        await mqtt.async_subscribe(hass, f"+/{device_id}/#", message_received, 0)


class SmartCloudPulseSensor(SensorEntity):
    """SmartCloudAge pulse counter sensor with configurable multiplier."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:counter"

    def __init__(self, name, pulse_id, device_id, alias=None, multiplier=1.0, unit="pulses"):
        self._attr_name = name
        self._pulse_id = pulse_id
        self._device_id = device_id
        self._alias = alias or device_id
        self._multiplier = _safe_float(multiplier)
        self._attr_native_unit_of_measurement = unit or DEFAULT_PULSE_UNIT
        self._attr_native_value = None
        self._raw_pulses = None
        self._last_lsb = None
        self._last_msb = None

    @property
    def unique_id(self):
        return f"smartcloudage_pulse_{self._alias}_{self._pulse_id + 1}"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": f"SmartCloudAge {self._alias}",
            "manufacturer": "SmartCloudAge",
            "model": "MQTT Controller",
        }

    @property
    def extra_state_attributes(self):
        return {
            "raw_pulses": self._raw_pulses,
            "multiplier": self._multiplier,
            "lsb": self._last_lsb,
            "msb": self._last_msb,
            "pulse_channel": self._pulse_id + 1,
            "firmware_sensor_index": self._pulse_id,
        }

    def update_from_mqtt(self, total, lsb=None, msb=None):
        self._raw_pulses = total
        self._last_lsb = int(lsb) if lsb is not None else None
        self._last_msb = int(msb) if msb is not None else None
        self._attr_native_value = total * self._multiplier
        self.async_write_ha_state()
