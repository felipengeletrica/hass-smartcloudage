"""Panasonic 216-bit A/C (IRremoteESP8266 ``IRPanasonicAc``).

Two sections (8 + 19 bytes), LSB first, 36.7 kHz. The remote model (NKE,
DKE, JKE, LKE, CKP, RKR) changes fixed bytes and where quiet/powerful and the
horizontal vane live.
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
    turbo_requested,
)
from .models import AcBrand

MODEL_NKE = "NKE"
MODEL_DKE = "DKE"
MODEL_JKE = "JKE"
MODEL_LKE = "LKE"
MODEL_CKP = "CKP"
MODEL_RKR = "RKR"

STATE_LENGTH = 27
SECTION1 = 8
FREQUENCY = 36700
HDR_MARK = 3456
HDR_SPACE = 1728
BIT_MARK = 432
ONE_SPACE = 1296
ZERO_SPACE = 432
SECTION_GAP = 10000
MESSAGE_GAP = 100000

MODE_AUTO, MODE_DRY, MODE_COOL, MODE_HEAT, MODE_FAN = 0, 2, 3, 4, 6
FAN_MODE_TEMP = 27
SWING_V_AUTO = 0xF
SWING_H_MIDDLE = 0x6

KNOWN_GOOD_STATE = bytes(
    [
        0x02, 0x20, 0xE0, 0x04, 0x00, 0x00, 0x00, 0x06, 0x02,
        0x20, 0xE0, 0x04, 0x00, 0x00, 0x00, 0x80, 0x00, 0x00,
        0x00, 0x0E, 0xE0, 0x00, 0x00, 0x81, 0x00, 0x00, 0x00,
    ]
)


class PanasonicHvacProtocol(HvacProtocol):
    brand = AcBrand.PANASONIC
    models = (MODEL_NKE, MODEL_DKE, MODEL_JKE, MODEL_LKE, MODEL_CKP, MODEL_RKR)
    default_model = MODEL_NKE
    capabilities = caps(16, 30, swing_v=True, swing_h=True, turbo=True, quiet=True)

    def build(self, state):
        s = self.normalize(state)
        self.validate(s)
        model = self.resolve_model(s.model)

        raw = bytearray(KNOWN_GOOD_STATE)
        _set_model(raw, model, SWING_H_MIDDLE)
        set_bit(raw, 13, 0, s.power)

        # setMode() re-applies the remembered temperature (fan mode forces 27C).
        mode = {
            StdMode.COOL: MODE_COOL,
            StdMode.HEAT: MODE_HEAT,
            StdMode.DRY: MODE_DRY,
            StdMode.FAN: MODE_FAN,
        }.get(std_mode(s.mode), MODE_AUTO)
        _set_temp_bits(raw, FAN_MODE_TEMP if mode == MODE_FAN else 25)
        raw[13] &= 0x0F
        set_bits(raw, 13, 4, 3, mode)

        _set_temp_bits(raw, s.temperature_c)
        fan = {
            StdFan.MIN: 0,
            StdFan.LOW: 1,
            StdFan.MEDIUM: 2,
            StdFan.HIGH: 3,
            StdFan.MAX: 4,
        }.get(std_fan(s.fan_speed), 7)
        set_bits(raw, 16, 4, 4, fan + 3)
        set_bits(
            raw, 16, 0, 4,
            {
                StdSwingV.HIGHEST: 0x1,
                StdSwingV.HIGH: 0x2,
                StdSwingV.MIDDLE: 0x3,
                StdSwingV.LOW: 0x4,
                StdSwingV.LOWEST: 0x5,
            }.get(std_swing_v(s.swing_vertical), SWING_V_AUTO),
        )
        _set_swing_horizontal(
            raw,
            model,
            {
                StdSwingH.LEFT_MAX: 0x9,
                StdSwingH.LEFT: 0xA,
                StdSwingH.MIDDLE: 0x6,
                StdSwingH.RIGHT: 0xB,
                StdSwingH.RIGHT_MAX: 0xC,
            }.get(std_swing_h(s.swing_horizontal), 0xD),
        )

        # setQuiet() then setPowerful(): mutually exclusive, bit positions per model.
        ckp = model in (MODEL_CKP, MODEL_RKR)
        quiet_bit = 5 if ckp else 0
        powerful_bit = 0 if ckp else 5
        quiet = s.quiet
        powerful = turbo_requested(s)
        if quiet:
            set_bit(raw, 21, powerful_bit, False)
        set_bit(raw, 21, quiet_bit, quiet)
        if powerful:
            set_bit(raw, 21, quiet_bit, False)
        set_bit(raw, 21, powerful_bit, powerful)
        if model == MODEL_DKE:  # setIon(false) only touches DKE remotes.
            set_bit(raw, 22, 0, False)

        raw[STATE_LENGTH - 1] = sum_bytes(raw, 0, STATE_LENGTH - 1, 0xF4)

        segments = [
            byte_segment(raw, 0, SECTION1, HDR_MARK, HDR_SPACE, BIT_MARK, SECTION_GAP),
            byte_segment(raw, SECTION1, STATE_LENGTH - SECTION1, HDR_MARK, HDR_SPACE, BIT_MARK, MESSAGE_GAP),
        ]
        return frame(s, FREQUENCY, 50, "LSB", BIT_MARK, ONE_SPACE, ZERO_SPACE, segments)


def _set_model(raw, model, saved_swing_h):
    raw[13] &= 0xF0
    raw[17] = 0x00
    raw[21] &= 0b11101111
    raw[23] = 0x81
    raw[25] = 0x00
    if model == MODEL_LKE:
        raw[13] |= 0x02
        raw[17] = 0x06
    elif model == MODEL_DKE:
        raw[23] = 0x01
        raw[25] = 0x06
        _set_swing_horizontal(raw, model, saved_swing_h)
    elif model == MODEL_NKE:
        raw[17] = 0x06
    elif model == MODEL_CKP:
        raw[21] |= 0x10
        raw[23] = 0x01
    elif model == MODEL_RKR:
        raw[13] |= 0x08
        raw[23] = 0x89


def _set_temp_bits(raw, celsius):
    set_bits(raw, 14, 1, 5, max(16, min(celsius, 30)))


def _set_swing_horizontal(raw, model, direction):
    if model in (MODEL_NKE, MODEL_LKE):
        direction = SWING_H_MIDDLE
    elif model not in (MODEL_DKE, MODEL_RKR):
        return  # JKE / CKP have no horizontal vane.
    set_bits(raw, 17, 0, 4, direction)
