import asyncio
import logging
import os

from core.config.properties_multi import PropertiesMulti
from core.meteora.hl_client import HlClient
from core.meteora.meteora_client import MeteoraClient
from core.web3.executors.solana_executor import SolanaExecutor
from core.web3.rpcs.solana_manager import SolanaManager

# 1. Descobrir onde o script Python está ( .../core/meteora )
base_path = os.path.dirname(os.path.abspath(__file__))

# 2. Corrigir de forma forçada caso o caminho já traga "core/meteora" duplicado
if "core/meteora" in base_path:
    # Se o base_path já inclui a pasta, apontamos direto ao ficheiro na mesma pasta
    js_script_path = os.path.join(base_path, "meteora_bot.js")
else:
    # Caso contrário, adicionamos a pasta manualmente
    js_script_path = os.path.join(base_path, "core", "meteora", "meteora_bot.js")

# 3. PRINT DE SEGURANÇA (Para vermos o resultado real no terminal)
print(f"🔍 [Debug Caminho] A tentar chamar o Node em: {js_script_path}")

USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
WSOL_MINT = "So11111111111111111111111111111111111111112"


class DeltaNeutralSniperBot:
    def __init__(self, total_usdc_capital, range_width_dollars):
        # Configuração de alocação de fundos
        self.total_usdc_capital = total_usdc_capital
        self.range_width = range_width_dollars

        # Variáveis geográficas que controlam os gatilhos (Preenchidas via Node.js)
        self.lower_price_bound = None
        self.upper_price_bound = None
        self.sol_short_size = None
        self.is_position_active = False

        self.meteora_client = MeteoraClient(js_script_path)
        self.hl_client = HlClient()
        solana_manager = SolanaManager()
        properties = PropertiesMulti()
        self.solana_executor = SolanaExecutor(solana_manager, properties)

    async def open_position(self, usdc_amount: float, current_price: float, range_width: float) -> bool:
        try:
            # 1. Abre na Meteora primeiro (é o core do investimento)
            is_open = self.meteora_client.open_position(usdc_amount, current_price, range_width)

            if not is_open:
                return False

            # 2. Tenta fazer o hedge na HL
            try:
                await self.hl_client.open_position(usdc_amount)
                return True
            except Exception as e:
                # ERRO CRÍTICO: Posição na Meteora aberta, mas sem hedge na HL
                logging.error(f"❌ Falha no Hedge HL: {e}. AÇÃO NECESSÁRIA: Fechar posição Meteora!")
                # Aqui poderias adicionar lógica para fechar a posição na Meteora automaticamente
                return False

        except Exception as e:
            logging.error(f"❌ Falha ao abrir na Meteora: {e}")
            return False

    async def rebalanced_position(self, pos_address: str, usdc_amount: float, current_price: float, range_width: float):
        try:
            # 1. Fechar Hedge na HL (Liberta capital ou encerra exposição)
            print("🔄 Fechando Hedge na Hyperliquid...")
            await self.hl_client.close_position()

            # 2. Rebalancear Meteora
            # Importante: verifica se esta função bloqueia até a transação ser confirmada na blockchain
            print("🔄 Atualizando Posição na Meteora...")
            is_rebalanced = self.meteora_client.rebalance_position(pos_address, usdc_amount, current_price, range_width)

            if not is_rebalanced:
                raise Exception("Meteora rebalance failed")

            # 3. Reabrir Hedge na HL
            print("🔄 Abrindo novo Hedge na Hyperliquid...")
            await self.hl_client.open_position(usdc_amount)

            return True

        except Exception as e:
            logging.error(f"❌ Erro Crítico no rebalanceamento: {e}")
            # AQUI É ONDE O TEU BOT DEVE ALERTAR (Telegram/Discord)
            # Porque se falhar no passo 2 ou 3, estás com a posição aberta na Meteora mas sem proteção.
            return False

    async def start_sniper_cycle(self):
        await self.hl_client.start()
        await asyncio.sleep(2)

        position_data = None

        usdc_capital = 10.0
        range_percentage = 0.02
        reserve_sol_usdc = 10.0
        usdc_capital_hl = (usdc_capital / 2) * 0.995

        await self.solana_executor.cleanup_wallet(reserve_sol_usdc=10)
        force_refresh = True
        while True:
            try:

                if force_refresh:
                    position_data = self.meteora_client.check_position()
                    force_refresh = False

                if position_data:
                    lower_price = position_data.lowerPrice
                    upper_price = position_data.upperPrice
                    address = position_data.address

                    print(f"🎯 Posição carregada: [{lower_price} - {upper_price}]")
                    is_outside = await self.hl_client.is_price_outside_range(
                        lower_price,
                        upper_price,
                        0.1  # Ajusta a tua tolerância
                    )

                    if is_outside:
                        print("🚨 PREÇO FORA DO RANGE! Rebalanceando...")
                        # wait self.perform_rebalance()
                        market_status = self.meteora_client.get_status()
                        is_rebalanced = await self.rebalanced_position(address, usdc_capital, market_status.raw_price,
                                                                       range_percentage)

                        if is_rebalanced:
                            await self.solana_executor.cleanup_wallet(reserve_sol_usdc=reserve_sol_usdc)
                            force_refresh = True
                        else:
                            raise Exception("Meteora rebalance failed")

                    await asyncio.sleep(1)  # Intervalo de polling da Hyperliquid (1 segundo é aceitável)

                else:
                    market_status = self.meteora_client.get_status()
                    is_open = self.meteora_client.open_position(usdc_capital, market_status.raw_price, range_percentage)
                    if is_open:
                        await self.solana_executor.cleanup_wallet(reserve_sol_usdc=reserve_sol_usdc)
                        force_refresh = True


            except Exception as e:
                print(f"❌ Erro no ciclo do sniper: {e}")
                await asyncio.sleep(5)  # Cooldown em caso de erro de rede


# =====================================================================
# SYSTEM ENTRYPOINT
# =====================================================================
if __name__ == "__main__":
    # Configuração de Arranque Inicial: Aloca $1000 USDC totais, com um range de 2 dólares de largura
    bot = DeltaNeutralSniperBot(total_usdc_capital=1000.00, range_width_dollars=2.0)
    asyncio.run(bot.start_sniper_cycle())
