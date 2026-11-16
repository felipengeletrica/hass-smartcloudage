"""TV remote codes and their command-32 frames.

Port of the SCA Command ``IrRemoteEncoder`` and ``RemoteCatalog``, which in
turn port the IRremoteESP8266 ``IRsend::send*`` / ``encode*`` pairs: every
frame is pulse for pulse what that library sends. ``tests/fixtures/
tv_remote_golden.tsv`` holds the payload the SCA Command builds for every key
of every built-in remote; the tests compare this module against it.

Pulse-distance and pulse-width protocols (NEC, Samsung, Sony, JVC, Panasonic)
go in protocol mode, MSB first. Bi-phase protocols (RC-5, RC-6) go in raw mode.
The trailing silence of a message is sent as the stop space, so repeated
presses need no extra frame gap.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class IrProtocol(StrEnum):
    """Remote control protocols of the built-in remotes."""

    NEC = "nec"  # 8-bit address (sent with its inverse), 8-bit command
    NEC_EXT = "nec_ext"  # 16-bit address, 8-bit command
    SAMSUNG32 = "samsung32"  # 8-bit customer sent twice, 8-bit command
    SONY12 = "sony12"  # 5-bit device, 7-bit command, sent three times
    SONY15 = "sony15"  # 8-bit device
    SONY20 = "sony20"  # 13-bit device (device | extended << 5)
    JVC = "jvc"  # 8-bit address, 8-bit command
    PANASONIC = "panasonic"  # address = device << 8 | subdevice, command = function
    RC5 = "rc5"  # Philips, 5-bit address, 7-bit command (RC-5X above 63), toggle
    RC6 = "rc6"  # Philips mode 0, 8-bit address, 8-bit command, toggle


## Keys of a TV remote, in the order shown in Home Assistant.
KEYS: tuple[str, ...] = (
    "power", "power_on", "power_off", "input", "mute",
    "volume_up", "volume_down", "channel_up", "channel_down",
    "up", "down", "left", "right", "ok",
    "menu", "home", "back", "exit", "info",
    "digit_0", "digit_1", "digit_2", "digit_3", "digit_4",
    "digit_5", "digit_6", "digit_7", "digit_8", "digit_9",
    "play", "pause", "stop", "fast_forward", "rewind",
)

## Everyday keys (the "simple" remote): their buttons are enabled by default;
## digits and media keys are created disabled and can be enabled one by one.
ESSENTIAL_KEYS: frozenset[str] = frozenset({
    "power", "input", "mute", "volume_up", "volume_down", "channel_up", "channel_down",
    "up", "down", "left", "right", "ok", "back", "home", "exit",
})


@dataclass(frozen=True)
class TvPreset:
    """A built-in remote: one protocol and address, a command per key."""

    id: str
    name: str
    brand: str
    protocol: IrProtocol
    address: int
    keys: dict[str, int]


def _digits(*zero_to_nine: int) -> dict[str, int]:
    return {f"digit_{i}": code for i, code in enumerate(zero_to_nine)}


## Built-in remotes. Codes are well-known codes of each brand (Samsung and LG
## checked against their published 32-bit hex codes); models vary, so confirm
## the keys on the bench. Same catalog as the SCA Command.
PRESETS: dict[str, TvPreset] = {
    preset.id: preset
    for preset in (
        # Samsung32, customer 0x07: E0E040BF = power, E0E0E01F = vol+ ...
        TvPreset("samsung-tv", "TV Samsung", "Samsung", IrProtocol.SAMSUNG32, 0x07, {
            "power": 0x02, "power_on": 0x99, "power_off": 0x98, "input": 0x01, "mute": 0x0F,
            "volume_up": 0x07, "volume_down": 0x0B, "channel_up": 0x12, "channel_down": 0x10,
            "up": 0x60, "down": 0x61, "left": 0x65, "right": 0x62,
            "ok": 0x68, "menu": 0x1A, "home": 0x79, "back": 0x58, "exit": 0x2D, "info": 0x1F,
            "play": 0x47, "pause": 0x4A, "stop": 0x46, "fast_forward": 0x48, "rewind": 0x45,
            **_digits(0x11, 0x04, 0x05, 0x06, 0x08, 0x09, 0x0A, 0x0C, 0x0D, 0x0E),
        }),
        # NEC, address 0x04: 20DF10EF = power, 20DF40BF = vol+ ...
        TvPreset("lg-tv", "TV LG", "LG", IrProtocol.NEC, 0x04, {
            "power": 0x08, "power_on": 0xC4, "power_off": 0xC5, "input": 0x0B, "mute": 0x09,
            "volume_up": 0x02, "volume_down": 0x03, "channel_up": 0x00, "channel_down": 0x01,
            "up": 0x40, "down": 0x41, "left": 0x07, "right": 0x06,
            "ok": 0x44, "menu": 0x43, "home": 0x7C, "back": 0x28, "exit": 0x5B, "info": 0xAA,
            "play": 0xB0, "pause": 0xBA, "stop": 0xB1,
            **_digits(0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19),
        }),
        # Sony SIRC 12 bits, device 1 (TV).
        TvPreset("sony-tv", "TV Sony", "Sony", IrProtocol.SONY12, 0x01, {
            "power": 21, "power_on": 46, "power_off": 47, "input": 37, "mute": 20,
            "volume_up": 18, "volume_down": 19, "channel_up": 16, "channel_down": 17,
            "up": 116, "down": 117, "left": 52, "right": 51,
            "ok": 101, "home": 96, "info": 58,
            **_digits(9, 0, 1, 2, 3, 4, 5, 6, 7, 8),
        }),
        # Philips RC-6 mode 0, address 0 (TV).
        TvPreset("philips-tv-rc6", "TV Philips (RC-6)", "Philips", IrProtocol.RC6, 0x00, {
            "power": 0x0C, "input": 0x38, "mute": 0x0D,
            "volume_up": 0x10, "volume_down": 0x11, "channel_up": 0x4C, "channel_down": 0x4D,
            "up": 0x58, "down": 0x59, "left": 0x5A, "right": 0x5B,
            "ok": 0x5C, "home": 0x54, "back": 0x0A, "info": 0x0F,
            "play": 0x2C, "pause": 0x30, "stop": 0x31, "fast_forward": 0x28, "rewind": 0x2B,
            **_digits(0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
        }),
        # Philips RC-5, address 0 (older TVs).
        TvPreset("philips-tv-rc5", "TV Philips (RC-5)", "Philips", IrProtocol.RC5, 0x00, {
            "power": 12, "mute": 13, "volume_up": 16, "volume_down": 17,
            "channel_up": 32, "channel_down": 33,
            **_digits(0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
        }),
        # Panasonic/Kaseikyo, device 0x01, subdevice 0x00: 40040100BCBD = power.
        TvPreset("panasonic-tv", "TV Panasonic", "Panasonic", IrProtocol.PANASONIC, 0x0100, {
            "power": 0xBC, "input": 0xA0, "mute": 0x4C,
            "volume_up": 0x04, "volume_down": 0x84, "channel_up": 0x2C, "channel_down": 0xAC,
            **_digits(0x98, 0x08, 0x88, 0x48, 0xC8, 0x28, 0xA8, 0x68, 0xE8, 0x18),
        }),
    )
}


def preset_keys(preset: TvPreset) -> list[str]:
    """Keys the remote has, in display order."""
    return [key for key in KEYS if key in preset.keys]


# ---------------------------------------------------------------------------
# Timings (IRremoteESP8266)
# ---------------------------------------------------------------------------

# NEC and Samsung: 560 us ticks (ir_NEC.h, ir_Samsung.cpp).
_NEC_HDR_MARK, _NEC_HDR_SPACE, _NEC_BIT_MARK = 16 * 560, 8 * 560, 560
_NEC_ONE_SPACE, _NEC_ZERO_SPACE = 3 * 560, 560
_NEC_MIN_COMMAND_LENGTH = 193 * 560
_NEC_MIN_GAP = (193 - (16 + 8 + 32 * 4 + 1)) * 560
_SAMSUNG_HDR_MARK, _SAMSUNG_HDR_SPACE = 8 * 560, 8 * 560
# Sony: 40 kHz, sent three times in 45 ms periods (ir_Sony.cpp).
_SONY_HDR_MARK, _SONY_SPACE, _SONY_ONE_MARK, _SONY_ZERO_MARK = 2400, 600, 1200, 600
_SONY_RPT_LENGTH, _SONY_MIN_GAP, _SONY_MIN_REPEAT, _SONY_FREQ = 45000, 10000, 2, 40000
# JVC: 75 us ticks, 60 ms period (ir_JVC.cpp).
_JVC_HDR_MARK, _JVC_HDR_SPACE, _JVC_BIT_MARK = 112 * 75, 56 * 75, 7 * 75
_JVC_ONE_SPACE, _JVC_ZERO_SPACE, _JVC_RPT_LENGTH = 23 * 75, 7 * 75, 800 * 75
_JVC_MIN_GAP = (800 - (112 + 56 + 16 * (7 + 23) + 7)) * 75
# Panasonic/Kaseikyo (ir_Panasonic.cpp).
_PAN_HDR_MARK, _PAN_HDR_SPACE, _PAN_BIT_MARK = 3456, 1728, 432
_PAN_ONE_SPACE, _PAN_ZERO_SPACE = 1296, 432
_PAN_MIN_COMMAND_LENGTH, _PAN_MIN_GAP, _PAN_FREQ = 163296, 74736, 36700
_PAN_MANUFACTURER = 0x4004
# Philips RC-5 / RC-6 (ir_RC5_RC6.cpp).
_RC5_T1 = 889
_RC6_TICK = 444
_RC6_HDR_MARK, _RC6_HDR_SPACE = 6 * _RC6_TICK, 2 * _RC6_TICK

## Raw items the firmware accepts.
MAX_RAW_ITEMS = 300

_LIMITS = {
    IrProtocol.NEC: (0xFF, 0xFF),
    IrProtocol.NEC_EXT: (0xFFFF, 0xFF),
    IrProtocol.SAMSUNG32: (0xFF, 0xFF),
    IrProtocol.SONY12: (0x1F, 0x7F),
    IrProtocol.SONY15: (0xFF, 0x7F),
    IrProtocol.SONY20: (0x1FFF, 0x7F),
    IrProtocol.JVC: (0xFF, 0xFF),
    IrProtocol.PANASONIC: (0xFFFF, 0xFF),
    IrProtocol.RC5: (0x1F, 0x7F),
    IrProtocol.RC6: (0xFF, 0xFF),
}


# ---------------------------------------------------------------------------
# encode* ports (values sent MSB first)
# ---------------------------------------------------------------------------

def reverse_bits(value: int, nbits: int) -> int:
    """Reverse the lowest ``nbits`` bits (IRutils reverseBits)."""
    result = 0
    for _ in range(nbits):
        result = (result << 1) | (value & 1)
        value >>= 1
    return result


def encode_nec(address: int, command: int, extended: bool = False) -> int:
    """IRsend::encodeNEC (extended when the address does not fit in 8 bits)."""
    cmd = reverse_bits(command, 8)
    cmd = (cmd << 8) + (cmd ^ 0xFF)
    if extended or address > 0xFF:
        return (reverse_bits(address, 16) << 16) + cmd
    addr = reverse_bits(address, 8)
    return (addr << 24) + ((addr ^ 0xFF) << 16) + cmd


def encode_samsung(customer: int, command: int) -> int:
    """IRsend::encodeSAMSUNG."""
    rc, rcmd = reverse_bits(customer, 8), reverse_bits(command, 8)
    return (rcmd ^ 0xFF) | (rcmd << 8) | (rc << 16) | (rc << 24)


def encode_sony(nbits: int, command: int, address: int) -> int:
    """IRsend::encodeSony for 12, 15 or 20 bits."""
    mask = {12: 0x1F, 15: 0xFF, 20: 0x1FFF}[nbits]
    return reverse_bits(((address & mask) << 7) | (command & 0x7F), nbits)


def encode_jvc(address: int, command: int) -> int:
    """IRsend::encodeJVC."""
    return reverse_bits((command << 8) | address, 16)


def encode_panasonic(manufacturer: int, device: int, subdevice: int, function: int) -> int:
    """IRsend::encodePanasonic (checksum = device ^ subdevice ^ function)."""
    checksum = (device ^ subdevice ^ function) & 0xFF
    return (manufacturer << 32) | (device << 24) | (subdevice << 16) | (function << 8) | checksum


# ---------------------------------------------------------------------------
# Frames
# ---------------------------------------------------------------------------

def _pulse(freq, duty, hdr_mark, hdr_space, one_mark, one_space, zero_mark, zero_space,
           footer_mark, min_gap, message_time, data, nbits, repeat, gpio) -> dict:
    """Protocol-mode payload equivalent to IRsend::sendGeneric: header, bits MSB
    first, footer mark, then max(min_gap, message_time - frame time) of silence."""
    frame = hdr_mark + hdr_space + footer_mark
    for i in range(nbits - 1, -1, -1):
        frame += one_mark + one_space if (data >> i) & 1 else zero_mark + zero_space
    payload = {"mode": "protocol"}
    if gpio is not None:
        payload["gpio"] = gpio
    payload.update({
        "frequency": freq,
        "duty_cycle": duty,
        "repeat": repeat,
        "frame_gap_us": 0,
        "nbits": nbits,
        "bit_order": "MSB",
        "header_mark_us": hdr_mark,
        "header_space_us": hdr_space,
        "one_mark_us": one_mark,
        "one_space_us": one_space,
        "zero_mark_us": zero_mark,
        "zero_space_us": zero_space,
        "stop_mark_us": footer_mark,
        "stop_space_us": max(min_gap, message_time - frame),
        "data": "0x" + format(data, f"0{(nbits + 3) // 4}X"),
    })
    return payload


def _raw(items: list[int], freq: int, duty: int, gpio) -> dict:
    """Raw-mode payload; the list starts with a mark."""
    if len(items) > MAX_RAW_ITEMS:
        raise ValueError(f"{len(items)} raw items; the firmware accepts at most {MAX_RAW_ITEMS}")
    payload = {"mode": "raw"}
    if gpio is not None:
        payload["gpio"] = gpio
    payload.update({
        "frequency": freq,
        "duty_cycle": duty,
        "repeat": 0,
        "frame_gap_us": 30000,
        "sequence_us": items,
    })
    return payload


class _Levels:
    """Mark/space list that merges consecutive items of the same level."""

    def __init__(self) -> None:
        self.items: list[int] = []
        self._last_mark = False

    def add(self, mark: bool, us: int) -> None:
        if us <= 0:
            return
        if not self.items and not mark:
            return  # leading silence
        if self.items and mark == self._last_mark:
            self.items[-1] += us
        else:
            self.items.append(us)
            self._last_mark = mark

    def done(self) -> list[int]:
        """The list without trailing silence (the frame gap covers it)."""
        if self.items and not self._last_mark:
            self.items.pop()
        return self.items


def rc5_items(address: int, command: int, toggle: bool) -> list[int]:
    """IRsend::sendRC5 with encodeRC5/encodeRC5X: Manchester, 1 = space then mark."""
    field_bit = ((command >> 6) & 1) == 0  # RC-5X: inverted 7th command bit
    data = (int(toggle) << 11) | ((address & 0x1F) << 6) | (command & 0x3F)
    levels = _Levels()
    levels.add(False, _RC5_T1)
    levels.add(True, _RC5_T1)  # first start bit (1); leading space dropped
    if field_bit:
        levels.add(False, _RC5_T1)
        levels.add(True, _RC5_T1)
    else:
        levels.add(True, _RC5_T1)
        levels.add(False, _RC5_T1)
    for i in range(11, -1, -1):
        if (data >> i) & 1:
            levels.add(False, _RC5_T1)
            levels.add(True, _RC5_T1)
        else:
            levels.add(True, _RC5_T1)
            levels.add(False, _RC5_T1)
    return levels.done()


def rc6_items(address: int, command: int, toggle: bool) -> list[int]:
    """IRsend::sendRC6 mode 0: 1 = mark then space; the 4th bit (toggle) is double width."""
    data = (int(toggle) << 16) | (address << 8) | command  # mode 000 in the top 3 bits
    levels = _Levels()
    levels.add(True, _RC6_HDR_MARK)
    levels.add(False, _RC6_HDR_SPACE)
    levels.add(True, _RC6_TICK)
    levels.add(False, _RC6_TICK)  # start bit (1)
    for n, i in enumerate(range(19, -1, -1), start=1):
        t = 2 * _RC6_TICK if n == 4 else _RC6_TICK
        if (data >> i) & 1:
            levels.add(True, t)
            levels.add(False, t)
        else:
            levels.add(False, t)
            levels.add(True, t)
    return levels.done()


def encode(protocol: IrProtocol, address: int, command: int, toggle: bool = False,
           gpio: int | None = None) -> dict:
    """Command-32 payload for one key press.

    @param toggle RC-5/RC-6 toggle bit: flip it on every new press so the TV
                  sees a new key instead of a held one.
    @param gpio IR output pin; None lets the firmware use its default.
    @throws ValueError for an address or command out of the protocol range.
    """
    max_address, max_command = _LIMITS[protocol]
    if not 0 <= address <= max_address:
        raise ValueError(f"address above 0x{max_address:X} for {protocol}")
    if not 0 <= command <= max_command:
        raise ValueError(f"command above 0x{max_command:X} for {protocol}")
    a, c = address & 0xFF, command & 0xFF

    if protocol in (IrProtocol.NEC, IrProtocol.NEC_EXT):
        return _pulse(38000, 33, _NEC_HDR_MARK, _NEC_HDR_SPACE, _NEC_BIT_MARK, _NEC_ONE_SPACE,
                      _NEC_BIT_MARK, _NEC_ZERO_SPACE, _NEC_BIT_MARK, _NEC_MIN_GAP, _NEC_MIN_COMMAND_LENGTH,
                      encode_nec(address & 0xFFFF, c, protocol == IrProtocol.NEC_EXT), 32, 0, gpio)
    if protocol == IrProtocol.SAMSUNG32:
        return _pulse(38000, 33, _SAMSUNG_HDR_MARK, _SAMSUNG_HDR_SPACE, _NEC_BIT_MARK, _NEC_ONE_SPACE,
                      _NEC_BIT_MARK, _NEC_ZERO_SPACE, _NEC_BIT_MARK, _NEC_MIN_GAP, _NEC_MIN_COMMAND_LENGTH,
                      encode_samsung(a, c), 32, 0, gpio)
    if protocol in (IrProtocol.SONY12, IrProtocol.SONY15, IrProtocol.SONY20):
        nbits = {IrProtocol.SONY12: 12, IrProtocol.SONY15: 15}.get(protocol, 20)
        # No footer mark; each of the three frames fills a 45 ms period.
        return _pulse(_SONY_FREQ, 33, _SONY_HDR_MARK, _SONY_SPACE, _SONY_ONE_MARK, _SONY_SPACE,
                      _SONY_ZERO_MARK, _SONY_SPACE, 0, _SONY_MIN_GAP, _SONY_RPT_LENGTH,
                      encode_sony(nbits, command, address), nbits, _SONY_MIN_REPEAT, gpio)
    if protocol == IrProtocol.JVC:
        return _pulse(38000, 33, _JVC_HDR_MARK, _JVC_HDR_SPACE, _JVC_BIT_MARK, _JVC_ONE_SPACE,
                      _JVC_BIT_MARK, _JVC_ZERO_SPACE, _JVC_BIT_MARK, _JVC_MIN_GAP, _JVC_RPT_LENGTH,
                      encode_jvc(a, c), 16, 0, gpio)
    if protocol == IrProtocol.PANASONIC:
        return _pulse(_PAN_FREQ, 50, _PAN_HDR_MARK, _PAN_HDR_SPACE, _PAN_BIT_MARK, _PAN_ONE_SPACE,
                      _PAN_BIT_MARK, _PAN_ZERO_SPACE, _PAN_BIT_MARK, _PAN_MIN_GAP, _PAN_MIN_COMMAND_LENGTH,
                      encode_panasonic(_PAN_MANUFACTURER, (address >> 8) & 0xFF, address & 0xFF, c),
                      48, 0, gpio)
    if protocol == IrProtocol.RC5:
        return _raw(rc5_items(a, c, toggle), 36000, 25, gpio)
    if protocol == IrProtocol.RC6:
        return _raw(rc6_items(a, c, toggle), 36000, 33, gpio)
    raise ValueError(f"unknown protocol {protocol}")


def encode_key(preset: TvPreset, key: str, toggle: bool = False, gpio: int | None = None) -> dict:
    """Command-32 payload of one key of a built-in remote.

    @throws KeyError when the remote has no such key.
    """
    return encode(preset.protocol, preset.address, preset.keys[key], toggle, gpio)
