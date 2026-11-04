"""Shared helpers for the state-based A/C IR encoders.

Port of ``HvacProtocolBase.cs`` and ``InfraredPayloads.cs`` from the
SmartCloudAge Command Center. Every brand encoder follows the matching
IRremoteESP8266 class as driven by its ``IRac`` front end, so a frame built
here is bit-for-bit the frame that library sends for the same settings.

A frame is the payload of firmware command 32 (``COMMAND_IR_SEND_ENUM``) in
protocol mode: shared bit timings plus a list of segments, each one
``[header mark][header space] bits [stop mark][stop space]``.
"""

from __future__ import annotations

from enum import IntEnum

from .models import (
    AcCommandState,
    AcFanSpeed,
    AcMode,
    AcSwingHorizontal,
    AcSwingVertical,
    HvacCapabilities,
)

## Largest data chunk the firmware accepts per segment (256 bits).
MAX_SEGMENT_BYTES = 32


class StdMode(IntEnum):
    """stdAc::opmode_t (power is handled separately)."""

    AUTO = 0
    COOL = 1
    HEAT = 2
    DRY = 3
    FAN = 4


class StdFan(IntEnum):
    """stdAc::fanspeed_t."""

    AUTO = 0
    MIN = 1
    LOW = 2
    MEDIUM = 3
    HIGH = 4
    MAX = 5


class StdSwingV(IntEnum):
    """stdAc::swingv_t."""

    OFF = 0
    AUTO = 1
    HIGHEST = 2
    HIGH = 3
    MIDDLE = 4
    LOW = 5
    LOWEST = 6


class StdSwingH(IntEnum):
    """stdAc::swingh_t."""

    OFF = 0
    AUTO = 1
    LEFT_MAX = 2
    LEFT = 3
    MIDDLE = 4
    RIGHT = 5
    RIGHT_MAX = 6


def std_mode(mode: AcMode) -> StdMode:
    """Map the logical mode to the IRremoteESP8266 vocabulary."""
    return {
        AcMode.COOL: StdMode.COOL,
        AcMode.HEAT: StdMode.HEAT,
        AcMode.DRY: StdMode.DRY,
        AcMode.FAN: StdMode.FAN,
    }.get(mode, StdMode.AUTO)


def std_fan(speed: AcFanSpeed) -> StdFan:
    """Map the logical fan speed to the IRremoteESP8266 vocabulary."""
    return {
        AcFanSpeed.QUIET: StdFan.MIN,
        AcFanSpeed.LOW: StdFan.LOW,
        AcFanSpeed.MEDIUM: StdFan.MEDIUM,
        AcFanSpeed.HIGH: StdFan.HIGH,
        AcFanSpeed.MAX: StdFan.MAX,
    }.get(speed, StdFan.AUTO)


def std_swing_v(swing: AcSwingVertical) -> StdSwingV:
    """Map the vertical vane; Auto and Swing keep the vane moving."""
    return {
        AcSwingVertical.OFF: StdSwingV.OFF,
        AcSwingVertical.POS1: StdSwingV.HIGHEST,
        AcSwingVertical.POS2: StdSwingV.HIGH,
        AcSwingVertical.POS3: StdSwingV.MIDDLE,
        AcSwingVertical.POS4: StdSwingV.LOW,
        AcSwingVertical.POS5: StdSwingV.LOWEST,
    }.get(swing, StdSwingV.AUTO)


def std_swing_h(swing: AcSwingHorizontal) -> StdSwingH:
    """Map the horizontal vane; Auto and Swing keep the vane moving."""
    return {
        AcSwingHorizontal.OFF: StdSwingH.OFF,
        AcSwingHorizontal.LEFT: StdSwingH.LEFT_MAX,
        AcSwingHorizontal.LEFT_CENTER: StdSwingH.LEFT,
        AcSwingHorizontal.CENTER: StdSwingH.MIDDLE,
        AcSwingHorizontal.RIGHT_CENTER: StdSwingH.RIGHT,
        AcSwingHorizontal.RIGHT: StdSwingH.RIGHT_MAX,
    }.get(swing, StdSwingH.AUTO)


def turbo_requested(s: AcCommandState) -> bool:
    """Turbo and Powerful are the same feature on every supported brand."""
    return s.turbo or s.powerful


# ----------------------------------------------------------------------
# Bit helpers (IRutils equivalents)
# ----------------------------------------------------------------------


def get_bits(value: int, offset: int, size: int) -> int:
    return (value >> offset) & ((1 << size) - 1)


def set_bits(state: bytearray, index: int, offset: int, size: int, value: int) -> None:
    mask = ((1 << size) - 1) << offset
    state[index] = (state[index] & ~mask & 0xFF) | ((value << offset) & mask)


def set_bit(state: bytearray, index: int, offset: int, on: bool) -> None:
    set_bits(state, index, offset, 1, 1 if on else 0)


def sum_bytes(state: bytes, start: int, count: int, init: int = 0) -> int:
    """8-bit sum of ``count`` bytes starting at ``start``."""
    return (init + sum(state[start : start + count])) & 0xFF


def xor_bytes(state: bytes, start: int, count: int, init: int = 0) -> int:
    value = init
    for byte in state[start : start + count]:
        value ^= byte
    return value & 0xFF


def count_bits(value: int) -> int:
    return bin(value & 0xFF).count("1")


def reverse_bits8(value: int) -> int:
    result = 0
    for i in range(8):
        if value & (1 << i):
            result |= 1 << (7 - i)
    return result


