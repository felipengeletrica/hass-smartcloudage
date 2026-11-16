"""TV remotes (beta): send any key with remote.send_command."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
import logging
from typing import Any

from homeassistant.components.remote import (
    ATTR_DELAY_SECS,
    ATTR_NUM_REPEATS,
    DEFAULT_DELAY_SECS,
    RemoteEntity,
)
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.restore_state import RestoreEntity

from .air_conditioner import DOMAIN
from .tv_remote import TvRemote

_LOGGER = logging.getLogger(__name__)

## Spellings accepted in send_command besides the key names ("0" = digit_0 ...).
_ALIASES = {
    **{str(i): f"digit_{i}" for i in range(10)},
    "on": "power_on",
    "off": "power_off",
    "source": "input",
    "enter": "ok",
    "select": "ok",
    "vol_up": "volume_up",
    "vol_down": "volume_down",
    "ch_up": "channel_up",
    "ch_down": "channel_down",
}


def normalize_key(command: str) -> str:
    """Key name of a send_command entry: case, spaces and dashes ignored."""
    key = command.strip().lower().replace(" ", "_").replace("-", "_")
    return _ALIASES.get(key, key)


## @brief Creates one remote entity per configured TV.
async def async_setup_entry(hass, entry, async_add_entities):
    """Create the TV remote entities."""
    remotes = hass.data[DOMAIN][entry.entry_id].get("tv_remotes", [])
    async_add_entities(SmartCloudAgeTvRemote(tv) for tv in remotes)


## @brief Remote entity of a TV: any key by name, power on/off.
class SmartCloudAgeTvRemote(RemoteEntity, RestoreEntity):
    """A TV remote driven by a SmartCloudAge IR emitter."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "tv_remote"
    _attr_assumed_state = True
    _attr_should_poll = False

    def __init__(self, tv: TvRemote) -> None:
        self.tv = tv
        self._attr_unique_id = f"{tv.unique_prefix}_remote"
        self._attr_device_info = tv.device_info
        self._attr_is_on = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "controller": self.tv.device_id,
            "remote_model": self.tv.preset.id,
            "keys": self.tv.keys,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        entry = self.registry_entry
        self.tv.link_to_controller(
            self.hass, entry.device_id if entry else None, entry.config_entry_id if entry else None
        )
        # Last power state sent before the restart; nothing is transmitted.
        if (last := await self.async_get_last_state()) is not None and last.state in (STATE_ON, STATE_OFF):
            self._attr_is_on = last.state == STATE_ON

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.tv.async_press("power_on" if "power_on" in self.tv.keys else "power")
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.tv.async_press("power_off" if "power_off" in self.tv.keys else "power")
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        """Send keys by name, e.g. ["volume_up", "volume_up"] or ["1", "2", "ok"]."""
        commands = list(command)
        keys = [normalize_key(c) for c in commands]
        unknown = [c for c, k in zip(commands, keys) if k not in self.tv.keys]
        if unknown:
            raise HomeAssistantError(
                f"Teclas desconhecidas para {self.tv.preset.name}: {', '.join(unknown)}. "
                f"Disponíveis: {', '.join(self.tv.keys)}."
            )
        repeats = max(1, int(kwargs.get(ATTR_NUM_REPEATS, 1)))
        delay = float(kwargs.get(ATTR_DELAY_SECS, DEFAULT_DELAY_SECS))
        first = True
        for _ in range(repeats):
            for key in keys:
                if not first and delay > 0:
                    await asyncio.sleep(delay)
                first = False
                await self.tv.async_press(key)
                if key == "power_on":
                    self._attr_is_on = True
                elif key == "power_off":
                    self._attr_is_on = False
        self.async_write_ha_state()
