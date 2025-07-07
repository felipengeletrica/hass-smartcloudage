import logging
import json
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.components import mqtt

_LOGGER = logging.getLogger(__name__)

DOMAIN = "smartcloudage"
DEFAULT_NAME = "SmartCloudAge Output"
HARDCODED_TOPIC_PREFIX = "CloudAge/"

async def async_setup_entry(hass, entry, async_add_entities):
    # Carrega devices do campo JSON
    try:
        devices = json.loads(entry.data.get("devices_json", "[]"))
    except Exception as e:
        _LOGGER.error(f"Erro ao carregar devices_json: {e}")
        devices = []
    entities = []
    entities_by_device = {}

    for device_conf in devices:
        device_id = device_conf.get("device_id")
        outputs = device_conf.get("outputs", 16)
        entities_by_device.setdefault(device_id, [])
        for output_id in range(outputs):
            entity = SmartCloudOutputSwitch(
                hass=hass,
                name=f"{DEFAULT_NAME} {device_id} Output {output_id+1}",
                output_id=output_id,
                base_topic=HARDCODED_TOPIC_PREFIX,
                device_id=device_id,
            )
            entities.append(entity)
            entities_by_device[device_id].append(entity)
    async_add_entities(entities)

    # Assina todos os tópicos de status
    async def message_received(msg):
        try:
            # Descobre o device_id pelo tópico recebido!
            # Exemplo: CloudAge/MTQ0Y...SA6=/OutTopic/
            topic_parts = msg.topic.split("/")
            if len(topic_parts) < 3:
                return
            device_id = topic_parts[1]
            data = json.loads(msg.payload)
            if data.get("message") == "OUTPUT_STATUS":
                outputs = data.get("Output", {}).get("Outputs")
                if outputs is not None and device_id in entities_by_device:
                    for i, ent in enumerate(entities_by_device[device_id]):
                        ent._state = bool((outputs >> i) & 1)
                        ent.async_write_ha_state()
        except Exception as e:
            _LOGGER.error(f"Erro processando mensagem MQTT: {e}")

    await mqtt.async_subscribe(hass, "CloudAge/+/OutTopic/", message_received, 0)

class SmartCloudOutputSwitch(SwitchEntity):
    def __init__(self, hass, name, output_id, base_topic, device_id):
        self.hass = hass
        self._attr_name = name
        self._state = False
        self._output_id = output_id
        self._device_id = device_id
        self._base_topic = base_topic
        self._attr_entity_category = EntityCategory.CONFIG

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
        # Ajuste para publicar no tópico correto de comando (verifique se precisa OutTopic ou outro)
        topic = f"{self._base_topic}{self._device_id}/InTopic/"
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
            json.dumps(payload),
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
