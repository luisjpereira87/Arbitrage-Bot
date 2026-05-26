import asyncio
import logging

from core.web3.jupiter_client import JupiterClient


class SolanaBot:
    def __init__(self):

        self.jupiter = JupiterClient()

        self.STABLE_COINS = [
            {"symbol": "USDC", "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "decimals": 6},
            {"symbol": "USDT", "mint": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", "decimals": 6},
            {"symbol": "USDS", "mint": "USDSwr9ApdHk5bvJKMjzff41FfuX8bSxdKcR81vTwcA", "decimals": 6},
            # Nova stable da Sky
            {"symbol": "PYUSD", "mint": "2b1kV6DkPAnxd5ixfnxCpjxmKwqjjaYmCZfHsFu24GXo", "decimals": 6},
            # PayPal USD na Solana
            {"symbol": "JUSD", "mint": "JuprjznTrTSp2UFa3ZBUFgwdAmtZCq4MQCwysN55USD", "decimals": 6}
        ]

    async def scan_jupiter_triangles(self):
        input_amount_human = 100.0  # Banca de teste: $100

        index = 0
        while True:
            try:

                coin_in = self.STABLE_COINS[0]
                coin_out = self.STABLE_COINS[index]

                # Consulta a rota específica na Jupiter
                dex_quote = await self.jupiter.get_quote(
                    addr_in=coin_in["mint"],
                    addr_out=coin_out["mint"],
                    amount_in_human=input_amount_human,
                    decimals_in=coin_in["decimals"],
                    decimals_out=coin_out["decimals"],
                    exclude_direct_route=True,  # Força a passar por voláteis
                    restrict_intermediate_tokens=False
                )

                if not dex_quote:
                    continue

                # Cálculo do spread adaptado ao par atual
                spread = (dex_quote.price_dex_net - 1.0) * 100
            
                # Log detalhado por vetor
                logging.info(
                    f"📊 Vetor [{coin_in['symbol']} ➡️ {coin_out['symbol']}] | Spread: {spread:.3f}%"
                )

                # Filtro de Execução
                if spread > 0.05:
                    logging.info(
                        f"🔥 DISPARO NO VETOR {coin_in['symbol']}->{coin_out['symbol']}! Spread: {spread:.3f}%")
                    # await self.executor.execute_solana_swap(dex_quote.data_quote)

                # Sleep no fim da matriz antes de começar a ronda novamente
                await asyncio.sleep(1)
                index = index + 1

                if index == len(self.STABLE_COINS):
                    index = 0
            except Exception as e:
                logging.error(f"Erro no scanner matricial: {e}")
                await asyncio.sleep(2)
