"""Shared fixtures for SmartCloudAge tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow Home Assistant to load this custom integration in every test."""


@pytest.fixture(autouse=True)
def mock_mqtt_publish():
    """Prevent tests from requiring a configured MQTT broker."""
    with patch(
        "custom_components.smartcloudage.mqtt.async_publish",
        new_callable=AsyncMock,
    ) as publish:
        yield publish


@pytest.fixture
def device_config():
    """Return a controller with representative meter configurations."""
    return {
        "device_id": "controller-01",
        "outputs": 4,
        "alias": "Bancada",
        "meters": [
            {
                "channel": 9,
                "name": "Água",
                "type": "water",
                "factor": 0.01,
                "offset": 5.502,
                "unit": "incorrect-unit",
            },
            {
                "channel": 10,
                "name": "Energia",
                "type": "energy",
                "factor": 0.01,
                "offset": 33.979,
                "unit": "m³",
            },
        ],
    }
