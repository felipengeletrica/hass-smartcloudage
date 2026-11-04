"""Hitachi A/C families (IRremoteESP8266 ``IRHitachiAc424``, ``IRHitachiAc344``,
``IRHitachiAc264`` and ``IRHitachiAc``).

* RAR-8P2 (RAS-AJ25H): 424 bits, leader + LSB bytes with inverted pairs.
* RF11T1 / RF07T4 (RAS-22NK): 344 bits, adds the horizontal vane.
* RAR-2P2 (RAK-25NH5): 264 bits.
* RAS-35THA6: 224 bits, MSB first, bit-reversed fields.

Vertical swing is a toggle button on the 424/344/264 family, so it is only
offered for RAS-35THA6.
"""

from __future__ import annotations

from .base import (
    HvacProtocol,
    StdFan,
    StdMode,
    StdSwingH,
    add_bytes,
    caps,
    frame,
    get_bits,
    invert_byte_pairs,
    marks,
    reverse_bits8,
    set_bit,
    set_bits,
    std_fan,
    std_mode,
    std_swing_h,
)
from .models import AcBrand, AcSwingHorizontal, AcSwingVertical

MODEL_RAR8P2 = "RAR-8P2"
MODEL_RF11T1 = "RF11T1"
MODEL_RAR2P2 = "RAR-2P2"
MODEL_RAS35THA6 = "RAS-35THA6"

FREQ = 38000
HDR_MARK = 3300
HDR_SPACE = 1700
BIT_MARK = 400
ONE_SPACE = 1250
ZERO_SPACE = 500
MIN_GAP = 100000
LDR424_MARK = 29784
LDR424_SPACE = 49290
HDR424_MARK = 3416
HDR424_SPACE = 1604
BIT424_MARK = 463
ONE424_SPACE = 1208
ZERO424_SPACE = 372

FAMILY_CAPS = caps(16, 32)
AC344_CAPS = caps(16, 32, swing_h=True)
AC_CAPS = caps(16, 32, swing_v=True, swing_h=True)

AC424, AC344, AC264 = "424", "344", "264"

F424_FAN, F424_COOL, F424_DRY, F424_HEAT = 1, 3, 5, 6
F424_FAN_MIN, F424_FAN_LOW, F424_FAN_MEDIUM, F424_FAN_HIGH, F424_FAN_AUTO, F424_FAN_MAX = 1, 2, 3, 4, 5, 6
F424_FAN_MAX_DRY = 2
F424_FAN_TEMP = 27
BUTTON_POWER_MODE, BUTTON_FAN, BUTTON_TEMP_DOWN, BUTTON_TEMP_UP = 0x13, 0x42, 0x43, 0x44
BUTTON_SWING_H = 0x8C

AC_AUTO, AC_HEAT, AC_COOL, AC_DRY, AC_FAN = 2, 3, 4, 5, 0xC
AC_FAN_AUTO, AC_FAN_LOW, AC_FAN_HIGH = 1, 2, 5


class HitachiHvacProtocol(HvacProtocol):
    brand = AcBrand.HITACHI
    models = (MODEL_RAR8P2, MODEL_RF11T1, MODEL_RAR2P2, MODEL_RAS35THA6)
    default_model = MODEL_RAR8P2
    capabilities = FAMILY_CAPS

    def capabilities_for(self, model):
        return {MODEL_RF11T1: AC344_CAPS, MODEL_RAS35THA6: AC_CAPS}.get(self.resolve_model(model), FAMILY_CAPS)

    def build(self, state):
        s = self.normalize(state)
        self.validate(s)
        model = self.resolve_model(s.model)
        if model == MODEL_RAS35THA6:
            return _build_ac(s)
        if model == MODEL_RF11T1:
            return _build_family(s, AC344)
        if model == MODEL_RAR2P2:
            return _build_family(s, AC264)
        return _build_family(s, AC424)


def _build_family(s, family):
    st = _Hitachi424(family)

    # IRac: mode, temp, fan, (344: swing H), power, swing-V toggle off.
    st.set_mode(
        {StdMode.HEAT: F424_HEAT, StdMode.DRY: F424_DRY, StdMode.FAN: F424_FAN}.get(
            std_mode(s.mode), F424_COOL  # Auto is not available: the remote maps it to cool.
        )
    )
    st.set_temp(s.temperature_c)
    st.set_fan(st.convert_fan(std_fan(s.fan_speed)))
    if family == AC344:
        st.set_swing_h(std_swing_h(s.swing_horizontal))
    st.set_power(s.power)

    raw = st.get_raw()
    segments = []
    if family == AC424:
        segments.append(marks(LDR424_MARK, LDR424_SPACE))
        add_bytes(segments, raw, 0, len(raw), HDR424_MARK, HDR424_SPACE, BIT424_MARK, MIN_GAP)
        return frame(s, FREQ, 50, "LSB", BIT424_MARK, ONE424_SPACE, ZERO424_SPACE, segments)

    add_bytes(segments, raw, 0, len(raw), HDR_MARK, HDR_SPACE, BIT_MARK, MIN_GAP)
    return frame(s, FREQ, 50, "LSB", BIT_MARK, ONE_SPACE, ZERO_SPACE, segments)


