"""Tests for SmartCloudAge pulse sensors."""

from __future__ import annotations

from unittest.mock import Mock

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfEnergy, UnitOfVolume

from custom_components.smartcloudage.sensor import SmartCloudAgePulseSensor


def _meter(meter_type: str, **overrides):
    meter = {
        "channel": 9,
        "name": "Medidor",
        "type": meter_type,
        "factor": 0.01,
        "offset": 5.502,
        "unit": "wrong-unit",
    }
    meter.update(overrides)
    return meter


def test_energy_metadata_uses_native_kwh():
    """Energy meters must be eligible for Home Assistant energy statistics."""
    sensor = SmartCloudAgePulseSensor(
        "controller-01", "Bancada", _meter("energy")
    )

    assert sensor.device_class is SensorDeviceClass.ENERGY
    assert sensor.state_class is SensorStateClass.TOTAL_INCREASING
    assert sensor.native_unit_of_measurement == UnitOfEnergy.KILO_WATT_HOUR


def test_water_and_gas_metadata_use_cubic_meters():
    """Volume meters must ignore an incompatible configured unit."""
    water = SmartCloudAgePulseSensor(
        "controller-01", "Bancada", _meter("water")
    )
    gas = SmartCloudAgePulseSensor(
        "controller-01", "Bancada", _meter("gas")
    )

    assert water.device_class is SensorDeviceClass.WATER
    assert gas.device_class is SensorDeviceClass.GAS
    assert water.native_unit_of_measurement == UnitOfVolume.CUBIC_METERS
    assert gas.native_unit_of_measurement == UnitOfVolume.CUBIC_METERS


def test_generic_counter_keeps_configured_unit():
    """Generic counters may keep the unit supplied by the user."""
    sensor = SmartCloudAgePulseSensor(
        "controller-01",
        "Bancada",
        _meter("count", unit="cycles", factor=1),
    )

    assert sensor.device_class is None
    assert sensor.native_unit_of_measurement == "cycles"


def test_pulse_conversion_applies_factor_and_offset():
    """The exposed total is raw pulses times factor plus offset."""
    sensor = SmartCloudAgePulseSensor(
        "controller-01",
        "Bancada",
        _meter("water", factor=0.01, offset=5.502),
    )
    sensor.async_write_ha_state = Mock()

    sensor.update_pulses(208)

    assert sensor.native_value == 7.582
    assert sensor.extra_state_attributes == {
        "raw_pulses": 208,
        "pulse_factor": 0.01,
        "offset": 5.502,
        "channel": 9,
    }
    sensor.async_write_ha_state.assert_called_once_with()


def test_unique_id_and_device_registry_identity_are_stable():
    """Entity and device identities must not depend on display names."""
    sensor = SmartCloudAgePulseSensor(
        "controller-01", "Bancada", _meter("water", channel=14)
    )

    assert sensor.unique_id == "smartcloudage_controller-01_pulse_14"
    assert sensor.device_info["identifiers"] == {
        ("smartcloudage", "controller-01")
    }
