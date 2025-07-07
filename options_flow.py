from homeassistant import config_entries
import voluptuous as vol

DOMAIN = "smartcloudage"

class SmartCloudAgeOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        # Pega a lista atual de devices
        devices = self.config_entry.options.get("devices", [])
        # Monta um formulário simples (exemplo: lista separada por vírgula)
        default_value = ",".join([d["device_id"] for d in devices]) if devices else ""
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("devices", default=default_value): str,
            }),
            errors={}
        )

    async def async_step_user(self, user_input=None):
        # Parse da lista
        device_ids = [d.strip() for d in user_input["devices"].split(",") if d.strip()]
        # Salva cada device_id como dict (aqui só device_id, mas pode expandir para outputs)
        devices = [{"device_id": d, "outputs": 4} for d in device_ids]  # outputs fixo 4 só como exemplo
        return self.async_create_entry(title="", data={"devices": devices})

# Em config_flow.py adicione:
    async def async_get_options_flow(config_entry):
        return SmartCloudAgeOptionsFlowHandler(config_entry)
