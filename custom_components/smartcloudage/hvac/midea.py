"""Midea / Springer / Carrier / Comfee 48-bit A/C (IRremoteESP8266 ``IRMideaAC``).

Each message is sent MSB first followed by its bitwise inverse. Quiet is a
separate absolute command sent after the state.
"""

from __future__ import annotations

from .base import (
    HvacProtocol,
    StdFan,
    StdMode,
    caps,
    frame,
    reverse_bits8,
    set_bit,
    set_bits,
    std_fan,
    std_mode,
    value_segment,
)
from .models import AcBrand

BITS = 48
TICK = 80
BIT_MARK = 7 * TICK
ONE_SPACE = 21 * TICK
ZERO_SPACE = 7 * TICK
HDR_MARK = 56 * TICK
HDR_SPACE = 56 * TICK
MIN_GAP = (56 + 7 + 7) * TICK
MESSAGE_GAP = 100000

RESET_STATE = 0xA1826FFFFF62
QUIET_ON = 0xA212FFFFFF6E
QUIET_OFF = 0xA213FFFFFF6F

MODE_COOL, MODE_DRY, MODE_AUTO, MODE_HEAT, MODE_FAN = 0, 1, 2, 3, 4
FAN_AUTO, FAN_LOW, FAN_MED, FAN_HIGH = 0, 1, 2, 3
MIN_TEMP_C = 17


class MideaHvacProtocol(HvacProtocol):
    brand = AcBrand.MIDEA
    default_model = "Midea"
    capabilities = caps(17, 30, sleep=True, quiet=True)

    def build(self, state):
        s = self.normalize(state)
        self.validate(s)

        b = bytearray(RESET_STATE.to_bytes(8, "little"))  # b[0] is the checksum byte.

        set_bit(b, 3, 5, False)  # setUseCelsius(true)
        set_bit(b, 4, 7, s.power)
        set_bits(
            b, 4, 0, 3,
            {
                StdMode.COOL: MODE_COOL,
                StdMode.HEAT: MODE_HEAT,
                StdMode.DRY: MODE_DRY,
                StdMode.FAN: MODE_FAN,
            }.get(std_mode(s.mode), MODE_AUTO),
        )
        set_bits(b, 3, 0, 5, max(MIN_TEMP_C, min(s.temperature_c, 30)) - MIN_TEMP_C)

        # setEnableSensorTemp(false): command type, beep disabled, no sensor temp.
        set_bit(b, 1, 7, True)
        set_bits(b, 5, 0, 3, 0b001)
        set_bit(b, 2, 7, True)
        set_bits(b, 1, 0, 7, 0b1111111)

        set_bits(
            b, 4, 3, 2,
            {
                StdFan.MIN: FAN_LOW,
                StdFan.LOW: FAN_LOW,
                StdFan.MEDIUM: FAN_MED,
                StdFan.HIGH: FAN_HIGH,
                StdFan.MAX: FAN_HIGH,
            }.get(std_fan(s.fan_speed), FAN_AUTO),
        )
        set_bit(b, 4, 6, s.sleep)

        code = int.from_bytes(b, "little") & 0xFFFFFFFFFFFF
        code = (code & ~0xFF) | _checksum(code)

        segments = []
        _add_message(segments, code)
        # Quiet is absolute: send it every time so the unit ends in the requested state.
        _add_message(segments, QUIET_ON if s.quiet else QUIET_OFF)
        return frame(s, 38000, 50, "MSB", BIT_MARK, ONE_SPACE, ZERO_SPACE, segments)


def _add_message(segments, code):
    """sendMidea(): the message, its inverse, then the message gap."""
    mask = (1 << BITS) - 1
    segments.append(value_segment(code & mask, BITS, HDR_MARK, HDR_SPACE, BIT_MARK, MIN_GAP))
    segments.append(value_segment(~code & mask, BITS, HDR_MARK, HDR_SPACE, BIT_MARK, MIN_GAP + MESSAGE_GAP))


def _checksum(state):
    """Bit-reversed two's complement of the reversed bytes 1..5."""
    total = 0
    temp = state
    for _ in range(5):
        temp >>= 8
        total += reverse_bits8(temp & 0xFF)
    return reverse_bits8((256 - total) & 0xFF)
