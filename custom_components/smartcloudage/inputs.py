"""Digital input helpers shared by the binary_sensor and sensor platforms.

The controller publishes ``INPUT_STATUS`` on ``CloudAge/<device_id>/OutTopic/``
whenever a debounced input changes (and once at boot). The ``message`` field
carries a JSON string with a 16-bit mask: input 1 is bit 0, input 16 is bit 15.
"""

from __future__ import annotations

import json
from typing import Any

## Number of digital inputs on a CloudAge controller.
INPUT_COUNT = 16
## Firmware message name of the input mask.
INPUT_STATUS = "INPUT_STATUS"


## @brief Decodes a controller payload and merges the inner JSON "message".
#  @param raw MQTT payload as text.
#  @return Decoded dictionary (inner fields win over outer ones).
#  @throws ValueError When @p raw is not a JSON object.
def decode_payload(raw: str) -> dict[str, Any]:
    """Decode the double-JSON envelope used by the firmware."""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("payload is not a JSON object")
    inner = data.get("message")
    if isinstance(inner, str) and inner.lstrip().startswith("{"):
        try:
            inner_obj = json.loads(inner)
        except json.JSONDecodeError:
            inner_obj = None
        if isinstance(inner_obj, dict):
            data = {**data, **inner_obj}
    return data


## @brief Extracts the input mask from a decoded payload.
#  @param data Payload returned by @ref decode_payload.
#  @return Input mask, or @c None when the payload is not an INPUT_STATUS.
def input_mask(data: dict[str, Any]) -> int | None:
    """Return the INPUT_STATUS mask, or None for any other message."""
    if str(data.get("message", "")).upper() != INPUT_STATUS:
        return None
    section = data.get("Input")
    if not isinstance(section, dict) or section.get("Inputs") is None:
        return None
    value = section["Inputs"]
    if isinstance(value, bool):
        return None
    return int(value) & ((1 << INPUT_COUNT) - 1)


## @brief Tells whether one input is active.
#  @param mask Input mask from the controller.
#  @param channel Input number, 1 to 16.
#  @param invert Swap the meaning of the bit (contact wired the other way).
#  @return @c True when the input is active.
def channel_active(mask: int, channel: int, invert: bool = False) -> bool:
    """Read one input from the mask."""
    return bool((mask >> (channel - 1)) & 1) != bool(invert)


## @brief Parses the level points typed in the options flow.
#  @param text Comma-separated "channel:percent" pairs, e.g. "1:25, 2:50".
#  @return Points sorted by percent.
#  @throws ValueError On bad syntax, out-of-range or duplicated values.
def parse_level_points(text: str) -> list[dict[str, int]]:
    """Parse "1:25, 2:50, 3:75, 4:100" into level points."""
    points: list[dict[str, int]] = []
    for item in text.replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        channel_text, sep, percent_text = item.partition(":")
        if not sep:
            raise ValueError(f"missing ':' in {item!r}")
        channel = int(channel_text.strip())
        percent = int(percent_text.strip().rstrip("%"))
        if not 1 <= channel <= INPUT_COUNT:
            raise ValueError(f"channel {channel} out of range")
        if not 0 < percent <= 100:
            raise ValueError(f"percent {percent} out of range")
        points.append({"channel": channel, "percent": percent})
    if not points:
        raise ValueError("no level points")
    if len({p["channel"] for p in points}) != len(points):
        raise ValueError("duplicated channel")
    if len({p["percent"] for p in points}) != len(points):
        raise ValueError("duplicated percent")
    return sorted(points, key=lambda p: p["percent"])


## @brief Formats level points back into the options-flow text.
#  @param points Stored level points.
#  @return Text accepted by @ref parse_level_points.
def format_level_points(points: list[dict[str, int]]) -> str:
    """Format level points as "1:25, 2:50"."""
    return ", ".join(f"{p['channel']}:{p['percent']}" for p in points)


## @brief Computes a stepped level from float switches.
#  @param mask Input mask from the controller.
#  @param points Level points (channel and percent of each switch).
#  @param invert Swap the meaning of every bit.
#  @return Tuple (level percent, consistent). The level is the highest active
#          point (0 when none is active). @c consistent is @c False when a
#          switch is active while a lower one is not, which points to a stuck
#          or broken switch.
def compute_level(
    mask: int, points: list[dict[str, int]], invert: bool = False
) -> tuple[int, bool]:
    """Return the level percent and whether the switches agree."""
    ordered = sorted(points, key=lambda p: p["percent"])
    states = [channel_active(mask, p["channel"], invert) for p in ordered]
    level = 0
    for point, active in zip(ordered, states):
        if active:
            level = point["percent"]
    highest = max((i for i, active in enumerate(states) if active), default=-1)
    consistent = all(states[: highest + 1])
    return level, consistent
