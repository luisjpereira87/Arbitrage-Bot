from dataclasses import dataclass

from core.dclass.cex_type_enum import CexType


@dataclass
class CexOpportunity():
    symbol: str
    route: str
    type: CexType
    capital_to_trade: float
    buy_price: float
    sell_price: float
    qtd_pair: float
    profit_usdc: float
    profit_percent: float
    hl_balance: float
    lighter_balance: float
