from dataclasses import dataclass


@dataclass
class WatchedPair:
    addr_a: str
    addr_b: str
    symbol_a: str
    symbol_b: str
    decimal_a: int
    decimal_b: int
    hl_pair: str
    pools_map: dict[str, str]
    z4o: bool