class _Hitachi424:
    """IRHitachiAc424 and its 344/264 subclasses."""

    def __init__(self, family):
        self._family = family
        self.raw = bytearray()
        self._previous_temp = 0
        # The C++ constructors run the base reset, then the subclass reset.
        self._base_reset(False)
        if family != AC424:
            self._subclass_reset()

    @property
    def _length(self):
        return {AC344: 43, AC264: 33}.get(self._family, 53)

    @property
    def mode(self):
        return get_bits(self.raw[25], 0, 4)

    @mode.setter
    def mode(self, value):
        set_bits(self.raw, 25, 0, 4, value)

    @property
    def fan(self):
        return get_bits(self.raw[25], 4, 4)

    @fan.setter
    def fan(self, value):
        set_bits(self.raw, 25, 4, 4, value)

    def _button(self, value):
        self.raw[11] = value

    def _base_reset(self, virtual_fan):
        self.raw = bytearray(self._length)
        for index, value in {0: 0x01, 1: 0x10, 3: 0x40, 5: 0xFF, 7: 0xCC, 27: 0xE1}.items():
            self.raw[index] = value
        if len(self.raw) > 33:
            for index, value in {33: 0x80, 35: 0x03, 37: 0x01, 39: 0x88}.items():
                self.raw[index] = value
        if len(self.raw) > 45:
            for index in (45, 47, 49, 51):
                self.raw[index] = 0xFF
        self.set_temp(23)
        self.set_power(True)
        self._set_mode(F424_COOL, virtual_fan)
        self._set_fan(F424_FAN_AUTO, virtual_fan)

    def _subclass_reset(self):
        self._base_reset(True)
        if self._family == AC344:
            self.raw[37] = 0x00
            self.raw[39] = 0x00
        else:
            self.raw[9] = 0x92
            self.raw[27] = 0xC1

    def set_power(self, on):
        set_bit(self.raw, 27, 4, on)
        self._button(BUTTON_POWER_MODE)

    def set_temp(self, celsius, set_previous=True):
        temp = max(16, min(celsius, 32))
        set_bits(self.raw, 13, 2, 6, temp)
        if self._previous_temp > temp:
            self._button(BUTTON_TEMP_DOWN)
        elif self._previous_temp < temp:
            self._button(BUTTON_TEMP_UP)
        if set_previous:
            self._previous_temp = temp

    def set_mode(self, mode):
        self._set_mode(mode, True)

    def _set_mode(self, mode, virtual_fan):
        new_mode = mode
        if mode == F424_FAN:
            self.set_temp(F424_FAN_TEMP, False)
        elif mode not in (F424_HEAT, F424_COOL, F424_DRY):
            new_mode = F424_COOL
        self.mode = new_mode
        if new_mode != F424_FAN:
            self.set_temp(self._previous_temp)
        self._set_fan(self.fan, virtual_fan)
        self._button(BUTTON_POWER_MODE)

    def set_fan(self, speed):
        self._set_fan(speed, True)

    def _set_fan(self, speed, virtual_fan):
        """setFan() is virtual: the 264 override applies only after the base constructor."""
        if virtual_fan and self._family == AC264:
            ok = speed in (F424_FAN_MIN, F424_FAN_MEDIUM, F424_FAN_HIGH, F424_FAN_AUTO)
            self.fan = speed if ok else F424_FAN_AUTO
            return

        new_speed = max(speed, F424_FAN_MIN)
        fan_max = F424_FAN_MAX
        if self.mode == F424_DRY and speed == F424_FAN_AUTO:
            fan_max = F424_FAN_AUTO
        elif self.mode == F424_DRY:
            fan_max = F424_FAN_MAX_DRY
        elif self.mode == F424_FAN and speed == F424_FAN_AUTO:
            new_speed = F424_FAN_MIN

        new_speed = min(new_speed, fan_max)
        if new_speed != self.fan:
            self._button(BUTTON_FAN)
        self.fan = new_speed
        self.raw[9] = 0x92
        self.raw[29] = 0x00
        if new_speed == F424_FAN_MIN:
            self.raw[9] = 0x98
        if new_speed == F424_FAN_MAX:
            self.raw[9] = 0xA9
            self.raw[29] = 0x30

    def convert_fan(self, fan):
        if self._family == AC264:
            return {
                StdFan.MIN: F424_FAN_MIN,
                StdFan.LOW: F424_FAN_MIN,
                StdFan.MEDIUM: F424_FAN_MEDIUM,
                StdFan.HIGH: F424_FAN_HIGH,
                StdFan.MAX: F424_FAN_HIGH,
            }.get(fan, F424_FAN_AUTO)
        return {
            StdFan.MIN: F424_FAN_MIN,
            StdFan.LOW: F424_FAN_LOW,
            StdFan.MEDIUM: F424_FAN_MEDIUM,
            StdFan.HIGH: F424_FAN_HIGH,
            StdFan.MAX: F424_FAN_MAX,
        }.get(fan, F424_FAN_AUTO)

    def set_swing_h(self, swing):
        position = {
            StdSwingH.AUTO: 0,
            StdSwingH.LEFT_MAX: 5,
            StdSwingH.LEFT: 4,
            StdSwingH.RIGHT: 2,
            StdSwingH.RIGHT_MAX: 1,
        }.get(swing, 3)  # Middle
        set_bits(self.raw, 35, 0, 3, position)
        self._button(BUTTON_SWING_H)

    def get_raw(self):
        """getRaw(): bytes 3.. are sent as (value, inverse) pairs."""
        invert_byte_pairs(self.raw, 3, len(self.raw) - 3)
        return self.raw


