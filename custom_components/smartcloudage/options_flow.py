from homeassistant import config_entries
import voluptuous as vol

DOMAIN = "smartcloudage"

def build_devices_schema(devices=None):
    devices = devices or []
    schema_dict = {}
    for i, dev in enumerate(devices):
        schema_dict[vol.Required(f"device_id_{i}", default=dev["device_id"])] = str
        schema_dict[vol.Required(f"outputs_{i}", default=dev.get("outputs", 10))] = vol.In([10, 16])
        schema_dict[vol.Required(f"alias_{i}", default=dev.get("alias", f"Device {i+1}"))] = str
    schema_dict[vol.Optional("new_device_id")] = str
    schema_dict[vol.Optional("new_outputs", default=10)] = vol.In([10, 16])
    schema_dict[vol.Optional("new_alias")] = str
    return vol.Schema(schema_dict)

class SmartCloudAgeOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        devices = self.config_entry.options.get("devices") or self.config_entry.data.get("devices") or []
        if user_input is not None:
            devs = []
            i = 0
            while f"device_id_{i}" in user_input:
                devs.append({
                    "device_id": user_input[f"device_id_{i}"],
                    "outputs": user_input[f"outputs_{i}"],
                    "alias": user_input[f"alias_{i}"]
                })
                i += 1
            if user_input.get("new_device_id"):
                devs.append({
                    "device_id": user_input["new_device_id"],
                    "outputs": user_input["new_outputs"],
                    "alias": user_input.get("new_alias", user_input["new_device_id"])
                })
            return self.async_create_entry(title="", data={"devices": devs})
        return self.async_show_form(
            step_id="init",
            data_schema=build_devices_schema(devices)
        )

async def async_get_options_flow(config_entry):
    return SmartCloudAgeOptionsFlowHandler(config_entry)
