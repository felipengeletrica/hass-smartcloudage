"""A/C infrared encoders for the SmartCloudAge IR emitter.

Python port of the SmartCloudAge Command Center HVAC encoders
(``Commands/Hvac``). :func:`build_payload` turns a logical A/C state into the
payload of firmware command 32 (``COMMAND_IR_SEND_ENUM``).
"""

from __future__ import annotations

from .base import HvacProtocol
from .coolix import CoolixHvacProtocol
from .daikin import DaikinHvacProtocol
from .electrolux import ElectroluxHvacProtocol
from .fujitsu import FujitsuHvacProtocol
from .gree import GreeHvacProtocol
from .haier import HaierHvacProtocol
from .hisense import HisenseHvacProtocol
from .hitachi import HitachiHvacProtocol
from .lg import LgHvacProtocol
from .midea import MideaHvacProtocol
from .mitsubishi import MitsubishiElectricHvacProtocol
from .models import (
    AcBrand,
    AcCommandState,
    AcFanSpeed,
    AcMode,
    AcSwingHorizontal,
    AcSwingVertical,
    HvacCapabilities,
)
from .panasonic import PanasonicHvacProtocol
from .samsung import SamsungHvacProtocol
from .tcl import TclHvacProtocol
from .toshiba import ToshibaHvacProtocol

## Brands with a state encoder, in display order.
PROTOCOLS: dict[AcBrand, HvacProtocol] = {
    protocol.brand: protocol
    for protocol in (
        LgHvacProtocol(),
        SamsungHvacProtocol(),
        DaikinHvacProtocol(),
        MideaHvacProtocol(),
        GreeHvacProtocol(),
        PanasonicHvacProtocol(),
        MitsubishiElectricHvacProtocol(),
        FujitsuHvacProtocol(),
        ElectroluxHvacProtocol(),
        HitachiHvacProtocol(),
        ToshibaHvacProtocol(),
        CoolixHvacProtocol(),
        TclHvacProtocol(),
        HaierHvacProtocol(),
        HisenseHvacProtocol(),
    )
}

## Name shown to the user, including the common rebrands.
BRAND_NAMES = {
    AcBrand.MIDEA: "Midea / Springer / Carrier",
    AcBrand.GREE: "Gree / Elgin",
    AcBrand.MITSUBISHI_ELECTRIC: "Mitsubishi Electric",
    AcBrand.COOLIX: "Coolix (Midea RG57, genéricos)",
    AcBrand.TCL: "TCL",
}


def brand_name(brand: AcBrand) -> str:
    """Display name of a brand."""
    return BRAND_NAMES.get(brand, brand.value)


def get_protocol(brand: AcBrand | str) -> HvacProtocol:
    """Encoder registered for ``brand``."""
    return PROTOCOLS[AcBrand(brand)]


def build_payload(state: AcCommandState) -> dict:
    """Command-32 payload (protocol mode) for a full logical A/C state."""
    return get_protocol(state.brand).build(state)


__all__ = [
    "AcBrand",
    "AcCommandState",
    "AcFanSpeed",
    "AcMode",
    "AcSwingHorizontal",
    "AcSwingVertical",
    "HvacCapabilities",
    "PROTOCOLS",
    "brand_name",
    "build_payload",
    "get_protocol",
]
