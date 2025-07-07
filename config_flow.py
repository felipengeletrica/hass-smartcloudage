import voluptuous as vol
from homeassistant import config_entries

class SmartCloudAgeConfigFlow(config_entries.ConfigFlow, domain="smartcloudage"):
    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            # Tenta validar o JSON
            import json
            try:
                devices = json.loads(user_input["devices_json"])
                assert isinstance(devices, list)
                for item in devices:
                    assert "device_id" in item and "outputs" in item
                return self.async_create_entry(
                    title="SmartCloudAge",
                    data={"devices_json": user_input["devices_json"]}
                )
            except Exception as e:
                errors["devices_json"] = "invalid_json"
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(
                    "devices_json", 
                    default='[\n  {"device_id": "device01", "outputs": 4}\n]'
                ): str
            }),
            errors=errors
        )
