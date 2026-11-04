"""Toshiba A/C (IRremoteESP8266 ``IRToshibaAC``), also Carrier 42NQV.

The state message is 9 bytes (10 with Turbo/Eco), MSB first, each sent twice.
The swing setting travels in a separate 7-byte message after the state.
"""

from __future__ import annotations

from .base import (
    HvacProtocol,
    StdFan,
    StdMode,
    byte_segment,
    caps,
    frame,
    get_bits,
    invert_byte_pairs,
    set_bit,
    set_bits,
    std_fan,
    std_mode,
    turbo_requested,
    xor_bytes,
)
from .models import AcBrand, AcSwingVertical

LENGTH_NORMAL = 9
LENGTH_SHORT = 7
LENGTH_LONG = 10
MIN_LENGTH = 6
COPIES = 2
HDR_MARK = 4400
HDR_SPACE = 4300
BIT_MARK = 580
ONE_SPACE = 1600
ZERO_SPACE = 490
USUAL_GAP = 7400

MODE_AUTO, MODE_COOL, MODE_DRY, MODE_HEAT, MODE_FAN, MODE_OFF = 0, 1, 2, 3, 4, 7
SWING_ON, SWING_OFF = 1, 2
TURBO_ON, ECONO_ON = 1, 3
MIN_TEMP = 17


class ToshibaHvacProtocol(HvacProtocol):
    brand = AcBrand.TOSHIBA
    default_model = "RAS"
    capabilities = caps(17, 30, swing_v=True, turbo=True, eco=True)

    def build(self, state):
        s = self.normalize(state)
        self.validate(s)

        # IRac::toshiba(): mode, temp, fan, swing, turbo, econo, filter, power.
        st = _State()
        st.set_mode(
            {
                StdMode.COOL: MODE_COOL,
                StdMode.HEAT: MODE_HEAT,
                StdMode.DRY: MODE_DRY,
                StdMode.FAN: MODE_FAN,
            }.get(std_mode(s.mode), MODE_AUTO)
        )
        st.set_temp(s.temperature_c)
        st.set_fan(
            {StdFan.MIN: 1, StdFan.LOW: 2, StdFan.MEDIUM: 3, StdFan.HIGH: 4, StdFan.MAX: 5}.get(
                std_fan(s.fan_speed), 0
            )
        )
        st.set_swing(SWING_ON if s.swing_vertical != AcSwingVertical.OFF else SWING_OFF)
        st.set_turbo(turbo_requested(s))
        st.set_econo(s.eco)
        st.set_filter(False)
        st.set_power(s.power)

        segments = [
            byte_segment(message, 0, len(message), HDR_MARK, HDR_SPACE, BIT_MARK, USUAL_GAP)
            for message in st.messages()
            for _ in range(COPIES)
        ]
        return frame(s, 38000, 50, "MSB", BIT_MARK, ONE_SPACE, ZERO_SPACE, segments)


class _State:
    """The IRToshibaAC state machine (length, previous mode, pending swing)."""

    def __init__(self):
        self.raw = bytearray(LENGTH_LONG)
        self.raw[0:5] = bytes([0xF2, 0x0D, 0x03, 0xFC, 0x01])
        self._swing_mode = 0
        self._send_swing = False
        self.set_temp(22)
        self.set_swing(SWING_OFF)
        self._prev_mode = self.mode

    @property
    def mode(self):
        return get_bits(self.raw[6], 0, 3)

    @mode.setter
    def mode(self, value):
        set_bits(self.raw, 6, 0, 3, value)

    @property
    def length(self):
        return min((self.raw[2] & 0x0F) + MIN_LENGTH, LENGTH_LONG)

    def _set_length(self, length):
        if length >= MIN_LENGTH:
            set_bits(self.raw, 2, 0, 4, length - MIN_LENGTH)

    def set_temp(self, celsius):
        set_bits(self.raw, 5, 4, 4, max(MIN_TEMP, min(celsius, 30)) - MIN_TEMP)

    def set_fan(self, speed):
        fan = min(speed, 5)
        if fan > 0:
            fan += 1
        set_bits(self.raw, 6, 5, 3, fan)

    def set_swing(self, setting):
        self._send_swing = True
        self._swing_mode = setting
        if self.length == LENGTH_SHORT:
            set_bits(self.raw, 5, 0, 3, setting)

    def set_mode(self, mode):
        if mode != self._prev_mode:
            self._set_length(LENGTH_NORMAL)
        if mode in (MODE_AUTO, MODE_COOL, MODE_DRY, MODE_HEAT, MODE_FAN):
            self._prev_mode = mode
            self.mode = mode
        elif mode == MODE_OFF:
            self.mode = mode
        else:
            self._prev_mode = MODE_AUTO
            self.mode = MODE_AUTO

    def _get_turbo(self):
        return self.length == LENGTH_LONG and self.raw[8] == TURBO_ON

    def _get_econo(self):
        return self.length == LENGTH_LONG and self.raw[8] == ECONO_ON

    def set_turbo(self, on):
        if on:
            self.raw[8] = TURBO_ON
            self._set_length(LENGTH_LONG)
        elif not self._get_econo():
            self._set_length(LENGTH_NORMAL)

    def set_econo(self, on):
        if on:
            self.raw[8] = ECONO_ON
            self._set_length(LENGTH_LONG)
        elif not self._get_turbo():
            self._set_length(LENGTH_NORMAL)

    def set_filter(self, on):
        set_bit(self.raw, 7, 4, on)
        if on:
            self._set_length(min(LENGTH_NORMAL, self.length))

    def set_power(self, on):
        if on:
            if self.mode == MODE_OFF:
                self.set_mode(self._prev_mode)
        else:
            self.set_mode(MODE_OFF)

    def _get_raw(self):
        length = self.length
        set_bit(self.raw, 4, 5, length == LENGTH_SHORT)  # ShortMsg
        set_bit(self.raw, 4, 3, length == LENGTH_LONG)  # LongMsg
        invert_byte_pairs(self.raw, 0, 4)
        self.raw[length - 1] = xor_bytes(self.raw, 0, length - 1)
        return bytes(self.raw[:length])

    def messages(self):
        """send(): the state, then (when swing was set) the short swing message."""
        backup = bytearray(self.raw)
        messages = [self._get_raw()]
        if self._send_swing and self.length != LENGTH_SHORT:
            self._set_length(LENGTH_SHORT)
            self.set_temp(MIN_TEMP)
            self.set_swing(self._swing_mode)
            messages.append(self._get_raw())
            self.raw[:] = backup
        return messages
