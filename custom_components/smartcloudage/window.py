"""Motorized windows driven by the controller's WINDOW scenario (firmware 2.0.9b+).

The firmware owns the motor safety: a pair of inputs (IN 1 opens, IN 2 closes,
IN 3/IN 4...) set to scenario type 8, one output per direction, a safety
timeout per run, an interlock with dead time between directions and an
optional limit switch (type 9). A direct write to a WINDOW output (command 11)
goes through the same logic: ON runs that direction with the timeout, OFF
stops it, ON while the other direction runs only stops the motor.

This module talks to that logic: it reads or writes the scenarios of the input
pair (command 15), switches the direction outputs (command 11) and estimates
the position from the travel time, since the controller reports only which
output is ON.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
import logging
import re

from homeassistant.components import mqtt
from homeassistant.helpers import device_registry as dr

from .air_conditioner import DOMAIN, find_device

_LOGGER = logging.getLogger(__name__)

COMMAND_OUTPUT = 11
COMMAND_SCENARIOS = 15
WRITE = 1
READ = 2
SCENARIO_WINDOW = 8
REQUEST_TIMEOUT_S = 5

## Firmware dead time between directions is 500 ms (WINDOW_DEAD_TIME_MS); wait a bit more.
DEAD_TIME_S = 0.7
## Firmware limits of the safety timeout (WINDOW_DEFAULT_TIMEOUT_MS / WINDOW_MAXIMUM_VALUE).
DEFAULT_TRAVEL_S = 30
MIN_TRAVEL_S = 2
MAX_TRAVEL_S = 600
## Safety timeout written to the controller: travel time plus this margin.
SAFETY_MARGIN_S = 5
## A run this close to the full travel time reached the end.
FULL_RUN_RATIO = 0.98
NUMBER_INPUTS = 16
NUMBER_OUTPUTS = 16

## Home Assistant cover device classes offered in the form.
DEVICE_CLASSES = {
    "window": "Janela",
    "shutter": "Persiana de enrolar",
    "blind": "Persiana de lâminas",
    "curtain": "Cortina",
    "awning": "Toldo",
    "shade": "Tela solar",
    "gate": "Portão",
    "garage": "Porta de garagem",
}
DEFAULT_DEVICE_CLASS = "window"

## Results of @ref async_probe_window.
WINDOW_OK = "ok"
WINDOW_NOT_CONFIGURED = "not_configured"  # the pair is not two WINDOW scenarios
WINDOW_BAD_OUTPUTS = "bad_outputs"  # a direction without output, or both on the same one
WINDOW_NO_RESPONSE = "no_response"  # controller offline or MQTT unreachable

_RESPONSE_15 = re.compile(r'"response"\s*:\s*\{\s*"type"\s*:\s*\d+\s*,\s*"command"\s*:\s*15\b')
## The firmware quotes the stored scenario without escaping it: "status":"{{...}}".
_SCENARIO = re.compile(r'"status"\s*:\s*"\{(\{[^{}]*\})\}"')
_STATUS_INT = re.compile(r'"status"\s*:\s*"?(-?\d+)')


def is_scenario_response(raw: str) -> bool:
    """Whether an MQTT message is a command-15 response (handled by @ref ScenarioClient)."""
    return bool(_RESPONSE_15.search(raw))


def parse_scenario(raw: str) -> dict | None:
    """Scenario object of a command-15 READ response, or None."""
    match = _SCENARIO.search(raw)
    if match is None:
        return None
    try:
        scenario = json.loads(match.group(1))
    except ValueError:
        return None
    return scenario if isinstance(scenario, dict) else None


def mask_outputs(mask: int) -> list[int]:
    """Output numbers (1-based) of a bit mask."""
    return [bit + 1 for bit in range(NUMBER_OUTPUTS) if mask >> bit & 1]


def output_mask(output: int) -> int:
    """Bit mask of one output number (1-based)."""
    return 1 << (int(output) - 1)


def outputs_from_message(raw: str) -> int | None:
    """Output bit mask of a controller status message, or None when it has none."""
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    inner = data.get("message")
    if isinstance(inner, str):
        if inner.upper() == "INPUT_STATUS":
            return None
        try:
            inner_obj = json.loads(inner)
        except ValueError:
            inner_obj = None
        if isinstance(inner_obj, dict):
            data = {**data, **inner_obj}
    section = data.get("Output")
    if not isinstance(section, dict) or section.get("Outputs") is None:
        return None
    try:
        return int(section["Outputs"])
    except (TypeError, ValueError):
        return None


## @brief Reads and writes the input scenarios of one controller (command 15).
class ScenarioClient:
    """Serializes command-15 requests to one controller and waits for each response."""

    def __init__(self, hass, device_id: str) -> None:
        self.hass = hass
        self.device_id = device_id
        self._lock = asyncio.Lock()
        self._pending: asyncio.Future | None = None
        self._unsubscribe: Callable[[], None] | None = None

    async def _async_request(self, command_type: int, payload: dict) -> str | None:
        async with self._lock:
            if self._unsubscribe is None:
                self._unsubscribe = await mqtt.async_subscribe(
                    self.hass, f"+/{self.device_id}/#", self._message_received, 0
                )
            self._pending = self.hass.loop.create_future()
            message = {
                "command": COMMAND_SCENARIOS,
                "type": command_type,
                "signature": self.device_id,
                "payload": payload,
            }
            await mqtt.async_publish(
                self.hass, f"CloudAge/{self.device_id}", json.dumps(message), 0, False
            )
            try:
                return await asyncio.wait_for(self._pending, REQUEST_TIMEOUT_S)
            except TimeoutError:
                return None
            finally:
                self._pending = None

    async def _message_received(self, msg) -> None:
        if self._pending is None or self._pending.done():
            return
        raw = msg.payload.decode("utf-8", "replace") if isinstance(msg.payload, bytes) else str(msg.payload)
        if is_scenario_response(raw):
            self._pending.set_result(raw)

    ## @brief Reads the scenario of one input.
    #  @return (answered, scenario): scenario is None when the input has none.
    async def async_read(self, index: int) -> tuple[bool, dict | None]:
        raw = await self._async_request(READ, {"index": index})
        if raw is None:
            return False, None
        return True, parse_scenario(raw)

    ## @brief Writes the scenario of one input.
    #  @return @c True when the controller saved it.
    async def async_write(self, scenario: dict) -> bool:
        raw = await self._async_request(WRITE, {"scenario": scenario})
        if raw is None:
            return False
        match = _STATUS_INT.search(raw)
        return bool(match and int(match.group(1)))

    def async_stop(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None


## @brief Checks that an input pair is a WINDOW pair and returns its outputs.
#  @param open_input Odd input of the pair (1, 3, 5...).
#  @return (result, details): details has open_mask, close_mask and time_ms when @ref WINDOW_OK.
async def async_probe_window(client: ScenarioClient, open_input: int) -> tuple[str, dict]:
    scenarios = []
    for index in (open_input, open_input + 1):
        answered, scenario = await client.async_read(index)
        if not answered:
            return WINDOW_NO_RESPONSE, {}
        scenarios.append(scenario or {})
    opening, closing = scenarios
    if any(int(s.get("type", 0)) != SCENARIO_WINDOW for s in scenarios):
        return WINDOW_NOT_CONFIGURED, {}
    open_mask = int(opening.get("output", 0)) & 0xFFFF
    close_mask = int(closing.get("output", 0)) & 0xFFFF
    if not open_mask or not close_mask or open_mask & close_mask:
        return WINDOW_BAD_OUTPUTS, {}
    return WINDOW_OK, {
        "open_mask": open_mask,
        "close_mask": close_mask,
        "time_ms": int(opening.get("time_ms", 0) or 0),
        "scenarios": scenarios,
    }


## @brief Writes the WINDOW scenario of both inputs of a pair.
#  @param previous Scenarios read before (their debounce and invert are kept), or None.
#  @return @c True when the controller saved both.
async def async_configure_window(
    client: ScenarioClient,
    open_input: int,
    open_output: int,
    close_output: int,
    travel_s: float,
    previous: list[dict] | None = None,
) -> bool:
    time_ms = int(min(MAX_TRAVEL_S, travel_s + SAFETY_MARGIN_S) * 1000)
    previous = previous or [{}, {}]
    for offset, output in ((0, open_output), (1, close_output)):
        old = previous[offset] or {}
        scenario = {
            "index": open_input + offset,
            "output": output_mask(output),
            "type": SCENARIO_WINDOW,
            "time_ms": time_ms,
            "debounce": int(old.get("debounce", 100) or 100),
            "invert": int(old.get("invert", 0) or 0),
        }
        if not await client.async_write(scenario):
            return False
    return True


## @brief Position estimate of a cover from its travel time.
class TravelTracker:
    """Estimates the position (0 closed .. 100 open) from how long the motor ran."""

    def __init__(self, travel_s: float, position: float | None = None) -> None:
        self.travel_s = max(float(travel_s), 0.1)
        self._position = position
        self.direction = 0  # 1 opening, -1 closing, 0 stopped
        self._start = 0.0
        self._start_position: float | None = position

    def position(self, now: float) -> float | None:
        """Estimated position at @p now, or None while unknown."""
        if self.direction == 0 or self._start_position is None:
            return self._position if self.direction == 0 else None
        moved = (now - self._start) / self.travel_s * 100.0
        return min(100.0, max(0.0, self._start_position + self.direction * moved))

    def set_position(self, position: float | None) -> None:
        """Known position (restored state or a full run)."""
        self._position = position
        if self.direction == 0:
            self._start_position = position

    def update(self, direction: int, now: float) -> None:
        """Motor direction confirmed by the controller."""
        if direction == self.direction:
            return
        if self.direction != 0:
            elapsed = now - self._start
            if self._start_position is None:
                if elapsed >= self.travel_s * FULL_RUN_RATIO:
                    self._position = 100.0 if self.direction > 0 else 0.0
            else:
                self._position = self.position(now)
        self.direction = direction
        self._start = now
        self._start_position = self._position

    def seconds_to(self, target: float, now: float) -> float | None:
        """Run time from the current estimate to @p target, or None while unknown."""
        current = self.position(now)
        if current is None:
            return None
        return abs(target - current) / 100.0 * self.travel_s


## @brief One motorized window: configuration and commands to its outputs.
class Window:
    """A window driven by a WINDOW scenario pair of a SmartCloudAge controller."""

    def __init__(self, hass, device_id: str, alias: str, config: dict) -> None:
        self.hass = hass
        self.device_id = device_id
        self.alias = alias
        self.window_id = config["id"]
        self.name = config["name"]
        self.open_input = int(config["open_input"])
        self.open_mask = int(config["open_mask"]) & 0xFFFF
        self.close_mask = int(config["close_mask"]) & 0xFFFF
        if not self.open_mask or not self.close_mask or self.open_mask & self.close_mask:
            raise ValueError("a window needs one distinct output per direction")
        self.travel_s = float(config.get("travel_time", DEFAULT_TRAVEL_S))
        device_class = config.get("device_class", DEFAULT_DEVICE_CLASS)
        self.device_class = device_class if device_class in DEVICE_CLASSES else DEFAULT_DEVICE_CLASS

    @property
    def unique_prefix(self) -> str:
        return f"smartcloudage_{self.device_id}_window_{self.window_id}"

    @property
    def inputs_label(self) -> str:
        return f"IN {self.open_input} / IN {self.open_input + 1}"

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, f"{self.device_id}_window_{self.window_id}")},
            "name": self.name,
            "manufacturer": "SmartCloudAge",
            "model": f"{DEVICE_CLASSES[self.device_class]} motorizada ({self.inputs_label})",
        }

    ## @brief Shows the window under its controller in the device registry.
    def link_to_controller(self, device_entry_id: str | None, config_entry_id: str | None) -> None:
        if not device_entry_id or not config_entry_id:
            return
        registry = dr.async_get(self.hass)
        controller = find_device(self.hass, config_entry_id, self.device_id)
        device = registry.async_get(device_entry_id)
        if controller is not None and device is not None and device.via_device_id != controller.id:
            registry.async_update_device(device.id, via_device_id=controller.id)

    ## @brief Direction (1 opening, -1 closing, 0 stopped) of a controller output mask.
    def direction(self, outputs: int) -> int:
        opening = bool(outputs & self.open_mask)
        closing = bool(outputs & self.close_mask)
        if opening and closing:
            _LOGGER.error("Window %s: both directions ON (outputs=%s)", self.name, outputs)
            return 0
        return 1 if opening else -1 if closing else 0

    async def _async_write(self, mask: int, value: int) -> None:
        for output in mask_outputs(mask):
            message = {
                "command": COMMAND_OUTPUT,
                "type": WRITE,
                "signature": self.device_id,
                "payload": {"id": output, "value": value},
            }
            await mqtt.async_publish(
                self.hass, f"CloudAge/{self.device_id}", json.dumps(message), 0, False
            )

    ## @brief Runs the motor in one direction; a run the other way stops first.
    #  @param direction 1 opens, -1 closes.
    #  @param moving Direction the motor runs now (from the controller status).
    async def async_run(self, direction: int, moving: int) -> None:
        if moving == -direction:
            # ON while the other direction runs would only stop the motor.
            await self._async_write(self.close_mask if direction > 0 else self.open_mask, 0)
            await asyncio.sleep(DEAD_TIME_S)
        await self._async_write(self.open_mask if direction > 0 else self.close_mask, 1)

    ## @brief Stops the motor (both directions OFF).
    async def async_stop(self) -> None:
        await self._async_write(self.open_mask, 0)
        await self._async_write(self.close_mask, 0)


## @brief Builds the windows of every controller.
def build_windows(hass, devices: list[dict]) -> list[Window]:
    windows: list[Window] = []
    for device in devices:
        device_id = device.get("device_id")
        if not device_id:
            continue
        alias = device.get("alias") or device_id
        for config in device.get("windows") or []:
            try:
                windows.append(Window(hass, device_id, alias, config))
            except (KeyError, ValueError, TypeError) as err:
                _LOGGER.error("Invalid window configuration %s: %s", config, err)
    return windows
