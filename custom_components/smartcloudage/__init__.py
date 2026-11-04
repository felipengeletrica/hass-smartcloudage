"""SmartCloudAge integration."""

from datetime import datetime, timedelta
import json
import logging

from homeassistant.components import mqtt
from homeassistant.helpers.event import async_track_time_interval

from .air_conditioner import build_air_conditioners

DOMAIN = "smartcloudage"
PLATFORMS = ["switch", "sensor", "climate"]
SYNC_RTC_INTERVAL = 5
CONFIG_DATE_TIME_ENUM = 9
WRITE = 1

_LOGGER = logging.getLogger(__name__)


## @brief Builds the command used to synchronize a controller's real-time clock.
#  @param device_id Unique identifier of the target SmartCloudAge controller.
#  @param signature Optional command signature; defaults to @p device_id.
#  @return Dictionary containing the command, current local date/time and signature.
def build_datetime_payload(device_id, signature=None):
    """Build the controller RTC synchronization command."""
    now = datetime.now()
    if not signature:
        signature = device_id
    return {
        "command": CONFIG_DATE_TIME_ENUM,
        "payload": {
            "datetime": {
                "day": now.day,
                "mon": now.month,
                "year": now.year,
                "hour": now.hour,
                "min": now.minute,
                "sec": now.second,
            }
        },
        "type": WRITE,
        "signature": signature,
    }


## @brief Sets up a SmartCloudAge configuration entry.
#  @param hass Active Home Assistant instance.
#  @param entry SmartCloudAge configuration entry being loaded.
#  @return @c True after platforms and periodic RTC synchronization are registered.
async def async_setup_entry(hass, entry):
    """Set up SmartCloudAge from a config entry."""
    devices = entry.options.get("devices", entry.data.get("devices", []))
    senders, air_conditioners = build_air_conditioners(hass, devices)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "ir_senders": senders,
        "air_conditioners": air_conditioners,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    ## @brief Publishes the current date and time to every configured controller.
    #  @param _now Timestamp supplied by Home Assistant's interval tracker.
    async def send_datetime_to_devices(_now):
        for device in devices:
            device_id = device.get("device_id")
            if not device_id:
                continue
            signature = device.get("signature", device_id)
            await mqtt.async_publish(
                hass,
                f"CloudAge/{device_id}",
                json.dumps(build_datetime_payload(device_id, signature)),
                0,
                False,
            )

    entry.async_on_unload(
        async_track_time_interval(
            hass,
            send_datetime_to_devices,
            timedelta(minutes=SYNC_RTC_INTERVAL),
        )
    )
    hass.async_create_task(send_datetime_to_devices(None))
    return True


## @brief Unloads all platforms associated with a configuration entry.
#  @param hass Active Home Assistant instance.
#  @param entry SmartCloudAge configuration entry being unloaded.
#  @return Whether every forwarded platform was successfully unloaded.
async def async_unload_entry(hass, entry):
    """Unload a SmartCloudAge config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, {})
        for sender in data.get("ir_senders", {}).values():
            sender.async_stop()
    return unloaded


## @brief Reloads a configuration entry after its options change.
#  @param hass Active Home Assistant instance.
#  @param entry Updated SmartCloudAge configuration entry.
async def _async_reload_entry(hass, entry):
    """Reload entities after an options change."""
    await hass.config_entries.async_reload(entry.entry_id)
