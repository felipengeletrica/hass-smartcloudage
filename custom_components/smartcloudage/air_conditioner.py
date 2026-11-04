"""Air conditioners driven by a controller's IR emitter.

IR is one-way: the controller only confirms it accepted and queued the frame
(command 32 response), not that the A/C received it. Every change is sent as
a full state, so the unit ends up in the state shown in Home Assistant even
when an earlier frame was missed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
import json
import logging
import re

from homeassistant.components import mqtt
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from .hvac import (
    AcBrand,
    AcCommandState,
    AcFanSpeed,
    AcMode,
    AcSwingHorizontal,
    AcSwingVertical,
    brand_name,
    build_payload,
    get_protocol,
)

DOMAIN = "smartcloudage"
COMMAND_IR_SEND = 32
WRITE = 1
READ = 2
ACK_TIMEOUT_S = 5

## The emitter sits on one of the two PWM outputs (firmware IOs.h: PIN_OUT_PWM1/2).
## The GPIO always goes in the command, even PWM2, which is the firmware default.
PWM1_GPIO = 26
PWM2_GPIO = 23
IR_OUTPUTS = {PWM1_GPIO: "PWM1", PWM2_GPIO: "PWM2"}

## Results of @ref IrSender.async_probe.
IR_SEGMENTS = "segments"  # 2.0.4e or later: segmented frames, what the A/C encoders send
IR_NO_SEGMENTS = "no_segments"  # IR without segmented frames (2.0.4 to 2.0.4d)
IR_UNSUPPORTED = "unsupported"  # command 32 unknown to the firmware
IR_NO_RESPONSE = "no_response"  # controller offline or MQTT unreachable

_RESPONSE_32 = re.compile(r'"response"\s*:\s*\{\s*"type"\s*:\s*\d+\s*,\s*"command"\s*:\s*32\b')

_LOGGER = logging.getLogger(__name__)


def is_ir_response(raw: str) -> bool:
    """Whether an MQTT message is a command-32 response (handled by @ref IrSender)."""
    return bool(_RESPONSE_32.search(raw))


def find_device(hass, config_entry_id: str, identifier: str):
    """Device of a config entry with identifier (DOMAIN, identifier), or None."""
    for device in dr.async_entries_for_config_entry(dr.async_get(hass), config_entry_id):
        if (DOMAIN, identifier) in device.identifiers:
            return device
    return None


def ir_output_gpio(value) -> int:
    """GPIO of a stored IR output; 0 or missing (before 1.3.0) meant PWM2."""
    gpio = int(value or 0)
    return gpio if gpio in IR_OUTPUTS else PWM2_GPIO


## @brief Serializes IR commands to one controller and waits for each response.
class IrSender:
    """Publishes command 32 to one controller and waits for its response."""

    def __init__(self, hass, device_id: str) -> None:
        self.hass = hass
        self.device_id = device_id
        self._lock = asyncio.Lock()
        self._pending: asyncio.Future | None = None
        self._unsubscribe: Callable[[], None] | None = None

    ## @brief Publishes a command-32 request and returns the raw response text.
    #  @param command_type @c WRITE to send a frame, @c READ for the IR status.
    #  @param payload Request payload.
    #  @return Response message text, or @c None after @ref ACK_TIMEOUT_S.
    async def _async_request(self, command_type: int, payload: dict) -> str | None:
        async with self._lock:
            if self._unsubscribe is None:
                self._unsubscribe = await mqtt.async_subscribe(
                    self.hass, f"+/{self.device_id}/#", self._message_received, 0
                )
            self._pending = self.hass.loop.create_future()
            message = {
                "command": COMMAND_IR_SEND,
                "type": command_type,
                "signature": self.device_id,
                "payload": payload,
            }
            await mqtt.async_publish(
                self.hass, f"CloudAge/{self.device_id}", json.dumps(message), 0, False
            )
            try:
                return await asyncio.wait_for(self._pending, ACK_TIMEOUT_S)
            except TimeoutError:
                return None
            finally:
                self._pending = None

    ## @brief Publishes one IR frame and waits for the controller response.
    #  @param payload Command-32 payload built by the encoders.
    #  @return @c True when the controller accepted the frame.
    async def async_send(self, payload: dict) -> bool:
        raw = await self._async_request(WRITE, payload)
        if raw is None:
            return False
        try:
            return bool(json.loads(raw)["response"]["status"])
        except (ValueError, KeyError, TypeError):
            return False

    ## @brief Asks the controller for its IR status to find what the firmware supports.
    #  @return One of @ref IR_SEGMENTS, @ref IR_NO_SEGMENTS, @ref IR_UNSUPPORTED, @ref IR_NO_RESPONSE.
    async def async_probe(self) -> str:
        raw = await self._async_request(READ, {})
        if raw is None:
            return IR_NO_RESPONSE
        # The firmware quotes the status object without escaping it, so the
        # reply is not valid JSON: look for the fields in the text.
        if '"last_segments"' in raw:
            return IR_SEGMENTS
        if '"initialized"' in raw:
            return IR_NO_SEGMENTS
        return IR_UNSUPPORTED

    ## @brief Resolves the pending request with a command-32 response.
    #  @param msg MQTT message received from the controller.
    async def _message_received(self, msg) -> None:
        if self._pending is None or self._pending.done():
            return
        raw = msg.payload.decode("utf-8", "replace") if isinstance(msg.payload, bytes) else str(msg.payload)
        if is_ir_response(raw):
            self._pending.set_result(raw)

    ## @brief Stops listening for controller responses.
    def async_stop(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None


## @brief One air conditioner: configuration, last confirmed state and listeners.
class AirConditioner:
    """Logical A/C state shared by the climate entity and its option switches."""

    def __init__(self, sender: IrSender, alias: str, config: dict) -> None:
        self.sender = sender
        self.device_id = sender.device_id
        self.alias = alias
        self.ac_id = config["id"]
        self.name = config["name"]
        self.brand = AcBrand(config["brand"])
        self.protocol = get_protocol(self.brand)
        self.model = self.protocol.resolve_model(config.get("model"))
        self.gpio = ir_output_gpio(config.get("ir_output"))
        self.temperature_sensor = config.get("temperature_sensor") or None
        self.capabilities = self.protocol.capabilities_for(self.model)
        self.state = AcCommandState(
            brand=self.brand,
            model=self.model,
            power=False,
            mode=AcMode.COOL,
            temperature_c=max(self.capabilities.min_temperature_c, min(23, self.capabilities.max_temperature_c)),
            fan_speed=AcFanSpeed.AUTO,
            gpio=self.gpio,
            light=True,
        )
        self._listeners: list[Callable[[], None]] = []

    @property
    def unique_prefix(self) -> str:
        return f"smartcloudage_{self.device_id}_ac_{self.ac_id}"

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, f"{self.device_id}_ac_{self.ac_id}")},
            "name": self.name,
            "manufacturer": brand_name(self.brand),
            "model": self.model,
        }

    ## @brief Shows the A/C under its controller in the device registry.
    #  @param hass Active Home Assistant instance.
    #  @param device_entry_id Registry ID of the A/C device.
    #  @param config_entry_id Config entry that owns both devices.
    def link_to_controller(self, hass, device_entry_id: str | None, config_entry_id: str | None) -> None:
        """Set via_device_id (the via_device key of DeviceInfo is deprecated)."""
        if not device_entry_id or not config_entry_id:
            return
        registry = dr.async_get(hass)
        controller = find_device(hass, config_entry_id, self.device_id)
        device = registry.async_get(device_entry_id)
        if controller is not None and device is not None and device.via_device_id != controller.id:
            registry.async_update_device(device.id, via_device_id=controller.id)

    ## @brief Registers a callback run after every confirmed or restored state.
    #  @return Callable that removes the listener.
    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    ## @brief Applies a state restored from Home Assistant without sending it.
    def restore(self, **changes) -> None:
        self.state = replace(self.state, **changes)

    ## @brief Sends a changed state; keeps the previous one when the controller refuses it.
    #  @param changes Fields of @ref AcCommandState to change.
    async def async_apply(self, **changes) -> None:
        new_state = replace(self.state, **changes)
        try:
            payload = build_payload(new_state)
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        if not await self.sender.async_send(payload):
            _LOGGER.warning(
                "Controller %s did not confirm the IR command for %s "
                "(command 32 needs firmware 2.0.4e or later)",
                self.device_id,
                self.name,
            )
            self._notify()
            raise HomeAssistantError(
                f"O controlador {self.alias} não confirmou o comando infravermelho de {self.name}."
            )
        self.state = new_state
        self._notify()

    # ------------------------------------------------------------------
    # What this brand/model actually distinguishes
    # ------------------------------------------------------------------

    def _distinct(self, field: str, candidates: list, priority: list | None = None) -> list:
        """Candidates whose frame differs from every other kept one.

        When two values send the same frame, the one earlier in ``priority``
        is kept (e.g. Cool over Auto on remotes without an auto mode). The
        result keeps the order of ``candidates``.
        """
        base = AcCommandState(self.brand, self.model, True, AcMode.COOL, 24, AcFanSpeed.AUTO)
        kept, frames = [], []
        for value in priority or candidates:
            try:
                frame = build_payload(replace(base, **{field: value}))
            except ValueError:
                continue
            if frame not in frames:
                kept.append(value)
                frames.append(frame)
        return [value for value in candidates if value in kept]

    def supported_modes(self) -> list[AcMode]:
        return self._distinct(
            "mode",
            [AcMode.AUTO, AcMode.COOL, AcMode.DRY, AcMode.FAN, AcMode.HEAT],
            [AcMode.COOL, AcMode.HEAT, AcMode.DRY, AcMode.FAN, AcMode.AUTO],
        )

    def supported_fan_speeds(self) -> list[AcFanSpeed]:
        return self._distinct(
            "fan_speed",
            [AcFanSpeed.AUTO, AcFanSpeed.QUIET, AcFanSpeed.LOW, AcFanSpeed.MEDIUM, AcFanSpeed.HIGH, AcFanSpeed.MAX],
            [AcFanSpeed.AUTO, AcFanSpeed.LOW, AcFanSpeed.MEDIUM, AcFanSpeed.HIGH, AcFanSpeed.MAX, AcFanSpeed.QUIET],
        )

    def supported_swing_vertical(self) -> list[AcSwingVertical]:
        if not self.capabilities.supports_swing_vertical:
            return []
        return self._distinct(
            "swing_vertical",
            [
                AcSwingVertical.OFF,
                AcSwingVertical.SWING,
                AcSwingVertical.POS1,
                AcSwingVertical.POS2,
                AcSwingVertical.POS3,
                AcSwingVertical.POS4,
                AcSwingVertical.POS5,
            ],
        )

    def supported_swing_horizontal(self) -> list[AcSwingHorizontal]:
        if not self.capabilities.supports_swing_horizontal:
            return []
        return self._distinct(
            "swing_horizontal",
            [
                AcSwingHorizontal.OFF,
                AcSwingHorizontal.SWING,
                AcSwingHorizontal.LEFT,
                AcSwingHorizontal.LEFT_CENTER,
                AcSwingHorizontal.CENTER,
                AcSwingHorizontal.RIGHT_CENTER,
                AcSwingHorizontal.RIGHT,
            ],
        )


## @brief Builds the air conditioners of every controller in a config entry.
#  @return Mapping from controller ID to its IR sender, and the list of A/Cs.
def build_air_conditioners(hass, devices: list[dict]) -> tuple[dict[str, IrSender], list[AirConditioner]]:
    senders: dict[str, IrSender] = {}
    units: list[AirConditioner] = []
    for device in devices:
        device_id = device.get("device_id")
        configs = device.get("air_conditioners") or []
        if not device_id or not configs:
            continue
        sender = senders.setdefault(device_id, IrSender(hass, device_id))
        alias = device.get("alias") or device_id
        for config in configs:
            try:
                units.append(AirConditioner(sender, alias, config))
            except (KeyError, ValueError) as err:
                _LOGGER.error("Invalid air conditioner configuration %s: %s", config, err)
    return senders, units
