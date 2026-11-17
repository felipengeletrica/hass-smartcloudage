"""Motorized windows as covers (time-based, like ESPHome/Shelly/Sonoff time-based covers).

The open/closing state is the real one, from the outputs the controller
reports. The position is estimated from the open and close travel times. A
full open or close (panel or wall button) runs until the controller stops the
motor (limit switch or safety timeout) and re-references the position, so
there is no drift to manage. The position is kept across restarts, and is
dropped only when a run was cut at an unknown point (the controller or Home
Assistant restarted while the motor ran); a full close or open restores it.

Calibration (Shelly-style, for motors without power metering): close until
the controller stops the motor, then open timing the run and close timing the
run. Each timed run ends when the user marks the end of travel, a wall button
stops the motor, or a limit switch does. The window ends closed.

Safety: a wall button cannot tell an emergency stop from the end of travel, so
the calibration never moves the window again by itself after a stop: each next
step starts only when the user presses "Fim do curso". The dashboard asks for
confirmation with a safety warning before starting, and notifications remind
the user to watch the window.
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
    uptime_from_message,
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
CALIBRATION_WAIT_OPEN = "aguardando para abrir"
CALIBRATION_OPENING = "medindo abertura"
CALIBRATION_WAIT_CLOSE = "aguardando para fechar"
CALIBRATION_TIMING_CLOSE = "medindo fechamento"
## How long the calibration waits for the user between steps before giving up.
CONTINUE_TIMEOUT_S = 300

SAFETY_REMINDER = (
    "⚠️ **Acompanhe visualmente.** A janela **{name}** vai fechar até o fim. Confira que não há pessoas, "
    "crianças, animais ou objetos no caminho. **Parar** (ou o botão de parede) interrompe na hora.\n\n"
    "A calibração **não continua sozinha**: depois de cada parada, a janela só volta a se mover quando você "
    "tocar em **Fim do curso**."
)
WAIT_OPEN = (
    "**{name}** está fechada. Olhando para a janela, toque em **Fim do curso** para ela **abrir** medindo o "
    "tempo; toque de novo (ou aperte o botão de parede) quando ela estiver totalmente aberta."
)
WAIT_CLOSE = (
    "Abertura medida: **{travel:.1f} s**. Olhando para a janela, toque em **Fim do curso** para ela **fechar** "
    "medindo o tempo; toque de novo (ou aperte o botão de parede) quando ela estiver totalmente fechada."
)


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
        self.tracker = TravelTracker(window.travel_s, window.travel_close_s)
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
        self._uptime: int | None = None
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
            "travel_time_open": self.window.travel_s,
            "travel_time_close": self.window.travel_close_s,
            "position_known": self.tracker.position(time.monotonic()) is not None,
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
            if last.state in ("opening", "closing"):
                # Home Assistant stopped while the motor ran: where it stopped is unknown.
                self._notify_lost("o Home Assistant reiniciou com a janela em movimento")
            elif isinstance(position, (int, float)):
                self.tracker.set_position(float(position))
        self.async_on_remove(
            await mqtt.async_subscribe(
                self.hass, f"+/{self.window.device_id}/#", self._message_received, 0
            )
        )
        self.async_on_remove(self._cancel_timers)

    async def _message_received(self, msg) -> None:
        raw = msg.payload.decode("utf-8", "replace") if isinstance(msg.payload, bytes) else str(msg.payload)
        uptime = uptime_from_message(raw)
        if uptime is not None:
            restarted = self._uptime is not None and uptime < self._uptime
            self._uptime = uptime
            if restarted and self.tracker.direction != 0:
                # Power failure or reset while the motor ran: the run was cut at an unknown point.
                self._lose_position("a controladora reiniciou (falta de energia?) com a janela em movimento")
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
            if self.tracker.position(now) in (0.0, 100.0):
                # A full run re-referenced the position: the "lost" notice no longer applies.
                persistent_notification.async_dismiss(self.hass, f"{self.window.unique_prefix}_reference")
        if direction != 0 and self._cancel_refresh is None:
            self._cancel_refresh = async_track_time_interval(self.hass, self._refresh, REFRESH_INTERVAL)
        elif direction == 0 and self._cancel_refresh is not None:
            self._cancel_refresh()
            self._cancel_refresh = None
        self.async_write_ha_state()

    @callback
    def _refresh(self, _now) -> None:
        self.async_write_ha_state()

    ## @brief Forgets the position (it can no longer be trusted) and tells the user.
    def _lose_position(self, reason: str) -> None:
        self.tracker.update(0, time.monotonic())
        self.tracker.set_position(None)
        self._target = None
        self._cancel_target_timer()
        self._notify_lost(reason)
        self.async_write_ha_state()

    def _notify_lost(self, reason: str) -> None:
        self._notify(
            f"A posição de **{self.window.name}** foi perdida: {reason}.\n\n"
            "Abrir e fechar continuam funcionando. Para a posição voltar, **acompanhando visualmente**, "
            "feche ou abra a janela até o fim (pelo painel ou pelo botão de parede).",
            "reference",
        )

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
        current = self.tracker.position(time.monotonic())
        if current is None:
            raise HomeAssistantError(
                f"A posição de {self.window.name} é desconhecida: feche ou abra a janela até o fim uma vez."
            )
        if abs(target - current) < 1:
            return
        await self._async_move(1 if target > current else -1, target)

    ## @brief Starts the motor; with a target, stops it when the estimate gets there.
    #  Without a target (full open/close) the controller stops it at the end, which re-references.
    async def _async_move(self, direction: int, target: float | None) -> None:
        self._guard_calibration()
        self._cancel_target_timer()
        self._target = target
        await self.window.async_run(direction, self.tracker.direction)
        if target is None:
            return
        # Counted from now: the controller confirms the start a moment later.
        seconds = self.tracker.seconds_to(target, time.monotonic()) or 0.0
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

    ## @brief Starts the calibration: close to the end, then time an open and a close.
    async def async_calibrate(self) -> None:
        self._guard_calibration()
        self._target = None
        self._cancel_target_timer()
        self.calibration = CALIBRATION_CLOSING
        self._notify(SAFETY_REMINDER.format(name=self.window.name))
        self.async_write_ha_state()
        self._calibration_task = self.hass.async_create_task(self._async_run_calibration())

    ## @brief Starts the next calibration step, or marks the end of the run being timed.
    async def async_calibrate_mark(self) -> None:
        if self.calibration not in (
            CALIBRATION_WAIT_OPEN, CALIBRATION_OPENING, CALIBRATION_WAIT_CLOSE, CALIBRATION_TIMING_CLOSE
        ):
            raise HomeAssistantError(
                "Toque em Fim do curso só durante a calibração, depois que a janela fechar."
            )
        self._marked.set()

    async def _async_wait_user(self, state: str, message: str) -> None:
        """Safety: after every stop, move again only when the user presses Fim do curso."""
        self._marked.clear()
        self.calibration = state
        self._notify(message)
        self.async_write_ha_state()
        if not await self._async_wait(self._marked, CONTINUE_TIMEOUT_S):
            raise HomeAssistantError("ninguém tocou em Fim do curso para continuar")
        await asyncio.sleep(DEAD_TIME_S)

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

    async def _async_close_to_end(self, limit: float) -> None:
        """Close until the controller stops the motor (limit switch or safety timeout)."""
        self._started.clear()
        self._stopped.clear()
        await self.window.async_run(-1, self.tracker.direction)
        if await self._async_wait(self._started, START_TIMEOUT_S):
            if not await self._async_wait(self._stopped, limit):
                await self.window.async_stop()
                raise HomeAssistantError("o motor não parou ao fechar")
        # No start: already at the closing limit switch.
        self.tracker.set_position(0.0)

    async def _async_timed_run(self, direction: int, safety: float) -> float:
        """Run one way and time it until the mark, a wall button or a limit switch stops it."""
        self._started.clear()
        self._stopped.clear()
        self._marked.clear()
        await self.window.async_run(direction, 0)
        if not await self._async_wait(self._started, START_TIMEOUT_S):
            raise HomeAssistantError("a controladora não ligou a saída do motor")
        start = self._last_change
        marked = asyncio.ensure_future(self._marked.wait())
        stopped = asyncio.ensure_future(self._stopped.wait())
        done, pending = await asyncio.wait(
            {marked, stopped}, timeout=safety + STOP_GRACE_S, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        if marked in done:
            end = time.monotonic()
            await self.window.async_stop()
            # The stop confirmation must arrive before the position is set to the end.
            await self._async_wait(self._stopped, START_TIMEOUT_S)
        elif stopped in done:
            end = self._last_change
        else:
            await self.window.async_stop()
            raise HomeAssistantError("o motor não parou")
        elapsed = end - start
        if marked not in done and elapsed >= safety * TIMEOUT_RATIO:
            raise HomeAssistantError(
                f"o tempo máximo de segurança da controladora ({safety:.0f} s) acabou antes do fim do curso. "
                "Toque em Fim do curso quando a janela chegar ao fim, ou aumente o tempo do cenário Janela "
                "no SCA Command"
            )
        if elapsed < MIN_TRAVEL_S:
            raise HomeAssistantError(f"curso de {elapsed:.1f} s é curto demais")
        return round(elapsed, 1)

    async def _async_run_calibration(self) -> None:
        try:
            safety = await self._async_safety_timeout()
            # 1) Reference: closed. Any stop (limit switch, timeout or a wall button) ends it,
            #    and nothing moves again until the user confirms.
            await self._async_close_to_end(safety + STOP_GRACE_S)
            await self._async_wait_user(CALIBRATION_WAIT_OPEN, WAIT_OPEN.format(name=self.window.name))
            # 2) Time the opening.
            self.calibration = CALIBRATION_OPENING
            self.async_write_ha_state()
            travel_open = await self._async_timed_run(1, safety)
            self.tracker.set_position(100.0)
            await self._async_wait_user(CALIBRATION_WAIT_CLOSE, WAIT_CLOSE.format(travel=travel_open))
            # 3) Time the closing (the window ends closed).
            self.calibration = CALIBRATION_TIMING_CLOSE
            self.async_write_ha_state()
            travel_close = await self._async_timed_run(-1, safety)
            self.tracker.set_position(0.0)

            self.window.travel_s = self.tracker.travel_up = travel_open
            self.window.travel_close_s = self.tracker.travel_down = travel_close
            self._notify(
                f"Calibração concluída: abrir em **{travel_open:.1f} s**, fechar em **{travel_close:.1f} s**. "
                "A janela está fechada (0 %)."
            )
            self._save_travel_times(travel_open, travel_close)
        except asyncio.CancelledError:
            self._notify("Calibração cancelada. Os tempos de curso não mudaram.")
            raise
        except HomeAssistantError as err:
            self._notify(f"Calibração não concluída: {err}. Os tempos de curso não mudaram.")
        except Exception:  # noqa: BLE001 - never leave the calibration state stuck
            _LOGGER.exception("Calibration of %s failed", self.window.name)
            self._notify("Calibração não concluída por um erro inesperado; veja o log.")
        finally:
            self.calibration = CALIBRATION_IDLE
            self._calibration_task = None
            if self.hass is not None:
                self.async_write_ha_state()

    def _notify(self, message: str, kind: str = "calibration") -> None:
        persistent_notification.async_create(
            self.hass,
            message,
            title=f"Janela {self.window.name}",
            notification_id=f"{self.window.unique_prefix}_{kind}",
        )

    ## @brief Stores the measured travel times in the config entry (reloads it).
    def _save_travel_times(self, travel_open: float, travel_close: float) -> None:
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
                    config["travel_time"] = travel_open
                    config["travel_time_close"] = travel_close
        self.hass.config_entries.async_update_entry(entry, options={**entry.options, "devices": devices})
