DOMAIN = "smartcloudage"

async def async_setup_entry(hass, entry):
    await hass.config_entries.async_forward_entry_setups(entry, ["switch"])
    return True

async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_forward_entry_unloads(entry, ["switch"])
