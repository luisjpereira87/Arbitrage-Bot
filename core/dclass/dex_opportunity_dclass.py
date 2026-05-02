from dataclasses import dataclass


@dataclass
class DexOpportunity:
    strategy: str
    profit: float
    spread: float
    symbol: str
    price_dex: float
    price_hl: float
    pool_addr: str
    dex_name: str
    dex_fee: float
    direction: bool
