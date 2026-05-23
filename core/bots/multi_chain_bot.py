import asyncio
import logging

from core.config.properties_multi import PropertiesMulti
from core.pools.pool_finder import PoolFinder
from core.strategies.multi_chain_strategy import MultiChainStrategy
from core.web3.executors.multi_chain_executor import MultiChainExecutor
from core.web3.rpcs.web3_manager import Web3Manager


class MultiChainBot():
    def __init__(self, properties: PropertiesMulti):
        self.web3_manager = Web3Manager()
        self.finder = PoolFinder(self.web3_manager)  # A classe que criámos
        self.wallet = MultiChainExecutor(properties)
        self.multi_chain = MultiChainStrategy(self.web3_manager, properties, self.finder, self.wallet, 0.0)

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

                await asyncio.sleep(20)
            except Exception as e:
                logging.error(f"Erro no loop: {e}")
                await asyncio.sleep(20)

    def test_manual_quote(self):
        # 1. Setup das configurações e wallet
        props = PropertiesMulti()
        # Substitui pela tua classe de wallet real

        print("🧪 Iniciando Teste Manual do Quoter...")

        # 2. Definir parâmetros de teste (Simular ARB -> USDC)
        token_arb = "0x912CE59144191C1204E64559FE8253a0e49E6548"
        token_usdc = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"

        # Vamos simular que temos 100 ARB e queremos ver quanto USDC recebemos
        units_to_sell = 100.0
        price_now = 0.116  # Preço aproximado do teu log
        expected_usdc = units_to_sell * price_now  # ~ $11.60

        print(f"Ref: Vendendo {units_to_sell} ARB. Esperado: ~${expected_usdc}")

        # 3. Chamada manual do método
        # Nota: Se o teu método for síncrono, remove o await
        viable, amount_out = self.wallet.is_swap_viable(
            token_in=token_arb,
            token_out=token_usdc,
            amount_in_usd=100,
            expected_out_units=11.0,
            tolerance=0.02,  # 2% de tolerância,
            fee=500

        )

        # 4. Resultados
        print("-" * 30)
        print(f"RESULTADO: {'✅ VIÁVEL' if viable else '❌ INVIÁVEL'}")
        print(f"Recebido na Simulação: ${amount_out:.4f} USDC")
        print("-" * 30)
