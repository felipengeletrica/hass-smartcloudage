"""Config flow for SmartCloudAge."""

from __future__ import annotations

import uuid

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr, entity_registry as er, selector

from .air_conditioner import (
    IR_NO_RESPONSE,
    IR_NO_SEGMENTS,
    IR_OUTPUTS,
    IR_SEGMENTS,
    IR_UNSUPPORTED,
    IrSender,
    find_device,
    ir_output_gpio,
)
from .hvac import PROTOCOLS, brand_name, get_protocol

DOMAIN = "smartcloudage"
## Form error for each firmware probe result that blocks the registration.
FIRMWARE_ERRORS = {
    IR_NO_SEGMENTS: "firmware_no_segments",
    IR_UNSUPPORTED: "firmware_no_ir",
    IR_NO_RESPONSE: "controller_no_response",
}
METER_TYPES = {
    "water": "Água",
    "gas": "Gás",
    "energy": "Energia elétrica",
    "count": "Contador genérico",
}


## @brief Builds the validation schema for a SmartCloudAge controller.
#  @param defaults Optional initial form values.
#  @return Voluptuous schema for controller identification and output settings.
def device_schema(defaults=None):
    """Build the controller form."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required("device_id", default=defaults.get("device_id", "")): str,
            vol.Required(
                "outputs", default=defaults.get("outputs", 16)
            ): vol.In({10: "10", 16: "16"}),
            vol.Optional("alias", default=defaults.get("alias", "")): str,
            vol.Required("configure_meter", default=False): bool,
        }
    )


## @brief Builds the validation schema for an accumulated pulse meter.
#  @param defaults Optional initial meter values.
#  @param include_add_another Whether to include the repeated-entry control.
#  @return Voluptuous schema containing channel, conversion and unit settings.
def meter_schema(defaults=None, *, include_add_another=True):
    """Build a pulse meter form."""
    defaults = defaults or {}
    fields = {
        vol.Required("channel", default=defaults.get("channel", 1)): vol.All(
            int, vol.Range(min=1, max=16)
        ),
        vol.Required("name", default=defaults.get("name", "")): str,
        vol.Required(
            "type", default=defaults.get("type", "water")
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value=value, label=label)
                    for value, label in METER_TYPES.items()
                ],
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Required("factor", default=defaults.get("factor", 0.01)): vol.Coerce(
            float
        ),
        vol.Required("offset", default=defaults.get("offset", 0.0)): vol.Coerce(
            float
        ),
        vol.Required("unit", default=defaults.get("unit", "m³")): str,
    }
    if include_add_another:
        fields[vol.Required("add_another", default=False)] = bool
    return vol.Schema(fields)


## @brief Required field, pre-filled only when editing an existing value.
#  @param key Field name.
#  @param default Current value, or @c None for a new air conditioner.
#  @return Voluptuous marker.
def _required(key, default):
    """A new A/C must choose brand and IR channel explicitly."""
    return vol.Required(key) if default is None else vol.Required(key, default=default)


## @brief Builds the form for an IR-controlled air conditioner.
#  @param defaults Optional initial values of the air conditioner.
#  @return Voluptuous schema with name, brand, IR output and room sensor.
def ac_schema(defaults=None):
    """Build the air conditioner form."""
    defaults = defaults or {}
    sensor_key = (
        vol.Optional(
            "temperature_sensor",
            description={"suggested_value": defaults["temperature_sensor"]},
        )
        if defaults.get("temperature_sensor")
        else vol.Optional("temperature_sensor")
    )
    return vol.Schema(
        {
            vol.Required("name", default=defaults.get("name", "")): str,
            _required("brand", defaults.get("brand")): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=brand.value, label=brand_name(brand))
                        for brand in PROTOCOLS
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            _required(
                "ir_output",
                str(ir_output_gpio(defaults["ir_output"])) if "ir_output" in defaults else None,
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=str(gpio), label=label)
                        for gpio, label in IR_OUTPUTS.items()
                    ],
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
            sensor_key: selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
            ),
            vol.Required("skip_firmware_check", default=False): bool,
        }
    )


## @brief Builds the remote-model form of a brand with more than one protocol variant.
#  @param brand Selected brand value.
#  @param default Model shown first.
#  @return Voluptuous schema with the model selector.
def ac_model_schema(brand, default=None):
    """Build the remote model form."""
    protocol = get_protocol(brand)
    return vol.Schema(
        {
            vol.Required(
                "model", default=protocol.resolve_model(default)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(protocol.models),
                    mode=selector.SelectSelectorMode.LIST,
                )
            )
        }
    )


## @brief Guides initial configuration of a controller and its pulse meters.
class SmartCloudAgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a SmartCloudAge controller and its pulse meters."""

    VERSION = 1

    def __init__(self):
        ## @brief Initializes the temporary controller configuration.
        self._device = None

    ## @brief Collects and validates the controller's primary settings.
    #  @param user_input Values submitted by the user, or @c None on first display.
    #  @return A form, the meter step, an abort result or a completed entry.
    async def async_step_user(self, user_input=None):
        """Configure the controller."""
        if user_input is not None:
            await self.async_set_unique_id(user_input["device_id"])
            self._abort_if_unique_id_configured()
            configure_meter = user_input.pop("configure_meter")
            self._device = {
                "device_id": user_input["device_id"],
                "outputs": user_input["outputs"],
                "alias": user_input.get("alias") or user_input["device_id"],
                "meters": [],
            }
            if configure_meter:
                return await self.async_step_meter()
            return self._create_entry()
        return self.async_show_form(step_id="user", data_schema=device_schema())

    ## @brief Collects one or more pulse-meter definitions.
    #  @param user_input Submitted meter values, or @c None on first display.
    #  @return The meter form or a completed configuration entry.
    async def async_step_meter(self, user_input=None):
        """Configure one or more accumulated pulse meters."""
        errors = {}
        if user_input is not None:
            if any(
                meter["channel"] == user_input["channel"]
                for meter in self._device["meters"]
            ):
                errors["channel"] = "channel_already_configured"
            elif user_input["factor"] <= 0:
                errors["factor"] = "factor_must_be_positive"
            else:
                add_another = user_input.pop("add_another")
                self._device["meters"].append(user_input)
                if not add_another:
                    return self._create_entry()
        return self.async_show_form(
            step_id="meter",
            data_schema=meter_schema(),
            errors=errors,
        )

    ## @brief Creates the Home Assistant entry from the collected controller data.
    #  @return Completed configuration-flow result.
    def _create_entry(self):
        return self.async_create_entry(
            title=self._device["alias"],
            data={"devices": [self._device]},
        )

    @staticmethod
    @callback
    ## @brief Creates the options flow associated with an existing entry.
    #  @param config_entry Existing entry supplied by Home Assistant.
    #  @return New SmartCloudAge options-flow instance.
    def async_get_options_flow(config_entry):
        return SmartCloudAgeOptionsFlow()


