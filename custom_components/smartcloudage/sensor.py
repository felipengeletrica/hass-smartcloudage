"""Pulse counter sensors for SmartCloudAge controllers."""

from __future__ import annotations

import json
import logging
from typing import Any

from homeassistant.components import mqtt
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfTime, UnitOfVolume

DOMAIN = "smartcloudage"
_LOGGER = logging.getLogger(__name__)

DEVICE_CLASSES = {
    "water": SensorDeviceClass.WATER,
    "gas": SensorDeviceClass.GAS,
    "energy": SensorDeviceClass.ENERGY,
}

NATIVE_UNITS = {
    "water": UnitOfVolume.CUBIC_METERS,
    "gas": UnitOfVolume.CUBIC_METERS,
    "energy": UnitOfEnergy.KILO_WATT_HOUR,
}

RSSI_WARNING_DBM = -75
RSSI_CRITICAL_DBM = -85


def classify_rssi(rssi: int) -> str:
    """Classify Wi-Fi signal strength for diagnostics and alarms."""
    if rssi <= RSSI_CRITICAL_DBM:
        return "critical"
    if rssi <= RSSI_WARNING_DBM:
        return "poor"
    if rssi <= -67:
        return "fair"
    return "good"


def _first_numeric(data: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    """Return the first numeric telemetry value matching any known firmware key."""
    for key in keys:
        value = data.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


async def async_setup_entry(hass, entry, async_add_entities):
    """Create configured pulse counter entities."""
    devices = entry.options.get("devices", entry.data.get("devices", []))
    entities_by_device: dict[str, dict[int, SmartCloudAgePulseSensor]] = {}
    diagnostics_by_device: dict[
        str, tuple[SmartCloudAgeRSSISensor, SmartCloudAgeUptimeSensor]
    ] = {}
    entities = []

    for device in devices:
        device_id = device.get("device_id")
        alias = device.get("alias") or device_id
        if not device_id:
            continue
        channel_entities = entities_by_device.setdefault(device_id, {})
        rssi_entity = SmartCloudAgeRSSISensor(device_id, alias)
        uptime_entity = SmartCloudAgeUptimeSensor(device_id, alias)
        diagnostics_by_device[device_id] = (rssi_entity, uptime_entity)
        entities.extend((rssi_entity, uptime_entity))
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

            device_id = data.get("device") or msg.topic.split("/")[1]
            configured = entities_by_device.get(device_id)
            if not configured:
                return

            rssi_entity, uptime_entity = diagnostics_by_device[device_id]
            rssi = _first_numeric(data, ("Wifi_db", "wifi_db", "RSSI", "rssi"))
            if rssi is not None:
                rssi_entity.update_rssi(round(rssi))

            uptime = _first_numeric(data, ("uptime", "Uptime", "UPTIME"))
            if uptime is not None and uptime >= 0:
                uptime_entity.update_uptime(round(uptime))

            if str(data.get("message", "")).upper() != "PULSE_SENSOR":
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


class SmartCloudAgeDiagnosticSensor(SensorEntity):
    """Base diagnostic entity linked to a SmartCloudAge controller."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, device_id: str, alias: str) -> None:
        self._device_id = device_id
        self._alias = alias
        self._attr_native_value = None

    @property
    def device_info(self):
        """Link the diagnostic sensor to its controller."""
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": f"SmartCloudAge {self._alias}",
            "manufacturer": "SmartCloudAge",
            "model": "MQTT Controller",
        }


class SmartCloudAgeRSSISensor(SmartCloudAgeDiagnosticSensor):
    """Wi-Fi signal strength with transition-based alarms."""

    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = "dBm"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, device_id: str, alias: str) -> None:
        super().__init__(device_id, alias)
        self._attr_name = f"{alias} Sinal Wi-Fi"
        self._attr_unique_id = f"smartcloudage_{device_id}_wifi_rssi"
        self._quality = None

    @property
    def extra_state_attributes(self):
        """Expose the quality and alarm thresholds."""
        return {
            "signal_quality": self._quality,
            "alarm": self._quality in {"poor", "critical"},
            "warning_threshold_dbm": RSSI_WARNING_DBM,
            "critical_threshold_dbm": RSSI_CRITICAL_DBM,
        }

    def update_rssi(self, rssi: int) -> None:
        """Update RSSI and log only signal quality transitions."""
        previous_quality = self._quality
        self._attr_native_value = rssi
        self._quality = classify_rssi(rssi)

        if self._quality != previous_quality:
            if self._quality == "critical":
                _LOGGER.error(
                    "SmartCloudAge %s Wi-Fi signal is critical: %d dBm",
                    self._alias,
                    rssi,
                )
            elif self._quality == "poor":
                _LOGGER.warning(
                    "SmartCloudAge %s Wi-Fi signal is poor: %d dBm",
                    self._alias,
                    rssi,
                )
            elif previous_quality in {"poor", "critical"}:
                _LOGGER.info(
                    "SmartCloudAge %s Wi-Fi signal recovered: %d dBm (%s)",
                    self._alias,
                    rssi,
                    self._quality,
                )

        self.async_write_ha_state()


class SmartCloudAgeUptimeSensor(SmartCloudAgeDiagnosticSensor):
    """Controller uptime with reboot detection."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_suggested_unit_of_measurement = UnitOfTime.HOURS

    def __init__(self, device_id: str, alias: str) -> None:
        super().__init__(device_id, alias)
        self._attr_name = f"{alias} Uptime"
        self._attr_unique_id = f"smartcloudage_{device_id}_uptime"

    def update_uptime(self, uptime: int) -> None:
        """Update uptime and report a controller restart."""
        previous_uptime = self._attr_native_value
        if previous_uptime is not None and uptime < previous_uptime:
            _LOGGER.warning(
                "SmartCloudAge %s restarted: uptime dropped from %d to %d seconds",
                self._alias,
                previous_uptime,
                uptime,
            )
        self._attr_native_value = uptime
        self.async_write_ha_state()


class SmartCloudAgePulseSensor(SensorEntity):
    """Accumulated pulse meter exposed as a native HA sensor."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 3
    _attr_should_poll = False

    def __init__(self, device_id: str, alias: str, meter: dict[str, Any]) -> None:
        self._device_id = device_id
        self._channel = int(meter["channel"])
        self._factor = float(meter.get("factor", 1.0))
        self._offset = float(meter.get("offset", 0.0))
        meter_type = meter.get("type")
        self._attr_name = meter.get("name") or f"{alias} Sensor {self._channel}"
        self._attr_unique_id = f"smartcloudage_{device_id}_pulse_{self._channel}"
        self._attr_native_unit_of_measurement = NATIVE_UNITS.get(
            meter_type, meter.get("unit") or "pulses"
        )
        self._attr_device_class = DEVICE_CLASSES.get(meter_type)
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
