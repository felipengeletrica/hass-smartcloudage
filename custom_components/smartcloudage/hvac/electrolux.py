"""Electrolux: two remote families with different protocols.

* YKR-H/531E: AUX/Electra 104-bit (IRremoteESP8266 ``IRElectraAc``).
* EACM CL/N3: TCL 112-bit (see :mod:`.tcl`).
"""

from __future__ import annotations

from .base import (
    HvacProtocol,
    StdFan,
    StdMode,
    add_bytes,
    caps,
    frame,
    set_bit,
    set_bits,
    std_fan,
    std_mode,
    sum_bytes,
    turbo_requested,
)
from .models import AcBrand, AcSwingHorizontal, AcSwingVertical
from .tcl import CAPS_112, encode_tcl112

MODEL_YKR_H_531E = "YKR-H/531E"
MODEL_EACM_CL_N3 = "EACM CL/N3"

STATE_LENGTH = 13
HDR_MARK = 9166
HDR_SPACE = 4470
BIT_MARK = 646
ONE_SPACE = 1647
ZERO_SPACE = 547
MESSAGE_GAP = 100000

MODE_AUTO, MODE_COOL, MODE_DRY, MODE_HEAT, MODE_FAN = 0b000, 0b001, 0b010, 0b100, 0b110
FAN_AUTO, FAN_LOW, FAN_MED, FAN_HIGH = 0b101, 0b011, 0b010, 0b001
SWING_ON, SWING_OFF = 0b000, 0b111
LIGHT_TOGGLE_OFF = 0x08

ELECTRA_CAPS = caps(16, 32, swing_v=True, swing_h=True, turbo=True, clean=True, quiet=True)


class ElectroluxHvacProtocol(HvacProtocol):
    brand = AcBrand.ELECTROLUX
    models = (MODEL_YKR_H_531E, MODEL_EACM_CL_N3)
    default_model = MODEL_YKR_H_531E
    capabilities = ELECTRA_CAPS

    def capabilities_for(self, model):
        return CAPS_112 if self.resolve_model(model) == MODEL_EACM_CL_N3 else ELECTRA_CAPS

    def build(self, state):
        s = self.normalize(state)
        self.validate(s)

        if self.resolve_model(s.model) == MODEL_EACM_CL_N3:
            return encode_tcl112(s, True)

        # IRac::electra() with no sensor temperature, I-Feel and light toggle off.
        raw = bytearray(STATE_LENGTH)
        raw[0] = 0xC3
        raw[11] = LIGHT_TOGGLE_OFF

        set_bit(raw, 9, 5, s.power)
        set_bits(
            raw, 6, 5, 3,
            {
                StdMode.COOL: MODE_COOL,
                StdMode.HEAT: MODE_HEAT,
                StdMode.DRY: MODE_DRY,
                StdMode.FAN: MODE_FAN,
            }.get(std_mode(s.mode), MODE_AUTO),
        )
        set_bits(raw, 1, 3, 5, max(16, min(s.temperature_c, 32)) - 8)
        set_bits(
            raw, 4, 5, 3,
            {
                StdFan.MIN: FAN_LOW,
                StdFan.LOW: FAN_LOW,
                StdFan.MEDIUM: FAN_MED,
                StdFan.HIGH: FAN_HIGH,
                StdFan.MAX: FAN_HIGH,
            }.get(std_fan(s.fan_speed), FAN_AUTO),
        )
        set_bits(raw, 1, 0, 3, SWING_ON if s.swing_vertical != AcSwingVertical.OFF else SWING_OFF)
        set_bits(raw, 2, 5, 3, SWING_ON if s.swing_horizontal != AcSwingHorizontal.OFF else SWING_OFF)
        set_bit(raw, 5, 7, s.quiet)
        set_bit(raw, 5, 6, turbo_requested(s))
        set_bit(raw, 9, 2, s.clean)
        set_bit(raw, 6, 3, False)  # I-Feel off: sensor temperature cleared
        raw[7] = 0

        raw[STATE_LENGTH - 1] = sum_bytes(raw, 0, STATE_LENGTH - 1)

        segments = []
        add_bytes(segments, raw, 0, STATE_LENGTH, HDR_MARK, HDR_SPACE, BIT_MARK, MESSAGE_GAP)
        return frame(s, 38000, 50, "LSB", BIT_MARK, ONE_SPACE, ZERO_SPACE, segments)
