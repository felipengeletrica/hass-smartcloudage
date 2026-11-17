"""TV remote keys as buttons (beta).

One button per key of the chosen remote. The everyday keys (the simple
remote) are enabled; digits and media keys come disabled and can be enabled
in the device page when needed (the full remote).
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory

from .air_conditioner import DOMAIN
from .tv_codes import ESSENTIAL_KEYS
from .tv_remote import TvRemote
from .window import Window

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


## @brief Creates the key buttons of every configured TV and the window calibration buttons.
async def async_setup_entry(hass, entry, async_add_entities):
    """Create the TV key buttons and the window calibration buttons."""
    data = hass.data[DOMAIN][entry.entry_id]
    remotes = data.get("tv_remotes", [])
    async_add_entities(TvKeyButton(tv, key) for tv in remotes for key in tv.keys)
    windows = data.get("windows", [])
    async_add_entities(
        WindowCalibrationButton(window, mark) for window in windows for mark in (False, True)
    )


## @brief Starts the travel-time calibration of a window, or marks its end of travel.
class WindowCalibrationButton(ButtonEntity):
    """Calibration of a window: start, and "end of travel" while it opens."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, window: Window, mark: bool) -> None:
        self.window = window
        self.mark = mark
        key = "window_calibrate_mark" if mark else "window_calibrate"
        self._attr_unique_id = f"{window.unique_prefix}_{key}"
        self._attr_translation_key = key
        self._attr_icon = "mdi:flag-checkered" if mark else "mdi:timer-cog-outline"
        self._attr_device_info = window.device_info

    async def async_press(self) -> None:
        cover = self.window.cover
        if cover is None:
            raise HomeAssistantError(f"A janela {self.window.name} ainda não está pronta.")
        if self.mark:
            await cover.async_calibrate_mark()
        else:
            await cover.async_calibrate()


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
