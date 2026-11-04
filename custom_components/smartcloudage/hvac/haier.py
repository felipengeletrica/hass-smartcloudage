"""Haier A/C families (IRremoteESP8266 ``IRHaierAC``, ``IRHaierACYRW02``,
``IRHaierAC176`` and ``IRHaierAC160``).

* YR-W02 (HSU-09HMC203): 112 bits.
* V9014557 A / V9014557 B (also Mabe, Daichi): 176 bits.
* KFR-26GW: 160 bits.
* HSU07-HEA03: 72 bits (older line).

All are MSB first after a 3 ms mark/space preamble.
"""

from __future__ import annotations

from .base import (
    HvacProtocol,
    StdFan,
    StdMode,
    StdSwingH,
    StdSwingV,
    add_bytes,
    caps,
    frame,
    get_bits,
    marks,
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

MODEL_YRW02 = "YR-W02"
MODEL_V9014557_A = "V9014557 A"
MODEL_V9014557_B = "V9014557 B"
MODEL_KFR26GW = "KFR-26GW"
MODEL_HSU07 = "HSU07-HEA03"

HDR = 3000
HDR_GAP = 4300
BIT_MARK = 520
ONE_SPACE = 1650
ZERO_SPACE = 650
MIN_GAP = 150000

YRW02_CAPS = caps(16, 30, swing_v=True, swing_h=True, turbo=True, sleep=True, quiet=True)
AC160_CAPS = caps(16, 30, swing_v=True, turbo=True, sleep=True, clean=True, quiet=True)
AC_CAPS = caps(16, 30, swing_v=True, sleep=True)

Y_AUTO, Y_COOL, Y_DRY, Y_HEAT, Y_FAN = 0, 1, 2, 4, 6
Y_FAN_HIGH, Y_FAN_MED, Y_FAN_LOW, Y_FAN_AUTO = 1, 2, 3, 5
Y_MODEL_A, Y_MODEL_B = 0xA6, 0x59
BUTTON_TEMP_UP, BUTTON_TEMP_DOWN, BUTTON_SWING_V, BUTTON_SWING_H, BUTTON_FAN = 0, 1, 2, 3, 4
BUTTON_POWER, BUTTON_MODE, BUTTON_HEALTH, BUTTON_TURBO, BUTTON_SLEEP = 5, 6, 7, 8, 11
BUTTON_CFAB, BUTTON_CLEAN160 = 26, 25


class HaierHvacProtocol(HvacProtocol):
    brand = AcBrand.HAIER
    models = (MODEL_YRW02, MODEL_V9014557_A, MODEL_V9014557_B, MODEL_KFR26GW, MODEL_HSU07)
    default_model = MODEL_YRW02
    capabilities = YRW02_CAPS

    def capabilities_for(self, model):
        return {MODEL_KFR26GW: AC160_CAPS, MODEL_HSU07: AC_CAPS}.get(self.resolve_model(model), YRW02_CAPS)

    def build(self, state):
        s = self.normalize(state)
        self.validate(s)

        model = self.resolve_model(s.model)
        if model == MODEL_HSU07:
            raw = _encode_ac(s)
        elif model == MODEL_KFR26GW:
            raw = _encode_ac160(s)
        elif model == MODEL_V9014557_A:
            raw = _encode_ac176(s, False, 22)
        elif model == MODEL_V9014557_B:
            raw = _encode_ac176(s, True, 22)
        else:
            raw = _encode_ac176(s, False, 14)  # YR-W02 is the first 14 bytes

        segments = [marks(HDR, HDR)]
        add_bytes(segments, raw, 0, len(raw), HDR, HDR_GAP, BIT_MARK, MIN_GAP)
        return frame(s, 38000, 50, "MSB", BIT_MARK, ONE_SPACE, ZERO_SPACE, segments)


def _mode_y(s):
    return {
        StdMode.COOL: Y_COOL,
        StdMode.HEAT: Y_HEAT,
        StdMode.DRY: Y_DRY,
        StdMode.FAN: Y_FAN,
    }.get(std_mode(s.mode), Y_AUTO)


def _fan_y(s):
    return {
        StdFan.MIN: Y_FAN_LOW,
        StdFan.LOW: Y_FAN_LOW,
        StdFan.MEDIUM: Y_FAN_MED,
        StdFan.HIGH: Y_FAN_HIGH,
        StdFan.MAX: Y_FAN_HIGH,
    }.get(std_fan(s.fan_speed), Y_FAN_AUTO)


class _YState:
    """Common YRW02-family setters on a 176/160 state."""

    def __init__(self, length, is160):
        self.raw = bytearray(length)
        self._is160 = is160

    @property
    def mode(self):
        return get_bits(self.raw[7], 5, 3)

    @mode.setter
    def mode(self, value):
        set_bits(self.raw, 7, 5, 3, value)

    def button(self, value):
        set_bits(self.raw, 12, 0, 5, value)

    @property
    def temp(self):
        return get_bits(self.raw[1], 4, 4) + 16

    def set_mode(self, mode):
        if mode in (Y_AUTO, Y_DRY, Y_FAN):
            set_bit(self.raw, 6, 6, False)  # Turbo
            set_bit(self.raw, 6, 7, False)  # Quiet
            self.button(BUTTON_MODE)
            self.mode = mode
        elif mode in (Y_COOL, Y_HEAT):
            self.button(BUTTON_MODE)
            self.mode = mode
        else:
            self.set_mode(Y_AUTO)
        if self._is160:
            set_bit(self.raw, 4, 7, self.mode == Y_HEAT)  # AuxHeating

    def set_temp(self, celsius):
        if self.temp == celsius:
            return
        self.button(BUTTON_TEMP_DOWN if self.temp > celsius else BUTTON_TEMP_UP)
        set_bit(self.raw, 10, 5, False)  # Celsius
        set_bits(self.raw, 1, 4, 4, max(16, min(celsius, 30)) - 16)

    def set_fan(self, speed):
        set_bits(self.raw, 5, 5, 3, speed)
        fan2 = 0 if speed == Y_FAN_AUTO else speed
        if self._is160:
            set_bits(self.raw, 16, 5, 3, fan2)
        elif len(self.raw) > 14:
            set_bits(self.raw, 16, 6, 2, fan2)
        self.button(BUTTON_FAN)

    def set_turbo(self, on):
        if self.mode not in (Y_COOL, Y_HEAT):
            return
        set_bit(self.raw, 6, 6, on)
        self.button(BUTTON_TURBO)
        if on:
            set_bit(self.raw, 6, 7, False)

    def set_quiet(self, on):
        if self.mode not in (Y_COOL, Y_HEAT):
            return
        set_bit(self.raw, 6, 7, on)
        self.button(BUTTON_TURBO)
        if on:
            set_bit(self.raw, 6, 6, False)

    def set_health(self, on):
        self.button(BUTTON_HEALTH)
        set_bit(self.raw, 3, 1, on)

    def set_sleep(self, on):
        self.button(BUTTON_SLEEP)
        set_bit(self.raw, 8, 7, on)

    def set_power(self, on):
        self.button(BUTTON_POWER)
        set_bit(self.raw, 4, 6, on)

    def checksum(self):
        """Sum of bytes 0-12 in byte 13; sum of the extension in the last byte."""
        self.raw[13] = sum_bytes(self.raw, 0, 13)
        if len(self.raw) > 14:
            self.raw[-1] = sum_bytes(self.raw, 14, len(self.raw) - 14 - 1)


def _encode_ac176(s, model_b, length):
    # A YR-W02 object is an AC176 object that sends only 14 bytes.
    st = _YState(22, False)
    st.raw[0] = Y_MODEL_A
    st.raw[14] = 0xB7  # Prefix2
    set_bits(st.raw, 1, 4, 4, 25 - 16)
    set_bit(st.raw, 3, 1, True)  # Health
    st.set_fan(Y_FAN_AUTO)
    set_bit(st.raw, 4, 6, True)
    st.button(BUTTON_POWER)

    if length == 22:
        st.button(BUTTON_CFAB)  # setModel()
        st.raw[0] = Y_MODEL_B if model_b else Y_MODEL_A

    # IRac::haier176() / haierYrwo2().
    st.set_mode(_mode_y(s))
    set_bit(st.raw, 10, 5, False)
    st.set_temp(s.temperature_c)
    st.set_fan(_fan_y(s))

    swing_v = {
        StdSwingV.HIGHEST: 0x1,
        StdSwingV.HIGH: 0x1,
        StdSwingV.MIDDLE: 0x2,
        StdSwingV.LOW: 0xA,
        StdSwingV.LOWEST: 0x3,
        StdSwingV.OFF: 0x0,
    }.get(std_swing_v(s.swing_vertical), 0xC)
    st.button(BUTTON_SWING_V)
    if swing_v == 0x2 and st.mode == Y_HEAT:
        swing_v = 0x3
    if swing_v == 0x3 and st.mode != Y_HEAT:
        swing_v = 0x2
    set_bits(st.raw, 1, 0, 4, swing_v)

    swing_h = {
        StdSwingH.LEFT_MAX: 0x3,
        StdSwingH.LEFT: 0x4,
        StdSwingH.RIGHT: 0x5,
        StdSwingH.RIGHT_MAX: 0x6,
        StdSwingH.AUTO: 0x7,
    }.get(std_swing_h(s.swing_horizontal), 0x0)
    st.button(BUTTON_SWING_H)
    set_bits(st.raw, 2, 5, 3, swing_h)

    st.set_quiet(s.quiet)
    st.set_turbo(turbo_requested(s))
    st.set_health(False)
    st.set_sleep(s.sleep)
    st.set_power(s.power)
    st.checksum()
    return st.raw[:length]


def _encode_ac160(s):
    st = _YState(20, True)
    st.raw[0] = Y_MODEL_A
    st.raw[14] = 0xB5  # Prefix
    set_bits(st.raw, 1, 4, 4, 25 - 16)
    _set_clean160(st, False)
    st.set_fan(Y_FAN_AUTO)
    set_bit(st.raw, 4, 6, True)
    st.button(BUTTON_POWER)

    # IRac::haier160().
    st.set_mode(_mode_y(s))
    set_bit(st.raw, 10, 5, False)
    st.set_temp(s.temperature_c)
    st.set_fan(_fan_y(s))
    swing_v = {
        StdSwingV.HIGHEST: 0b0001,
        StdSwingV.HIGH: 0b0100,
        StdSwingV.MIDDLE: 0b0110,
        StdSwingV.LOW: 0b1000,
        StdSwingV.LOWEST: 0b0011,
        StdSwingV.OFF: 0b0000,
    }.get(std_swing_v(s.swing_vertical), 0b1100)
    st.button(BUTTON_SWING_V)
    set_bits(st.raw, 1, 0, 4, swing_v)
    st.set_quiet(s.quiet)
    st.set_turbo(turbo_requested(s))
    st.set_health(False)
    _set_clean160(st, s.clean)
    st.set_sleep(s.sleep)
    st.set_power(s.power)
    st.button(BUTTON_POWER)  # setLightToggle(false)
    st.checksum()
    return st.raw


def _set_clean160(st, on):
    st.button(BUTTON_CLEAN160)
    set_bit(st.raw, 10, 4, on)
    set_bit(st.raw, 15, 6, on)


def _encode_ac(s):
    """HSU07-HEA03 (IRHaierAC, 9 bytes)."""
    raw = bytearray(9)
    raw[0] = 0xA5  # Prefix
    set_bit(raw, 2, 5, True)
    set_bits(raw, 4, 0, 5, 12)  # OffHours
    set_bits(raw, 1, 4, 4, 25 - 16)
    set_bits(raw, 5, 6, 2, 3)
    set_bits(raw, 1, 0, 4, 1)  # Command On

    # IRac::haier(): mode, temp, fan, swing, health (off), sleep, command.
    set_bits(
        raw, 6, 5, 3,
        {StdMode.COOL: 1, StdMode.DRY: 2, StdMode.HEAT: 3, StdMode.FAN: 4}.get(std_mode(s.mode), 0),
    )
    set_bits(raw, 1, 4, 4, max(16, min(s.temperature_c, 30)) - 16)
    set_bits(
        raw, 5, 6, 2,
        {StdFan.MIN: 3, StdFan.LOW: 3, StdFan.MEDIUM: 2, StdFan.HIGH: 1, StdFan.MAX: 1}.get(
            std_fan(s.fan_speed), 0
        ),
    )
    set_bits(
        raw, 2, 6, 2,
        {
            StdSwingV.HIGHEST: 0b01,
            StdSwingV.HIGH: 0b01,
            StdSwingV.MIDDLE: 0b01,
            StdSwingV.LOW: 0b10,
            StdSwingV.LOWEST: 0b10,
            StdSwingV.OFF: 0b00,
        }.get(std_swing_v(s.swing_vertical), 0b11),
    )
    set_bit(raw, 4, 5, False)  # Health
    set_bit(raw, 7, 6, s.sleep)
    set_bits(raw, 1, 0, 4, 1 if s.power else 0)
    raw[8] = sum_bytes(raw, 0, 8)
    return raw
