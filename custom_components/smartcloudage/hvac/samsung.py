"""Samsung A/C (IRremoteESP8266 ``IRSamsungAc``), extended 21-byte message.

Every command is the extended message (three 7-byte sections, LSB first),
which carries power changes, as IRac does with ``forceextended = true``.
"""

from __future__ import annotations

from .base import (
    HvacProtocol,
    StdFan,
    StdMode,
    byte_segment,
    caps,
    count_bits,
    frame,
    get_bits,
    marks,
    set_bit,
    set_bits,
    std_fan,
    std_mode,
    turbo_requested,
)
from .models import AcBrand, AcSwingHorizontal, AcSwingVertical

SECTION_LENGTH = 7
EXTENDED_LENGTH = 21
HDR_MARK = 690
HDR_SPACE = 17844
SECTION_MARK = 3086
SECTION_SPACE = 8864
SECTION_GAP = 2886
BIT_MARK = 586
ONE_SPACE = 1432
ZERO_SPACE = 436
MESSAGE_GAP = 100000

MODE_AUTO, MODE_COOL, MODE_DRY, MODE_FAN, MODE_HEAT = 0, 1, 2, 3, 4
FAN_AUTO, FAN_LOW, FAN_MED, FAN_HIGH, FAN_AUTO2, FAN_TURBO = 0, 2, 4, 5, 6, 7
SWING_V, SWING_H, SWING_BOTH, SWING_OFF = 0b010, 0b011, 0b100, 0b111
SPECIAL_OFF, POWERFUL_ON, BREEZE_ON, ECONO_ON = 0b000, 0b011, 0b101, 0b111

RESET = bytes(
    [0x02, 0x92, 0x0F, 0x00, 0x00, 0x00, 0xF0, 0x01, 0x02, 0xAE, 0x71, 0x00, 0x15, 0xF0]
)
EXTENDED_MIDDLE = bytes([0x01, 0xD2, 0x0F, 0x00, 0x00, 0x00, 0x00])


class SamsungHvacProtocol(HvacProtocol):
    brand = AcBrand.SAMSUNG
    default_model = "AR"
    capabilities = caps(16, 30, swing_v=True, swing_h=True, turbo=True, eco=True, light=True, quiet=True)

    def build(self, state):
        s = self.normalize(state)
        self.validate(s)

        st = _State()
        st.set_power(s.power)
        mode = {
            StdMode.COOL: MODE_COOL,
            StdMode.HEAT: MODE_HEAT,
            StdMode.DRY: MODE_DRY,
            StdMode.FAN: MODE_FAN,
        }.get(std_mode(s.mode), MODE_AUTO)
        st.set_mode(mode)
        st.set_temp(s.temperature_c)
        st.set_fan(
            {
                StdFan.MIN: FAN_LOW,
                StdFan.LOW: FAN_LOW,
                StdFan.MEDIUM: FAN_MED,
                StdFan.HIGH: FAN_HIGH,
                StdFan.MAX: FAN_TURBO,
            }.get(std_fan(s.fan_speed), FAN_AUTO)
        )
        st.set_swing(s.swing_vertical != AcSwingVertical.OFF)
        st.set_swing_h(s.swing_horizontal != AcSwingHorizontal.OFF)
        st.set_quiet(s.quiet)
        st.set_powerful(turbo_requested(s))
        set_bit(st.raw, 10, 4, s.light)  # Display
        st.set_econo(s.eco)
        st.set_mode(mode)  # As IRac: setMode() again, it can affect the fan.

        raw = st.to_extended()
        segments = [marks(HDR_MARK, HDR_SPACE)]
        for offset in range(0, EXTENDED_LENGTH, SECTION_LENGTH):
            last = offset + SECTION_LENGTH >= EXTENDED_LENGTH
            segments.append(
                byte_segment(
                    raw, offset, SECTION_LENGTH, SECTION_MARK, SECTION_SPACE, BIT_MARK,
                    MESSAGE_GAP if last else SECTION_GAP,
                )
            )
        return frame(s, 38000, 50, "LSB", BIT_MARK, ONE_SPACE, ZERO_SPACE, segments)


