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
        self.usdc_hl_leg = (self.total_usdc_capital / 2) * 0.995
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

        self.lookback_range = 50
        self.lookback_limit = 100
        self.range_margin_pct = 0.2

        slippage_buffer = 0.0002
        fee_rate = 0.00025 + slippage_buffer
        self.hyperliquid_fees = max((self.usdc_hl_leg * fee_rate) * 2, 0.02)

    async def calculate_open_balance(self, price_token: float) -> tuple[float, float]:

        capital_para_hedge = self.total_usdc_capital / 2
        _, usdc_hl_leg = await self.hl_client.adjust_balance(capital_para_hedge, price_token)
        usdc_meteora = usdc_hl_leg * 2

        return usdc_meteora, usdc_hl_leg

    async def open_position(self, current_price: float, range_width: float) -> bool:
        try:

            usdc_meteora, usdc_hl_leg = await self.calculate_open_balance(current_price)

            logging.info(
                f"🚀 [BALANCEAMENTO] Meteora: {usdc_meteora:.4f} | HL: {usdc_hl_leg:.4f} (Ratio: {usdc_meteora / usdc_hl_leg:.2f}x)")

            # 1. Abre na Meteora primeiro (é o core do investimento)
            is_open = await self.meteora_client.open_position(usdc_meteora, current_price, range_width)
            logging.info(f"Posição aberta na Meteora?: {is_open}")
            if not is_open:
                return False
            # 2. Tenta fazer o hedge na HL
            try:
                logging.info("A abrir posição na Hyperliquid")
                await self.hl_client.open_position(usdc_hl_leg)
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

            usdc_meteora, usdc_hl_leg = await self.calculate_open_balance(current_price)

            logging.info(
                f"🚀 [BALANCEAMENTO] Meteora: {usdc_meteora:.4f} | HL: {usdc_hl_leg:.4f} (Ratio: {usdc_meteora / usdc_hl_leg:.2f}x)")

            is_rebalanced = await self.meteora_client.rebalance_position(usdc_meteora,
                                                                         current_price,
                                                                         range_width)
            logging.info(f"Posição rebalanceada na Meteora?: {is_rebalanced}")
            if not is_rebalanced:
                raise RuntimeError("Meteora rebalance failed")

            # 3. Reabrir Hedge na HL
            logging.info("🔄 Abrindo novo Hedge na Hyperliquid...")
            await self.hl_client.open_position(usdc_hl_leg)
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

            hl_pnl, hl_balance = await self.hl_client.get_balance()  # Em USDC

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

    async def is_price_outside_range_sustained_old(self, min_price: float, max_price: float,
                                                   duration_seconds: int = 300) -> bool:

        hl_pnl, _, _ = await self.hl_client.get_balance()
        position_data = await self.meteora_client.get_position()
        total_pnl = (hl_pnl + position_data.pnlUsd - self.hyperliquid_fees)
        PROFIT_TARGET = self.total_usdc_capital * 0.004

        if total_pnl >= PROFIT_TARGET:
            logging.info(f"✅ Preço atingiu a meta de 0.4%: {total_pnl:.2f}")
            return True

        status = await self.hl_client.check_range_status(min_price, max_price, self.range_margin_pct)
        """
        # Check de turbulência (novo)
        is_turbulent = await self.hl_client.is_market_turbulent(threshold=0.005)

        if is_turbulent and total_pnl > 0:
            logging.warning(f"🚨 SPIKE/TURBULÊNCIA + FORA DO RANGE ({status}). Fecho imediato!")
            self.out_of_range_since = None
            return True
        """
        # 1. SE SAIU DO RANGE
        if status == RangeStatus.OUT_UPPER or status == RangeStatus.OUT_LOWER:

            """
            # AÇÃO IMEDIATA: Se estiver fora DO RANGE E o mercado estiver TURBULENTO,
            # não esperes os 300 segundos. Sai já!
            if is_turbulent and total_pnl < 0:
                logging.warning(f"🚨 SPIKE/TURBULÊNCIA + FORA DO RANGE ({status}). Fecho imediato!")
                self.out_of_range_since = None
                return True
            """

            if total_pnl > 0:
                logging.warning(f"🚨 Preço fora do range mas pnl positivo: {total_pnl:.2f}, fechp imediato...")
                self.out_of_range_since = None
                return True

            # Caso contrário, mantém o comportamento normal de esperar o timer
            if self.out_of_range_since is None:
                self.out_of_range_since = time.time()
                logging.info(f"⚠️ Preço {status}. Iniciando timer de {duration_seconds / 60} min...")
                return False

            elapsed = time.time() - self.out_of_range_since
            if elapsed >= duration_seconds:
                logging.info(f"🚨 Tempo sustentado atingido. Rebalanceamento autorizado!")
                return True

            # Log periódico para não inundar a consola (a cada 20 segundos)
            if time.time() - getattr(self, 'last_log_time', 0) > 20:
                logging.info(f"⏳ Aguardando... Abaixo do range há {elapsed:.0f}s de {duration_seconds}s.")
                self.last_log_time = time.time()

            return False

        # 2. SE VOLTOU PARA DENTRO
        if status == RangeStatus.INSIDE:
            if self.out_of_range_since is not None:
                logging.info("✅ Preço voltou para dentro. Timer resetado.")
                self.out_of_range_since = None
            return False

        return False

    async def is_price_outside_range_sustained(self, min_price: float, max_price: float,
                                               duration_seconds: int = 300) -> bool:

        """
        hl_pnl, _, is_position = await self.hl_client.get_balance()
        position_data = await self.meteora_client.get_position()
        total_pnl = (hl_pnl + position_data.pnlUsd - self.hyperliquid_fees)

        if position_data is not None and is_position:
            profit_pct = 0.004
        elif position_data is None or is_position is False:
            profit_pct = 0.002

        PROFIT_TARGET = self.total_usdc_capital * profit_pct
        """

        # 1. Obtenção segura
        hl_pnl, _, is_hl_active = await self.hl_client.get_balance()
        position_data = await self.meteora_client.get_position()
        is_meteora_active = position_data is not None

        # 2. Cálculo do PnL
        meteora_pnl = position_data.pnlUsd if is_meteora_active else 0.0
        total_pnl = (hl_pnl + meteora_pnl - self.hyperliquid_fees)

        # 3. Target dinâmico (mais seguro)
        # Se ambas estão ativas = 0.4%, se apenas uma estiver = 0.2%
        active_legs = (1 if is_hl_active else 0) + (1 if is_meteora_active else 0)
        profit_pct = 0.004 if active_legs == 2 else 0.002
        PROFIT_TARGET = self.total_usdc_capital * profit_pct

        status_with_margin = await self.hl_client.check_range_status(min_price, max_price, self.range_margin_pct)
        status_without_margin = await self.hl_client.check_range_status(min_price, max_price, 0.0)

        # --- LÓGICA DE FECHO ANTECIPADO (Early Exit - 10% antes) ---

        # --- LÓGICA DE FECHO FINAL (Hard Exit - No limite do range) ---

        # Se saiu totalmente do range: Fecha TUDO o que ainda estiver aberto
        if status_without_margin == RangeStatus.OUT_UPPER or status_without_margin == RangeStatus.OUT_LOWER:
            logging.info("🛑 Preço fora do range total: Fecho final de tudo.")
            await self.hl_client.close_position()
            await self.meteora_client.close_all()
            return True  # Aqui sim, terminamos a operação

        # Se subiu e bateu no buffer: Fecha SÓ o Hedge (HL) e deixa a Meteora fluir
        if status_with_margin == RangeStatus.OUT_UPPER:
            logging.info("🚀 Buffer superior atingido: Fechando HL antecipadamente.")
            await self.hl_client.close_position()
            return False  # Retornamos False para o bot NÃO parar e deixar a Meteora continuar

        # Se caiu e bateu no buffer: Fecha SÓ a Pool (Meteora) e deixa o Hedge fluir
        if status_with_margin == RangeStatus.OUT_LOWER:
            logging.info("📉 Buffer inferior atingido: Fechando Meteora antecipadamente.")
            await self.meteora_client.close_all()
            return False  # Retornamos False para o bot continuar a monitorizar o Hedge

        if total_pnl >= PROFIT_TARGET:
            logging.info(f"✅ Preço atingiu a meta de 0.4%: {total_pnl:.2f}")
            return True

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

    async def rebalanced_management(self, position: PositionStatus) -> PositionStatus | None:

        if position is None or position.size != 1:
            return position

        lower_price = position.lowerPrice
        upper_price = position.upperPrice

        is_outside = await self.is_price_outside_range_sustained(
            lower_price,
            upper_price,
            300
        )

        if is_outside:
            if await self.should_wait_for_market():
                is_closed = await self.close_position(position)
                if is_closed:
                    return None

            logging.warning("🚨 PREÇO FORA DO RANGE! Rebalanceando...")
            market_status = await self.meteora_client.get_status()
            range_percentage_raw = await self.hl_client.calculate_dynamic_range_width(limit=self.lookback_limit,
                                                                                      lookback=self.lookback_range)
            range_percentage = range_percentage_raw * (1 + (self.range_margin_pct * 2))
            logging.info(f"Range calculado Original: {range_percentage_raw}, Reajustado: {range_percentage}")
            is_rebalanced = await self.rebalanced_position(market_status.raw_price,
                                                           range_percentage)
            position = await self.meteora_client.get_position()
            if is_rebalanced:
                self.out_of_range_since = None
                position = await self.meteora_client.get_position()
            else:
                logging.error("Meteora rebalance failed")
        return position

    async def loop_management(self) -> PositionStatus | None:
        position = await self.meteora_client.get_position()
        hl_position = await self.hl_client.get_position()

        if position is not None or hl_position is not None:

            if position is None:
                last_position = await self.meteora_client.get_last_position()
                lower_price = last_position.lowerPrice
                upper_price = last_position.upperPrice
            else:
                lower_price = position.lowerPrice
                upper_price = position.upperPrice

            is_outside = await self.is_price_outside_range_sustained(
                lower_price,
                upper_price,
                300
            )
            return position

        if position is None:
            if not await self.should_wait_for_market():
                return await self.open_position_management(position)
            else:
                await asyncio.sleep(10)  # Descanso profundo
            return position
        elif position.size > 1:
            is_closed = await self.close_position(position)
            if is_closed:
                return None

        return await self.rebalanced_management(position)

    async def open_position_management(self, position: PositionStatus | None) -> PositionStatus | None:
        # meteora_position = await self.meteora_client.get_position()
        # hl_position = await self.hl_client.get_position()

        if position is None:
            logging.info("A efetuar a abertura de posição...")
            market_status = await self.meteora_client.get_status()
            range_percentage_raw = await self.hl_client.calculate_dynamic_range_width(limit=self.lookback_limit,
                                                                                      lookback=self.lookback_range)
            range_percentage = range_percentage_raw * (1 + (self.range_margin_pct * 2))
            logging.info(f"Range calculado Original: {range_percentage}, Reajustado: {range_percentage}")
            await self.open_position(market_status.raw_price, range_percentage)
            position = await self.meteora_client.get_position()
        return position

    async def heartbeat_log(self, last_heartbeat: float, heartbeat_interval: int):
        now = time.time()
        if now - last_heartbeat >= heartbeat_interval:
            formated_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            position_data = await self.meteora_client.get_position()

            # Log único e informativo
            msg = f"💚 [SINAL DE VIDA] {formated_time}"
            if position_data:
                wallet_balance = await self.get_balance(position_data)
                hl_pnl, hl_balance, _ = await self.hl_client.get_balance()
                total_pnl = (hl_pnl + position_data.pnlUsd - self.hyperliquid_fees)
                # is_turbulent = await self.hl_client.is_market_turbulent()
                msg += f" | Ativa: {position_data.address[:6]}... | Range: [{position_data.lowerPrice} - {position_data.upperPrice}] | Pnl: [Meteora: {position_data.pnlUsd:.2f}, Hyperliquid: {hl_pnl:.2f}, Total: {total_pnl:.2f}] | Balanço: [Wallet: {wallet_balance}, Hyperliquid: {hl_balance}]"
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
        MAX_RANGE_PCT = 0.02
        CALC_INTERVAL = 100
        COOLDOWN_DURATION = 300
        TURBULENCE_THRESHOLD = 0.005  # 0.5% de amplitude

        if await self.hl_client.is_market_turbulent(threshold=TURBULENCE_THRESHOLD):
            self.cooldown_until = current_time + 60  # Cooldown curto de 1min para turbulência
            logging.warning("⚠️ Mercado turbulento detetado (Amplitude elevada). Pausando.")
            return True

        # Verifica se precisamos de atualizar o range da Hyperliquid
        if current_time - self.last_calculation_time >= CALC_INTERVAL:
            self.last_known_range = await self.hl_client.calculate_dynamic_range_width(limit=self.lookback_limit,
                                                                                       lookback=self.lookback_range)
            self.last_calculation_time = current_time

        # Verifica se o range é abusivo
        if self.last_known_range > MAX_RANGE_PCT:
            if current_time < self.cooldown_until:
                # Ainda no tempo de espera
                return True
            else:
                # Acabou de entrar em volatilidade
                self.cooldown_until = current_time + COOLDOWN_DURATION
                logging.warning(f"⚠️ Range {self.last_known_range:.2%} > {MAX_RANGE_PCT:.2%}. Cooldown ativo.")
                return True

        # Mercado está estável
        return False

    async def start_sniper_cycle(self):
        await self.hl_client.start()
        await asyncio.sleep(2)

        # margin_percentage = 0.1
        heartbeat_interval = 120
        last_heartbeat = time.time()

        # position_data = await self.meteora_client.get_position()
        while True:
            try:

                """
                if position_data is None:
                    if not await self.should_wait_for_market():
                        position_data = await self.open_position_management(position_data)
                    else:
                        await asyncio.sleep(10)  # Descanso profundo
                    continue
                elif position_data.size > 1:
                    is_closed = await self.close_position(position_data)
                    if is_closed:
                        position_data = None
                        continue
                position_data = await self.rebalanced_management(position_data)
                """
                await self.loop_management()
                last_heartbeat = await self.heartbeat_log(last_heartbeat, heartbeat_interval)

                await asyncio.sleep(5)
            except Exception as e:
                logging.error(f"❌ Erro no ciclo do sniper: {e}")
                await asyncio.sleep(30)  # Cooldown em caso de erro de rede


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
    # bot.meteora_client.get_last_position()
    asyncio.run(bot.start_sniper_cycle())
