"""Air conditioners controlled through the controller's IR emitter."""

from __future__ import annotations

import logging

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfTemperature
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity

from .air_conditioner import DOMAIN, AirConditioner
from .hvac import AcFanSpeed, AcMode, AcSwingHorizontal, AcSwingVertical

_LOGGER = logging.getLogger(__name__)

HVAC_MODES = {
    AcMode.AUTO: HVACMode.AUTO,
    AcMode.COOL: HVACMode.COOL,
    AcMode.DRY: HVACMode.DRY,
    AcMode.FAN: HVACMode.FAN_ONLY,
    AcMode.HEAT: HVACMode.HEAT,
}
FAN_MODES = {
    AcFanSpeed.AUTO: "auto",
    AcFanSpeed.QUIET: "min",
    AcFanSpeed.LOW: "low",
    AcFanSpeed.MEDIUM: "medium",
    AcFanSpeed.HIGH: "high",
    AcFanSpeed.MAX: "max",
}
SWING_MODES = {
    AcSwingVertical.OFF: "off",
    AcSwingVertical.SWING: "on",
    AcSwingVertical.POS1: "highest",
    AcSwingVertical.POS2: "high",
    AcSwingVertical.POS3: "middle",
    AcSwingVertical.POS4: "low",
    AcSwingVertical.POS5: "lowest",
}
SWING_HORIZONTAL_MODES = {
    AcSwingHorizontal.OFF: "off",
    AcSwingHorizontal.SWING: "on",
    AcSwingHorizontal.LEFT: "left_max",
    AcSwingHorizontal.LEFT_CENTER: "left",
    AcSwingHorizontal.CENTER: "middle",
    AcSwingHorizontal.RIGHT_CENTER: "right",
    AcSwingHorizontal.RIGHT: "right_max",
}
## Presets map to mutually exclusive A/C flags.
PRESET_NONE = "none"
PRESET_FLAGS = {"eco": "eco", "boost": "turbo", "sleep": "sleep", "quiet": "quiet"}


def _reverse(mapping: dict, value):
    return next((key for key, item in mapping.items() if item == value), None)


## @brief Creates one climate entity per configured air conditioner.
#  @param hass Active Home Assistant instance.
#  @param entry SmartCloudAge configuration entry.
#  @param async_add_entities Callback used to register entities.
async def async_setup_entry(hass, entry, async_add_entities):
    """Create the air conditioner entities."""
    units = hass.data[DOMAIN][entry.entry_id]["air_conditioners"]
    async_add_entities(SmartCloudAgeClimate(unit) for unit in units)


