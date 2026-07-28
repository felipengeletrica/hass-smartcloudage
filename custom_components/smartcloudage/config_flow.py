"""Config flow for SmartCloudAge."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback

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


def meter_schema():
    """Build a pulse meter form."""
    return vol.Schema(
        {
            vol.Required("channel", default=1): vol.All(int, vol.Range(min=1, max=16)),
            vol.Required("name"): str,
            vol.Required("type", default="water"): vol.In(METER_TYPES),
            vol.Required("factor", default=0.01): vol.Coerce(float),
            vol.Required("unit", default="m³"): str,
            vol.Required("add_another", default=False): bool,
        }
    )


class SmartCloudAgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a SmartCloudAge controller and its pulse meters."""

    VERSION = 2

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
        return SmartCloudAgeOptionsFlow(config_entry)


class SmartCloudAgeOptionsFlow(config_entries.OptionsFlow):
    """Add pulse meters to an existing controller."""

    def __init__(self, config_entry):
        self._config_entry = config_entry
        source = config_entry.options or config_entry.data
        self._devices = [
            {**device, "meters": list(device.get("meters", []))}
            for device in source.get("devices", [])
        ]

    async def async_step_init(self, user_input=None):
        """Show the available options."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["add_meter", "finish"],
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

    async def async_step_finish(self, user_input=None):
        """Save options and reload the integration."""
        return self.async_create_entry(title="", data={"devices": self._devices})
