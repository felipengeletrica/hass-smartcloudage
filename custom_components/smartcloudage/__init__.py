from datetime import datetime, timedelta
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.components import mqtt
import json
import logging

DOMAIN = "smartcloudage"
SYNC_RTC_INTERVAL = 5
CONFIG_DATE_TIME_ENUM = 9
WRITE = 1
_LOGGER = logging.getLogger(__name__)

def build_datetime_payload(device_id, signature=None):
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

async def async_setup_entry(hass, entry):
    await hass.config_entries.async_forward_entry_setups(entry, ["switch"])

    devices = entry.options.get("devices")
    if devices is None:
        devices = entry.data.get("devices", [])


    async def send_datetime_to_devices(_now):
        for device in devices:
            device_id = device.get("device_id")
            signature = device.get("signature", device_id)
            payload = build_datetime_payload(device_id, signature)
            topic = f"CloudAge/{device_id}"

            await mqtt.async_publish(
            hass,
            topic,
            json.dumps(payload),
            0,
            False
        )

    # Register schedule
    async_track_time_interval(
        hass, send_datetime_to_devices, timedelta(minutes=SYNC_RTC_INTERVAL)
    )

    # First send trigger
    hass.async_create_task(send_datetime_to_devices(None))

    return True
