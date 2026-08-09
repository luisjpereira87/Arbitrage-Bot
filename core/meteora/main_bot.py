import asyncio
import logging
import os
import sys
import time
from datetime import datetime

from core.meteora.dclass import PositionStatus, RangeStatus
from core.meteora.hl_client import HlClient
from core.meteora.meteora_client import MeteoraClient
from core.meteora.pool_manager_dclass import PoolManager


class DeltaNeutralSniperBot:
    def __init__(self, usdc_min_hl: float, total_usdc_capital: float, profit_target_pct: float, sdk_file_path: str):

        base_path = os.path.dirname(os.path.abspath(__file__))

        # 2. Corrigir de forma forçada caso o caminho já traga "core/meteora" duplicado
        if "core/meteora" in base_path:
            self.js_script_path = os.path.join(base_path, sdk_file_path)
        else:
            self.js_script_path = os.path.join(base_path, "core", "meteora", sdk_file_path)

        # 3. PRINT DE SEGURANÇA
        print(f"🔍 [Debug Caminho] A tentar chamar o Node em: {self.js_script_path}")

        # Configuração de alocação de fundos
        self.total_usdc_capital = total_usdc_capital
        self.usdc_min_hl = usdc_min_hl
        self.usdc_hl_leg = (self.total_usdc_capital / 2) * 0.995

        if self.usdc_min_hl < self.usdc_hl_leg:
            logging.error(
                f"❌ Falha: usdc_hl_leg {self.usdc_hl_leg} tem que ser maior que usdc_min_hl {self.usdc_min_hl}!")
            raise RuntimeError("Something bad happened")

        self.profit_target_pct = profit_target_pct
        self.pool_config = PoolManager().get("SOL/USDC-ORCA")

        self.meteora_client = MeteoraClient(self.js_script_path, self.pool_config)
        self.hl_client = HlClient()

        self.out_of_range_since = None
        self.last_log_time = 0

        self.cooldown_until = 0
        self.last_known_range = 0.0
        self.last_calculation_time = 0

        self.lookback_range = 96
        self.lookback_limit = 100
        self.range_margin_pct = 0.15

        slippage_buffer = 0.0002
        fee_rate = 0.00025 + slippage_buffer
        self.hyperliquid_fees = max((self.usdc_hl_leg * fee_rate) * 2, 0.02)

    async def calculate_open_balance_old(self, price_token: float) -> tuple[float, float]:
        capital_para_hedge = self.total_usdc_capital / 2
        _, usdc_hl_leg = await self.hl_client.adjust_balance(capital_para_hedge, price_token)
        usdc_meteora = usdc_hl_leg * 2

        return usdc_meteora, usdc_hl_leg

    async def calculate_open_balance(self, price_token: float) -> tuple[float, float]:
        capital_para_hedge = self.total_usdc_capital / 2

        # 1. Ajusta aos limites da exchange
        _, capital_ajustado_hl = await self.hl_client.adjust_balance(capital_para_hedge, price_token)

        # 2. A Meteora usa o capital cheio e original (sem o desconto do alpha)
        usdc_meteora = capital_ajustado_hl * 2

        # 3. Aplica o desconto do alpha APENAS para calcular o tamanho do short na Hyperliquid
        final_usdc_hl_leg = await self.hl_client.calculate_hedge_size_based_on_alpha(price_token, capital_ajustado_hl)

        # 4. Garante que o valor final com desconto ainda cumpre as regras da exchange
        _, final_usdc_hl_leg_ajustado = await self.hl_client.adjust_balance(final_usdc_hl_leg, price_token)

        return usdc_meteora, final_usdc_hl_leg_ajustado

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
            # TODO ATUALIZAR NO SDK DA METEORA CONFORME ESTÁ NA ORCA
            sol_balance_strategy = position.totalXAmount  # / (10 ** self.pool_config.tokenX.decimals)
            usdc_balance_strategy = position.totalYAmount  # / (10 ** self.pool_config.tokenY.decimals)

        usdc_balance_total_strategy = (sol_price * sol_balance_strategy) + usdc_balance_strategy

        return usdc_balance_total_wallet + usdc_balance_total_strategy

    async def is_price_outside_range_sustained(self, min_price: float, max_price: float) -> bool:

        # 1. Obtenção de dados e cálculo do PnL total
        hl_pnl, _, _ = await self.hl_client.get_balance()
        position_data = await self.meteora_client.get_position()

        meteora_pnl = position_data.pnlUsd if position_data is not None else 0.0
        total_pnl = (hl_pnl + meteora_pnl - self.hyperliquid_fees)

        # Novo target de lucro ajustado para a nova estratégia (0.2%)
        profit_target = self.total_usdc_capital * self.profit_target_pct

        # Se atingiu a meta de lucro, fecha independentemente de estar dentro ou fora
        if total_pnl >= profit_target:
            logging.info(f"✅ Preço atingiu a meta de lucro de 0.2%: {total_pnl:.2f}")
            self.out_of_range_since = None
            return True

        # 2. Verificar o estado atual do range
        status = await self.hl_client.check_range_status(min_price, max_price, self.range_margin_pct)

        # 3. SE ESTIVER DENTRO DO RANGE
        if status == RangeStatus.INSIDE:
            if self.out_of_range_since is not None:
                logging.info("✅ Preço voltou para dentro do range. Timer resetado.")
                self.out_of_range_since = None
            return False

        # 4. SE ESTIVER FORA DO RANGE (OUT_UPPER ou OUT_LOWER)
        if status == RangeStatus.OUT_UPPER or status == RangeStatus.OUT_LOWER:

            # Se já está fora mas o PnL é positivo, fecha logo para garantir o ganho
            if total_pnl > 0:
                logging.warning(f"🚨 Preço fora do range com PnL positivo: {total_pnl:.2f}, fecho imediato...")
                self.out_of_range_since = None
                return True

            # Calcular a distância percentual ao limite para definir a urgência do timer
            distance_pct = await self.hl_client.get_range_distance_percentage(min_price, max_price)

            if distance_pct >= 0.15:
                dynamic_duration = 600  # Acima de 15% (ou mais longe)
            elif distance_pct >= 0.10:
                dynamic_duration = 300  # Intervalo exato entre 0.15 e 0.10 faz 300s
            elif distance_pct >= 0.05:
                dynamic_duration = 60  # Intervalo exato entre 0.10 e 0.05 faz 60s
            else:
                dynamic_duration = 0  # Abaixo de 0.05 (muito perto do limite) -> Fecho imediato

            if dynamic_duration == 0:
                logging.warning(
                    f"🚨 Preço ultrapassou o limiar crítico de proximidade ({distance_pct * 100:.1f}%). Fecho imediato!")
                self.out_of_range_since = None
                return True

            # Iniciar ou acompanhar o temporizador dinâmico
            if self.out_of_range_since is None:
                self.out_of_range_since = time.time()
                logging.info(
                    f"⚠️ Preço {status} ({distance_pct * 100:.1f}%). Iniciando timer dinâmico de {dynamic_duration}s...")
                return False

            elapsed = time.time() - self.out_of_range_since
            if elapsed >= dynamic_duration:
                logging.info(f"🚨 Timer dinâmico de {dynamic_duration}s atingido. Rebalanceamento autorizado!")
                self.out_of_range_since = None
                return True

            # Log periódico para evitar spam na consola (a cada 20 segundos)
            if time.time() - getattr(self, 'last_log_time', 0) > 20:
                logging.info(f"⏳ Aguardando... Fora há {elapsed:.0f}s de {dynamic_duration}s necessários.")
                self.last_log_time = time.time()

            return False

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
            upper_price
        )

        if is_outside:
            if await self.should_wait_for_market():
                is_closed = await self.close_position(position)
                if is_closed:
                    return None

            logging.warning("🚨 PREÇO FORA DO RANGE! Rebalanceando...")
            market_status = await self.meteora_client.get_status()
            range_percentage_raw = await self.hl_client.calculate_dynamic_range_width(limit=self.lookback_limit,
                                                                                      lookback=self.lookback_range,
                                                                                      buffer=self.range_margin_pct)
            logging.info(f"Range calculado Original: {range_percentage_raw}")
            is_rebalanced = await self.rebalanced_position(market_status.raw_price,
                                                           range_percentage_raw)
            position = await self.meteora_client.get_position()
            if is_rebalanced:
                self.out_of_range_since = None
                position = await self.meteora_client.get_position()
            else:
                logging.error("Meteora rebalance failed")
        return position

    async def check_and_close_management(self, position: PositionStatus) -> PositionStatus | None:
        if position is None or position.size != 1:
            return position

        lower_price = position.lowerPrice
        upper_price = position.upperPrice

        # 1. Verifica se o preço está fora do range de forma sustentada
        is_outside = await self.is_price_outside_range_sustained(
            lower_price,
            upper_price
        )

        if not is_outside:
            # Se estiver dentro do range, mantém a gestão normal
            return position

        logging.warning("🚨 PREÇO FORA DO RANGE! A iniciar processo de fecho...")

        # 2. Opcional: validações de mercado antes de fechar
        # if await self.should_wait_for_market():
        # 3. Executa APENAS o fecho da posição e do hedge
        is_closed = await self.close_position(position)

        if is_closed:
            logging.info("✅ Posição e hedge fechados com sucesso. Abertura agendada para o próximo loop.")
            self.out_of_range_since = None
            # Retorna None para indicar que a posição atual deixou de existir,
            # permitindo que o próximo ciclo trate da nova abertura de forma isolada.
            return None
        else:
            logging.error("❌ Falha ao fechar a posição neste ciclo.")
            return position

    async def loop_management(self) -> PositionStatus | None:
        position = await self.meteora_client.get_position()

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

        return await self.check_and_close_management(position)

    async def open_position_management(self, position: PositionStatus | None) -> PositionStatus | None:
        if position is None:
            logging.info("A efetuar a abertura de posição...")
            market_status = await self.meteora_client.get_status()
            range_percentage_raw = await self.hl_client.calculate_dynamic_range_width(limit=self.lookback_limit,
                                                                                      lookback=self.lookback_range,
                                                                                      buffer=self.range_margin_pct)
            logging.info(f"Range calculado Original: {range_percentage_raw}")
            await self.open_position(market_status.raw_price, range_percentage_raw)
            position = await self.meteora_client.get_position()
        return position

    async def heartbeat_log(self, position_data: PositionStatus | None, last_heartbeat: float, heartbeat_interval: int):
        now = time.time()
        if now - last_heartbeat >= heartbeat_interval:
            formated_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            msg = f"💚 [SINAL DE VIDA] {formated_time}"
            if position_data:
                wallet_balance = await self.get_balance(position_data)
                hl_pnl, hl_balance, _ = await self.hl_client.get_balance()
                total_pnl = (hl_pnl + position_data.pnlUsd - self.hyperliquid_fees)
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
        MAX_RANGE_PCT = 0.05
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
                                                                                       lookback=self.lookback_range,
                                                                                       buffer=self.range_margin_pct)
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

        heartbeat_interval = 120
        last_heartbeat = time.time()

        while True:
            try:
                position = await self.loop_management()
                last_heartbeat = await self.heartbeat_log(position, last_heartbeat, heartbeat_interval)

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
    bot = DeltaNeutralSniperBot(usdc_min_hl=15, total_usdc_capital=30, profit_target_pct=0.002,
                                sdk_file_path="orca_bot.js")
    # bot.meteora_client.get_last_position()

    asyncio.run(bot.start_sniper_cycle())

    # asyncio.run(bot.meteora_client.open_position(24.0, 867.88697, 0.04))
    # asyncio.run(bot.hl_client.calculate_dynamic_range_width(limit=100, lookback=96, buffer=0))