## @brief Manages pulse meters belonging to an existing controller.
class SmartCloudAgeOptionsFlow(config_entries.OptionsFlow):
    """Add, edit or delete pulse meters on an existing device."""

    def __init__(self):
        ## @brief Initializes the editable device list and meter selection.
        self._devices = None
        self._meter_index = None
        self._ac_index = None
        self._ac_draft = None

    ## @brief Displays the available meter-management operations.
    #  @param user_input Menu input supplied by Home Assistant.
    #  @return Options-flow menu.
    async def async_step_init(self, user_input=None):
        """Show the available options."""
        if self._devices is None:
            source = self.config_entry.options or self.config_entry.data
            self._devices = [
                {
                    **device,
                    "meters": list(device.get("meters", [])),
                    "air_conditioners": [
                        dict(unit) for unit in device.get("air_conditioners", [])
                    ],
                }
                for device in source.get("devices", [])
            ]
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "add_meter",
                "edit_meter",
                "delete_meter",
                "add_ac",
                "edit_ac",
                "delete_ac",
                "finish",
            ],
        )

    ## @brief Validates, adds and persists a new pulse meter.
    #  @param user_input Submitted meter values, or @c None on first display.
    #  @return Meter form or completed options entry.
    async def async_step_add_meter(self, user_input=None):
        """Add and immediately persist a meter."""
        errors = {}
        if user_input is not None:
            meters = self._devices[0].setdefault("meters", [])
            if any(meter["channel"] == user_input["channel"] for meter in meters):
                errors["channel"] = "channel_already_configured"
            elif user_input["factor"] <= 0:
                errors["factor"] = "factor_must_be_positive"
            else:
                meters.append(user_input)
                return self._save_options()
        return self.async_show_form(
            step_id="add_meter",
            data_schema=meter_schema(include_add_another=False),
            errors=errors,
        )

    ## @brief Builds labels for the currently configured meters.
    #  @return Mapping from meter index to a human-readable channel and name.
    def _meter_choices(self):
        """Return configured meters as selector choices."""
        meters = self._devices[0].get("meters", [])
        return {
            index: f"Canal {meter['channel']} — {meter.get('name', 'Medidor')}"
            for index, meter in enumerate(meters)
        }

    ## @brief Lets the user select a configured meter for editing.
    #  @param user_input Selected meter index, or @c None on first display.
    #  @return Selection form, edit form or abort result.
    async def async_step_edit_meter(self, user_input=None):
        """Choose an existing meter to edit."""
        meters = self._devices[0].get("meters", [])
        if not meters:
            return self.async_abort(reason="no_meters_configured")
        if user_input is not None:
            self._meter_index = int(user_input["meter"])
            return await self.async_step_edit_meter_details()
        return self.async_show_form(
            step_id="edit_meter",
            data_schema=vol.Schema(
                {vol.Required("meter"): vol.In(self._meter_choices())}
            ),
        )

    ## @brief Validates and persists changes to the selected meter.
    #  @param user_input Updated meter values, or @c None on first display.
    #  @return Edit form or completed options entry.
    async def async_step_edit_meter_details(self, user_input=None):
        """Edit and immediately persist a configured meter."""
        meters = self._devices[0]["meters"]
        current = meters[self._meter_index]
        errors = {}
        if user_input is not None:
            if any(
                index != self._meter_index
                and meter["channel"] == user_input["channel"]
                for index, meter in enumerate(meters)
            ):
                errors["channel"] = "channel_already_configured"
            elif user_input["factor"] <= 0:
                errors["factor"] = "factor_must_be_positive"
            else:
                meters[self._meter_index] = user_input
                self._meter_index = None
                return self._save_options()
        return self.async_show_form(
            step_id="edit_meter_details",
            data_schema=meter_schema(current, include_add_another=False),
            errors=errors,
        )

    ## @brief Lets the user choose a configured meter for deletion.
    #  @param user_input Selected meter index, or @c None on first display.
    #  @return Selection form, confirmation form or abort result.
    async def async_step_delete_meter(self, user_input=None):
        """Choose an existing meter to delete."""
        meters = self._devices[0].get("meters", [])
        if not meters:
            return self.async_abort(reason="no_meters_configured")
        if user_input is not None:
            self._meter_index = int(user_input["meter"])
            return await self.async_step_confirm_delete_meter()
        return self.async_show_form(
            step_id="delete_meter",
            data_schema=vol.Schema(
                {vol.Required("meter"): vol.In(self._meter_choices())}
            ),
        )

    ## @brief Confirms deletion and removes the selected meter entity.
    #  @param user_input Confirmation submitted by the user.
    #  @return Confirmation form or completed options entry.
    async def async_step_confirm_delete_meter(self, user_input=None):
        """Confirm and delete the selected meter."""
        meters = self._devices[0]["meters"]
        meter = meters[self._meter_index]

        if user_input is not None and user_input["confirm"]:
            device_id = self._devices[0].get("device_id")
            channel = int(meter["channel"])
            unique_id = f"smartcloudage_{device_id}_pulse_{channel}"
            entity_registry = er.async_get(self.hass)
            entity_id = entity_registry.async_get_entity_id(
                "sensor", DOMAIN, unique_id
            )
            if entity_id is not None:
                entity_registry.async_remove(entity_id)

            meters.pop(self._meter_index)
            self._meter_index = None
            return self._save_options()

        placeholders = {
            "meter": meter.get("name", "Medidor"),
            "channel": str(meter["channel"]),
        }
        return self.async_show_form(
            step_id="confirm_delete_meter",
            data_schema=vol.Schema({vol.Required("confirm", default=False): bool}),
            description_placeholders=placeholders,
        )

    ## @brief Builds labels for the configured air conditioners.
    #  @return Mapping from A/C index to its name and brand.
    def _ac_choices(self):
        """Return configured air conditioners as selector choices."""
        units = self._devices[0].get("air_conditioners", [])
        return {
            index: f"{unit['name']} — {brand_name(get_protocol(unit['brand']).brand)}"
            for index, unit in enumerate(units)
        }

    ## @brief Starts the form for a new air conditioner.
    #  @param user_input Unused menu input.
    #  @return Air conditioner form.
    async def async_step_add_ac(self, user_input=None):
        """Add an air conditioner controlled by the IR emitter."""
        self._ac_index = None
        return await self.async_step_ac()

    ## @brief Lets the user select a configured air conditioner for editing.
    #  @param user_input Selected A/C index, or @c None on first display.
    #  @return Selection form, A/C form or abort result.
    async def async_step_edit_ac(self, user_input=None):
        """Choose an existing air conditioner to edit."""
        if not self._devices[0].get("air_conditioners"):
            return self.async_abort(reason="no_acs_configured")
        if user_input is not None:
            self._ac_index = int(user_input["air_conditioner"])
            return await self.async_step_ac()
        return self.async_show_form(
            step_id="edit_ac",
            data_schema=vol.Schema(
                {vol.Required("air_conditioner"): vol.In(self._ac_choices())}
            ),
        )

    ## @brief Collects name, brand, IR output and room sensor of an air conditioner.
    #  @param user_input Submitted values, or @c None on first display.
    #  @return A/C form, model form or completed options entry.
    async def async_step_ac(self, user_input=None):
        """Configure an air conditioner."""
        units = self._devices[0].setdefault("air_conditioners", [])
        current = units[self._ac_index] if self._ac_index is not None else {}
        errors = {}
        if user_input is not None:
            if not user_input["name"].strip():
                errors["name"] = "name_required"
            elif not user_input.get("skip_firmware_check") and (
                probe := await self._async_probe_ir()
            ) != IR_SEGMENTS:
                errors["base"] = FIRMWARE_ERRORS[probe]
            else:
                self._ac_draft = {
                    "id": current.get("id") or uuid.uuid4().hex[:8],
                    "name": user_input["name"].strip(),
                    "brand": user_input["brand"],
                    "model": current.get("model", "")
                    if current.get("brand") == user_input["brand"]
                    else "",
                    "ir_output": int(user_input["ir_output"]),
                    "temperature_sensor": user_input.get("temperature_sensor") or None,
                }
                if len(get_protocol(user_input["brand"]).models) > 1:
                    return await self.async_step_ac_model()
                self._ac_draft["model"] = get_protocol(user_input["brand"]).default_model
                return self._save_ac()
        return self.async_show_form(
            step_id="ac",
            data_schema=self.add_suggested_values_to_schema(ac_schema(current), user_input or {})
            if user_input
            else ac_schema(current),
            errors=errors,
            description_placeholders={"controller": self._devices[0].get("alias") or ""},
        )

    ## @brief Asks the controller whether its firmware sends segmented IR frames.
    #  @return Probe result from @ref IrSender.async_probe.
    async def _async_probe_ir(self):
        """Query the IR status of the controller (command 32, read)."""
        device_id = self._devices[0].get("device_id")
        senders = (
            self.hass.data.get(DOMAIN, {})
            .get(self.config_entry.entry_id, {})
            .get("ir_senders", {})
        )
        sender = senders.get(device_id)
        if sender is not None:
            return await sender.async_probe()
        sender = IrSender(self.hass, device_id)
        try:
            return await sender.async_probe()
        finally:
            sender.async_stop()

    ## @brief Collects the remote model of brands with several protocol variants.
    #  @param user_input Selected model, or @c None on first display.
    #  @return Model form or completed options entry.
    async def async_step_ac_model(self, user_input=None):
        """Choose the remote control model."""
        if user_input is not None:
            self._ac_draft["model"] = user_input["model"]
            return self._save_ac()
        return self.async_show_form(
            step_id="ac_model",
            data_schema=ac_model_schema(self._ac_draft["brand"], self._ac_draft["model"]),
            description_placeholders={"brand": brand_name(get_protocol(self._ac_draft["brand"]).brand)},
        )

    ## @brief Stores the air conditioner being added or edited.
    #  @return Completed options-flow result.
    def _save_ac(self):
        units = self._devices[0].setdefault("air_conditioners", [])
        if self._ac_index is None:
            units.append(self._ac_draft)
        else:
            units[self._ac_index] = self._ac_draft
        self._ac_index = None
        self._ac_draft = None
        return self._save_options()

    ## @brief Lets the user choose a configured air conditioner for deletion.
    #  @param user_input Selected A/C index, or @c None on first display.
    #  @return Selection form, confirmation form or abort result.
    async def async_step_delete_ac(self, user_input=None):
        """Choose an existing air conditioner to delete."""
        if not self._devices[0].get("air_conditioners"):
            return self.async_abort(reason="no_acs_configured")
        if user_input is not None:
            self._ac_index = int(user_input["air_conditioner"])
            return await self.async_step_confirm_delete_ac()
        return self.async_show_form(
            step_id="delete_ac",
            data_schema=vol.Schema(
                {vol.Required("air_conditioner"): vol.In(self._ac_choices())}
            ),
        )

    ## @brief Confirms deletion and removes the air conditioner device.
    #  @param user_input Confirmation submitted by the user.
    #  @return Confirmation form or completed options entry.
    async def async_step_confirm_delete_ac(self, user_input=None):
        """Confirm and delete the selected air conditioner."""
        units = self._devices[0]["air_conditioners"]
        unit = units[self._ac_index]

        if user_input is not None and user_input["confirm"]:
            device_id = self._devices[0].get("device_id")
            device = find_device(self.hass, self.config_entry.entry_id, f"{device_id}_ac_{unit['id']}")
            if device is not None:
                dr.async_get(self.hass).async_remove_device(device.id)
            units.pop(self._ac_index)
            self._ac_index = None
            return self._save_options()

        return self.async_show_form(
            step_id="confirm_delete_ac",
            data_schema=vol.Schema({vol.Required("confirm", default=False): bool}),
            description_placeholders={"name": unit["name"]},
        )

    ## @brief Closes the options flow without changing the working data.
    #  @param user_input Unused menu input.
    #  @return Completed options entry.
    async def async_step_finish(self, user_input=None):
        """Save unchanged options and close the flow."""
        return self._save_options()

    ## @brief Persists the current device list.
    #  @return Completed options-flow result.
    def _save_options(self):
        """Persist the current device list and close the options flow."""
        return self.async_create_entry(title="", data={"devices": self._devices})
