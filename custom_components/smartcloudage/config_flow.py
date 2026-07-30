"""Config flow for SmartCloudAge."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er, selector

DOMAIN = "smartcloudage"
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

    ## @brief Displays the available meter-management operations.
    #  @param user_input Menu input supplied by Home Assistant.
    #  @return Options-flow menu.
    async def async_step_init(self, user_input=None):
        """Show the available options."""
        if self._devices is None:
            source = self.config_entry.options or self.config_entry.data
            self._devices = [
                {**device, "meters": list(device.get("meters", []))}
                for device in source.get("devices", [])
            ]
        return self.async_show_menu(
            step_id="init",
            menu_options=["add_meter", "edit_meter", "delete_meter", "finish"],
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
