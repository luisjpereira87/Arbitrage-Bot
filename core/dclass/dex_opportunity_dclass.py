from dataclasses import dataclass

from core.dclass.chains_enum import Chains


@dataclass
class DexOpportunity:
    chain: Chains
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
    data_quote: dict | None
