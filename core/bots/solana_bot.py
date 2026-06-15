import asyncio
import logging

from core.config.properties_dex import PropertiesDex
from core.dclass.chains_enum import Chains
from core.web3.executors.solana_executor import SolanaExecutor
from core.web3.jupiter_client import JupiterClient
from core.web3.rpcs.solana_manager import SolanaManager


class SolanaBot:
    def __init__(self):

        self.jupiter = JupiterClient()
        self.solana_manager = SolanaManager()
        properties_dex = PropertiesDex()
        self.solana_executor = SolanaExecutor(self.solana_manager, properties_dex)

        self.USDC = {"symbol": "USDC", "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "decimals": 6}
        self.STABLE_COINS = [
            {"symbol": "USDC", "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "decimals": 6},
            {"symbol": "USDT", "mint": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", "decimals": 6},
            {"symbol": "USDS", "mint": "USDSwr9ApdHk5bvJKMjzff41FfuX8bSxdKcR81vTwcA", "decimals": 6},
            # Nova stable da Sky
            {"symbol": "PYUSD", "mint": "2b1kV6DkPAnxd5ixfnxCpjxmKwqjjaYmCZfHsFu24GXo", "decimals": 6},
            # PayPal USD na Solana
            {"symbol": "JUSD", "mint": "JuprjznTrTSp2UFa3ZBUFgwdAmtZCq4MQCwysN55USD", "decimals": 6}
        ]

    async def get_all_stable_balances(self, stables_list: list) -> dict:
        """
        Consulta os saldos das stables de forma sequencial controlada para evitar
        o erro de 'Event loop is closed' do asyncio no arranque.
        """
        balances = {}

        logging.info("⏳ A inicializar sockets de saldo no Event Loop ativo...")

        for stable in stables_list:
            try:
                # Fazemos a chamada direta e sequencial (sem gather) para garantir estabilidade
                saldo = await self.solana_executor.get_token_balance(token_address=stable["mint"], chain=Chains.SOLANA)
                saldo_real_humano = float(saldo / (10 ** stable["decimals"]))
                balances[stable["mint"]] = saldo_real_humano
                # balances[stable["mint"]] = float(saldo) if saldo else 0.0
            except Exception as e:
                # Captura o erro do Event Loop de forma limpa sem estragar o arranque
                logging.debug(f"ℹ️ Ajustando socket para {stable['symbol']}: {e}")
                balances[stable["mint"]] = 0.0

        return balances

    async def scan_jupiter_triangles(self):
        logging.info("🚀 Scanner Triangular Autónomo Multi-Entrada Iniciado...")

        # 1️⃣ OBTÉM OS SALDOS FORA DO LOOP (Como tinhas bem no início!)
        balances = await self.get_all_stable_balances(self.STABLE_COINS)

        # 2️⃣ FILTRA AS MOEDAS QUE TÊM SALDO REAL (Serão as tuas moedas de ENTRADA)
        # Criamos uma lista apenas com os objetos das moedas que têm dinheiro
        moedas_entrada = []
        for stable in self.STABLE_COINS:
            saldo_bruto = balances.get(stable["mint"], 0.0)
            if saldo_bruto > 10:
                moeda = stable.copy()
                moeda["banca_humana"] = saldo_bruto  # Guarda a banca direta na moeda
                moedas_entrada.append(moeda)

        if not moedas_entrada:
            logging.error("❌ Nenhuma stablecoin com saldo ativo (>= $10) na carteira. Scanner abortado.")
            return

        logging.info(f"💳 Moedas de entrada ativas para o loop: {[m['symbol'] for m in moedas_entrada]}")

        # 3️⃣ O WHILE TRUE ITERA DIRETAMENTE SOBRE AS MOEDAS DE ENTRADA
        index = 0
        while True:
            try:
                # A cada volta do while, o index escolhe a moeda de entrada atual (ex: roda USDC, depois roda USDT)
                coin_in = moedas_entrada[index]
                input_amount_human = coin_in["banca_humana"]

                # Para a saída (coin_out), podemos testar contra qualquer uma da tua lista global
                # (A Jupiter trata de calcular as rotas automáticas)
                for coin_out in self.STABLE_COINS:

                    # Opcional: Se a saída for igual à entrada, salta para poupar requests
                    if coin_in["mint"] == coin_out["mint"]:
                        continue

                    dex_quote = await self.jupiter.get_quote_triangular(
                        addr_in=coin_in["mint"],
                        addr_out=coin_out["mint"],
                        amount_in_human=input_amount_human,
                        decimals_in=coin_in["decimals"],
                        decimals_out=coin_out["decimals"],
                        exclude_direct_route=True,
                        restrict_intermediate_tokens=True,
                        intermediate_tokens_mint=coin_out["mint"]
                    )
                    if not dex_quote or not dex_quote.data_quote:
                        await asyncio.sleep(0.2)
                        continue

                    # Extração e cálculo do spread real
                    out_raw = int(dex_quote.data_quote.get('outAmount', 0))
                    amount_out_human_real = out_raw / (10 ** coin_out["decimals"])
                    spread = ((amount_out_human_real - input_amount_human) / input_amount_human) * 100

                    """
                    logging.info(
                        f"📊 Vetor [{coin_in['symbol']} ➡️ VOLÁTIL ➡️ {coin_out['symbol']}] | "
                        f"Banca: ${input_amount_human:.2f} | Spread Real: {spread:+.3f}% |"
                    )
                    """
                    if spread > 0.05:
                        logging.info(
                            f"======================================================================\n"
                            f"🔥 OPORTUNIDADE REAL DETETADA: {coin_in['symbol']} ➡️ {coin_out['symbol']}\n"
                            f"💰 Lucro Esperado: +${(amount_out_human_real - input_amount_human):.4f}\n"
                            f"======================================================================"
                        )
                        # await self.executor.execute_solana_swap(dex_quote.data_quote)

                    await asyncio.sleep(0.5)  # Pequena pausa entre sub-rotas

                # Avança para a PRÓXIMA moeda de entrada que tem saldo
                index = (index + 1) % len(moedas_entrada)
                await asyncio.sleep(1)

            except Exception as e:
                logging.error(f"Erro no scanner matricial multi-entrada: {e}")
                index = (index + 1) % len(moedas_entrada)
                await asyncio.sleep(2)

    async def scan_jupiter_triangles_(self):
        input_amount_human = 100.0  # A tua banca de ataque fixa
        index = 0

        logging.info("🚀 Scanner Triangular Ancorado (Versão Segura contra Bug de Decimais) Iniciado...")
        balances = await self.get_all_stable_balances(self.STABLE_COINS)
        print(balances)
        while True:
            try:
                coin_in = self.USDC  # USDC Fixa
                coin_out = self.STABLE_COINS[index]

                dex_quote = await self.jupiter.get_quote_triangular(
                    addr_in=coin_in["mint"],
                    addr_out=coin_out["mint"],
                    amount_in_human=input_amount_human,
                    decimals_in=coin_in["decimals"],
                    decimals_out=coin_out["decimals"],
                    exclude_direct_route=True,
                    restrict_intermediate_tokens=True,
                    intermediate_tokens_mint=coin_out["mint"]
                )

                # Avança o índice para a próxima moeda imediatamente
                index = (index + 1) % len(self.STABLE_COINS)

                if not dex_quote or not dex_quote.data_quote:
                    await asyncio.sleep(0.3)
                    continue

                # 🛠️ EXTRAÇÃO DO RETORNO REAL (Ignorando rácios invertidos do get_quote)
                out_raw = int(dex_quote.data_quote.get('outAmount', 0))

                # Usamos os decimais que a própria JUPITER usou na resposta para não haver erros de config
                amount_out_human_real = out_raw / (10 ** coin_out["decimals"])

                # 🧮 CÁLCULO DIRECTO DO SPREAD EM DÓLARES
                # Se entrou $100 e saiu $99.80, o spread é negativo. Sem margem para bugs.
                spread = ((amount_out_human_real - input_amount_human) / input_amount_human) * 100

                logging.info(
                    f"📊 Vetor [{coin_in['symbol']} ➡️ VOLÁTIL ➡️ {coin_out['symbol']}] | "
                    f"Recebe: ${amount_out_human_real:.4f} | Spread Real: {spread:+.3f}%"
                )

                # Filtro de Execução Seguro
                if spread > 0.05:
                    logging.info(
                        f"======================================================================\n"
                        f"🔥 OPORTUNIDADE REAL DETETADA: {coin_in['symbol']} ➡️ {coin_out['symbol']}\n"
                        f"💰 Lucro Esperado: +${(amount_out_human_real - input_amount_human):.4f}\n"
                        f"======================================================================"
                    )
                    # await self.executor.execute_solana_swap(dex_quote.data_quote)

                await asyncio.sleep(1)

            except Exception as e:
                logging.error(f"Erro no scanner matricial: {e}")
                index = (index + 1) % len(self.STABLE_COINS)
                await asyncio.sleep(2)
