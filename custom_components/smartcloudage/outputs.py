# outputs.py

import logging
import voluptuous as vol

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import CONF_NAME
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers import config_validation as cv
from homeassistant.components import mqtt

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "SmartCloudAge Output"
DOMAIN = "smartcloudage"

CONF_DEVICES = "devices"
CONF_DEVICE_ID = "device_id"
CONF_OUTPUTS = "outputs"

HARDCODED_TOPIC_PREFIX = "CloudAge/"

DEVICE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEVICE_ID): cv.string,
        vol.Optional(CONF_OUTPUTS, default=16): vol.All(int, vol.Range(min=1, max=16)),
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_DEVICES): vol.All(cv.ensure_list, [DEVICE_SCHEMA])
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

async def async_setup(hass, config):
    conf = config[DOMAIN][CONF_DEVICES]
    devices = []

    for device_conf in conf:
        device_id = device_conf[CONF_DEVICE_ID]
        outputs = device_conf[CONF_OUTPUTS]

        for output_id in range(outputs):
            devices.append(
                SmartCloudOutputSwitch(
                    name=f"{DEFAULT_NAME} {device_id} Output {output_id}",
                    output_id=output_id,
                    base_topic=HARDCODED_TOPIC_PREFIX,
                    device_id=device_id,
                )
            )

    async_add_entities = hass.helpers.entity_platform.async_add_entities
    async_add_entities(devices, update_before_add=False)

    return True

class SmartCloudOutputSwitch(SwitchEntity):
    def __init__(self, name, output_id, base_topic, device_id):
        self._name = name
        self._state = False
        self._output_id = output_id
        self._device_id = device_id
        self._base_topic = base_topic
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def name(self):
        return self._name

    @property
    def is_on(self):
        return self._state

    async def async_turn_on(self, **kwargs):
        await self._publish_mqtt(1)
        self._state = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        await self._publish_mqtt(0)
        self._state = False
        self.async_write_ha_state()

    async def _publish_mqtt(self, value):
        topic = f"{self._base_topic}{self._device_id}"
        payload = {
            "command": 11,
            "type": 1,
            "signature": self._device_id,
            "payload": {
                "id": self._output_id,
                "value": value
            }
        }

        _LOGGER.debug("Publishing to %s: %s", topic, payload)

        await mqtt.async_publish(
            self.hass,
            topic,
            self.hass.helpers.json.dumps(payload),
            0,
            False
        )

    @property
    def unique_id(self):
        return f"smartcloudage_output_{self._device_id}_{self._output_id}"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": f"SmartCloudAge Device {self._device_id}",
            "manufacturer": "SmartCloudAge",
            "model": "MQTT Controller"
        }
