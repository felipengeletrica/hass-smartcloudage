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
- `pulses_i` — Number of pulse counter sensors (**0** to **16**)
- `alias_i` — Alias for UI display (optional)

### Add a new device
At the bottom of the form there are optional fields:

- `new_device_id` — ID of the new device  
- `new_outputs` — **10** or **16** (default: 10)  
- `new_pulses` — **0** to **16** pulse counter sensors (default: 16)
- `new_alias` — Alias (optional; if empty, falls back to `new_device_id`)

Click **Submit** to save.

## 🔢 Pulse Counter Sensors

The integration creates one Home Assistant sensor for each configured pulse channel.
The firmware payload is expected in the current SmartCloudAge format:

```json
{
  "message": "PULSE_SENSOR",
  "Pulses": [
    {"Sensor": 0, "lsb": 1234, "msb": 0}
  ]
}
```

The Home Assistant driver rebuilds the 64-bit value using:

```text
value = (msb << 32) | lsb
```

Each pulse entity uses `state_class: total_increasing`, so it can be used by dashboards, history statistics and consumption calculations.

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

## Sensores de pulso com multiplicador

A integração cria sensores de pulso a partir das mensagens MQTT publicadas pelo firmware SmartCloudAge.

Payload esperado:

```json
{
  "message": "PULSE_SENSOR",
  "Pulses": [
    {
      "Sensor": 0,
      "lsb": 1234,
      "msb": 0
    }
  ]
}
```

O contador bruto é reconstruído em 64 bits:

```text
raw_pulses = (msb << 32) | lsb
```

Durante a configuração do dispositivo é possível informar:

- `pulses`: quantidade de canais de pulso, de 0 a 16.
- `pulse_multiplier`: fator aplicado ao contador bruto.
- `pulse_unit`: unidade final exibida no Home Assistant.

Valor publicado no sensor:

```text
valor = raw_pulses * pulse_multiplier
```

Exemplos:

```text
1 pulso = 0,001 m³  -> pulse_multiplier = 0.001, pulse_unit = m³
1 pulso = 0,01 kWh  -> pulse_multiplier = 0.01,  pulse_unit = kWh
1 pulso = 1 pulso   -> pulse_multiplier = 1.0,   pulse_unit = pulses
```

O valor bruto não é perdido. Ele fica disponível nos atributos do sensor:

- `raw_pulses`
- `multiplier`
- `lsb`
- `msb`
- `pulse_channel`
- `firmware_sensor_index`
