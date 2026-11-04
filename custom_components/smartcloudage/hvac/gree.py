"""Gree / Elgin 64-bit A/C (IRremoteESP8266 ``IRGreeAC``).

Two 4-byte blocks, LSB first: block 1, the 3-bit connector ``010``, a 20 ms
message space, then block 2.
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
    get_bits,
    set_bit,
    set_bits,
    std_fan,
    std_mode,
    std_swing_h,
    std_swing_v,
    turbo_requested,
    value_segment,
)
from .models import AcBrand

MODEL_YAW1F = "YAW1F"
MODEL_YBOFB = "YBOFB"
MODEL_YX1FSF = "YX1FSF"

STATE_LENGTH = 8
HDR_MARK = 9000
HDR_SPACE = 4500
BIT_MARK = 620
ONE_SPACE = 1600
ZERO_SPACE = 540
MSG_SPACE = 19980
BLOCK_FOOTER = 0b010

MODE_AUTO, MODE_COOL, MODE_DRY, MODE_FAN, MODE_HEAT, MODE_ECONO = 0, 1, 2, 3, 4, 5
FAN_AUTO, FAN_MIN, FAN_MAX = 0, 1, 3


class GreeHvacProtocol(HvacProtocol):
    brand = AcBrand.GREE
    models = (MODEL_YAW1F, MODEL_YBOFB, MODEL_YX1FSF)
    default_model = MODEL_YAW1F
    capabilities = caps(
        16, 30, swing_v=True, swing_h=True, turbo=True, eco=True, sleep=True,
        light=True, clean=True, ifeel=True,
    )

    def build(self, state):
        s = self.normalize(state)
        self.validate(s)
        model = self.resolve_model(s.model)

        raw = bytearray(STATE_LENGTH)  # stateReset()
        set_bits(raw, 1, 0, 4, 9)
        set_bit(raw, 2, 5, True)
        set_bits(raw, 3, 4, 4, 5)
        set_bits(raw, 5, 3, 3, 4)

        mode = {
            StdMode.COOL: MODE_COOL,
            StdMode.HEAT: MODE_HEAT,
            StdMode.DRY: MODE_DRY,
            StdMode.FAN: MODE_FAN,
        }.get(std_mode(s.mode), MODE_AUTO)
        set_bit(raw, 0, 3, s.power)
        _set_mode(raw, mode)
        _set_temp(raw, s.temperature_c)
        _set_fan(
            raw,
            {
                StdFan.MIN: FAN_MIN,
                StdFan.LOW: FAN_MAX - 1,
                StdFan.MEDIUM: FAN_MAX - 1,
                StdFan.HIGH: FAN_MAX,
                StdFan.MAX: FAN_MAX,
            }.get(std_fan(s.fan_speed), FAN_AUTO),
        )

        swing_v = std_swing_v(s.swing_vertical)
        _set_swing_vertical(
            raw,
            swing_v == StdSwingV.AUTO,
            {
                StdSwingV.OFF: 0,
                StdSwingV.HIGHEST: 2,
                StdSwingV.HIGH: 3,
                StdSwingV.MIDDLE: 4,
                StdSwingV.LOW: 5,
                StdSwingV.LOWEST: 6,
            }.get(swing_v, 1),
        )
        set_bits(
            raw, 4, 4, 3,
            {
                StdSwingH.AUTO: 1,
                StdSwingH.LEFT_MAX: 2,
                StdSwingH.LEFT: 3,
                StdSwingH.MIDDLE: 4,
                StdSwingH.RIGHT: 5,
                StdSwingH.RIGHT_MAX: 6,
            }.get(std_swing_h(s.swing_horizontal), 0),
        )
        set_bit(raw, 5, 2, s.sensor_ifeel)
        set_bit(raw, 2, 5, s.light)
        set_bit(raw, 2, 4, turbo_requested(s))
        set_bit(raw, 7, 2, s.eco)
        if s.eco and model == MODEL_YX1FSF:
            _set_mode(raw, MODE_ECONO)
        set_bit(raw, 2, 7, s.clean)  # X-Fan
        set_bit(raw, 0, 7, s.sleep)

        # fixup(): ModelA only when powered on with a YAW1F remote.
        set_bit(raw, 2, 6, s.power and model == MODEL_YAW1F)
        set_bits(raw, 7, 4, 4, _checksum(raw))

        segments = [
            byte_segment(raw, 0, 4, HDR_MARK, HDR_SPACE, 0, 0),
            value_segment(BLOCK_FOOTER, 3, 0, 0, BIT_MARK, MSG_SPACE),
            byte_segment(raw, 4, 4, 0, 0, BIT_MARK, MSG_SPACE),
        ]
        return frame(s, 38000, 50, "LSB", BIT_MARK, ONE_SPACE, ZERO_SPACE, segments)


def _mode(raw):
    return get_bits(raw[0], 0, 3)


def _set_mode(raw, mode):
    if mode == MODE_AUTO:
        _set_temp(raw, 25)
    elif mode == MODE_DRY:
        _set_fan(raw, 1)
    elif mode not in (MODE_COOL, MODE_FAN, MODE_ECONO, MODE_HEAT):
        mode = MODE_AUTO
    set_bits(raw, 0, 0, 3, mode)


def _set_temp(raw, celsius):
    c = max(16, min(celsius, 30))
    if _mode(raw) == MODE_AUTO:
        c = 25
    set_bit(raw, 3, 3, False)  # UseFahrenheit
    set_bits(raw, 1, 0, 4, c - 16)
    set_bit(raw, 3, 2, False)  # TempExtraDegreeF


def _set_fan(raw, speed):
    fan = min(FAN_MAX, speed)
    if _mode(raw) == MODE_DRY:
        fan = 1
    set_bits(raw, 0, 4, 2, fan)


def _set_swing_vertical(raw, automatic, position):
    set_bit(raw, 0, 6, automatic)
    p = position
    if not automatic:
        if p not in (0, 2, 3, 4, 5, 6):
            p = 0
    elif p not in (1, 7, 9, 11):
        p = 1
    set_bits(raw, 4, 0, 4, p)


def _checksum(raw):
    """IRKelvinatorAC::calcBlockChecksum() over the 8-byte state."""
    total = 10 + sum(raw[i] & 0x0F for i in range(4)) + sum(raw[i] >> 4 for i in range(4, STATE_LENGTH - 1))
    return total & 0x0F
