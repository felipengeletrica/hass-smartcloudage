"""TV remote frames match the SCA Command, key by key.

tests/fixtures/tv_remote_golden.tsv was generated from the SCA Command
IrRemoteEncoder (itself checked pulse for pulse against IRremoteESP8266):
every key of every built-in remote, both toggle states, both IR outputs.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from custom_components.smartcloudage.tv_codes import (
    ESSENTIAL_KEYS,
    KEYS,
    PRESETS,
    IrProtocol,
    encode,
    encode_key,
    preset_keys,
)

GOLDEN = Path(__file__).parent / "fixtures" / "tv_remote_golden.tsv"


def _key_name(csharp_key: str) -> str:
    """SCA Command key name (VolumeUp, Digit7) to the integration's (volume_up, digit_7)."""
    if digit := re.fullmatch(r"Digit(\d)", csharp_key):
        return f"digit_{digit.group(1)}"
    return re.sub(r"(?<!^)([A-Z])", r"_\1", csharp_key).lower()


def _golden_rows():
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            preset, key, toggle, gpio, payload = line.split("\t")
            yield preset, _key_name(key), toggle == "1", int(gpio), json.loads(payload)


def test_golden_table_covers_every_key():
    rows = list(_golden_rows())
    expected = sum(len(p.keys) for p in PRESETS.values()) * 4  # 2 toggles x 2 outputs
    assert len(rows) == expected
    assert {(r[0], r[1]) for r in rows} == {(p.id, k) for p in PRESETS.values() for k in p.keys}


@pytest.mark.parametrize(("preset", "key", "toggle", "gpio", "payload"), list(_golden_rows()))
def test_frame_matches_sca_command(preset, key, toggle, gpio, payload):
    assert encode_key(PRESETS[preset], key, toggle, gpio) == payload


def test_published_codes():
    """Samsung and LG power keys are their well-known 32-bit codes."""
    assert encode_key(PRESETS["samsung-tv"], "power")["data"] == "0xE0E040BF"
    assert encode_key(PRESETS["lg-tv"], "power")["data"] == "0x20DF10EF"
    assert encode_key(PRESETS["panasonic-tv"], "power")["data"] == "0x40040100BCBD"


def test_rc6_toggle_changes_the_frame():
    preset = PRESETS["philips-tv-rc6"]
    assert encode_key(preset, "volume_up", False) != encode_key(preset, "volume_up", True)


def test_gpio_omitted_when_none():
    assert "gpio" not in encode(IrProtocol.NEC, 0x04, 0x08)


@pytest.mark.parametrize(
    ("protocol", "address", "command"),
    [(IrProtocol.NEC, 0x100, 0), (IrProtocol.SONY12, 0x20, 0), (IrProtocol.RC5, 0, 0x80)],
)
def test_out_of_range_rejected(protocol, address, command):
    with pytest.raises(ValueError):
        encode(protocol, address, command)


def test_keys_are_known_and_ordered():
    for preset in PRESETS.values():
        assert set(preset.keys) <= set(KEYS)
        assert preset_keys(preset) == [k for k in KEYS if k in preset.keys]
    assert ESSENTIAL_KEYS <= set(KEYS)
