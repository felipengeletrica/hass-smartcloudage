from homeassistant import config_entries
import voluptuous as vol

DOMAIN = "smartcloudage"

class SmartCloudAgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Fluxo de configuração inicial do SmartCloudAge."""

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            # Salva um device inicial, podendo adicionar depois pelo options
            return self.async_create_entry(
                title="SmartCloudAge",
                data={
                    "devices": [
                        {
                            "device_id": user_input["device_id"],
                            "outputs": user_input["outputs"],
                            "alias": user_input.get("alias", user_input["device_id"])
                        }
                    ]
                }
            )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("device_id"): str,
                vol.Required("outputs", default=4): vol.All(int, vol.Range(min=1, max=16)),
                vol.Optional("alias"): str,
            })
        )
