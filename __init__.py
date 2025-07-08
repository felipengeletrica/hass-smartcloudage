from datetime import datetime, timedelta
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.components import mqtt
import json
import logging

DOMAIN = "smartcloudage"
SYNC_RTC_INTERVAL = 1
_LOGGER = logging.getLogger(__name__)

def build_datetime_payload(device_id, signature=None):
    now = datetime.now()
    if not signature:
        signature = device_id
    return {
        "command": 9,  # CONFIG_DATE_TIME_ENUM
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
        "type": 1,  # WRITE
        "signature": signature,
    }

async def async_setup_entry(hass, entry):
    await hass.config_entries.async_forward_entry_setups(entry, ["switch"])

    devices = entry.options.get("devices")
    if devices is None:
        devices = entry.data.get("devices", [])


    async def send_datetime_to_devices(_now):
        _LOGGER.warning(f"Enviando data/hora para dispositivos: {[d['device_id'] for d in devices]}")
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

    # Registra o scheduler para rodar a cada minuto
    async_track_time_interval(
        hass, send_datetime_to_devices, timedelta(minutes=SYNC_RTC_INTERVAL)
    )

    # Opcional: dispara uma vez na inicialização (para testar)
    hass.async_create_task(send_datetime_to_devices(None))

    return True
