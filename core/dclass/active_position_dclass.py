from pydantic import BaseModel


class ActivePosition(BaseModel):
    status: str
    symbol: str
    units_dex: float
    total_balance_before_usd: float
    initial_balance_dex_usd: float
    initial_balance_hl_usd: float
    total_balance_after_usd: float
    entry_price_hl: float
    entry_price_dex: float
    timestamp: str
