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

## 🧪 Example Lovelace Button Card using device "serial_6"

```yaml
type: grid
columns: 4
square: false
cards:
  - type: button
    name: Saída 1
    icon: mdi:power
    entity: switch.serial_6_output_1
    show_icon: true
    show_name: true
  - type: button
    name: Saída 2
    icon: mdi:power
    entity: switch.serial_6_output_2
    show_icon: true
    show_name: true
  - type: button
    name: Saída 3
    icon: mdi:power
    entity: switch.serial_6_output_3
    show_icon: true
    show_name: true
  - type: button
    name: Saída 4
    icon: mdi:power
    entity: switch.serial_6_output_4
    show_icon: true
    show_name: true
  - type: button
    name: Saída 5
    icon: mdi:power
    entity: switch.serial_6_output_5
    show_icon: true
    show_name: true
  - type: button
    name: Saída 6
    icon: mdi:power
    entity: switch.serial_6_output_6
    show_icon: true
    show_name: true
  - type: button
    name: Saída 7
    icon: mdi:power
    entity: switch.serial_6_output_7
    show_icon: true
    show_name: true
  - type: button
    name: Saída 8
    icon: mdi:power
    entity: switch.serial_6_output_8
    show_icon: true
    show_name: true
  - type: button
    name: Saída 9
    icon: mdi:power
    entity: switch.serial_6_output_9
    show_icon: true
    show_name: true
  - type: button
    name: Saída 10
    icon: mdi:power
    entity: switch.serial_6_output_10
    show_icon: true
    show_name: true
  - type: button
    name: Saída 11
    icon: mdi:power
    entity: switch.serial_6_output_11
    show_icon: true
    show_name: true
  - type: button
    name: Saída 12
    icon: mdi:power
    entity: switch.serial_6_output_12
    show_icon: true
    show_name: true
  - type: button
    name: Saída 13
    icon: mdi:power
    entity: switch.serial_6_output_13
    show_icon: true
    show_name: true
  - type: button
    name: Saída 14
    icon: mdi:power
    entity: switch.serial_6_output_14
    show_icon: true
    show_name: true
  - type: button
    name: Saída 15
    icon: mdi:power
    entity: switch.serial_6_output_15
    show_icon: true
    show_name: true
  - type: button
    name: Saída 16
    icon: mdi:power
    entity: switch.serial_6_output_16
    show_icon: true
    show_name: true
```

## 🚀 Installation

1. Copy this folder into `custom_components/smartcloudage`
2. Restart Home Assistant
3. Enjoy!
