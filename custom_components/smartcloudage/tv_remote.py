"""TVs driven by a controller's IR emitter (beta).

Same path as the air conditioners: one command-32 frame per key, through the
controller's @ref IrSender. IR is one-way: the controller only confirms it
accepted the frame, not that the TV received it, so the power state is an
assumption (the last power key sent).
"""

from __future__ import annotations

import logging

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from .air_conditioner import DOMAIN, IrSender, find_device, ir_output_gpio
from .tv_codes import PRESETS, TvPreset, encode_key, preset_keys

_LOGGER = logging.getLogger(__name__)

DEFAULT_PRESET = "samsung-tv"


def tv_preset(preset_id: str | None) -> TvPreset:
    """Built-in remote by id (the default one when the id is unknown)."""
    return PRESETS.get(preset_id or "", PRESETS[DEFAULT_PRESET])


## @brief One TV: configuration, IR sender and the RC-5/RC-6 toggle bit.
class TvRemote:
    """A TV controlled through a SmartCloudAge IR emitter."""

    def __init__(self, sender: IrSender, alias: str, config: dict) -> None:
        self.sender = sender
        self.device_id = sender.device_id
        self.alias = alias
        self.tv_id = config["id"]
        self.name = config["name"]
        self.preset = tv_preset(config.get("preset"))
        self.gpio = ir_output_gpio(config.get("ir_output"))
        ## RC-5/RC-6 flip this bit on every press, so the TV sees a new key
        ## instead of a held one.
        self._toggle = False

    @property
    def unique_prefix(self) -> str:
        return f"smartcloudage_{self.device_id}_tv_{self.tv_id}"

    @property
    def keys(self) -> list[str]:
        """Keys the chosen remote has, in display order."""
        return preset_keys(self.preset)

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, f"{self.device_id}_tv_{self.tv_id}")},
            "name": self.name,
            "manufacturer": self.preset.brand,
            "model": self.preset.name,
        }

    ## @brief Shows the TV under its controller in the device registry.
    def link_to_controller(self, hass, device_entry_id: str | None, config_entry_id: str | None) -> None:
        if not device_entry_id or not config_entry_id:
            return
        registry = dr.async_get(hass)
        controller = find_device(hass, config_entry_id, self.device_id)
        device = registry.async_get(device_entry_id)
        if controller is not None and device is not None and device.via_device_id != controller.id:
            registry.async_update_device(device.id, via_device_id=controller.id)

    ## @brief Sends one key.
    #  @param key Key name (see tv_codes.KEYS).
    #  @throws HomeAssistantError when the remote has no such key or the controller does not confirm.
    async def async_press(self, key: str) -> None:
        if key not in self.preset.keys:
            raise HomeAssistantError(
                f"O controle {self.preset.name} não tem a tecla '{key}'. "
                f"Teclas disponíveis: {', '.join(self.keys)}."
            )
        self._toggle = not self._toggle
        payload = encode_key(self.preset, key, self._toggle, self.gpio)
        if not await self.sender.async_send(payload):
            _LOGGER.warning(
                "Controller %s did not confirm the IR key %s for %s", self.device_id, key, self.name
            )
            raise HomeAssistantError(
                f"O controlador {self.alias} não confirmou a tecla {key} da TV {self.name}."
            )


## @brief Builds the TVs of every controller, sharing the IR senders of the A/Cs.
#  @param senders Controller ID to IR sender; new senders are added to it.
def build_tv_remotes(hass, devices: list[dict], senders: dict[str, IrSender]) -> list[TvRemote]:
    remotes: list[TvRemote] = []
    for device in devices:
        device_id = device.get("device_id")
        configs = device.get("tv_remotes") or []
        if not device_id or not configs:
            continue
        sender = senders.setdefault(device_id, IrSender(hass, device_id))
        alias = device.get("alias") or device_id
        for config in configs:
            try:
                remotes.append(TvRemote(sender, alias, config))
            except (KeyError, ValueError) as err:
                _LOGGER.error("Invalid TV remote configuration %s: %s", config, err)
    return remotes
