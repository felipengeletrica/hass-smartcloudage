"""Motorized windows as covers.

The open/closing state is the real one, from the outputs the controller
reports. The position is an estimate from the travel time: it becomes known
after one full run (open or close until the end) and is kept across restarts.
"""

from __future__ import annotations

from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.components import mqtt
from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity

from .air_conditioner import DOMAIN
from .window import TravelTracker, Window, mask_outputs, outputs_from_message

_LOGGER = logging.getLogger(__name__)

## How often the estimated position is refreshed while the motor runs.
REFRESH_INTERVAL = timedelta(seconds=1)


## @brief Creates one cover per configured window.
async def async_setup_entry(hass, entry, async_add_entities):
    """Create the window covers."""
    windows = hass.data[DOMAIN][entry.entry_id].get("windows", [])
    async_add_entities(SmartCloudAgeWindowCover(window) for window in windows)


## @brief A motorized window of a SmartCloudAge controller.
class SmartCloudAgeWindowCover(CoverEntity, RestoreEntity):
    """Window driven by the controller's WINDOW scenario."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(self, window: Window) -> None:
        self.window = window
        self.tracker = TravelTracker(window.travel_s)
        self._attr_unique_id = f"{window.unique_prefix}_cover"
        self._attr_device_info = window.device_info
        self._attr_device_class = CoverDeviceClass(window.device_class)
        self._target: float | None = None
        self._cancel_target: Any = None
        self._cancel_refresh: Any = None

    # ------------------------------------------------------------------ state

    @property
    def current_cover_position(self) -> int | None:
        position = self.tracker.position(time.monotonic())
        return None if position is None else round(position)

    @property
    def is_opening(self) -> bool:
        return self.tracker.direction > 0

    @property
    def is_closing(self) -> bool:
        return self.tracker.direction < 0

    @property
    def is_closed(self) -> bool | None:
        position = self.current_cover_position
        return None if position is None else position == 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "controller": self.window.device_id,
            "inputs": self.window.inputs_label,
            "open_outputs": mask_outputs(self.window.open_mask),
            "close_outputs": mask_outputs(self.window.close_mask),
            "travel_time": self.window.travel_s,
            "position_estimated": True,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        entry = self.registry_entry
        self.window.link_to_controller(
            entry.device_id if entry else None, entry.config_entry_id if entry else None
        )
        if (last := await self.async_get_last_state()) is not None:
            position = last.attributes.get(ATTR_CURRENT_POSITION)
            if isinstance(position, (int, float)):
                self.tracker.set_position(float(position))
        self.async_on_remove(
            await mqtt.async_subscribe(
                self.hass, f"+/{self.window.device_id}/#", self._message_received, 0
            )
        )
        self.async_on_remove(self._cancel_timers)

    async def _message_received(self, msg) -> None:
        raw = msg.payload.decode("utf-8", "replace") if isinstance(msg.payload, bytes) else str(msg.payload)
        outputs = outputs_from_message(raw)
        if outputs is not None:
            self.apply_outputs(outputs)

    ## @brief Applies the output mask reported by the controller.
    @callback
    def apply_outputs(self, outputs: int) -> None:
        direction = self.window.direction(outputs)
        previous = self.tracker.direction
        self.tracker.update(direction, time.monotonic())
        if direction == 0 and previous != 0:
            self._target = None
            self._cancel_target_timer()
        if direction != 0 and self._cancel_refresh is None:
            self._cancel_refresh = async_track_time_interval(self.hass, self._refresh, REFRESH_INTERVAL)
        elif direction == 0 and self._cancel_refresh is not None:
            self._cancel_refresh()
            self._cancel_refresh = None
        self.async_write_ha_state()

    @callback
    def _refresh(self, _now) -> None:
        self.async_write_ha_state()

    # --------------------------------------------------------------- commands

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._async_move(1, None)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._async_move(-1, None)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        self._target = None
        self._cancel_target_timer()
        await self.window.async_stop()

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        target = float(kwargs[ATTR_POSITION])
        if target >= 100:
            await self._async_move(1, None)
            return
        if target <= 0:
            await self._async_move(-1, None)
            return
        now = time.monotonic()
        current = self.tracker.position(now)
        if current is None:
            raise HomeAssistantError(
                f"A posição de {self.window.name} ainda é desconhecida: abra ou feche "
                "até o fim uma vez para a integração aprender onde ela está."
            )
        if abs(target - current) < 1:
            return
        await self._async_move(1 if target > current else -1, target)

    ## @brief Starts the motor; with a target, stops it when the estimate gets there.
    async def _async_move(self, direction: int, target: float | None) -> None:
        self._cancel_target_timer()
        self._target = target
        await self.window.async_run(direction, self.tracker.direction)
        if target is None:
            return
        # Counted from now: the controller confirms the start a moment later.
        seconds = abs(target - (self.tracker.position(time.monotonic()) or 0.0)) / 100.0 * self.window.travel_s
        self._cancel_target = async_call_later(self.hass, seconds, self._target_reached)

    async def _target_reached(self, _now) -> None:
        self._cancel_target = None
        if self._target is None:
            return
        self._target = None
        await self.window.async_stop()

    def _cancel_target_timer(self) -> None:
        if self._cancel_target is not None:
            self._cancel_target()
            self._cancel_target = None

    @callback
    def _cancel_timers(self) -> None:
        self._cancel_target_timer()
        if self._cancel_refresh is not None:
            self._cancel_refresh()
            self._cancel_refresh = None
