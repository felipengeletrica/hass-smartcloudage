import json
import logging
from typing import Any, Callable, Dict, List, Optional

from homeassistant.components import mqtt
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

_LOGGER = logging.getLogger(__name__)

DOMAIN = "smartcloudage"
DEFAULT_OUTPUTS = 16
HARDCODED_TOPIC_PREFIX = "CloudAge/"  # Tópico de comandos e status: CloudAge/<device_id>[/*]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities
) -> None:
    """Cria entidades e registra assinaturas MQTT para cada device."""

    # ---- Carrega devices do config entry ---------------------------------------------------------
    try:
        devices = entry.options.get("devices")
        if devices is None:
            devices = entry.data.get("devices", [])
        if not isinstance(devices, list):
            _LOGGER.warning("Formato inesperado para 'devices'. Usando lista vazia.")
            devices = []
    except Exception as exc:
        _LOGGER.error("Erro ao carregar devices do entry: %s", exc)
        devices = []

    entities: List[SmartCloudOutputSwitch] = []
    entities_by_device: Dict[str, List["SmartCloudOutputSwitch"]] = {}

    for device_conf in devices:
        device_id: str = device_conf.get("device_id")
        if not device_id:
            _LOGGER.warning("Device sem 'device_id' ignorado: %s", device_conf)
            continue

        alias: str = device_conf.get("alias") or device_id
        outputs: int = int(device_conf.get("outputs", DEFAULT_OUTPUTS))

        entities_by_device.setdefault(device_id, [])

        for output_idx in range(outputs):
            ent = SmartCloudOutputSwitch(
                hass=hass,
                name=f"{alias} Output {output_idx + 1}",
                output_id=output_idx,
                base_topic=HARDCODED_TOPIC_PREFIX,
                device_id=device_id,
                alias=alias,
            )
            entities.append(ent)
            entities_by_device[device_id].append(ent)

    async_add_entities(entities)
    _LOGGER.info("Criadas %d entidades para %d devices.", len(entities), len(entities_by_device))

    # ---- Callback de mensagens MQTT --------------------------------------------------------------
    @callback
    def _handle_status_message(msg: mqtt.ReceiveMessage) -> None:
        try:
            # CloudAge/<device_id> [ /... ]
            parts = msg.topic.split("/")
            if len(parts) < 2 or not msg.topic.startswith(HARDCODED_TOPIC_PREFIX):
                return

            device_id = parts[1]
            if device_id not in entities_by_device:
                return

            data = json.loads(msg.payload)

            # ---- 1) Tenta Output no topo (prioritário)
            outputs_value = None
            if isinstance(data.get("Output"), dict):
                ov = data["Output"].get("Outputs")
                if ov is not None:
                    outputs_value = ov

            # ---- 2) Se não veio no topo, tenta decodificar message quando for JSON string
            msg_field = data.get("message")
            inner_type = None
            if outputs_value is None and isinstance(msg_field, str) and msg_field.startswith("{"):
                try:
                    inner = json.loads(msg_field)
                    inner_type = (inner.get("message") or "").upper()
                    if isinstance(inner.get("Output"), dict):
                        ov = inner["Output"].get("Outputs")
                        if ov is not None:
                            outputs_value = ov
                except Exception:
                    pass  # message não era JSON válido – ignora

            # ---- 3) Se ainda não tem Output, nada para atualizar
            if outputs_value is None:
                return

            # Aceita atualizar em OUTPUT_STATUS ou KeepAlive (ou sempre que houver Output)
            # (Opcional) você pode filtrar por inner_type == OUTPUT_STATUS, mas KeepAlive também serve
            try:
                bitmask = int(outputs_value)
            except (TypeError, ValueError):
                _LOGGER.debug("Outputs não numérico em %s: %r", device_id, outputs_value)
                return

            for i, ent in enumerate(entities_by_device[device_id]):
                ent._attr_is_on = bool((bitmask >> i) & 1)
                ent.async_write_ha_state()

            _LOGGER.debug(
                "Atualizados %d switches de %s. bitmask=%s (inner_type=%s, topic=%s)",
                len(entities_by_device[device_id]), device_id, bitmask, inner_type, msg.topic
            )

        except Exception as exc:
            _LOGGER.error("Erro processando MQTT (%s): %s", msg.topic, exc)


    # ---- Assinaturas (raiz + wildcard) e descarte no unload -------------------------------------
    for device_id in entities_by_device.keys():
        topic_root = f"{HARDCODED_TOPIC_PREFIX}{device_id}"
        topic_wild = f"{topic_root}/#"

        unsub_root = await mqtt.async_subscribe(hass, topic_root, _handle_status_message, qos=0)
        unsub_wild = await mqtt.async_subscribe(hass, topic_wild, _handle_status_message, qos=0)

        entry.async_on_unload(unsub_root)
        entry.async_on_unload(unsub_wild)

        _LOGGER.debug("Assinado: %s e %s", topic_root, topic_wild)


class SmartCloudOutputSwitch(SwitchEntity):
    """Entidade de saída (switch) controlada por MQTT usando protocolo SmartCloudAge."""

    _attr_should_poll = False  # Estado vem por MQTT (push)

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        output_id: int,
        base_topic: str,
        device_id: str,
        alias: Optional[str] = None,
    ) -> None:
        self.hass = hass
        self._attr_name = name
        self._attr_is_on = False
        self._output_id = int(output_id)
        self._device_id = device_id
        self._alias = alias or device_id
        self._base_topic = base_topic

    # ---------------- Switch API ----------------

    @property
    def unique_id(self) -> str:
        # Inclui alias para facilitar identificar no HA sem colidir com múltiplos devices
        return f"smartcloudage_output::{self._device_id}::{self._output_id + 1}"

    @property
    def device_info(self) -> Dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": f"SmartCloudAge {self._alias}",
            "manufacturer": "SmartCloudAge",
            "model": "MQTT Controller",
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._publish_output_command(1)
        # Não setamos _attr_is_on aqui — aguardamos confirmação do device

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._publish_output_command(0)
        # Não setamos _attr_is_on aqui — aguardamos confirmação do device

    # ---------------- MQTT Command ----------------

    async def _publish_output_command(self, value: int) -> None:
        """
        Publica comando no tópico CloudAge/<device_id> seguindo o protocolo:
        {
          "command": 11,           # OUTPUT_ENUM
          "type": 1,               # WRITE
          "signature": "<device_id>",
          "payload": {"id": <1..N>, "value": 0|1}
        }
        """
        topic = f"{self._base_topic}{self._device_id}"
        payload = {
            "command": 11,
            "type": 1,
            "signature": self._device_id,
            "payload": {
                "id": self._output_id + 1,  # 1-based no device
                "value": 1 if value else 0,
            },
        }

        _LOGGER.debug("MQTT publish -> %s: %s", topic, payload)

        await mqtt.async_publish(
            self.hass,
            topic,
            json.dumps(payload),
            qos=0,
            retain=False,
        )
