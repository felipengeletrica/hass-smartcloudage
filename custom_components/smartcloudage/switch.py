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
    # --- Carrega devices do config entry ---
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

    # --- Cria entidades por device/alias/saídas ---
    for device_conf in devices:
        device_id = device_conf.get("device_id")
        if not device_id:
            _LOGGER.warning("Device sem device_id ignorado.")
            continue

        alias = device_conf.get("alias") or device_id
        outputs = int(device_conf.get("outputs", 16))

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

    _LOGGER.info(f"Aliases cadastrados: {list(entities_by_alias.keys())}")

    # --- Função auxiliar para aplicar bitmask de saídas ---
    def _apply_outputs(device_id: str, outputs_value: int):
        if device_id not in entities_by_device:
            return
        for i, ent in enumerate(entities_by_device[device_id]):
            try:
                ent._state = bool((outputs_value >> i) & 1)
                ent.async_write_ha_state()
            except Exception as e:
                _LOGGER.error(f"Falha ao aplicar bit {i} para {device_id}: {e}")

    # --- Callback MQTT unificado ---
    async def message_received(msg):
        try:
            # Tópicos esperados: CloudAge/<device_id>/OutTopic[...]
            topic_parts = msg.topic.split("/")
            if len(topic_parts) < 3:
                return
            device_id = topic_parts[1]

            raw = msg.payload
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="ignore")

            # 1) Decodifica payload primário
            data = json.loads(raw)

            # 2) Se "message" vier como string JSON (duplo-JSON), tenta desembrulhar e mesclar
            inner = data.get("message")
            if isinstance(inner, str):
                try:
                    inner_obj = json.loads(inner)
                    # Mescla, priorizando campos internos (OUTPUT_STATUS etc.)
                    data = {**data, **inner_obj}
                except Exception:
                    # Pode ser só um literal como "KeepAlive" => ok ignorar
                    pass

            # 3) Atualiza sempre que houver seção Output/Outputs
            outputs = None
            output_section = data.get("Output")
            if isinstance(output_section, dict):
                outputs = output_section.get("Outputs")

            if outputs is not None:
                try:
                    outputs_int = int(outputs)
                    _LOGGER.debug(
                        "Atualizando %s de topic=%s com Outputs=%s",
                        device_id, msg.topic, outputs_int
                    )
                    _apply_outputs(device_id, outputs_int)
                except Exception as e:
                    _LOGGER.error(f"Outputs inválido ({outputs}) para {device_id}: {e}")
            else:
                # Log útil para depurar KeepAlive sem snapshot
                msg_type = data.get("message")
                _LOGGER.debug(
                    "Mensagem sem Outputs para %s (type=%s, topic=%s)",
                    device_id, msg_type, msg.topic
                )

        except Exception as e:
            _LOGGER.error(f"Erro processando mensagem MQTT (topic={msg.topic}): {e}")

    # --- Assinaturas por device (sem barra, com barra e com curinga) ---
    for device_id in entities_by_device.keys():
        base = f"{HARDCODED_TOPIC_PREFIX}{device_id}/OutTopic"
        try:
            # Sem barra final
            await mqtt.async_subscribe(hass, base, message_received, 0)
            # Com barra final
            await mqtt.async_subscribe(hass, base + "/", message_received, 0)
            # Com curinga para subníveis (se existirem)
            await mqtt.async_subscribe(hass, base + "/#", message_received, 0)
            _LOGGER.info(f"Assinando: {base}, {base + '/'}, {base + '/#'}")
        except Exception as e:
            _LOGGER.error(f"Falha ao assinar tópicos para {device_id}: {e}")


class SmartCloudOutputSwitch(SwitchEntity):
    _attr_should_poll = False  # MQTT é push

    def __init__(self, hass, name, output_id, base_topic, device_id, alias=None):
        self.hass = hass
        self._attr_name = name
        self._state = False
        self._output_id = int(output_id)
        self._device_id = device_id
        self._alias = alias or device_id
        self._base_topic = base_topic
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self):
        return self._state

    async def async_turn_on(self, **kwargs):
        await self._publish_mqtt(1)
        # Opcional: otimismo. O estado “real” será corrigido pelo próximo status.
        self._state = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        await self._publish_mqtt(0)
        self._state = False
        self.async_write_ha_state()

    async def _publish_mqtt(self, value):
        # Publica para: CloudAge/<device_id>
        topic = f"{self._base_topic}{self._device_id}"
        payload = {
            "command": 11,         # OUTPUT_ENUM
            "type": 1,
            "signature": self._device_id,
            "payload": {
                "id": self._output_id + 1,  # 1-based no protocolo
                "value": int(value)
            }
        }
        _LOGGER.debug("Publishing to %s: %s", topic, payload)
        await mqtt.async_publish(
            self.hass,
            topic,
            json.dumps(payload),
            qos=0,
            retain=False
        )

    @property
    def unique_id(self):
        return f"smartcloudage_output_{self._alias}_{self._output_id + 1}"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": f"SmartCloudAge {self._alias}",
            "manufacturer": "SmartCloudAge",
            "model": "MQTT Controller"
        }
