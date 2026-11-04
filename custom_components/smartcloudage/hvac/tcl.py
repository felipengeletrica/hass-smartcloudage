"""TCL 112-bit A/C (IRremoteESP8266 ``IRTcl112Ac``), also Teknopoint, Leberg
and the Electrolux EACM CL/N3 remote.

14 bytes, LSB first, 8-bit sum. Quiet is not offered: the remote sends it as
a separate message only when it changes.
"""

from __future__ import annotations

from .base import (
    HvacProtocol,
    StdFan,
    StdMode,
    StdSwingV,
    add_bytes,
    caps,
    frame,
    set_bit,
    set_bits,
    std_fan,
    std_mode,
    std_swing_v,
    sum_bytes,
    turbo_requested,
)
from .models import AcBrand, AcSwingHorizontal

MODEL_TAC09CHSD = "TAC09CHSD"
MODEL_GZ055BE1 = "GZ055BE1"

STATE_LENGTH = 14
HDR_MARK = 3000
HDR_SPACE = 1650
BIT_MARK = 500
ONE_SPACE = 1050
ZERO_SPACE = 325
GAP = 100000

MODE_HEAT, MODE_DRY, MODE_COOL, MODE_FAN, MODE_AUTO = 1, 2, 3, 7, 8
FAN_AUTO, FAN_MIN, FAN_LOW, FAN_MED, FAN_HIGH = 0, 1, 2, 3, 5
SWING_V_ON = 0b111

RESET = bytes([0x23, 0xCB, 0x26, 0x01, 0x00, 0x24, 0x03, 0x07, 0x40, 0x00, 0x00, 0x00, 0x00, 0x03])

CAPS_112 = caps(16, 31, swing_v=True, swing_h=True, turbo=True, eco=True, light=True)


class TclHvacProtocol(HvacProtocol):
    brand = AcBrand.TCL
    models = (MODEL_TAC09CHSD, MODEL_GZ055BE1)
    default_model = MODEL_TAC09CHSD
    capabilities = CAPS_112

    def build(self, state):
        s = self.normalize(state)
        self.validate(s)
        return encode_tcl112(s, self.resolve_model(s.model) != MODEL_GZ055BE1)


def encode_tcl112(s, is_tcl):
    """IRac::tcl112() with quiet and health off."""
    raw = bytearray(RESET)

    set_bit(raw, 12, 7, is_tcl)  # setModel
    set_bit(raw, 5, 2, s.power)
    mode = {
        StdMode.COOL: MODE_COOL,
        StdMode.HEAT: MODE_HEAT,
        StdMode.DRY: MODE_DRY,
        StdMode.FAN: MODE_FAN,
    }.get(std_mode(s.mode), MODE_AUTO)
    if mode == MODE_FAN:
        set_bits(raw, 8, 0, 3, FAN_HIGH)  # setMode(Fan) forces high
    set_bits(raw, 6, 0, 4, mode)

    half_degrees = max(16, min(s.temperature_c, 31)) * 2
    set_bit(raw, 12, 5, bool(half_degrees & 1))
    set_bits(raw, 7, 0, 4, 31 - half_degrees // 2)

    set_bits(
        raw, 8, 0, 3,
        {
            StdFan.MIN: FAN_MIN,
            StdFan.LOW: FAN_LOW,
            StdFan.MEDIUM: FAN_MED,
            StdFan.HIGH: FAN_HIGH,
            StdFan.MAX: FAN_HIGH,
        }.get(std_fan(s.fan_speed), FAN_AUTO),
    )
    set_bits(
        raw, 8, 3, 3,
        {
            StdSwingV.OFF: 0b000,
            StdSwingV.HIGHEST: 0b001,
            StdSwingV.HIGH: 0b010,
            StdSwingV.MIDDLE: 0b011,
            StdSwingV.LOW: 0b100,
            StdSwingV.LOWEST: 0b101,
        }.get(std_swing_v(s.swing_vertical), SWING_V_ON),
    )
    set_bit(raw, 12, 3, s.swing_horizontal != AcSwingHorizontal.OFF)

    turbo = turbo_requested(s)
    set_bit(raw, 6, 5, turbo)
    if turbo:
        set_bits(raw, 8, 0, 3, FAN_HIGH)
        set_bits(raw, 8, 3, 3, SWING_V_ON)
    set_bit(raw, 5, 6, not s.light)  # Light bit is cleared when the display is on.
    set_bit(raw, 5, 7, s.eco)
    set_bit(raw, 6, 4, False)  # Health

    raw[STATE_LENGTH - 1] = sum_bytes(raw, 0, STATE_LENGTH - 1)

    segments = []
    add_bytes(segments, raw, 0, STATE_LENGTH, HDR_MARK, HDR_SPACE, BIT_MARK, GAP)
    return frame(s, 38000, 50, "LSB", BIT_MARK, ONE_SPACE, ZERO_SPACE, segments)
