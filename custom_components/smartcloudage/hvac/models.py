"""Logical A/C state shared by every brand encoder.

Port of ``AcInfraredModels.cs`` from the SmartCloudAge Command Center.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum


class AcBrand(str, Enum):
    """A/C vendors with a state encoder."""

    LG = "LG"
    SAMSUNG = "Samsung"
    DAIKIN = "Daikin"
    MIDEA = "Midea"
    GREE = "Gree"
    PANASONIC = "Panasonic"
    MITSUBISHI_ELECTRIC = "MitsubishiElectric"
    FUJITSU = "Fujitsu"
    ELECTROLUX = "Electrolux"
    HITACHI = "Hitachi"
    TOSHIBA = "Toshiba"
    COOLIX = "Coolix"
    TCL = "Tcl"
    HAIER = "Haier"
    HISENSE = "Hisense"


class AcMode(str, Enum):
    """Logical operating mode."""

    AUTO = "Auto"
    COOL = "Cool"
    DRY = "Dry"
    FAN = "Fan"
    HEAT = "Heat"
    OFF = "Off"


class AcFanSpeed(str, Enum):
    """Logical fan speed."""

    AUTO = "Auto"
    QUIET = "Quiet"
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    MAX = "Max"


class AcSwingVertical(str, Enum):
    """Vertical vane position or swing."""

    OFF = "Off"
    AUTO = "Auto"
    POS1 = "Pos1"
    POS2 = "Pos2"
    POS3 = "Pos3"
    POS4 = "Pos4"
    POS5 = "Pos5"
    SWING = "Swing"


class AcSwingHorizontal(str, Enum):
    """Horizontal vane position or swing."""

    OFF = "Off"
    AUTO = "Auto"
    LEFT = "Left"
    LEFT_CENTER = "LeftCenter"
    CENTER = "Center"
    RIGHT_CENTER = "RightCenter"
    RIGHT = "Right"
    SWING = "Swing"


@dataclass(frozen=True)
class AcCommandState:
    """Complete logical A/C state converted to one IR frame."""

    brand: AcBrand
    model: str
    power: bool
    mode: AcMode
    temperature_c: int
    fan_speed: AcFanSpeed
    gpio: int | None = None
    repeat: int = 0
    frame_gap_us: int = 30000
    duty_cycle: int = 33
    swing_vertical: AcSwingVertical = AcSwingVertical.OFF
    swing_horizontal: AcSwingHorizontal = AcSwingHorizontal.OFF
    turbo: bool = False
    eco: bool = False
    sleep: bool = False
    light: bool = False
    beep: bool = False
    clean: bool = False
    quiet: bool = False
    powerful: bool = False
    sensor_ifeel: bool = False

    def normalize(self, min_temp_c: int = 16, max_temp_c: int = 30) -> AcCommandState:
        """Return a copy with safe bounds; power off clears turbo."""
        temp = max(min_temp_c, min(self.temperature_c, max_temp_c))
        repeat = max(0, self.repeat)
        frame_gap = 30000 if self.frame_gap_us <= 0 else self.frame_gap_us
        duty = 33 if self.duty_cycle <= 0 else min(self.duty_cycle, 100)
        if not self.power or self.mode == AcMode.OFF:
            return replace(
                self,
                power=False,
                mode=AcMode.OFF,
                temperature_c=temp,
                repeat=repeat,
                frame_gap_us=frame_gap,
                duty_cycle=duty,
                turbo=False,
                powerful=False,
            )
        return replace(
            self,
            temperature_c=temp,
            repeat=repeat,
            frame_gap_us=frame_gap,
            duty_cycle=duty,
        )


@dataclass(frozen=True)
class HvacCapabilities:
    """What one brand/model encoder can send."""

    min_temperature_c: int
    max_temperature_c: int
    supports_swing_vertical: bool = False
    supports_swing_horizontal: bool = False
    supports_turbo: bool = False
    supports_eco: bool = False
    supports_sleep: bool = False
    supports_light: bool = False
    supports_clean: bool = False
    supports_quiet: bool = False
    supports_ifeel: bool = False
