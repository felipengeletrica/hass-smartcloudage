"""The Python A/C encoders must reproduce the Command Center frames exactly.

``fixtures/hvac_golden.tsv`` holds vectors from the Command Center
``tools/ir-verify/gen_vectors.py`` and, for each one, the command-32 message
printed by its C# runner (``tools/ir-verify/runner``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.smartcloudage.hvac import (
    PROTOCOLS,
    AcBrand,
    AcCommandState,
    AcFanSpeed,
    AcMode,
    AcSwingHorizontal,
    AcSwingVertical,
    build_payload,
)

GOLDEN = Path(__file__).parent / "fixtures" / "hvac_golden.tsv"


def _state(vector: str) -> AcCommandState:
    f = vector.split(" ")
    flag = lambda i: f[i] == "1"  # noqa: E731
    return AcCommandState(
        AcBrand(f[0]),
        f[1].replace("_", " "),
        f[2] == "1",
        AcMode(f[3]),
        int(f[4]),
        AcFanSpeed(f[5]),
        swing_vertical=AcSwingVertical(f[6]),
        swing_horizontal=AcSwingHorizontal(f[7]),
        quiet=flag(8),
        turbo=flag(9),
        eco=flag(10),
        clean=flag(11),
        light=flag(12),
        sleep=flag(13),
        sensor_ifeel=flag(14),
    )


def _cases():
    for line in GOLDEN.read_text().splitlines():
        vector, expected = line.split("\t")
        yield pytest.param(vector, expected, id=vector)


@pytest.mark.parametrize(("vector", "expected"), _cases())
def test_frame_matches_command_center(vector, expected):
    """Same JSON, byte for byte, as the C# encoder for the same state."""
    payload = build_payload(_state(vector))
    assert '{"payload":' + json.dumps(payload, separators=(",", ":")) + "}" == expected


def test_every_brand_and_model_is_covered():
    covered = {tuple(line.split(" ")[:2]) for line in GOLDEN.read_text().splitlines()}
    for brand, protocol in PROTOCOLS.items():
        for model in protocol.models:
            assert (brand.value, model.replace(" ", "_")) in covered


def test_unsupported_feature_is_rejected():
    state = AcCommandState(AcBrand.LG, "", True, AcMode.COOL, 24, AcFanSpeed.AUTO, turbo=True)
    with pytest.raises(ValueError, match="turbo"):
        build_payload(state)


def test_gpio_and_power_off_normalization():
    state = AcCommandState(
        AcBrand.SAMSUNG, "AR", False, AcMode.COOL, 40, AcFanSpeed.AUTO, gpio=26, turbo=True
    )
    payload = build_payload(state)
    assert payload["gpio"] == 26
    assert payload["mode"] == "protocol"
    # Power off clears turbo instead of rejecting it, and clamps the temperature.
    assert payload == build_payload(
        AcCommandState(AcBrand.SAMSUNG, "AR", False, AcMode.OFF, 30, AcFanSpeed.AUTO, gpio=26)
    )
