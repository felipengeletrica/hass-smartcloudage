"""Daikin ARC433 family, 280 bits (IRremoteESP8266 ``IRDaikinESP``).

A 5-bit zero preamble, then three sections of 8, 8 and 19 bytes (LSB first),
each closed by a 29 ms gap and ending with an 8-bit sum of its other bytes.
"""

from __future__ import annotations

from .base import (
    HvacProtocol,
    StdFan,
    StdMode,
    byte_segment,
    caps,
    frame,
    set_bit,
    set_bits,
    std_fan,
    std_mode,
    sum_bytes,
    turbo_requested,
    value_segment,
)
from .models import AcBrand, AcSwingHorizontal, AcSwingVertical

STATE_LENGTH = 35
SECTION1 = 8
SECTION2 = 8
SECTION3 = STATE_LENGTH - SECTION1 - SECTION2
HDR_MARK = 3650
HDR_SPACE = 1623
BIT_MARK = 428
ZERO_SPACE = 428
ONE_SPACE = 1280
GAP = 29000
PREAMBLE_BITS = 5

MODE_AUTO, MODE_DRY, MODE_COOL, MODE_HEAT, MODE_FAN = 0b000, 0b010, 0b011, 0b100, 0b110
FAN_MIN, FAN_MED, FAN_MAX, FAN_AUTO, FAN_QUIET = 1, 3, 5, 0b1010, 0b1011


class DaikinHvacProtocol(HvacProtocol):
    brand = AcBrand.DAIKIN
    default_model = "ARC433"
    capabilities = caps(10, 32, swing_v=True, swing_h=True, turbo=True, eco=True, clean=True, quiet=True)

    def build(self, state):
        s = self.normalize(state)
        self.validate(s)

        raw = bytearray(STATE_LENGTH)
        for index, value in {
            0: 0x11, 1: 0xDA, 2: 0x27, 4: 0xC5, 8: 0x11, 9: 0xDA, 10: 0x27, 12: 0x42,
            16: 0x11, 17: 0xDA, 18: 0x27, 21: 0x49, 22: 0x1E, 24: 0xB0, 27: 0x06,
            28: 0x60, 31: 0xC0,
        }.items():
            raw[index] = value

        set_bit(raw, 21, 0, s.power)
        set_bits(
            raw, 21, 4, 3,
            {
                StdMode.COOL: MODE_COOL,
                StdMode.HEAT: MODE_HEAT,
                StdMode.DRY: MODE_DRY,
                StdMode.FAN: MODE_FAN,
            }.get(std_mode(s.mode), MODE_AUTO),
        )
        raw[22] = max(10, min(s.temperature_c, 32)) * 2
        fan = {
            StdFan.MIN: FAN_QUIET,
            StdFan.LOW: FAN_MIN,
            StdFan.MEDIUM: FAN_MED,
            StdFan.HIGH: FAN_MAX - 1,
            StdFan.MAX: FAN_MAX,
        }.get(std_fan(s.fan_speed), FAN_AUTO)
        set_bits(raw, 24, 4, 4, _fan_bits(fan))
        set_bits(raw, 24, 0, 4, 0b1111 if s.swing_vertical != AcSwingVertical.OFF else 0)
        set_bits(raw, 25, 0, 4, 0b1111 if s.swing_horizontal != AcSwingHorizontal.OFF else 0)

        # setQuiet / setPowerful / setEcono are mutually exclusive, in IRac order.
        quiet = s.quiet
        powerful = turbo_requested(s)
        econo = s.eco
        if powerful:
            quiet = False
        if econo:
            powerful = False
        set_bit(raw, 29, 5, quiet)
        set_bit(raw, 29, 0, powerful)
        set_bit(raw, 32, 2, econo)
        set_bit(raw, 33, 1, s.clean)  # Mold

        raw[SECTION1 - 1] = sum_bytes(raw, 0, SECTION1 - 1)
        raw[SECTION1 + SECTION2 - 1] = sum_bytes(raw, SECTION1, SECTION2 - 1)
        raw[STATE_LENGTH - 1] = sum_bytes(raw, SECTION1 + SECTION2, SECTION3 - 1)

        segments = [
            value_segment(0, PREAMBLE_BITS, 0, 0, BIT_MARK, ZERO_SPACE + GAP),
            byte_segment(raw, 0, SECTION1, HDR_MARK, HDR_SPACE, BIT_MARK, ZERO_SPACE + GAP),
            byte_segment(raw, SECTION1, SECTION2, HDR_MARK, HDR_SPACE, BIT_MARK, ZERO_SPACE + GAP),
            byte_segment(raw, SECTION1 + SECTION2, SECTION3, HDR_MARK, HDR_SPACE, BIT_MARK, ZERO_SPACE + GAP),
        ]
        return frame(s, 38000, 50, "LSB", BIT_MARK, ONE_SPACE, ZERO_SPACE, segments)


def _fan_bits(fan):
    """setFan(): speeds 1..5 are stored as 3..7."""
    if fan in (FAN_QUIET, FAN_AUTO):
        return fan
    if fan < FAN_MIN or fan > FAN_MAX:
        return FAN_AUTO
    return 2 + fan
