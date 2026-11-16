"""TV remote keys as buttons (beta).

One button per key of the chosen remote. The everyday keys (the simple
remote) are enabled; digits and media keys come disabled and can be enabled
in the device page when needed (the full remote).
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity

from .air_conditioner import DOMAIN
from .tv_codes import ESSENTIAL_KEYS
from .tv_remote import TvRemote

## Material Design icon of each key.
KEY_ICONS = {
    "power": "mdi:power",
    "power_on": "mdi:power-on",
    "power_off": "mdi:power-off",
    "input": "mdi:import",
    "mute": "mdi:volume-mute",
    "volume_up": "mdi:volume-plus",
    "volume_down": "mdi:volume-minus",
    "channel_up": "mdi:arrow-up-bold-box-outline",
    "channel_down": "mdi:arrow-down-bold-box-outline",
    "up": "mdi:chevron-up",
    "down": "mdi:chevron-down",
    "left": "mdi:chevron-left",
    "right": "mdi:chevron-right",
    "ok": "mdi:checkbox-blank-circle-outline",
    "menu": "mdi:menu",
    "home": "mdi:home",
    "back": "mdi:arrow-u-left-top",
    "exit": "mdi:exit-to-app",
    "info": "mdi:information-outline",
    "play": "mdi:play",
    "pause": "mdi:pause",
    "stop": "mdi:stop",
    "fast_forward": "mdi:fast-forward",
    "rewind": "mdi:rewind",
    **{f"digit_{i}": f"mdi:numeric-{i}" for i in range(10)},
}


## @brief Creates the key buttons of every configured TV.
async def async_setup_entry(hass, entry, async_add_entities):
    """Create the TV key buttons."""
    remotes = hass.data[DOMAIN][entry.entry_id].get("tv_remotes", [])
    async_add_entities(TvKeyButton(tv, key) for tv in remotes for key in tv.keys)


## @brief One key of a TV remote.
class TvKeyButton(ButtonEntity):
    """Presses one key of a TV through the IR emitter."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, tv: TvRemote, key: str) -> None:
        self.tv = tv
        self.key = key
        self._attr_unique_id = f"{tv.unique_prefix}_key_{key}"
        self._attr_device_info = tv.device_info
        self._attr_translation_key = f"key_{key}"
        self._attr_icon = KEY_ICONS.get(key)
        self._attr_entity_registry_enabled_default = key in ESSENTIAL_KEYS

    async def async_press(self) -> None:
        await self.tv.async_press(self.key)
