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
    try:
        devices = entry.options.get("devices")
        if devices is None:
            devices = entry.data.get("devices", [])

    except Exception as e:
        _LOGGER.error(f"Erro ao carregar devices: {e}")
        devices = []
    entities = []
    entities_by_device = {}
    entities_by_alias = {}

    for device_conf in devices:
        device_id = device_conf.get("device_id")
        alias = device_conf.get("alias") or device_id  # Usa alias se existir
        outputs = device_conf.get("outputs", 16)
        entities_by_device.setdefault(device_id, [])
        entities_by_alias.setdefault(alias, [])
        for output_id in range(outputs):
            entity = SmartCloudOutputSwitch(
                hass=hass,
                name=f"{alias} Output {output_id + 1}",
                output_id=output_id,
                base_topic=HARDCODED_TOPIC_PREFIX,
                device_id=device_id,
                alias=alias,
            )
            entities.append(entity)
            entities_by_device[device_id].append(entity)
            entities_by_alias[alias].append(entity)
    async_add_entities(entities)

    # Exemplo: listar aliases (pode usar para log ou debug)
    _LOGGER.info(f"Aliases cadastrados: {list(entities_by_alias.keys())}")

    async def message_received(msg):
        try:
            topic_parts = msg.topic.split("/")
            if len(topic_parts) < 2:   # <-- antes era < 3
                return
            device_id = topic_parts[1]

            # se o device_id do tópico não é um cadastrado, ignora
            if device_id not in entities_by_device:
                return

            raw = msg.payload.decode("utf-8") if isinstance(msg.payload, bytes) else msg.payload
            data = json.loads(raw)

            # Desembrulha se "message" vier como JSON string (duplo-JSON)
            inner = data.get("message")
            if isinstance(inner, str):
                try:
                    inner_obj = json.loads(inner)
                    data = {**data, **inner_obj}
                except Exception:
                    pass

            output_section = data.get("Output")
            outputs = output_section.get("Outputs") if isinstance(output_section, dict) else None
            if outputs is not None:
                outputs = int(outputs)
                _LOGGER.debug("MQTT update topic=%s device=%s Outputs=%s", msg.topic, device_id, outputs)
                for i, ent in enumerate(entities_by_device[device_id]):
                    ent._state = bool((outputs >> i) & 1)
                    ent.async_write_ha_state()
        except Exception as e:
            _LOGGER.error(f"Erro processando mensagem MQTT: {e}")

    for device_id in entities_by_device.keys():
        # cobre qualquer product_name: +/<device_id>/OutTopic/...
        await mqtt.async_subscribe(hass, f"+/{device_id}/OutTopic/#", message_received, 0)
        # opcional (se algum status sair fora de OutTopic):
        await mqtt.async_subscribe(hass, f"+/{device_id}/#",            message_received, 0)




class SmartCloudOutputSwitch(SwitchEntity):
    def __init__(self, hass, name, output_id, base_topic, device_id, alias=None):
        self.hass = hass
        self._attr_name = name
        self._state = False
        self._output_id = output_id
        self._device_id = device_id
        self._alias = alias or device_id
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
        # Publica para topic: CloudAge/<device_id>
        topic = f"{self._base_topic}{self._device_id}"
        payload = {
            "command": 11,
            "type": 1,
            "signature": self._device_id,
            "payload": {
                "id": self._output_id + 1,
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
        # Use alias no unique_id para fácil identificação
        return f"smartcloudage_output_{self._alias}_{self._output_id + 1}"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": f"SmartCloudAge {self._alias}",
            "manufacturer": "SmartCloudAge",
            "model": "MQTT Controller"
        }