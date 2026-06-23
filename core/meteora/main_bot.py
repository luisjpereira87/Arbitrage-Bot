import asyncio
import csv
import logging
import os
import sys
import time
from datetime import datetime

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
            # usdc_amount_hl = (usdc_amount / 2) * 0.995
            # usdc_amount_hl = usdc_amount * 0.995
            usdc_amount_hl = usdc_amount

            if usdc_amount_hl < 12.0:
                logging.error(f"❌ Valor inferior a 12 usdc")
                return False

            # 1. Abre na Meteora primeiro (é o core do investimento)
            is_open = self.meteora_client.open_position(usdc_amount, current_price, range_width)

            if not is_open:
                return False

            # 2. Tenta fazer o hedge na HL
            try:
                await self.hl_client.open_position(usdc_amount_hl)
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
            # usdc_amount_hl = (usdc_amount / 2) * 0.995
            # usdc_amount_hl = usdc_amount * 0.995
            usdc_amount_hl = usdc_amount

            if usdc_amount_hl < 12.0:
                logging.error(f"❌ Valor inferior a 12 usdc")
                return False

            # 1. Fechar Hedge na HL (Liberta capital ou encerra exposição)
            logging.info("🔄 Fechando Hedge na Hyperliquid...")
            await self.hl_client.close_position()

            # 2. Rebalancear Meteora
            # Importante: verifica se esta função bloqueia até a transação ser confirmada na blockchain
            logging.info("🔄 Atualizando Posição na Meteora...")
            is_rebalanced = self.meteora_client.rebalance_position(pos_address, usdc_amount, current_price, range_width)

            if not is_rebalanced:
                raise Exception("Meteora rebalance failed")

            # 3. Reabrir Hedge na HL
            logging.info("🔄 Abrindo novo Hedge na Hyperliquid...")
            await self.hl_client.open_position(usdc_amount_hl)

            return True

        except Exception as e:
            logging.error(f"❌ Erro Crítico no rebalanceamento: {e}")
            # AQUI É ONDE O TEU BOT DEVE ALERTAR (Telegram/Discord)
            # Porque se falhar no passo 2 ou 3, estás com a posição aberta na Meteora mas sem proteção.
            return False

    async def log_financial_state(self, action_type: str, status: str, sol_price: float):
        """
        Regista o estado financeiro e compara com o anterior para medir performance.
        """
        file_path = "bot_performance.csv"

        try:
            # 1. Obter saldos atuais (ajusta os métodos conforme o teu código)
            sol_bal = await self.solana_executor.get_token_balance()  # Em SOL
            usdc_bal = await self.solana_executor.get_token_balance()  # Em USDC
            hl_bal = await self.hl_client.get_balance()  # Em USDC

            # 2. Obter valor atual do SOL para normalizar o Total
            # sol_price = await self.get_current_sol_price()
            total_valor_usdc = (sol_bal * sol_price) + usdc_bal + hl_bal

            # 3. Ler o último saldo registado para comparação
            last_total = 0.0
            if os.path.exists(file_path):
                with open(file_path, "r") as f:
                    reader = csv.DictReader(f)
                    rows = list(reader)
                    if rows:
                        last_total = float(rows[-1]['Total_USDC'])

            # 4. Cálculo da variação
            diff = total_valor_usdc - last_total

            # 5. Escrever o novo registo
            file_exists = os.path.exists(file_path)
            with open(file_path, "a", newline='') as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["Timestamp", "Action", "Status", "SOL", "USDC", "HL_Margin", "Total_USDC", "Diff"])

                writer.writerow([
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    action_type, status,
                    round(sol_bal, 4), round(usdc_bal, 2),
                    round(hl_bal, 2), round(total_valor_usdc, 2),
                    round(diff, 4)
                ])

            # 6. Alerta se a performance for negativa
            if last_total > 0 and diff < 0:
                logging.warning(f"📉 [ALERTA] Saldo caiu {abs(diff):.2f} USDC após {action_type}!")
            else:
                logging.info(f"💰 [LOG] Saldo atualizado: {total_valor_usdc:.2f} USDC (Variação: {diff:+.4f})")

        except Exception as e:
            logging.error(f"❌ Erro ao registar saldo financeiro: {e}")

    async def start_sniper_cycle(self):
        await self.hl_client.start()
        await asyncio.sleep(2)

        position_data = None

        usdc_capital = 12.0
        range_percentage = await self.hl_client.calculate_dynamic_range_width()
        margin_percentage = 0.05
        reserve_sol_usdc = 10.0

        heartbeat_interval = 120
        last_heartbeat = time.time()

        # await self.solana_executor.cleanup_wallet(reserve_sol_usdc=10)
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

                    logging.info(f"🎯 Posição carregada: [{lower_price} - {upper_price}]")
                    is_outside = await self.hl_client.is_price_outside_range(
                        lower_price,
                        upper_price,
                        margin_percentage  # Ajusta a tua tolerância
                    )

                    if is_outside:
                        logging.warning("🚨 PREÇO FORA DO RANGE! Rebalanceando...")
                        market_status = self.meteora_client.get_status()
                        is_rebalanced = await self.rebalanced_position(address, usdc_capital, market_status.raw_price,
                                                                       range_percentage)

                        if is_rebalanced:
                            logging.info("A efetuar o rebalanceando...")
                            await self.solana_executor.cleanup_wallet(reserve_sol_usdc=reserve_sol_usdc)
                            force_refresh = True
                        else:
                            force_refresh = True
                            raise Exception("Meteora rebalance failed")

                    await asyncio.sleep(1)  # Intervalo de polling da Hyperliquid (1 segundo é aceitável)

                else:
                    logging.info("A efetuar a abertura de posição...")
                    market_status = self.meteora_client.get_status()
                    is_open = await self.open_position(usdc_capital, market_status.raw_price, range_percentage)
                    if is_open:
                        await self.solana_executor.cleanup_wallet(reserve_sol_usdc=reserve_sol_usdc)
                    force_refresh = True

                now = time.time()
                if now - last_heartbeat >= heartbeat_interval:
                    formated_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    # Log único e informativo
                    msg = f"💚 [SINAL DE VIDA] {formated_time}"
                    if position_data:
                        msg += f" | Ativa: {position_data.address[:6]}... | Range: [{position_data.lowerPrice} - {position_data.upperPrice}]"
                    else:
                        msg += " | Sem posição ativa."

                    logging.info(msg)
                    last_heartbeat = now

                await asyncio.sleep(0.001)
            except Exception as e:
                logging.error(f"❌ Erro no ciclo do sniper: {e}")
                await asyncio.sleep(5)  # Cooldown em caso de erro de rede

    async def test(self):
        return await self.hl_client.calculate_dynamic_range_width()


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
# =====================================================================
# SYSTEM ENTRYPOINT
# =====================================================================
if __name__ == "__main__":
    # Configuração de Arranque Inicial: Aloca $1000 USDC totais, com um range de 2 dólares de largura
    bot = DeltaNeutralSniperBot(total_usdc_capital=1000.00, range_width_dollars=2.0)
    asyncio.run(bot.start_sniper_cycle())
