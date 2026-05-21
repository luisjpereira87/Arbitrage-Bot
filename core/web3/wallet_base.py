from abc import abstractmethod, ABC

from core.config.properties_base import PropertiesBase
from core.dclass.chains_enum import Chains


class WalletBase(ABC):
    @abstractmethod
    def __init__(self, web3_manager, properties: PropertiesBase):
        pass

    @abstractmethod
    async def check_and_approve_executor(self, amount_usd: float, chain: Chains) -> bool:
        pass

    @abstractmethod
    async def send_transaction(self, pools_list: list[str], dir_list: list[bool], tokens_list: list[str],
                               amount_usd: float,
                               chain: Chains, quote_data: dict | None):
        pass

    @abstractmethod
    async def get_usdc_balance(self, chain: Chains) -> int:
        pass

    @abstractmethod
    async def get_token_balance(self, token_address: str, chain: Chains) -> int:
        pass

    @abstractmethod
    async def get_gas_cost_usd(self, eth_price: (float | None), chain: Chains) -> float:
        pass

    @abstractmethod
    async def is_swap_viable(self, token_in: str, token_out: str, amount_in_usd: float, expected_out_units: float,
                             fee: int, tolerance: float, chain: Chains) -> tuple[bool, float]:
        pass
