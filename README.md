# SmartCloudAge MQTT Integration for Home Assistant

This custom component integrates **SmartCloudAge hardware** with Home Assistant using MQTT.  
The SmartCloudAge controller provides **16 inputs, 16 outputs, 2 dimming outputs (PWM)** and native integrations with **Home Assistant**

---

## ⚙️ Installation via HACS (Custom Repository)

> Requirements: Home Assistant with **HACS** installed and MQTT integration already configured.

1. In Home Assistant, open **HACS → Integrations → ⋯ (menu) → Custom repositories**.  
2. In **Repository**, enter this repo URL (e.g., `https://github.com/felipengeletrica/hass-smartcloudage`).  
3. In **Category**, choose **Integration** and click **Add**.  
4. In the HACS main screen, search for **SmartCloudAge** and click **Install**.  
5. **Restart** Home Assistant.  
6. Go to **Settings → Devices & Services → Add Integration** and search for **SmartCloudAge** to add it.

---

## 🔧 Device Configuration (Options Flow)

After adding the integration, click on **Options** in the SmartCloudAge card to open the configuration form.

### Edit existing devices
For each registered device you will see indexed fields (`*_i`):

- `device_id_i` — Device ID (e.g., `serial_6`)  
- `outputs_i` — Number of outputs (**10** or **16**)  
- `alias_i` — Alias for UI display (optional)

### Add a new device
At the bottom of the form there are optional fields:

- `new_device_id` — ID of the new device  
- `new_outputs` — **10** or **16** (default: 10)  
- `new_alias` — Alias (optional; if empty, falls back to `new_device_id`)

Click **Submit** to save.

---

## ✅ Best Practices

- Use a stable `device_id` (e.g., serial number) — it defines the topic `CloudAge/<device_id>`.  
- Adjust `outputs` according to the hardware (**10** or **16**).  
- Use `alias` for easy identification in the dashboard.

---

## 🧪 Example Dashboard (optional)

```yaml
type: grid
columns: 4
square: false
cards:
  - type: button
    name: Output 1
    icon: mdi:power
    entity: switch.serial_6_output_1
  # ...repeat as needed
```

---

## 🛠️ Troubleshooting

- **Integration not showing in Add Integration**: check if installed via HACS and restart HA.  
- **Saving does nothing**: verify all `device_id_i` are filled and `outputs_i` is 10 or 16.  
- **No MQTT action**: confirm broker connection and ensure `device_id` matches the physical device.

---

## 📞 Contact

- 🌐 [smartcloudage.com.br](http://smartcloudage.com.br)  
- 📧 [felipe@smartcloudage.com.br](mailto:felipe@smartcloudage.com.br)  
- 📱 +55 (51) 99269-7065