## @brief Climate entity backed by an IR-controlled air conditioner.
class SmartCloudAgeClimate(ClimateEntity, RestoreEntity):
    """An air conditioner driven by a SmartCloudAge IR emitter."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "air_conditioner"
    _attr_assumed_state = True
    _attr_should_poll = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1
    _attr_precision = 1

    def __init__(self, unit: AirConditioner) -> None:
        self.unit = unit
        caps = unit.capabilities
        self._attr_unique_id = f"{unit.unique_prefix}_climate"
        self._attr_device_info = unit.device_info
        self._attr_min_temp = caps.min_temperature_c
        self._attr_max_temp = caps.max_temperature_c
        self._attr_current_temperature = None

        self._attr_hvac_modes = [HVACMode.OFF] + [HVAC_MODES[m] for m in unit.supported_modes()]
        self._attr_fan_modes = [FAN_MODES[f] for f in unit.supported_fan_speeds()]
        self._attr_swing_modes = [SWING_MODES[s] for s in unit.supported_swing_vertical()]
        self._attr_swing_horizontal_modes = [
            SWING_HORIZONTAL_MODES[s] for s in unit.supported_swing_horizontal()
        ]
        supported = {
            "eco": caps.supports_eco,
            "boost": caps.supports_turbo,
            "sleep": caps.supports_sleep,
            "quiet": caps.supports_quiet,
        }
        presets = [preset for preset, ok in supported.items() if ok]
        self._attr_preset_modes = [PRESET_NONE, *presets] if presets else None

        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        if len(self._attr_swing_modes) > 1:
            features |= ClimateEntityFeature.SWING_MODE
        if len(self._attr_swing_horizontal_modes) > 1:
            features |= ClimateEntityFeature.SWING_HORIZONTAL_MODE
        if self._attr_preset_modes:
            features |= ClimateEntityFeature.PRESET_MODE
        self._attr_supported_features = features

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def hvac_mode(self) -> HVACMode:
        s = self.unit.state
        if not s.power:
            return HVACMode.OFF
        return HVAC_MODES.get(s.mode, HVACMode.COOL)

    @property
    def target_temperature(self) -> float:
        return self.unit.state.temperature_c

    @property
    def fan_mode(self) -> str:
        return FAN_MODES[self.unit.state.fan_speed]

    @property
    def swing_mode(self) -> str | None:
        return SWING_MODES[self.unit.state.swing_vertical] if self._attr_swing_modes else None

    @property
    def swing_horizontal_mode(self) -> str | None:
        if not self._attr_swing_horizontal_modes:
            return None
        return SWING_HORIZONTAL_MODES[self.unit.state.swing_horizontal]

    @property
    def preset_mode(self) -> str | None:
        if not self._attr_preset_modes:
            return None
        s = self.unit.state
        for preset, flag in PRESET_FLAGS.items():
            if preset in self._attr_preset_modes and getattr(s, flag):
                return preset
        return PRESET_NONE

    @property
    def extra_state_attributes(self):
        return {
            "controller": self.unit.device_id,
            "brand": self.unit.brand.value,
            "remote_model": self.unit.model,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.unit.add_listener(self.async_write_ha_state))
        entry = self.registry_entry
        self.unit.link_to_controller(
            self.hass, entry.device_id if entry else None, entry.config_entry_id if entry else None
        )
        await self._async_restore()
        if self.unit.temperature_sensor:
            self._update_current_temperature(self.hass.states.get(self.unit.temperature_sensor))
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [self.unit.temperature_sensor], self._temperature_changed
                )
            )

    async def _async_restore(self) -> None:
        """Show the last state sent before the restart; nothing is transmitted."""
        last = await self.async_get_last_state()
        if last is None:
            return
        attrs = last.attributes
        changes = {}
        if last.state == HVACMode.OFF:
            changes["power"] = False
        elif (mode := _reverse(HVAC_MODES, last.state)) is not None:
            changes.update(power=True, mode=mode)
        if isinstance(attrs.get(ATTR_TEMPERATURE), (int, float)):
            changes["temperature_c"] = int(
                max(self.min_temp, min(attrs[ATTR_TEMPERATURE], self.max_temp))
            )
        if (fan := _reverse(FAN_MODES, attrs.get("fan_mode"))) is not None:
            changes["fan_speed"] = fan
        if self._attr_swing_modes and (sv := _reverse(SWING_MODES, attrs.get("swing_mode"))):
            changes["swing_vertical"] = sv
        if self._attr_swing_horizontal_modes and (
            sh := _reverse(SWING_HORIZONTAL_MODES, attrs.get("swing_horizontal_mode"))
        ):
            changes["swing_horizontal"] = sh
        preset = attrs.get("preset_mode")
        if self._attr_preset_modes and preset in self._attr_preset_modes:
            changes.update(_preset_flags(preset))
        self.unit.restore(**changes)

    @callback
    def _temperature_changed(self, event) -> None:
        self._update_current_temperature(event.data.get("new_state"))
        self.async_write_ha_state()

    def _update_current_temperature(self, state) -> None:
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            self._attr_current_temperature = None
            return
        try:
            self._attr_current_temperature = float(state.state)
        except ValueError:
            self._attr_current_temperature = None

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.unit.async_apply(power=False)
            return
        await self.unit.async_apply(power=True, mode=_reverse(HVAC_MODES, hvac_mode))

    async def async_turn_on(self) -> None:
        await self.unit.async_apply(power=True)

    async def async_turn_off(self) -> None:
        await self.unit.async_apply(power=False)

    async def async_set_temperature(self, **kwargs) -> None:
        changes = {}
        if (hvac_mode := kwargs.get("hvac_mode")) is not None:
            if hvac_mode == HVACMode.OFF:
                changes["power"] = False
            else:
                changes.update(power=True, mode=_reverse(HVAC_MODES, hvac_mode))
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            changes["temperature_c"] = int(round(max(self.min_temp, min(temperature, self.max_temp))))
        if changes:
            await self.unit.async_apply(**changes)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self.unit.async_apply(fan_speed=_reverse(FAN_MODES, fan_mode))

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        await self.unit.async_apply(swing_vertical=_reverse(SWING_MODES, swing_mode))

    async def async_set_swing_horizontal_mode(self, swing_horizontal_mode: str) -> None:
        await self.unit.async_apply(
            swing_horizontal=_reverse(SWING_HORIZONTAL_MODES, swing_horizontal_mode)
        )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        await self.unit.async_apply(**_preset_flags(preset_mode))


def _preset_flags(preset: str) -> dict[str, bool]:
    """Flags for a preset: the chosen one on, every other preset flag off."""
    return {flag: preset_name == preset for preset_name, flag in PRESET_FLAGS.items()}
