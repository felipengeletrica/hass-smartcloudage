"""Config flow for SmartCloudAge."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

DOMAIN = "smartcloudage"
METER_TYPES = {
    "water": "Água",
    "gas": "Gás",
    "energy": "Energia elétrica",
    "count": "Contador genérico",
}


def device_schema(defaults=None):
    """Build the controller form."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required("device_id", default=defaults.get("device_id", "")): str,
            vol.Required("outputs", default=defaults.get("outputs", 4)): vol.All(
                int, vol.Range(min=0, max=16)
            ),
            vol.Optional("alias", default=defaults.get("alias", "")): str,
            vol.Required("configure_meter", default=False): bool,
        }
    )


def meter_schema(defaults=None):
    """Build a pulse meter form."""
    defaults = defaults or {}
    return vol.Schema(
        {
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
            vol.Required("add_another", default=False): bool,
        }
    )


class SmartCloudAgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a SmartCloudAge controller and its pulse meters."""

    VERSION = 1

    def __init__(self):
        self._device = None

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

    def _create_entry(self):
        return self.async_create_entry(
            title=self._device["alias"],
            data={"devices": [self._device]},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SmartCloudAgeOptionsFlow()


class SmartCloudAgeOptionsFlow(config_entries.OptionsFlow):
    """Add pulse meters to an existing controller."""

    def __init__(self):
        self._devices = None
        self._meter_index = None

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
            menu_options=["add_meter", "edit_meter", "finish"],
        )

    async def async_step_add_meter(self, user_input=None):
        """Add a meter to the first configured controller."""
        errors = {}
        if user_input is not None:
            meters = self._devices[0].setdefault("meters", [])
            if any(meter["channel"] == user_input["channel"] for meter in meters):
                errors["channel"] = "channel_already_configured"
            elif user_input["factor"] <= 0:
                errors["factor"] = "factor_must_be_positive"
            else:
                user_input.pop("add_another", None)
                meters.append(user_input)
                return await self.async_step_init()
        return self.async_show_form(
            step_id="add_meter",
            data_schema=meter_schema(),
            errors=errors,
        )

    async def async_step_edit_meter(self, user_input=None):
        """Choose an existing meter to edit."""
        meters = self._devices[0].get("meters", [])
        if not meters:
            return self.async_abort(reason="no_meters_configured")
        if user_input is not None:
            self._meter_index = int(user_input["meter"])
            return await self.async_step_edit_meter_details()
        choices = {
            index: f"Canal {meter['channel']} — {meter.get('name', 'Medidor')}"
            for index, meter in enumerate(meters)
        }
        return self.async_show_form(
            step_id="edit_meter",
            data_schema=vol.Schema({vol.Required("meter"): vol.In(choices)}),
        )

    async def async_step_edit_meter_details(self, user_input=None):
        """Edit conversion settings for a configured meter."""
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
                user_input.pop("add_another", None)
                meters[self._meter_index] = user_input
                self._meter_index = None
                return await self.async_step_init()
        return self.async_show_form(
            step_id="edit_meter_details",
            data_schema=meter_schema(current),
            errors=errors,
        )

    async def async_step_finish(self, user_input=None):
        """Save options and reload the integration."""
        return self.async_create_entry(title="", data={"devices": self._devices})