def _build_ac(s):
    """RAS-35THA6 (IRHitachiAc, 28 bytes, MSB first)."""
    st = _HitachiAc()
    st.set_power(s.power)
    st.set_mode(
        {
            StdMode.COOL: AC_COOL,
            StdMode.HEAT: AC_HEAT,
            StdMode.DRY: AC_DRY,
            StdMode.FAN: AC_FAN,
        }.get(std_mode(s.mode), AC_AUTO)
    )
    st.set_temp(s.temperature_c)
    st.set_fan(
        {
            StdFan.MIN: AC_FAN_LOW,
            StdFan.LOW: AC_FAN_LOW,
            StdFan.MEDIUM: AC_FAN_LOW + 1,
            StdFan.HIGH: AC_FAN_HIGH - 1,
            StdFan.MAX: AC_FAN_HIGH,
        }.get(std_fan(s.fan_speed), AC_FAN_AUTO)
    )
    set_bit(st.raw, 14, 7, s.swing_vertical != AcSwingVertical.OFF)
    set_bit(st.raw, 15, 7, s.swing_horizontal != AcSwingHorizontal.OFF)

    raw = st.get_raw()
    segments = []
    add_bytes(segments, raw, 0, len(raw), HDR_MARK, HDR_SPACE, BIT_MARK, MIN_GAP)
    return frame(s, FREQ, 50, "MSB", BIT_MARK, ONE_SPACE, ZERO_SPACE, segments)


class _HitachiAc:
    def __init__(self):
        self.raw = bytearray(28)
        self.raw[0:10] = bytes([0x80, 0x08, 0x0C, 0x02, 0xFD, 0x80, 0x7F, 0x88, 0x48, 0x10])
        self.raw[14] = 0x60
        self.raw[15] = 0x60
        self.raw[24] = 0x80
        self._previous_temp = 0
        self.set_temp(23)

    def set_power(self, on):
        set_bit(self.raw, 17, 0, on)

    def set_mode(self, mode):
        new_mode = mode
        if mode == AC_FAN:
            self.set_temp(64)
        elif mode not in (AC_AUTO, AC_HEAT, AC_COOL, AC_DRY):
            new_mode = AC_AUTO
        self.raw[10] = reverse_bits8(new_mode)
        if mode != AC_FAN:
            self.set_temp(self._previous_temp)
        self.set_fan(reverse_bits8(self.raw[13]))

    def set_temp(self, celsius):
        if celsius != 64:
            self._previous_temp = celsius
        temp = 64 if celsius == 64 else max(16, min(celsius, 32))
        self.raw[11] = reverse_bits8((temp << 1) & 0xFF)
        self.raw[9] = 0x90 if temp == 16 else 0x10

    def set_fan(self, speed):
        fan_min, fan_max = AC_FAN_AUTO, AC_FAN_HIGH
        mode = reverse_bits8(self.raw[10])
        if mode == AC_DRY:
            fan_min, fan_max = AC_FAN_LOW, AC_FAN_LOW + 1
        elif mode == AC_FAN:
            fan_min = AC_FAN_LOW
        self.raw[13] = reverse_bits8(min(max(speed, fan_min), fan_max))

    def get_raw(self):
        total = 62
        for byte in self.raw[:-1]:
            total -= reverse_bits8(byte)
        self.raw[-1] = reverse_bits8(total & 0xFF)
        return self.raw
