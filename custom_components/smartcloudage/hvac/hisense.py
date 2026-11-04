"""Hisense / Kelon 168-bit A/C (IRremoteESP8266 ``IRKelon168Ac``), remote
DG11R2-01 / RCH-R0Y3.

Three sections (6 + 8 + 7 bytes), LSB first, with two XOR checksums. The
library has no ``IRac`` front end for it, so the setter order is ours; it is
the least field-validated brand.
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
    xor_bytes,
)
from .models import AcBrand, AcSwingVertical

STATE_LENGTH = 21
SECTION1 = 6
SECTION2 = 8
HDR_MARK = 9000
HDR_SPACE = 4600
BIT_MARK = 560
ONE_SPACE = 1680
ZERO_SPACE = 600
FOOTER_SPACE = 8000
GAP = 200000

MODE_HEAT, MODE_AUTO, MODE_COOL, MODE_DRY, MODE_FAN = 0, 1, 2, 3, 4
FAN_AUTO, FAN_MIN, FAN_LOW, FAN_MEDIUM, FAN_HIGH, FAN_MAX = 0, 1, 2, 3, 4, 5
AUTO_TEMP = 23
CMD_POWER, CMD_TEMP, CMD_SLEEP, CMD_SUPER, CMD_MODE, CMD_SWING, CMD_IFEEL, CMD_FAN = (
    0x01, 0x02, 0x03, 0x04, 0x06, 0x07, 0x0D, 0x11,
)


class HisenseHvacProtocol(HvacProtocol):
    brand = AcBrand.HISENSE
    default_model = "DG11R2-01"
    capabilities = caps(16, 32, swing_v=True, sleep=True, light=True)

    def build(self, state):
        s = self.normalize(state)
        self.validate(s)

        st = _State()
        st.set_mode(
            {
                StdMode.HEAT: MODE_HEAT,
                StdMode.AUTO: MODE_AUTO,
                StdMode.DRY: MODE_DRY,
                StdMode.FAN: MODE_FAN,
            }.get(std_mode(s.mode), MODE_COOL)
        )
        st.set_temp(s.temperature_c)
        st.set_fan(
            {
                StdFan.MIN: FAN_MIN,
                StdFan.LOW: FAN_LOW,
                StdFan.MEDIUM: FAN_MEDIUM,
                StdFan.HIGH: FAN_HIGH,
                StdFan.MAX: FAN_MAX,
            }.get(std_fan(s.fan_speed), FAN_AUTO)
        )
        st.set_swing(s.swing_vertical != AcSwingVertical.OFF)
        st.set_sleep(s.sleep)
        st.set_light(s.light)
        st.set_power(s.power)

        raw = st.get_raw()
        segments = [
            byte_segment(raw, 0, SECTION1, HDR_MARK, HDR_SPACE, BIT_MARK, FOOTER_SPACE),
            byte_segment(raw, SECTION1, SECTION2, 0, 0, BIT_MARK, FOOTER_SPACE),
            byte_segment(raw, SECTION1 + SECTION2, STATE_LENGTH - SECTION1 - SECTION2, 0, 0, BIT_MARK, GAP),
        ]
        return frame(s, 38000, 50, "LSB", BIT_MARK, ONE_SPACE, ZERO_SPACE, segments)


class _State:
    """The IRKelon168Ac state."""

    def __init__(self):
        self.raw = bytearray(STATE_LENGTH)
        self.raw[0] = 0x83
        self.raw[1] = 0x06
        self.raw[6] = 0x80
        self.raw[18] = 0x28  # Remote model, device off
        self._set_temp_bits(AUTO_TEMP)

    def _cmd(self, value):
        self.raw[15] = value

    def _set_temp_bits(self, celsius):
        set_bits(self.raw, 3, 4, 4, max(16, min(celsius, 32)) - 16)

    def _clear_super(self):
        set_bit(self.raw, 5, 4, False)
        set_bit(self.raw, 5, 7, False)
        self._cmd(CMD_SUPER)

    def set_mode(self, mode):
        self._clear_super()
        if mode == MODE_AUTO:
            self.set_fan(FAN_AUTO)
            self._set_temp_bits(AUTO_TEMP)
            self.set_sleep(False)
            set_bits(self.raw, 3, 0, 3, mode)
            self._cmd(CMD_IFEEL)
        elif mode in (MODE_HEAT, MODE_COOL, MODE_DRY, MODE_FAN):
            set_bits(self.raw, 3, 0, 3, mode)
            self._cmd(CMD_MODE)

    def set_temp(self, celsius):
        self._set_temp_bits(celsius)
        self._clear_super()
        self._cmd(CMD_TEMP)

    def set_fan(self, speed):
        fan, fan2 = {
            FAN_MIN: (0b11, 0),
            FAN_LOW: (0b11, 1),
            FAN_MEDIUM: (0b10, 0),
            FAN_HIGH: (0b01, 1),
            FAN_MAX: (0b01, 0),
        }.get(speed, (0, 0))
        set_bits(self.raw, 2, 0, 2, fan)
        set_bit(self.raw, 16, 1, fan2 != 0)
        self._clear_super()
        self._cmd(CMD_FAN)

    def set_swing(self, on):
        set_bit(self.raw, 2, 7, on)
        set_bit(self.raw, 8, 6, on)
        self._cmd(CMD_SWING)

    def set_sleep(self, on):
        set_bit(self.raw, 2, 3, on)
        if on:
            self.set_fan(FAN_LOW)
        self._cmd(CMD_SLEEP)

    def set_light(self, on):
        set_bit(self.raw, 6, 5, not on)

    def set_power(self, on):
        set_bit(self.raw, 2, 2, True)
        set_bit(self.raw, 18, 4, on)
        self._clear_super()
        self._cmd(CMD_POWER)

    def get_raw(self):
        self.raw[13] = xor_bytes(self.raw, 2, 10)
        self.raw[20] = xor_bytes(self.raw, 14, 6)
        return self.raw