class _State:
    """Mutable 14-byte state with the IRSamsungAc setter rules."""

    def __init__(self):
        self.raw = bytearray(RESET)

    @property
    def mode(self):
        return get_bits(self.raw[12], 4, 3)

    @mode.setter
    def mode(self, value):
        set_bits(self.raw, 12, 4, 3, value)

    @property
    def fan(self):
        return get_bits(self.raw[12], 1, 3)

    @fan.setter
    def fan(self, value):
        set_bits(self.raw, 12, 1, 3, value)

    @property
    def swing(self):
        return get_bits(self.raw[9], 4, 3)

    @swing.setter
    def swing(self, value):
        set_bits(self.raw, 9, 4, 3, value)

    @property
    def fan_special(self):
        return get_bits(self.raw[10], 1, 3)

    @fan_special.setter
    def fan_special(self, value):
        set_bits(self.raw, 10, 1, 3, value)

    def set_power(self, on):
        set_bits(self.raw, 6, 4, 2, 0b11 if on else 0)
        set_bits(self.raw, 13, 4, 2, 0b11 if on else 0)

    def set_temp(self, celsius):
        set_bits(self.raw, 11, 4, 4, max(16, min(celsius, 30)) - 16)

    def set_mode(self, mode):
        if mode > MODE_HEAT:
            mode = MODE_AUTO
        self.mode = mode
        if mode == MODE_AUTO:
            self.fan = FAN_AUTO2
        elif self.fan == FAN_AUTO2:
            self.fan = FAN_AUTO

    def set_fan(self, speed):
        if speed in (FAN_AUTO, FAN_LOW, FAN_MED, FAN_HIGH, FAN_TURBO):
            if self.mode == MODE_AUTO:
                return
        elif speed == FAN_AUTO2:
            if self.mode != MODE_AUTO:
                return
        else:
            return
        self.fan = speed

    def _get_swing(self):
        return self.swing in (SWING_V, SWING_BOTH)

    def set_swing(self, on):
        if self.swing in (SWING_BOTH, SWING_H):
            self.swing = SWING_BOTH if on else SWING_H
        else:
            self.swing = SWING_V if on else SWING_OFF

    def set_swing_h(self, on):
        if self.swing in (SWING_V, SWING_BOTH):
            self.swing = SWING_BOTH if on else SWING_V
        else:
            self.swing = SWING_H if on else SWING_OFF

    def _get_powerful(self):
        return self.fan_special == POWERFUL_ON and self.fan == FAN_TURBO

    def _get_breeze(self):
        return self.fan_special == BREEZE_ON and self.fan == FAN_AUTO and not self._get_swing()

    def _get_econo(self):
        return self.fan_special == ECONO_ON and self.fan == FAN_AUTO and self._get_swing()

    def _set_quiet_bit(self, on):
        set_bit(self.raw, 5, 5, on)

    def set_quiet(self, on):
        self._set_quiet_bit(on)
        if on:
            self.set_fan(FAN_AUTO)
            self.set_powerful(False)

    def set_powerful(self, on):
        off_value = self.fan_special if (self._get_breeze() or self._get_econo()) else SPECIAL_OFF
        self.fan_special = POWERFUL_ON if on else off_value
        if on:
            self.set_fan(FAN_TURBO)
            self._set_quiet_bit(False)

    def set_econo(self, on):
        off_value = self.fan_special if (self._get_breeze() or self._get_powerful()) else SPECIAL_OFF
        self.fan_special = ECONO_ON if on else off_value
        if on:
            self.set_fan(FAN_AUTO)
            self.set_swing(True)

    def to_extended(self):
        """sendExtended(): section 2 moves to 3, a fixed section goes in the middle."""
        ext = bytearray(self.raw[:SECTION_LENGTH] + EXTENDED_MIDDLE + self.raw[SECTION_LENGTH:])
        for section in range(3):
            _checksum(ext, section * SECTION_LENGTH)
        return ext


def _checksum(state, offset):
    total = (
        count_bits(state[offset])
        + count_bits(state[offset + 1] & 0x0F)
        + count_bits(state[offset + 2] >> 4)
    )
    for i in range(3, SECTION_LENGTH):
        total += count_bits(state[offset + i])
    value = (total ^ 0xFF) & 0xFF
    set_bits(state, offset + 1, 4, 4, value & 0x0F)
    set_bits(state, offset + 2, 0, 4, value >> 4)
