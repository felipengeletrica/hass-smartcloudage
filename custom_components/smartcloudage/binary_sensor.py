"""Digital input binary sensors for SmartCloudAge controllers (beta)."""

from __future__ import annotations

import json
import logging

from homeassistant.components import mqtt
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.helpers.restore_state import RestoreEntity

from .air_conditioner import is_ir_response
from .inputs import channel_active, decode_payload, input_mask

DOMAIN = "smartcloudage"
_LOGGER = logging.getLogger(__name__)


## @brief Creates one binary sensor per input selected in the options flow.
#  @param hass Active Home Assistant instance.
#  @param entry SmartCloudAge configuration entry.
#  @param async_add_entities Callback used to register entities.
async def async_setup_entry(hass, entry, async_add_entities):
    """Create the configured input entities."""
    devices = entry.options.get("devices", entry.data.get("devices", []))
    entities_by_device: dict[str, list[SmartCloudAgeInputSensor]] = {}

    for device in devices:
        device_id = device.get("device_id")
        if not device_id:
            continue
        alias = device.get("alias") or device_id
        inputs = device.get("inputs") or {}
        invert = bool(inputs.get("invert", False))
        for channel in sorted({int(c) for c in inputs.get("channels", [])}):
            entities_by_device.setdefault(device_id, []).append(
                SmartCloudAgeInputSensor(device_id, alias, channel, invert)
            )

    if not entities_by_device:
        return
    async_add_entities(
        [entity for entities in entities_by_device.values() for entity in entities]
    )

    ## @brief Applies an INPUT_STATUS mask to the device's input entities.
    #  @param msg MQTT message published by the controller.
    async def message_received(msg):
        try:
            raw = msg.payload.decode("utf-8") if isinstance(msg.payload, bytes) else msg.payload
            if is_ir_response(raw):
                return
            data = decode_payload(raw)
            mask = input_mask(data)
            if mask is None:
                return
            device_id = msg.topic.split("/")[1]
            for entity in entities_by_device.get(device_id, []):
                entity.update_mask(mask)
        except (IndexError, TypeError, ValueError, json.JSONDecodeError) as err:
            _LOGGER.warning("Invalid SmartCloudAge input payload on %s: %s", msg.topic, err)

    for device_id in entities_by_device:
        unsubscribe = await mqtt.async_subscribe(
            hass,
            f"CloudAge/{device_id}/OutTopic/#",
            message_received,
            0,
        )
        entry.async_on_unload(unsubscribe)


## @brief One controller digital input exposed as a binary sensor.
class SmartCloudAgeInputSensor(BinarySensorEntity, RestoreEntity):
    """State of one controller input, restored across HA restarts."""

    _attr_should_poll = False

    def __init__(self, device_id: str, alias: str, channel: int, invert: bool) -> None:
        ## @brief Initializes the input identity.
        #  @param device_id Unique controller identifier.
        #  @param alias Human-readable controller name.
        #  @param channel Input number, 1 to 16.
        #  @param invert Swap the meaning of the input bit.
        self._device_id = device_id
        self._alias = alias
        self._channel = channel
        self._invert = invert
        self._attr_name = f"{alias} Entrada {channel}"
        self._attr_unique_id = f"smartcloudage_{device_id}_input_{channel}"
        self._attr_is_on = None

    async def async_added_to_hass(self) -> None:
        """Restore the last state: the firmware only publishes on change."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in (STATE_ON, STATE_OFF):
            self._attr_is_on = last.state == STATE_ON

    @property
    def extra_state_attributes(self):
        """Expose the channel and polarity."""
        return {"channel": self._channel, "inverted": self._invert}

    @property
    def device_info(self):
        """Link the input to its controller."""
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": f"SmartCloudAge {self._alias}",
            "manufacturer": "SmartCloudAge",
            "model": "MQTT Controller",
        }

    ## @brief Updates the state from an INPUT_STATUS mask.
    #  @param mask Input mask from the controller.
    def update_mask(self, mask: int) -> None:
        """Apply a new input mask."""
        is_on = channel_active(mask, self._channel, self._invert)
        if is_on == self._attr_is_on:
            return
        self._attr_is_on = is_on
        self.async_write_ha_state()
