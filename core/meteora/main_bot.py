import asyncio
import csv
import logging
import os
import sys
import time
from datetime import datetime

from core.config.properties_multi import PropertiesMulti
from core.meteora.dclass import PositionStatus, RangeStatus
from core.meteora.hl_client import HlClient
from core.meteora.meteora_client import MeteoraClient
from core.meteora.pool_manager_dclass import PoolManager
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

POOL_CONFIG = {
    "SOL/USDC": {
        "address": "5rCf1DM8LjKTw4YqhnoLcngyZYeNnQqztScTogYHAS6",
        "binStep": 4,
        "feePct": 0.0020,
        "tokenX": {"symbol": "SOL", "decimals": 9},
        "tokenY": {"symbol": "USDC", "decimals": 6}
    }
};


class DeltaNeutralSniperBot:
    def __init__(self, usdc_min_hl: float, total_usdc_capital: float):
        # Configuração de alocação de fundos
        self.total_usdc_capital = total_usdc_capital
        self.usdc_min_hl = usdc_min_hl
        self.usdc_hl_leg = self.total_usdc_capital / 2
        # self.range_width = range_width_dollars
        # print("AQUIII", self.total_usdc_capital, self.usdc_min_hl, self.usdc_hl_leg)
        if self.usdc_min_hl < self.usdc_hl_leg:
            logging.error(
                f"❌ Falha: usdc_hl_leg {self.usdc_hl_leg} tem que ser maior que usdc_min_hl {self.usdc_min_hl}!")
            raise RuntimeError("Something bad happened")

            # Variáveis geográficas que controlam os gatilhos (Preenchidas via Node.js)
        # self.lower_price_bound = None
        # self.upper_price_bound = None
        # self.sol_short_size = None
        # self.is_position_active = False

        self.pool_config = PoolManager().get("SOL/USDC")

        self.meteora_client = MeteoraClient(js_script_path, self.pool_config)
        self.hl_client = HlClient()
        solana_manager = SolanaManager()
        properties = PropertiesMulti()
        self.solana_executor = SolanaExecutor(solana_manager, properties)

        self.out_of_range_since = None
        self.last_log_time = 0

        self.cooldown_until = 0
        self.last_known_range = 0.0
        self.last_calculation_time = 0

    async def open_position(self, current_price: float, range_width: float) -> bool:
        try:
            # 1. Abre na Meteora primeiro (é o core do investimento)
            is_open = await self.meteora_client.open_position(self.total_usdc_capital, current_price, range_width)
            logging.info(f"Posição aberta na Meteora?: {is_open}")
            if not is_open:
                return False

            # 2. Tenta fazer o hedge na HL
            try:
                logging.info("A abrir posição na Hyperliquid")
                await self.hl_client.open_position(self.usdc_hl_leg)
                return True
            except Exception as e:
                logging.error(f"❌ Falha no Hedge HL: {e}. AÇÃO NECESSÁRIA: Fechar posição Meteora!")
                return False

        except Exception as e:
            logging.error(f"❌ Falha ao abrir na Meteora: {e}")
            return False

    async def rebalanced_position(self, current_price: float, range_width: float):
        try:
            # 1. Fechar Hedge na HL (Liberta capital ou encerra exposição)
            logging.info("🔄 Fechando Hedge na Hyperliquid...")
            await self.hl_client.close_position()

            # 2. Rebalancear Meteora
            # Importante: verifica se esta função bloqueia até a transação ser confirmada na blockchain
            logging.info("🔄 Atualizando Posição na Meteora...")
            is_rebalanced = await self.meteora_client.rebalance_position(self.total_usdc_capital,
                                                                         current_price,
                                                                         range_width)
            logging.info(f"Posição rebalanceada na Meteora?: {is_rebalanced}")
            if not is_rebalanced:
                raise RuntimeError("Meteora rebalance failed")

            # 3. Reabrir Hedge na HL
            logging.info("🔄 Abrindo novo Hedge na Hyperliquid...")
            await self.hl_client.open_position(self.usdc_hl_leg)
            return True

        except Exception as e:
            logging.error(f"❌ Erro Crítico no rebalanceamento: {e}")
            return False

    async def get_balance(self, position: PositionStatus):

        market_status = await self.meteora_client.get_status()
        sol_price = market_status.raw_price
        usdc_balance_wallet = market_status.usdc_balance
        sol_balance_wallet = market_status.sol_balance

        usdc_balance_total_wallet = (sol_price * sol_balance_wallet) + usdc_balance_wallet

        sol_balance_strategy = 0
        usdc_balance_strategy = 0
        if position is not None:
            sol_balance_strategy = position.totalXAmount / (10 ** self.pool_config.tokenX.decimals)
            usdc_balance_strategy = position.totalYAmount / (10 ** self.pool_config.tokenY.decimals)

        usdc_balance_total_strategy = (sol_price * sol_balance_strategy) + usdc_balance_strategy

        return usdc_balance_total_wallet + usdc_balance_total_strategy

    async def log_financial_state(self, action_type: str, status: str, position: PositionStatus):
        """
        Regista o estado financeiro e compara com o anterior para medir performance.
        """
        file_path = "bot_performance.csv"

        try:
            wallet_balance = await self.get_balance(position)
            """
            market_status = await self.meteora_client.get_status()

            # 1. Obter saldos atuais (ajusta os métodos conforme o teu código)
            sol_bal = await self.solana_executor.get_token_balance('So11111111111111111111111111111111111111112',
                                                                   Chains.SOLANA)  # Em SOL
            usdc_bal = await self.solana_executor.get_token_balance('EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
                                                                    Chains.SOLANA)  # Em USDC
            """

            sol_balance_strategy = position.totalXAmount / (10 ** self.pool_config.tokenX.decimals)
            usdc_balance_strategy = position.totalYAmount / (10 ** self.pool_config.tokenY.decimals)

            hl_balance = await self.hl_client.get_balance()  # Em USDC

            # 2. Obter valor atual do SOL para normalizar o Total
            # sol_price = await self.get_current_sol_price()
            total_valor_usdc = wallet_balance + hl_balance

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
                    round(sol_balance_strategy, 4), round(usdc_balance_strategy, 2),
                    round(hl_balance, 2), round(total_valor_usdc, 2),
                    round(diff, 4)
                ])

            # 6. Alerta se a performance for negativa
            if last_total > 0 > diff:
                logging.warning(f"📉 [ALERTA] Saldo caiu {abs(diff):.2f} USDC após {action_type}!")
            else:
                logging.info(f"💰 [LOG] Saldo atualizado: {total_valor_usdc:.2f} USDC (Variação: {diff:+.4f})")

        except Exception as e:
            logging.error(f"❌ Erro ao registar saldo financeiro: {e}")

    async def is_price_outside_range_sustained__(self, min_price: float, max_price: float,
                                                 margin_percent: float = 0.0,
                                                 duration_seconds: int = 300) -> bool:  # 300s = 5min

        # 1. Verifica se está fora do range (tua lógica atual)
        is_outside = await self.hl_client.is_price_outside_range(min_price, max_price, margin_percent)

        if not is_outside:
            # Preço voltou para dentro: reseta o timer
            if self.out_of_range_since is not None:
                logging.info("✅ Preço voltou para o range. Timer de rebalanceamento resetado.")
                self.out_of_range_since = None
            return False

        # 2. Se está fora, verifica o timer
        if self.out_of_range_since is None:
            self.out_of_range_since = time.time()
            logging.info(f"⚠️ Preço fora do range. Iniciando contagem de {duration_seconds / 60} min...")
            return False

        elapsed = time.time() - self.out_of_range_since
        if elapsed >= duration_seconds:
            logging.info(f"🚨 Preço fora do range por {elapsed / 60:.1f} min. Rebalanceamento autorizado!")
            return True

        if time.time() - getattr(self, 'last_log_time', 0) > 20:
            logging.info(f"⏳ Aguardando... Fora do range há {elapsed:.0f}s de {duration_seconds}s.")
            self.last_log_time = time.time()
        return False

    async def is_price_outside_range_sustained(self, min_price: float, max_price: float,
                                               margin_percent: float = 0.0,
                                               duration_seconds: int = 300) -> bool:

        status = await self.hl_client.check_range_status(min_price, max_price, margin_percent)

        # 1. AÇÃO IMEDIATA: Spike de alta (Ignora timer)
        if status == RangeStatus.OUT_UPPER:
            logging.warning("🚀 SPIKE DE ALTA DETETADO! Fecho imediato.")
            self.out_of_range_since = None  # Limpa qualquer timer pendente
            return True

        # 2. SE VOLTOU PARA DENTRO (Reset do timer)
        if status == RangeStatus.INSIDE:
            if self.out_of_range_since is not None:
                logging.info("✅ Preço voltou para o range. Timer de rebalanceamento resetado.")
                self.out_of_range_since = None
            return False

        # 3. SE ESTÁ OUT_LOWER (Lógica de espera com feedback)
        if status == RangeStatus.OUT_LOWER:
            # Inicia timer se for a primeira vez
            if self.out_of_range_since is None:
                self.out_of_range_since = time.time()
                logging.info(f"⚠️ Preço abaixo do range. Iniciando contagem de {duration_seconds / 60} min...")
                return False

            # Verifica tempo decorrido
            elapsed = time.time() - self.out_of_range_since
            if elapsed >= duration_seconds:
                logging.info(f"🚨 Preço baixo sustentado por {elapsed / 60:.1f} min. Rebalanceamento autorizado!")
                return True

            # Log periódico para não inundar a consola (a cada 20 segundos)
            if time.time() - getattr(self, 'last_log_time', 0) > 20:
                logging.info(f"⏳ Aguardando... Abaixo do range há {elapsed:.0f}s de {duration_seconds}s.")
                self.last_log_time = time.time()

        return False

    async def close_position(self, position: PositionStatus) -> bool:
        if position is not None:
            is_closed_meteora = await self.meteora_client.close_all()
            if is_closed_meteora:
                logging.info("⏳ Posição de Meteora fechado com sucesso. A Fechar posição da Hyperliquid...")
                is_closed_hl = await self.hl_client.close_position()
                if is_closed_hl:
                    logging.info("✅ Posição de Hyperliquid fechado com sucesso.")
                    return True
        return False

    async def rebalanced_management(self, position: PositionStatus, range_margin_pct=0.005,
                                    reserve_sol_usdc=10.0) -> PositionStatus | None:

        if position is None or position.size != 1:
            return position

        lower_price = position.lowerPrice
        upper_price = position.upperPrice

        is_outside = await self.is_price_outside_range_sustained(
            lower_price,
            upper_price,
            range_margin_pct,  # Ajusta a tua tolerância
            300
        )

        if is_outside:
            if await self.should_wait_for_market():
                is_closed = await self.close_position(position)
                if is_closed:
                    return None

            logging.warning("🚨 PREÇO FORA DO RANGE! Rebalanceando...")
            market_status = await self.meteora_client.get_status()
            range_percentage = await self.hl_client.calculate_dynamic_range_width()
            is_rebalanced = await self.rebalanced_position(market_status.raw_price,
                                                           range_percentage)
            position = await self.meteora_client.get_position()
            if is_rebalanced:
                self.out_of_range_since = None
                position = await self.meteora_client.get_position()
                await self.solana_executor.cleanup_wallet(reserve_sol_usdc=reserve_sol_usdc)
            else:
                logging.error("Meteora rebalance failed")
        return position

    async def open_position_management(self, position: PositionStatus | None,
                                       reserve_sol_usdc=10.0) -> PositionStatus | None:
        if position is None:
            logging.info("A efetuar a abertura de posição...")
            market_status = await self.meteora_client.get_status()
            range_percentage = await self.hl_client.calculate_dynamic_range_width()
            is_open = await self.open_position(market_status.raw_price, range_percentage)
            position = await self.meteora_client.get_position()
            if is_open:
                await self.solana_executor.cleanup_wallet(reserve_sol_usdc=reserve_sol_usdc)
        return position

    async def heartbeat_log(self, position_data: PositionStatus, last_heartbeat: float, heartbeat_interval: int):
        now = time.time()
        if now - last_heartbeat >= heartbeat_interval:
            formated_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Log único e informativo
            msg = f"💚 [SINAL DE VIDA] {formated_time}"
            if position_data:
                wallet_balance = await self.get_balance(position_data)
                hl_balance = await self.hl_client.get_balance()
                msg += f" | Ativa: {position_data.address[:6]}... | Range: [{position_data.lowerPrice} - {position_data.upperPrice}] | Balanço: [Wallet: {wallet_balance}, Hyperliquid: {hl_balance}]"
            else:
                msg += " | Sem posição ativa."

            logging.info(msg)
            return now
        return last_heartbeat

    async def should_wait_for_market(self):
        """
        Retorna True se o bot deve pausar a operação (modo de espera),
        e False se estiver apto para operar.
        """
        current_time = time.time()
        MAX_RANGE_PCT = 0.025
        CALC_INTERVAL = 100
        COOLDOWN_DURATION = 300

        # Verifica se precisamos de atualizar o range da Hyperliquid
        if current_time - self.last_calculation_time >= CALC_INTERVAL:
            self.last_known_range = await self.hl_client.calculate_dynamic_range_width()
            self.last_calculation_time = current_time

        # Verifica se o range é abusivo
        if self.last_known_range > MAX_RANGE_PCT:
            if current_time < self.cooldown_until:
                # Ainda no tempo de espera
                return True
            else:
                # Acabou de entrar em volatilidade
                self.cooldown_until = current_time + COOLDOWN_DURATION
                logging.warning(f"⚠️ Range {self.last_known_range:.2%} > 2%. Cooldown ativo.")
                return True

        # Mercado está estável
        return False

    async def start_sniper_cycle(self):
        await self.hl_client.start()
        await asyncio.sleep(2)

        margin_percentage = 0.05
        reserve_sol_usdc = 10.0

        heartbeat_interval = 120
        last_heartbeat = time.time()

        position_data = await self.meteora_client.get_position()
        # balance = await self.get_balance(position_data)
        while True:
            try:

                if position_data is None:
                    if not await self.should_wait_for_market():
                        position_data = await self.open_position_management(position_data, reserve_sol_usdc)
                    else:
                        await asyncio.sleep(10)  # Descanso profundo
                    continue
                elif position_data.size > 1:
                    is_closed = await self.close_position(position_data)
                    if is_closed:
                        position_data = None
                        continue
                position_data = await self.rebalanced_management(position_data, margin_percentage,
                                                                 reserve_sol_usdc)
                last_heartbeat = await self.heartbeat_log(position_data, last_heartbeat, heartbeat_interval)

                await asyncio.sleep(5)
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
    bot = DeltaNeutralSniperBot(usdc_min_hl=12, total_usdc_capital=24)
    asyncio.run(bot.start_sniper_cycle())
