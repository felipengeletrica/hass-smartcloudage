"""Motorized windows as covers.

The open/closing state is the real one, from the outputs the controller
reports. The position is an estimate from the travel time: it becomes known
after one full run (open or close until the end) and is kept across restarts.

Travel-time calibration: close until the controller stops the motor (limit
switch or safety timeout), then open and time the run until the user marks
the end ("Fim do curso" button), presses a wall button (the firmware stops),
or a limit switch stops it. The measured time is saved as the travel time.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.components import mqtt, persistent_notification
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
from .window import (
    DEAD_TIME_S,
    DEFAULT_TRAVEL_S,
    MAX_TRAVEL_S,
    MIN_TRAVEL_S,
    ScenarioClient,
    TravelTracker,
    Window,
    mask_outputs,
    outputs_from_message,
)

_LOGGER = logging.getLogger(__name__)

## How often the estimated position is refreshed while the motor runs.
REFRESH_INTERVAL = timedelta(seconds=1)
## Time for the controller to confirm that the motor started.
START_TIMEOUT_S = 5
## Extra wait beyond the firmware safety timeout before giving up.
STOP_GRACE_S = 3
## A run this close to the safety timeout was cut by it, not by the end of travel.
TIMEOUT_RATIO = 0.95

CALIBRATION_IDLE = None
CALIBRATION_CLOSING = "fechando"
CALIBRATION_OPENING = "abrindo"


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
        self.calibration: str | None = CALIBRATION_IDLE
        self._calibration_task: asyncio.Task | None = None
        self._started = asyncio.Event()
        self._stopped = asyncio.Event()
        self._marked = asyncio.Event()
        self._last_change = 0.0
        window.cover = self

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
            "calibration": self.calibration,
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
        now = time.monotonic()
        self.tracker.update(direction, now)
        if direction != previous:
            self._last_change = now
            if direction != 0:
                self._started.set()
            elif previous != 0:
                self._stopped.set()
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
        if self._calibration_task is not None:
            self._calibration_task.cancel()
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
        self._guard_calibration()
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
        if self._calibration_task is not None:
            self._calibration_task.cancel()

    # ------------------------------------------------------------ calibration

    def _guard_calibration(self) -> None:
        if self.calibration is not CALIBRATION_IDLE:
            raise HomeAssistantError(
                f"{self.window.name} está calibrando: aguarde ou toque em Parar para cancelar."
            )

    ## @brief Starts the travel-time calibration (close, then open and time it).
    async def async_calibrate(self) -> None:
        self._guard_calibration()
        self._target = None
        self._cancel_target_timer()
        self.calibration = CALIBRATION_CLOSING
        self.async_write_ha_state()
        self._calibration_task = self.hass.async_create_task(self._async_run_calibration())

    ## @brief Marks the end of travel while the calibration opens the window.
    async def async_calibrate_mark(self) -> None:
        if self.calibration != CALIBRATION_OPENING:
            raise HomeAssistantError(
                "Toque em Fim do curso só durante a calibração, quando a janela estiver abrindo."
            )
        self._marked.set()

    async def _async_safety_timeout(self) -> float:
        """Safety timeout of the open input (command 15), or the firmware default."""
        client = ScenarioClient(self.hass, self.window.device_id)
        try:
            answered, scenario = await client.async_read(self.window.open_input)
        finally:
            client.async_stop()
        time_ms = int((scenario or {}).get("time_ms", 0) or 0) if answered else 0
        return min(MAX_TRAVEL_S, time_ms / 1000) if time_ms else DEFAULT_TRAVEL_S

    async def _async_wait(self, event: asyncio.Event, timeout: float) -> bool:
        try:
            await asyncio.wait_for(event.wait(), timeout)
        except TimeoutError:
            return False
        return True

    async def _async_run_calibration(self) -> None:
        name = self.window.name
        try:
            safety = await self._async_safety_timeout()
            limit = safety + STOP_GRACE_S

            # 1) Close until the controller stops the motor: the window is closed.
            self._started.clear()
            self._stopped.clear()
            await self.window.async_run(-1, self.tracker.direction)
            if await self._async_wait(self._started, START_TIMEOUT_S):
                if not await self._async_wait(self._stopped, limit):
                    await self.window.async_stop()
                    raise HomeAssistantError("o motor não parou ao fechar")
            # No start: already at the closing limit switch.
            self.tracker.set_position(0.0)
            await asyncio.sleep(DEAD_TIME_S)

            # 2) Open and time it until the end of travel.
            self.calibration = CALIBRATION_OPENING
            self._started.clear()
            self._stopped.clear()
            self._marked.clear()
            self.async_write_ha_state()
            await self.window.async_run(1, 0)
            if not await self._async_wait(self._started, START_TIMEOUT_S):
                raise HomeAssistantError("a controladora não ligou a saída de abrir")
            start = self._last_change
            marked = asyncio.ensure_future(self._marked.wait())
            stopped = asyncio.ensure_future(self._stopped.wait())
            done, pending = await asyncio.wait({marked, stopped}, timeout=limit,
                                               return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            if marked in done:
                end = time.monotonic()
                await self.window.async_stop()
                # The stop confirmation must arrive before the position is set to 100 %.
                await self._async_wait(self._stopped, START_TIMEOUT_S)
            elif stopped in done:
                end = self._last_change
            else:
                await self.window.async_stop()
                raise HomeAssistantError("o motor não parou ao abrir")
            elapsed = end - start
            if marked not in done and elapsed >= safety * TIMEOUT_RATIO:
                raise HomeAssistantError(
                    f"o tempo máximo de segurança da controladora ({safety:.0f} s) acabou antes do fim do "
                    "curso. Toque em Fim do curso quando a janela abrir toda, ou aumente o tempo do cenário "
                    "Janela no SCA Command"
                )
            if elapsed < MIN_TRAVEL_S:
                raise HomeAssistantError(f"curso de {elapsed:.1f} s é curto demais")

            travel = round(elapsed, 1)
            self.tracker.travel_s = travel
            self.window.travel_s = travel
            self.tracker.set_position(100.0)
            self._notify(
                f"Calibração concluída: **{travel:.1f} s** de curso. A janela está aberta (100 %) e a "
                "posição passa a ser estimada com esse tempo."
            )
            self._save_travel_time(travel)
        except asyncio.CancelledError:
            self._notify("Calibração cancelada. O tempo de curso não mudou.")
            raise
        except HomeAssistantError as err:
            self._notify(f"Calibração não concluída: {err}. O tempo de curso não mudou.")
        except Exception:  # noqa: BLE001 - never leave the calibration state stuck
            _LOGGER.exception("Calibration of %s failed", name)
            self._notify("Calibração não concluída por um erro inesperado; veja o log.")
        finally:
            self.calibration = CALIBRATION_IDLE
            self._calibration_task = None
            if self.hass is not None:
                self.async_write_ha_state()

    def _notify(self, message: str) -> None:
        persistent_notification.async_create(
            self.hass,
            message,
            title=f"Janela {self.window.name}",
            notification_id=f"{self.window.unique_prefix}_calibration",
        )

    ## @brief Stores the measured travel time in the config entry (reloads it).
    def _save_travel_time(self, travel: float) -> None:
        entry_id = self.registry_entry.config_entry_id if self.registry_entry else None
        entry = self.hass.config_entries.async_get_entry(entry_id) if entry_id else None
        if entry is None:
            return
        devices = copy.deepcopy(entry.options.get("devices") or entry.data.get("devices", []))
        for device in devices:
            if device.get("device_id") != self.window.device_id:
                continue
            for config in device.get("windows") or []:
                if config.get("id") == self.window.window_id:
                    config["travel_time"] = travel
        self.hass.config_entries.async_update_entry(entry, options={**entry.options, "devices": devices})
