"""Coolix 24-bit A/C (IRremoteESP8266 ``IRCoolixAC``): older Midea lines
(RG57/RG52 remotes), Beko, Tokio, Bosch, Kaysun and many unbranded splits.

Each byte is sent MSB first followed by its inverse; the message is sent
twice. Only power, mode, temperature and fan are absolute on this protocol.
"""

from __future__ import annotations

from .base import HvacProtocol, StdFan, StdMode, caps, frame, std_fan, std_mode, value_segment
from .models import AcBrand

TICK = 276
BIT_MARK = 2 * TICK
ONE_SPACE = 6 * TICK
ZERO_SPACE = 2 * TICK
HDR_MARK = 17 * TICK
HDR_SPACE = 16 * TICK
MIN_GAP = (17 + 2) * TICK
MESSAGE_GAP = 100000
COPIES = 2

OFF_COMMAND = 0xB27BE0
DEFAULT_STATE = 0xB21FC8

MODE_COOL, MODE_DRY, MODE_AUTO, MODE_HEAT, MODE_FAN = 0b000, 0b001, 0b010, 0b011, 0b100
FAN_MIN, FAN_MED, FAN_MAX, FAN_AUTO, FAN_AUTO0 = 0b100, 0b010, 0b001, 0b101, 0b000
FAN_ZONE_FOLLOW, FAN_FIXED = 0b110, 0b111
FAN_TEMP_CODE = 0b1110
SENSOR_IGNORE = 0b11111

TEMP_MAP = (
    0b0000, 0b0001, 0b0011, 0b0010, 0b0110, 0b0111, 0b0101,
    0b0100, 0b1100, 0b1101, 0b1001, 0b1000, 0b1010, 0b1011,
)


class CoolixHvacProtocol(HvacProtocol):
    brand = AcBrand.COOLIX
    default_model = "RG57"
    capabilities = caps(17, 30)

    def build(self, state):
        s = self.normalize(state)
        self.validate(s)

        if not s.power:
            code = OFF_COMMAND
        else:
            # IRac::coolix(): temp, mode, fan, clear sensor temp, zone follow off.
            st = _State()
            st.set_temp(s.temperature_c)
            st.set_mode(
                {
                    StdMode.COOL: MODE_COOL,
                    StdMode.HEAT: MODE_HEAT,
                    StdMode.DRY: MODE_DRY,
                    StdMode.FAN: MODE_FAN,
                }.get(std_mode(s.mode), MODE_AUTO)
            )
            st.set_fan(
                {
                    StdFan.MIN: FAN_MIN,
                    StdFan.LOW: FAN_MIN,
                    StdFan.MEDIUM: FAN_MED,
                    StdFan.HIGH: FAN_MAX,
                    StdFan.MAX: FAN_MAX,
                }.get(std_fan(s.fan_speed), FAN_AUTO)
            )
            st.clear_sensor_temp()
            st.set_zone_follow(False)
            code = st.raw

        wire = 0
        for i in range(2, -1, -1):
            b = (code >> (8 * i)) & 0xFF
            wire = (wire << 16) | (b << 8) | (b ^ 0xFF)

        segments = [
            value_segment(
                wire, 48, HDR_MARK, HDR_SPACE, BIT_MARK,
                MIN_GAP + MESSAGE_GAP if i == COPIES - 1 else MIN_GAP,
            )
            for i in range(COPIES)
        ]
        return frame(s, 38000, 50, "MSB", BIT_MARK, ONE_SPACE, ZERO_SPACE, segments)


class _State:
    """The IRCoolixAC 24-bit state and its saved-fan bookkeeping."""

    def __init__(self):
        self.raw = DEFAULT_STATE
        self._saved_fan = self.fan

    def _get(self, offset, size):
        return (self.raw >> offset) & ((1 << size) - 1)

    def _set(self, offset, size, value):
        mask = ((1 << size) - 1) << offset
        self.raw = (self.raw & ~mask) | ((value << offset) & mask)

    @property
    def mode_raw(self):
        return self._get(2, 2)

    @mode_raw.setter
    def mode_raw(self, value):
        self._set(2, 2, value)

    @property
    def temp_raw(self):
        return self._get(4, 4)

    @temp_raw.setter
    def temp_raw(self, value):
        self._set(4, 4, value)

    @property
    def fan(self):
        return self._get(13, 3)

    @fan.setter
    def fan(self, value):
        self._set(13, 3, value)

    def _get_mode(self):
        if self.mode_raw == MODE_DRY and self.temp_raw == FAN_TEMP_CODE:
            return MODE_FAN
        return self.mode_raw

    def _get_temp(self):
        return 17 + TEMP_MAP.index(self.temp_raw) if self.temp_raw in TEMP_MAP else 30

    def set_temp(self, celsius):
        self.temp_raw = TEMP_MAP[max(17, min(celsius, 30)) - 17]

    def set_mode(self, mode):
        if mode in (MODE_AUTO, MODE_DRY):
            self.set_fan(FAN_AUTO0, mode_check=False)
        elif mode in (MODE_COOL, MODE_HEAT, MODE_FAN):
            self.set_fan(FAN_AUTO, mode_check=False)
        else:
            self.set_mode(MODE_AUTO)
            self.set_fan(FAN_AUTO0, mode_check=False)
            return
        self.set_temp(self._get_temp())
        actual = mode
        if mode == MODE_FAN:
            actual = MODE_DRY
            self.temp_raw = FAN_TEMP_CODE
        self.mode_raw = actual

    def set_fan(self, speed, mode_check=True):
        new_speed = speed
        if speed == FAN_AUTO:
            if mode_check and self._get_mode() in (MODE_AUTO, MODE_DRY):
                new_speed = FAN_AUTO0
        elif speed == FAN_AUTO0:
            if mode_check and self._get_mode() not in (MODE_AUTO, MODE_DRY):
                new_speed = FAN_AUTO
        elif speed not in (FAN_MIN, FAN_MED, FAN_MAX, FAN_ZONE_FOLLOW, FAN_FIXED):
            new_speed = FAN_AUTO
        self._saved_fan = self._saved_fan if self.fan == FAN_ZONE_FOLLOW else self.fan
        self.fan = new_speed

    def set_zone_follow(self, on):
        self._set(1, 1, 1 if on else 0)
        self._set(19, 1, 1 if on else 0)
        self.set_fan(FAN_ZONE_FOLLOW if on else self._saved_fan)

    def clear_sensor_temp(self):
        self.set_zone_follow(False)
        self._set(8, 5, SENSOR_IGNORE)
