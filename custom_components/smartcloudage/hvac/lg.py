"""LG 28-bit A/C (IRremoteESP8266 ``IRLgAc``).

Frame (MSB first): Sign 0x88 [27:20] | Power [19:18] | Mode [14:12] |
Temp-15 [11:8] | Fan [7:4] | nibble checksum [3:0]. GE6711AR2853M is "LG"
(8500/4250 header); AKB75215403 is "LG2" (3200/9900 header, Brazilian Dual
Inverter line).
"""

from __future__ import annotations

from .base import HvacProtocol, StdFan, StdMode, caps, frame, std_fan, std_mode, value_segment
from .models import AcBrand

MODEL_GE6711AR2853M = "GE6711AR2853M"
MODEL_AKB75215403 = "AKB75215403"

BITS = 28
OFF_COMMAND = 0x88C0051
SIGNATURE = 0x88
BIT_MARK = 550
ONE_SPACE = 1600
ZERO_SPACE = 550
HDR_MARK = 8500
HDR_SPACE = 4250
LG2_HDR_MARK = 3200
LG2_HDR_SPACE = 9900
LG2_BIT_MARK = 480
MIN_GAP = 39750
MIN_MESSAGE_LENGTH = 108050


class LgHvacProtocol(HvacProtocol):
    brand = AcBrand.LG
    models = (MODEL_GE6711AR2853M, MODEL_AKB75215403)
    default_model = MODEL_GE6711AR2853M
    capabilities = caps(16, 30)

    def build(self, state):
        s = self.normalize(state)
        self.validate(s)

        lg2 = _is_lg2(s.model)
        code = _encode(s) if s.power else OFF_COMMAND

        hdr_mark = LG2_HDR_MARK if lg2 else HDR_MARK
        hdr_space = LG2_HDR_SPACE if lg2 else HDR_SPACE
        bit_mark = LG2_BIT_MARK if lg2 else BIT_MARK

        # sendGeneric() pads the message to kLgMinMessageLength.
        elapsed = hdr_mark + hdr_space + bit_mark
        for i in range(BITS):
            elapsed += bit_mark + (ONE_SPACE if (code >> i) & 1 else ZERO_SPACE)
        gap = max(MIN_GAP, MIN_MESSAGE_LENGTH - elapsed)

        segments = [value_segment(code, BITS, hdr_mark, hdr_space, bit_mark, gap)]
        return frame(s, 38000, 33 if lg2 else 50, "MSB", bit_mark, ONE_SPACE, ZERO_SPACE, segments)


def _encode(s):
    mode = {StdMode.COOL: 0, StdMode.DRY: 1, StdMode.FAN: 2, StdMode.HEAT: 4}.get(std_mode(s.mode), 3)
    # convertFan() then setFan(): kHigh collapses to Max.
    fan = {StdFan.MIN: 0, StdFan.LOW: 1, StdFan.MEDIUM: 2, StdFan.HIGH: 4, StdFan.MAX: 4}.get(
        std_fan(s.fan_speed), 5
    )
    temp = max(16, min(s.temperature_c, 30)) - 15
    code = (SIGNATURE << 20) | (mode << 12) | (temp << 8) | (fan << 4)
    return code | _checksum(code)


def _checksum(code):
    """Sum of the six data nibbles above the checksum, mod 16."""
    return sum((code >> shift) & 0xF for shift in range(4, BITS, 4)) & 0xF


def _is_lg2(model):
    if not model or not model.strip():
        return False
    m = model.strip().upper()
    return m.startswith((MODEL_AKB75215403, "S4NW", "S4UW")) or "DUAL" in m