def invert_byte_pairs(state: bytearray, start: int, length: int) -> None:
    """Every odd byte becomes the inverse of the previous one."""
    for i in range(1, length, 2):
        state[start + i] = ~state[start + i - 1] & 0xFF


# ----------------------------------------------------------------------
# Segment builders
# ----------------------------------------------------------------------


def _segment(data, nbits, header_mark, header_space, stop_mark, stop_space, bit_order=None):
    segment = {}
    if data is not None:
        segment["data"] = data
    segment["nbits"] = nbits
    if bit_order is not None:
        segment["bit_order"] = bit_order
    segment["header_mark_us"] = header_mark
    segment["header_space_us"] = header_space
    segment["stop_mark_us"] = stop_mark
    segment["stop_space_us"] = stop_space
    return segment


def byte_segment(state, start, count, header_mark, header_space, stop_mark, stop_space):
    """A byte section, sent in the protocol's bit order."""
    data = "0x" + bytes(state[start : start + count]).hex().upper()
    return _segment(data, count * 8, header_mark, header_space, stop_mark, stop_space)


def add_bytes(segments, state, start, count, header_mark, header_space, stop_mark, stop_space):
    """Add a byte section split into firmware-sized chunks with no gap between them."""
    for offset in range(0, count, MAX_SEGMENT_BYTES):
        n = min(MAX_SEGMENT_BYTES, count - offset)
        first = offset == 0
        last = offset + n >= count
        segments.append(
            byte_segment(
                state,
                start + offset,
                n,
                header_mark if first else 0,
                header_space if first else 0,
                stop_mark if last else 0,
                stop_space if last else 0,
            )
        )


def value_segment(value, nbits, header_mark, header_space, stop_mark, stop_space, bit_order=None):
    """A section carrying an integer of ``nbits`` bits (right-aligned hex)."""
    digits = max(1, (nbits + 3) // 4)
    data = "0x" + format(value, f"0{digits}X")
    return _segment(data, nbits, header_mark, header_space, stop_mark, stop_space, bit_order)


def marks(header_mark, header_space, stop_mark=0, stop_space=0):
    """A header/footer-only section without data bits."""
    return _segment(None, 0, header_mark, header_space, stop_mark, stop_space)


def frame(s, frequency, duty_cycle, bit_order, bit_mark, one_space, zero_space, segments):
    """Assemble the command-32 payload; every segment shares one bit timing."""
    payload = {"mode": "protocol"}
    if s.gpio is not None:
        payload["gpio"] = s.gpio
    payload.update(
        {
            "frequency": frequency,
            "duty_cycle": duty_cycle,
            "repeat": max(0, s.repeat),
            "frame_gap_us": 30000 if s.frame_gap_us <= 0 else s.frame_gap_us,
            "bit_order": bit_order,
            "header_mark_us": 0,
            "header_space_us": 0,
            "one_mark_us": bit_mark,
            "one_space_us": one_space,
            "zero_mark_us": bit_mark,
            "zero_space_us": zero_space,
            "stop_mark_us": 0,
            "stop_space_us": 0,
            "segments": list(segments),
        }
    )
    return payload


def caps(min_c, max_c, *, swing_v=False, swing_h=False, turbo=False, eco=False,
         sleep=False, light=False, clean=False, quiet=False, ifeel=False):
    """Capability set from a temperature range and the supported features."""
    return HvacCapabilities(
        min_c, max_c, swing_v, swing_h, turbo, eco, sleep, light, clean, quiet, ifeel
    )


class HvacProtocol:
    """Brand-specific IR encoder."""

    brand = None
    capabilities: HvacCapabilities
    models: tuple[str, ...] = ()
    default_model = ""

    def __init__(self):
        if not self.models:
            self.models = (self.default_model,)

    def capabilities_for(self, model: str | None) -> HvacCapabilities:
        """What the given remote model supports."""
        return self.capabilities

    def resolve_model(self, model: str | None) -> str:
        """The state's model when listed, otherwise the default."""
        if model and model.strip():
            wanted = model.strip().lower()
            for known in self.models:
                if known.lower() == wanted:
                    return known
        return self.default_model

    def normalize(self, state: AcCommandState) -> AcCommandState:
        c = self.capabilities_for(state.model)
        return state.normalize(c.min_temperature_c, c.max_temperature_c)

    def validate(self, s: AcCommandState) -> None:
        """Reject features the encoder cannot send (light is ignored, not rejected)."""
        c = self.capabilities_for(s.model)
        checks = (
            (s.swing_vertical != AcSwingVertical.OFF, c.supports_swing_vertical, "vertical swing"),
            (s.swing_horizontal != AcSwingHorizontal.OFF, c.supports_swing_horizontal, "horizontal swing"),
            (s.turbo or s.powerful, c.supports_turbo, "turbo"),
            (s.eco, c.supports_eco, "eco"),
            (s.sleep, c.supports_sleep, "sleep"),
            (s.clean, c.supports_clean, "clean"),
            (s.quiet, c.supports_quiet, "quiet"),
            (s.sensor_ifeel, c.supports_ifeel, "I-Feel"),
        )
        for requested, supported, name in checks:
            if requested and not supported:
                raise ValueError(f"{self.brand.value} does not support {name}")
        if s.beep:
            raise ValueError(f"{self.brand.value} does not support beep")

    def build(self, state: AcCommandState) -> dict:
        raise NotImplementedError
