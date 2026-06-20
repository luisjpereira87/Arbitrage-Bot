from abc import ABC, abstractmethod

from core.dclass.open_position_dclass import OpenPosition
from core.dclass.opened_order_dclass import OpenedOrder
from core.dclass.prices_dclass import Prices
from core.dclass.signal_enum import Signal


class ExchangeBase(ABC):

    def __init__(self):
        # TODO document why this method is empty
        pass

    @abstractmethod
    def get_name(self):
        return "Exchange"

    @abstractmethod
    async def get_available_balance(self) -> float:
        pass

    @abstractmethod
    async def get_open_position(self, symbol: str) -> (OpenPosition | None):
        pass

    @abstractmethod
    async def cancel_all_orders(self, symbol: str):
        pass

    @abstractmethod
    async def get_prices(self, pair: str) -> (Prices | None):
        pass

    @abstractmethod
    async def get_multiple_prices(self, pairs: list[str]) -> (dict[str, Prices] | None):
        pass

    @abstractmethod
    async def close_position(self, symbol: str, amount: float, side: Signal):
        pass

    @abstractmethod
    async def place_entry_order(self, symbol: str, leverage: float, entry_amount: float, price_ref: float,
                                side: Signal) -> OpenedOrder:
        pass

    @abstractmethod
    async def print_open_orders(self, symbol: str):
        pass

    @abstractmethod
    async def print_balance(self):
        pass

    @abstractmethod
    async def validate_lighter_client(self):
        pass
