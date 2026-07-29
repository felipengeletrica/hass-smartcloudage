"""Tests for SmartCloudAge integration setup helpers."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from custom_components.smartcloudage import build_datetime_payload


def test_build_datetime_payload_uses_device_as_default_signature():
    """RTC command must contain the protocol fields expected by firmware."""
    fixed_now = datetime(2026, 7, 29, 21, 10, 5)

    with patch(
        "custom_components.smartcloudage.datetime"
    ) as datetime_mock:
        datetime_mock.now.return_value = fixed_now
        payload = build_datetime_payload("controller-01")

    assert payload == {
        "command": 9,
        "payload": {
            "datetime": {
                "day": 29,
                "mon": 7,
                "year": 2026,
                "hour": 21,
                "min": 10,
                "sec": 5,
            }
        },
        "type": 1,
        "signature": "controller-01",
    }


def test_build_datetime_payload_accepts_custom_signature():
    """An explicitly configured firmware signature must be preserved."""
    payload = build_datetime_payload("controller-01", "signed-device")

    assert payload["signature"] == "signed-device"
