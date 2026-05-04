from abc import abstractmethod, ABC

from core.config.properties_base import PropertiesBase


class WalletBase(ABC):
    @abstractmethod
    def __init__(self, web3_manager, properties: PropertiesBase):
        pass

    @abstractmethod
    def check_and_approve_executor(self, amount_usd: float):
        pass

    @abstractmethod
    def send_transaction(self, pools_list: list[str], dir_list: list[bool], tokens_list: list[str], amount_usd: float):
        pass

    @abstractmethod
    def get_usdc_balance(self) -> int:
        pass

    @abstractmethod
    def get_token_balance(self, token_address: str) -> int:
        pass

    @abstractmethod
    def get_gas_cost_usd(self, eth_price: (float | None)) -> float:
        pass

    @abstractmethod
    def is_swap_viable(self, token_in: str, token_out: str, amount_in_usd: float, expected_out_units: float,
                       fee: int = 3000, tolerance: float = 0.007) -> tuple[bool, float]:
        pass
