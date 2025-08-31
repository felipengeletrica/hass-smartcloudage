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
    """Set up SmartCloudAge switches from a config entry."""
    # ---------- Load devices ----------
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

    # ---------- Create entities ----------
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
            ent = SmartCloudOutputSwitch(
                hass=hass,
                name=f"{alias} Output {output_id + 1}",
                output_id=output_id,
                base_topic=HARDCODED_TOPIC_PREFIX,
                device_id=device_id,
                alias=alias,
            )
            entities.append(ent)
            entities_by_device[device_id].append(ent)
            entities_by_alias[alias].append(ent)

    async_add_entities(entities)

    _LOGGER.info(f"Aliases cadastrados: {list(entities_by_alias.keys())}")

    # ---------- Helpers ----------
    def _apply_outputs(device_id: str, outputs_value: int):
        """Apply bitmask to all entities of a device."""
        if device_id not in entities_by_device:
            return
        for i, ent in enumerate(entities_by_device[device_id]):
            try:
                ent._state = bool((outputs_value >> i) & 1)
                ent.async_write_ha_state()
            except Exception as e:
                _LOGGER.error(f"Falha ao aplicar bit {i} para {device_id}: {e}")

    def _merge_inner_message_if_json(data: dict) -> dict:
        """
        If data['message'] is a JSON-encoded string (double-JSON), decode and merge.
        Inner keys (e.g., 'Output', 'Input', 'message') override outer.
        """
        inner = data.get("message")
        if isinstance(inner, str):
            try:
                inner_obj = json.loads(inner)
                # merge giving priority to inner object
                data = {**data, **inner_obj}
            except Exception:
                # inner was a plain string (e.g., "KeepAlive"); ignore
                pass
        return data

    # ---------- Unified MQTT callback ----------
    async def message_received(msg):
        try:
            topic_parts = msg.topic.split("/")
            if len(topic_parts) < 2:
                return

            # Expected: CloudAge/<device_id>/OutTopic[...]   (we also accept other variants)
            device_id = topic_parts[1]

            raw = msg.payload
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="ignore")

            data = json.loads(raw)
            data = _merge_inner_message_if_json(data)

            # Update whenever Output/Outputs exists (independent of 'message' type)
            outputs = None
            output_section = data.get("Output")
            if isinstance(output_section, dict):
                outputs = output_section.get("Outputs")

            if outputs is not None:
                try:
                    outputs_int = int(outputs)
                    _LOGGER.debug(
                        "Atualizando %s (topic=%s) com Outputs=%s",
                        device_id, msg.topic, outputs_int
                    )
                    _apply_outputs(device_id, outputs_int)
                except Exception as e:
                    _LOGGER.error(f"Outputs inválido ({outputs}) para {device_id}: {e}")
            else:
                # Useful for KeepAlive without snapshot, or other messages
                _LOGGER.debug(
                    "Sem 'Output/Outputs' para %s (message=%s, topic=%s)",
                    device_id, data.get("message"), msg.topic
                )

        except Exception as e:
            _LOGGER.error(f"Erro processando mensagem MQTT (topic={msg.topic}): {e}")

    # ---------- Subscriptions ----------
    for device_id in entities_by_device.keys():
        # Status topics commonly used by device firmware
        out_base = f"{HARDCODED_TOPIC_PREFIX}{device_id}/OutTopic"

        # Subscribe to all common variants:
        to_subscribe = [
            out_base,            # no trailing slash
            f"{out_base}/",      # trailing slash
            f"{out_base}/#",     # subtopics
            # Optional catch-all: if firmware publishes elsewhere, we'll still catch it.
            f"{HARDCODED_TOPIC_PREFIX}{device_id}/#",
        ]

        for t in to_subscribe:
            try:
                await mqtt.async_subscribe(hass, t, message_received, qos=0)
                _LOGGER.info(f"Assinado: {t}")
            except Exception as e:
                _LOGGER.error(f"Falha ao assinar {t} para {device_id}: {e}")


class SmartCloudOutputSwitch(SwitchEntity):
    """SmartCloudAge output as a HA switch controlled via MQTT."""
    _attr_should_poll = False  # MQTT push updates; no polling

    def __init__(self, hass, name, output_id, base_topic, device_id, alias=None):
        self.hass = hass
        self._attr_name = name
        self._state = False
        self._output_id = int(output_id)       # 0-based index
        self._device_id = device_id
        self._alias = alias or device_id
        self._base_topic = base_topic
        self._attr_entity_category = EntityCategory.CONFIG

    # --------- SwitchEntity API ---------
    @property
    def is_on(self):
        return self._state

    async def async_turn_on(self, **kwargs):
        await self._publish_mqtt(1)
        # optimistic: real state will be corrected by next status
        self._state = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        await self._publish_mqtt(0)
        self._state = False
        self.async_write_ha_state()

    # --------- MQTT command publisher ---------
    async def _publish_mqtt(self, value: int):
        """
        Publish command to device control topic:
          CloudAge/<device_id>
        Protocol (from your spec):
          command = 11 (OUTPUT_ENUM)
          payload.id = 1-based output index
          payload.value = 0/1
        """
        topic = f"{self._base_topic}{self._device_id}"
        payload = {
            "command": 11,  # OUTPUT_ENUM
            "type": 1,
            "signature": self._device_id,
            "payload": {
                "id": self._output_id + 1,  # 1-based for device protocol
                "value": int(value),
            },
        }
        _LOGGER.debug("Publishing to %s: %s", topic, payload)
        await mqtt.async_publish(
            self.hass,
            topic,
            json.dumps(payload),
            qos=0,
            retain=False,
        )

    # --------- HA metadata ---------
    @property
    def unique_id(self):
        return f"smartcloudage_output_{self._alias}_{self._output_id + 1}"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": f"SmartCloudAge {self._alias}",
            "manufacturer": "SmartCloudAge",
            "model": "MQTT Controller",
        }
