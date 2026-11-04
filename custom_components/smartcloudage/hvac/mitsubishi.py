"""Mitsubishi Electric 144-bit A/C (IRremoteESP8266 ``IRMitsubishiAC``).

18 bytes, LSB first, sent twice, each copy followed by a 440 us mark and a
15.5 ms space. The last byte is the 8-bit sum of the others.
"""

from __future__ import annotations

from .base import (
    HvacProtocol,
    StdFan,
    StdMode,
    StdSwingH,
    StdSwingV,
    byte_segment,
    caps,
    frame,
    set_bit,
    set_bits,
    std_fan,
    std_mode,
    std_swing_h,
    std_swing_v,
    sum_bytes,
)
from .models import AcBrand

STATE_LENGTH = 18
COPIES = 2
HDR_MARK = 3400
HDR_SPACE = 1750
BIT_MARK = 450
ONE_SPACE = 1300
ZERO_SPACE = 420
RPT_MARK = 440
RPT_SPACE = 15500

MODE_AUTO, MODE_COOL, MODE_DRY, MODE_HEAT, MODE_FAN = 0b100, 0b011, 0b010, 0b001, 0b111
FAN_AUTO, FAN_MAX, FAN_REAL_MAX, FAN_SILENT = 0, 5, 4, 6

RESET = bytes([0x23, 0xCB, 0x26, 0x01, 0x00, 0x20, 0x08, 0x06, 0x30, 0x45, 0x67])


class MitsubishiElectricHvacProtocol(HvacProtocol):
    brand = AcBrand.MITSUBISHI_ELECTRIC
    default_model = "MSZ"
    capabilities = caps(16, 31, swing_v=True, swing_h=True, quiet=True)

    def build(self, state):
        s = self.normalize(state)
        self.validate(s)

        raw = bytearray(STATE_LENGTH)
        raw[: len(RESET)] = RESET

        set_bit(raw, 5, 5, s.power)
        _set_mode(
            raw,
            {
                StdMode.COOL: MODE_COOL,
                StdMode.HEAT: MODE_HEAT,
                StdMode.DRY: MODE_DRY,
                StdMode.FAN: MODE_FAN,
            }.get(std_mode(s.mode), MODE_AUTO),
        )

        half_degrees = max(16, min(s.temperature_c, 31)) * 2
        set_bit(raw, 7, 4, bool(half_degrees & 1))
        set_bits(raw, 7, 0, 4, half_degrees // 2 - 16)
        set_bit(raw, 15, 5, False)  # iSave10C

        _set_fan(
            raw,
            {
                StdFan.MIN: FAN_SILENT,
                StdFan.LOW: FAN_REAL_MAX - 3,
                StdFan.MEDIUM: FAN_REAL_MAX - 2,
                StdFan.HIGH: FAN_REAL_MAX - 1,
                StdFan.MAX: FAN_REAL_MAX,
            }.get(std_fan(s.fan_speed), FAN_AUTO),
        )
        vane = {
            StdSwingV.HIGHEST: 0b001,
            StdSwingV.HIGH: 0b010,
            StdSwingV.MIDDLE: 0b011,
            StdSwingV.LOW: 0b100,
            StdSwingV.LOWEST: 0b101,
            StdSwingV.AUTO: 0b111,  # kMitsubishiAcVaneSwing
        }.get(std_swing_v(s.swing_vertical), 0b000)
        set_bit(raw, 9, 6, True)  # VaneBit
        set_bits(raw, 9, 3, 3, vane)
        set_bits(raw, 16, 3, 3, vane)  # VaneLeft
        set_bits(
            raw, 8, 4, 4,
            {
                StdSwingH.LEFT_MAX: 0b0001,
                StdSwingH.LEFT: 0b0010,
                StdSwingH.MIDDLE: 0b0011,
                StdSwingH.RIGHT: 0b0100,
                StdSwingH.RIGHT_MAX: 0b0101,
                StdSwingH.AUTO: 0b1000,
            }.get(std_swing_h(s.swing_horizontal), 0b0011),
        )
        if s.quiet:
            _set_fan(raw, FAN_SILENT)
        set_bit(raw, 15, 5, False)

        raw[STATE_LENGTH - 1] = sum_bytes(raw, 0, STATE_LENGTH - 1)

        segments = [
            byte_segment(raw, 0, STATE_LENGTH, HDR_MARK, HDR_SPACE, RPT_MARK, RPT_SPACE)
            for _ in range(COPIES)
        ]
        return frame(s, 38000, 50, "LSB", BIT_MARK, ONE_SPACE, ZERO_SPACE, segments)


def _set_mode(raw, mode):
    """setMode(): byte 8 is rewritten whole, so it runs before setWideVane()."""
    byte8 = {
        MODE_AUTO: 0b00110000,
        MODE_COOL: 0b00110110,
        MODE_DRY: 0b00110010,
        MODE_HEAT: 0b00110000,
        MODE_FAN: 0b00110111,
    }
    if mode not in byte8:
        mode = MODE_AUTO
    raw[8] = byte8[mode]
    set_bits(raw, 6, 3, 3, mode)


def _set_fan(raw, speed):
    fan = FAN_MAX if speed > FAN_SILENT else speed
    set_bit(raw, 9, 7, fan == FAN_AUTO)
    if fan >= FAN_MAX:
        fan -= 1
    set_bits(raw, 9, 0, 3, fan)
