import asyncio
import logging

from core.config.properties_multi import PropertiesMulti
from core.pools.pool_finder import PoolFinder
from core.strategies.multi_chain_strategy import MultiChainStrategy
from core.web3.wallet_manager import WalletManager
from core.web3.web3_manager import Web3Manager


class MultiChainBot():
    def __init__(self, properties: PropertiesMulti):
        self.web3_manager = Web3Manager()

        self.web3_manager = Web3Manager()
        self.finder = PoolFinder(self.web3_manager)  # A classe que criámos

        print(properties.WALLET_ADDRESS)
        self.wallet = WalletManager(self.web3_manager, properties)

        self.multi_chain = MultiChainStrategy(self.web3_manager, properties, self.finder, self.wallet,
                                              self.wallet.get_usdc_balance())

    @property
    def w3(self):
        """Retorna o Web3 atualizado do manager sempre que o core precisar dele"""
        return self.web3_manager.w3

    async def run(self):
        logging.info(f"Conectado ao RPC: {self.w3.is_connected()}")
        logging.info(f"Monitor Ativo. Bloco: {self.w3.eth.block_number}")
        while True:
            try:

                await self.multi_chain.analyze_all_pairs()
                await asyncio.sleep(2)
            except Exception as e:
                logging.error(f"Erro no loop: {e}")
                await asyncio.sleep(2)
