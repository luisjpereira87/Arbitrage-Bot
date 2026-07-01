from dataclasses import dataclass
from enum import IntEnum
from typing import Optional


@dataclass
class PositionStatus:
    exists: bool
    address: Optional[str] = None
    inRange: Optional[bool] = None
    activeBin: Optional[int] = None
    lowerBin: Optional[int] = None
    upperBin: Optional[int] = None
    lowerPrice: Optional[float] = None
    upperPrice: Optional[float] = None
    size: Optional[int] = None
    totalXAmount: Optional[float] = None
    totalYAmount: Optional[float] = None


@dataclass
class MarketStatus:
    sol_balance: float
    usdc_balance: float
    raw_price: float
    wallet: str


@dataclass
class CalculateRange:
    status: Optional[str] = None
    bins_offset: Optional[float] = None
    total_bins_width: Optional[float] = None
    capital_multiplier: Optional[float] = None
    active_bin_id: Optional[float] = None


class RangeStatus(IntEnum):
    INSIDE = 0
    OUT_LOWER = 1
    OUT_UPPER = 2
