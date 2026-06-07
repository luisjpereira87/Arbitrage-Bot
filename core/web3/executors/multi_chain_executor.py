from core.config.properties_base import PropertiesBase
from core.dclass.chains_enum import Chains
from core.web3.executors.arbitrum_executor import ArbitrumExecutor
from core.web3.executors.executor_base import ExecutorBase
from core.web3.executors.solana_executor import SolanaExecutor
from core.web3.rpcs.solana_manager import SolanaManager
from core.web3.rpcs.web3_manager import Web3Manager


class MultiChainExecutor(ExecutorBase):
    def __init__(self, properties: PropertiesBase):
        self.web3_manager = Web3Manager()
        self.solana_manager = SolanaManager()
        self.arbitrum_executor = ArbitrumExecutor(self.web3_manager, properties)
        self.solana_executor = SolanaExecutor(self.solana_manager, properties)

    async def check_and_approve_executor(self, amount_usd: float, chain: Chains) -> bool:
        if chain == Chains.ARBITRUM:
            return await self.arbitrum_executor.check_and_approve_executor(amount_usd, chain)
        else:
            return await self.solana_executor.check_and_approve_executor(amount_usd, chain)

    async def send_transaction(self, pools_list: list[str], dir_list: list[bool], tokens_list: list[str],
                               amount_usd: float,
                               chain: Chains, quote_data: dict | None) -> tuple[bool, float]:

        if chain == Chains.ARBITRUM:
            return await self.arbitrum_executor.send_transaction(pools_list, dir_list, tokens_list, amount_usd, chain,
                                                                 quote_data)
        elif chain == Chains.SOLANA:
            if quote_data is None:
                return False, 0.0
            return await self.solana_executor.send_transaction(pools_list, dir_list, tokens_list, amount_usd, chain,
                                                               quote_data)
        return False, 0.0

    async def get_usdc_balance(self, chain: Chains) -> int:
        if chain == Chains.ARBITRUM:
            return await self.arbitrum_executor.get_usdc_balance(chain)
        elif chain == Chains.SOLANA:
            return await self.solana_executor.get_usdc_balance(chain)
        return 0

    async def get_token_balance(self, token_address: str, chain: Chains) -> int:
        if chain == Chains.ARBITRUM:
            return await self.arbitrum_executor.get_token_balance(token_address, chain)
        elif chain == Chains.SOLANA:
            return await self.solana_executor.get_token_balance(token_address, chain)
        return 0

    async def get_gas_cost_usd(self, eth_price: (float | None), chain: Chains) -> float:
        if chain == Chains.ARBITRUM:
            return await self.arbitrum_executor.get_gas_cost_usd(eth_price, chain)
        elif chain == Chains.SOLANA:
            return 0.0
        return 0.0

    async def is_swap_viable(self, token_in: str, token_out: str, amount_in_usd: float, expected_out_units: float,
                             fee: int, tolerance: float, chain, quote_data: dict | None, is_exit: bool) -> tuple[
        bool, float]:
        if chain == Chains.ARBITRUM:
            return await self.arbitrum_executor.is_swap_viable(token_in, token_out, amount_in_usd, expected_out_units,
                                                               fee,
                                                               tolerance, chain, None, is_exit)
        elif chain == Chains.SOLANA:
            return await self.solana_executor.is_swap_viable(token_in, token_out, amount_in_usd, expected_out_units,
                                                             fee, tolerance, chain, quote_data, is_exit)

        return False, 0.0
