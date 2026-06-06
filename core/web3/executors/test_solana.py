import asyncio
import logging

from core.config.properties_multi import PropertiesMulti
from core.dclass.chains_enum import Chains
from core.web3.executors.solana_executor import SolanaExecutor
from core.web3.jupiter_client import JupiterClient
from core.web3.rpcs.solana_manager import SolanaManager


class TestSolana:
    def __init__(self):
        properties_multi = PropertiesMulti()
        solana_manager = SolanaManager()
        self.solana_executor = SolanaExecutor(solana_manager, properties_multi)
        self.jupiter_client = JupiterClient()

    async def test_execute(self):
        USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        SOL_MINT = "So11111111111111111111111111111111111111112"

        # Valores para o teste (Exemplo: 1.0 USDC)
        valor_teste_human = 1.0
        decimals_usdc = 6
        decimals_sol = 9

        # Converte para Wei porque o teu 'send_transaction' espera o valor em formato inteiro/Wei
        amount_in_wei = int(valor_teste_human * (10 ** decimals_usdc))

        logging.info(f"📡 A procurar Quote na Jupiter para {valor_teste_human} USDC -> SOL...")

        # 2. Busca o Quote Real (Passando os valores reais, não os tipos)
        data_quote = await self.jupiter_client.get_quote(
            addr_in=USDC_MINT,
            addr_out=SOL_MINT,
            amount_in_human=valor_teste_human,
            decimals_in=decimals_usdc,
            decimals_out=decimals_sol
        )

        if not data_quote:
            logging.error("❌ Não foi possível obter o quote da Jupiter. Abortando teste.")
            return False

        logging.info("✅ Quote obtido com sucesso! A disparar transação via Helius...")

        tx_hash = await self.solana_executor.send_transaction(
            pools_list=["NOME_DE_UMA_POOL_OU_QUALQUER_STRING"],  # O que o teu código esperar
            dir_list=[True],
            tokens_list=[USDC_MINT, SOL_MINT],
            amount_usd=amount_in_wei,
            chain=Chains.SOLANA,
            quote_data=data_quote.data_quote  # Deixa a Jupiter ir buscar um quote fresco automático
        )

        if tx_hash:
            logging.info(f"🥳 SUCESSO! Transação confirmada no bloco pela Helius.")
            logging.info(f"🔗 Verifica aqui: https://solscan.io/tx/{tx_hash}")
            return True
        else:
            logging.error("❌ O Polling falhou: a transação foi dropada ou fez Revert.")
            return False


if __name__ == "__main__":
    test = TestSolana()
    asyncio.run(test.test_execute())
