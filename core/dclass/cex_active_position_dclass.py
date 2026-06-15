from pydantic import BaseModel

from core.dclass.cex_type_enum import CexType


class CexActivePosition(BaseModel):
    status: str  # "OPEN" ou "CLOSED"
    symbol: str  # Ex: "BTC/USDC:USDC"
    type: CexType  # "LIGHTER_TO_HL" ou "HL_TO_LIGHTER"
    qty_pair: float  # A quantidade exata do ativo (ex: BTC) aberta em ambas as plataformas

    # Saldos iniciais (Para a tua Soma Soberana)
    initial_balance_lighter_usd: float  # Saldo USDC na Lighter antes do trade
    initial_balance_hl_usd: float  # Saldo USDC na Hyperliquid antes do trade
    capital_to_trade_usd: float

    # Preços de Entrada (Para histórico e auditoria)
    entry_price_hl: float
    entry_price_lighter: float

    timestamp: str
