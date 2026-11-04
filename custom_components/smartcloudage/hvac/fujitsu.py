"""Fujitsu General A/C (IRremoteESP8266 ``IRFujitsuAC``).

A state change is a long code (16 bytes, 15 for ARDB1/ARJW2) with the
"turn on" command; power off is a 7-byte (6) short code. LSB first.
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
)
from .models import AcBrand, AcSwingHorizontal, AcSwingVertical

MODEL_ARRAH2E = "ARRAH2E"
MODEL_ARDB1 = "ARDB1"
MODEL_ARREB1E = "ARREB1E"
MODEL_ARJW2 = "ARJW2"
MODEL_ARRY4 = "ARRY4"
MODEL_ARREW4E = "ARREW4E"

STATE_LENGTH = 16
STATE_LENGTH_SHORT = 7
HDR_MARK = 3324
HDR_SPACE = 1574
BIT_MARK = 448
ONE_SPACE = 1182
ZERO_SPACE = 390
MIN_GAP = 8100

CMD_TURN_OFF = 0x02
MODE_AUTO, MODE_COOL, MODE_DRY, MODE_FAN, MODE_HEAT = 0, 1, 2, 3, 4
FAN_AUTO, FAN_HIGH, FAN_MED, FAN_LOW, FAN_QUIET = 0, 1, 2, 3, 4
SWING_OFF, SWING_VERT, SWING_HORIZ, SWING_BOTH = 0, 1, 2, 3


class FujitsuHvacProtocol(HvacProtocol):
    brand = AcBrand.FUJITSU
    models = (MODEL_ARRAH2E, MODEL_ARDB1, MODEL_ARREB1E, MODEL_ARJW2, MODEL_ARRY4, MODEL_ARREW4E)
    default_model = MODEL_ARRAH2E
    capabilities = caps(16, 30, swing_v=True, swing_h=True, clean=True, quiet=True)

    def build(self, state):
        s = self.normalize(state)
        self.validate(s)
        model = self.resolve_model(s.model)

        short_model = model in (MODEL_ARDB1, MODEL_ARJW2)
        length = STATE_LENGTH - 1 if short_model else STATE_LENGTH
        length_short = STATE_LENGTH_SHORT - 1 if short_model else STATE_LENGTH_SHORT

        raw = _reset_state(model)
        if s.power:
            mode = {
                StdMode.COOL: MODE_COOL,
                StdMode.HEAT: MODE_HEAT,
                StdMode.DRY: MODE_DRY,
                StdMode.FAN: MODE_FAN,
            }.get(std_mode(s.mode), MODE_AUTO)
            set_bits(raw, 9, 0, 3, min(mode, MODE_HEAT))
            _set_temp(raw, model, s.temperature_c)
            set_bits(
                raw, 10, 0, 3,
                {
                    StdFan.MIN: FAN_QUIET,
                    StdFan.LOW: FAN_LOW,
                    StdFan.MEDIUM: FAN_MED,
                    StdFan.HIGH: FAN_HIGH,
                    StdFan.MAX: FAN_HIGH,
                }.get(std_fan(s.fan_speed), FAN_AUTO),
            )
            swing = SWING_OFF
            if s.swing_vertical != AcSwingVertical.OFF:
                swing |= SWING_VERT
            if s.swing_horizontal != AcSwingHorizontal.OFF:
                swing |= SWING_HORIZ
            _set_swing(raw, model, swing)
            if s.quiet:
                set_bits(raw, 10, 0, 3, FAN_QUIET)
            set_bit(raw, 14, 3, False)  # Filter
            set_bit(raw, 9, 3, s.clean)
            code = _long_code(raw, model, length, length_short)
        else:
            raw[5] = CMD_TURN_OFF
            code = bytearray(raw[:length_short])
            if not short_model:
                code[length_short - 1] = ~code[length_short - 2] & 0xFF

        segments = [byte_segment(code, 0, len(code), HDR_MARK, HDR_SPACE, BIT_MARK, MIN_GAP)]
        return frame(s, 38000, 50, "LSB", BIT_MARK, ONE_SPACE, ZERO_SPACE, segments)


def _reset_state(model):
    raw = bytearray(STATE_LENGTH)
    _set_temp(raw, model, 24)
    set_bits(raw, 10, 0, 3, FAN_HIGH)
    set_bits(raw, 9, 0, 3, MODE_COOL)
    set_bits(raw, 10, 4, 2, SWING_BOTH)
    raw[0] = 0x14
    raw[1] = 0x63
    raw[3] = 0x10
    raw[4] = 0x10
    return raw


def _set_temp(raw, model, celsius):
    c = max(16, min(celsius, 30))
    set_bit(raw, 8, 1, False)  # Celsius
    set_bits(raw, 8, 2, 6, (c - 8) * 2 if model == MODEL_ARREW4E else (c - 16) * 4)


def _set_swing(raw, model, swing):
    if model in (MODEL_ARDB1, MODEL_ARREB1E, MODEL_ARRY4):
        swing = min(swing, SWING_VERT)
    else:
        swing = min(swing, SWING_BOTH)
    set_bits(raw, 10, 4, 2, swing)


def _long_code(raw, model, length, length_short):
    """checkSum() for a long "turn on" code."""
    raw[5] = 0xFC if model in (MODEL_ARDB1, MODEL_ARJW2) else 0xFE
    raw[6] = length - 7
    raw[7] = 0x31 if model == MODEL_ARREW4E else 0x30
    set_bit(raw, 8, 0, True)  # Power (turn on)

    if model not in (MODEL_ARREB1E, MODEL_ARREW4E):
        set_bit(raw, 14, 7, False)  # OutsideQuiet
        if model != MODEL_ARRAH2E:
            set_bits(raw, 9, 4, 2, 0)  # TimerType = stop
    if model != MODEL_ARRY4:
        if model not in (MODEL_ARRAH2E, MODEL_ARREW4E):
            set_bit(raw, 9, 3, False)  # Clean
        set_bit(raw, 14, 3, False)  # Filter
    raw[11] = raw[12] = raw[13] = 0  # No timers.

    complement = 0
    if model in (MODEL_ARDB1, MODEL_ARJW2):
        set_bits(raw, 10, 4, 2, SWING_OFF)
        checksum = sum_bytes(raw, 0, length - 1)
        complement = 0x9B
    else:
        if model in (MODEL_ARREB1E, MODEL_ARRAH2E, MODEL_ARRY4):
            set_bit(raw, 14, 5, True)
        checksum = sum_bytes(raw, length_short, length - length_short - 1)
    raw[length - 1] = (complement - checksum) & 0xFF
    return bytearray(raw[:length])
