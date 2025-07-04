# SmartCloudAge MQTT Integration for Home Assistant

This custom component integrates SmartCloudAge hardware with Home Assistant using MQTT.

## ✅ Features

- Supports multiple devices with individual `device_id`s
- Up to 16 digital outputs per device
- Sends commands to `CloudAge/<device_id>` MQTT topic
- Creates switch entities for each output:
  - `switch.smartcloudage_output_<device_id>_<output_id>`

## 📋 Requirements

- MQTT Broker (e.g., Mosquitto)
- Home Assistant with MQTT integration enabled

## ⚙️ Configuration

In your `configuration.yaml`:

```yaml
smartcloudage:
  devices:
    - device_id: "device01"
      outputs: 4
    - device_id: "device02"
      outputs: 8
```

## 🧪 Example Lovelace Button Card

```yaml
type: button
name: Toggle Output 1
icon: mdi:power
tap_action:
  action: toggle
entity: switch.smartcloudage_output_device01_1
```

## 📨 Example MQTT Payload Sent

Topic:
```
CloudAge/device01
```

Payload:
```json
{
  "command": 11,
  "type": 1,
  "signature": "device01",
  "payload": {
    "id": 1,
    "value": 1
  }
}
```

## 🚀 Installation

1. Copy this folder into `custom_components/smartcloudage`
2. Restart Home Assistant
3. Add config to `configuration.yaml`
4. Enjoy!
