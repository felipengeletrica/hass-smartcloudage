import logging
import json
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.components import mqtt
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.restore_state import RestoreEntity

from .air_conditioner import is_ir_response

_LOGGER = logging.getLogger(__name__)

DOMAIN = "smartcloudage"
DEFAULT_NAME = "SmartCloudAge Output"
HARDCODED_TOPIC_PREFIX = "CloudAge/"

## @brief Creates output switch entities and subscribes to controller status.
#  @param hass Active Home Assistant instance.
#  @param entry SmartCloudAge configuration entry.
#  @param async_add_entities Callback used to register entities.
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
    units = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("air_conditioners", [])
    option_switches = build_ac_option_switches(units)
    remove_stale_ac_option_switches(hass, units, option_switches)
    entities.extend(option_switches)
    async_add_entities(entities)

    # Exemplo: listar aliases (pode usar para log ou debug)
    _LOGGER.info(f"Aliases cadastrados: {list(entities_by_alias.keys())}")

    ## @brief Updates switch states from an MQTT controller status message.
    #  @param msg MQTT message containing controller output state.
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
            if is_ir_response(raw):
                return
            data = json.loads(raw)

            # Desembrulha se "message" vier como JSON string (duplo-JSON)
            inner = data.get("message")
            if isinstance(inner, str):
                try:
                    inner_obj = json.loads(inner)
                    data = {**data, **inner_obj}
                except Exception:
                    pass

            # Ignora mensagens de INPUT_STATUS
            msg_type = data.get("message")
            if isinstance(msg_type, str) and msg_type.upper() == "INPUT_STATUS":
                _LOGGER.debug("Ignorando INPUT_STATUS (topic=%s)", msg.topic)
                return

            output_section = data.get("Output")
            outputs = output_section.get("Outputs") if isinstance(output_section, dict) else None
            if outputs is not None:
                outputs = int(outputs)
                _LOGGER.debug("MQTT update topic=%s device=%s Outputs=%s", msg.topic, device_id, outputs)
                for i, ent in enumerate(entities_by_device[device_id]):
                    ent.update_state_from_controller(bool((outputs >> i) & 1))
        except Exception as e:
            _LOGGER.error(f"Erro processando mensagem MQTT: {e}  Payload: {msg.payload}")

    for device_id in entities_by_device.keys():
        # cobre qualquer product_name: +/<device_id>/OutTopic/...
        await mqtt.async_subscribe(hass, f"+/{device_id}/OutTopic/#", message_received, 0)
        # opcional (se algum status sair fora de OutTopic):
        await mqtt.async_subscribe(hass, f"+/{device_id}/#", message_received, 0)





## @brief Represents one physical output of a SmartCloudAge controller.
class SmartCloudOutputSwitch(SwitchEntity):
    ## @brief Initializes an MQTT-backed controller output.
    #  @param hass Active Home Assistant instance.
    #  @param name Human-readable entity name.
    #  @param output_id Zero-based output index.
    #  @param base_topic MQTT command topic prefix.
    #  @param device_id Unique controller identifier.
    #  @param alias Optional human-readable controller alias.
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
    ## @brief Returns the current output state.
    #  @return @c True when the output is on.
    def is_on(self):
        return self._state

    ## @brief Publishes an ON command; state changes only on controller feedback.
    #  @param kwargs Additional Home Assistant service-call arguments.
    async def async_turn_on(self, **kwargs):
        await self._publish_mqtt(1)

    ## @brief Publishes an OFF command; state changes only on controller feedback.
    #  @param kwargs Additional Home Assistant service-call arguments.
    async def async_turn_off(self, **kwargs):
        await self._publish_mqtt(0)

    ## @brief Applies an output state confirmed by the physical controller.
    #  @param state Confirmed output state received through MQTT.
    def update_state_from_controller(self, state):
        self._state = state
        self.async_write_ha_state()

    ## @brief Publishes an output command to the controller.
    #  @param value Numeric output state, where 1 is on and 0 is off.
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
    ## @brief Builds the persistent identifier for this output.
    #  @return Unique Home Assistant entity identifier.
    def unique_id(self):
        # Use alias no unique_id para fácil identificação
        return f"smartcloudage_output_{self._alias}_{self._output_id + 1}"

    @property
    ## @brief Links this switch to its SmartCloudAge controller device.
    #  @return Home Assistant device-registry metadata.
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": f"SmartCloudAge {self._alias}",
            "manufacturer": "SmartCloudAge",
            "model": "MQTT Controller"
        }


## @brief Removes option switches the A/C no longer has, e.g. after a brand change.
#  @param hass Active Home Assistant instance.
#  @param units Air conditioners of the config entry.
#  @param option_switches Switches created for the current configuration.
def remove_stale_ac_option_switches(hass, units, option_switches):
    registry = er.async_get(hass)
    kept = {switch.unique_id for switch in option_switches}
    for unit in units:
        for key in ("display", "clean"):
            unique_id = f"{unit.unique_prefix}_{key}"
            if unique_id in kept:
                continue
            entity_id = registry.async_get_entity_id("switch", DOMAIN, unique_id)
            if entity_id is not None:
                registry.async_remove(entity_id)


## @brief Creates the display and clean switches of each air conditioner.
#  @param units Air conditioners of the config entry.
#  @return Switch entities for the options each brand supports.
def build_ac_option_switches(units):
    entities = []
    for unit in units:
        if unit.capabilities.supports_light:
            entities.append(SmartCloudAgeAcOptionSwitch(unit, "light", "display"))
        if unit.capabilities.supports_clean:
            entities.append(SmartCloudAgeAcOptionSwitch(unit, "clean", "clean"))
    return entities


## @brief On/off option of an air conditioner, sent with the full A/C state.
class SmartCloudAgeAcOptionSwitch(SwitchEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_assumed_state = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG

    ## @brief Links the switch to one flag of the A/C state.
    #  @param unit Air conditioner that owns the option.
    #  @param flag Field of the A/C state (@c light or @c clean).
    #  @param key Translation and unique-ID key.
    def __init__(self, unit, flag, key):
        self.unit = unit
        self._flag = flag
        self._attr_translation_key = f"ac_{key}"
        self._attr_unique_id = f"{unit.unique_prefix}_{key}"
        self._attr_device_info = unit.device_info

    @property
    def is_on(self):
        return getattr(self.unit.state, self._flag)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.unit.add_listener(self.async_write_ha_state))
        last = await self.async_get_last_state()
        if last is not None and last.state in ("on", "off"):
            self.unit.restore(**{self._flag: last.state == "on"})

    async def async_turn_on(self, **kwargs):
        await self.unit.async_apply(**{self._flag: True})

    async def async_turn_off(self, **kwargs):
        await self.unit.async_apply(**{self._flag: False})
