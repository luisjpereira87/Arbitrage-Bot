import asyncio
import enum
import logging
import os
import sys
import time
from datetime import datetime

from core.bots.exchanges.indicators_utils import RsiCondition, RsiMomentum
from core.meteora.dclass import PositionStatus, RangeStatus
from core.meteora.hl_client import HlClient, DirectionMarket
from core.meteora.meteora_client import MeteoraClient
from core.meteora.pool_manager_dclass import PoolManager


class ActionType(enum.Enum):
    TAKE_PROFIT_ORCA = "TAKE_PROFIT_ORCA"
    STOP_LOSS_REVERSE_TO_HL = "STOP_LOSS_REVERSE_TO_HL"
    OUT_OF_RANGE_CLOSE = "OUT_OF_RANGE_CLOSE"
    NONE = "NONE"


class MarketAction(enum.Enum):
    OPEN_LONG = "open_long"
    OPEN_SHORT = "open_short"
    CONTINUE_LONG = "continue_long"
    CONTINUE_SHORT = "continue_short"


class DeltaNeutralSniperAggressiveBot:
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

        self.peak_pnl_orca = 0.0
        self.peak_pnl_hl = 0.0

        self.cooldown_duration_15m = 900
        self.cooldown_duration_5m = 300

        # 🧠 NOVAS VARIÁVEIS PARA O COOLDOWN INTELIGENTE BASEADO EM RETRACE
        self.waiting_for_retrace = False
        self.target_short_price = 0.0
        self.orca_loss_to_compensate = 0.0
        self.retrace_start_time = 0.0

    async def _evaluate_position_exit(self, current_pnl: float, current_peak: float, target_profit: float,
                                      action: MarketAction, label: str, is_stop_loss=False) -> tuple[bool, float]:
        """
        Avalia o fecho da posição combinando:
        1. Lógica mecânica de Trailing Take Profit.
        2. Validação técnica (RSI/EMA) através do motor central.
        """

        # A. Validação Técnica (Decisão Antecipada)
        # Se o motor disser que não devemos continuar, fechamos mesmo que o PnL esteja abaixo do pico
        if not await self.evaluate_market_condition(action):
            logging.info(f"🛑 [{label}] Fecho técnico acionado pelo RSI/EMA.")
            return True, 0.0

        # 1. Validação de Stop Loss (Apenas tiver ativo)
        if is_stop_loss:
            stop_loss_limit = -(target_profit / 2.0)
            if current_pnl <= stop_loss_limit:
                logging.warning(
                    f"🚨 [{label}] Stop-Loss acionado! PnL atual: ${current_pnl:.2f} (Limite: ${stop_loss_limit:.2f})")
                return True, 0.0

        # B. Lógica de Trailing (O PnL dita a regra)
        if current_pnl < target_profit:
            return False, 0.0

        if current_peak == 0.0:
            return False, current_pnl

        if current_pnl > current_peak:
            return False, current_pnl

        if current_pnl <= current_peak:
            logging.info(f"📉 [{label}] Lucro a recuar (${current_peak:.2f} -> ${current_pnl:.2f}). A fechar!")
            return True, 0.0

        return False, current_peak

    async def evaluate_market_condition(self, action: MarketAction) -> bool:
        """
        Motor central de decisão. Avalia o RSI, a EMA do RSI, turbulência
        e devolve True se a ação for permitida, ou False caso contrário.
        """
        TURBULENCE_THRESHOLD = 0.005

        # 1. Verificação global de turbulência (aplica-se a qualquer ação)
        is_turbulent, direction = await self.hl_client.is_market_turbulent(threshold=TURBULENCE_THRESHOLD)
        if is_turbulent:
            # Se houver turbulência violenta contra a direção pretendida, bloqueia
            if action in [MarketAction.OPEN_LONG, MarketAction.CONTINUE_LONG] and direction == DirectionMarket.DOWN:
                return False
            if action in [MarketAction.OPEN_SHORT, MarketAction.CONTINUE_SHORT] and direction == DirectionMarket.UP:
                return False

        # 2. Obter o contexto técnico avançado do RSI de uma só vez
        rsi_info = await self.hl_client.check_rsi_condition()

        # 3. Avaliação específica por cada intenção de trading

        # --- ABRIR LONG ---
        if action == MarketAction.OPEN_LONG:
            # Não abrir Long se estivermos em extremos de sobrecompra ou se a EMA estiver a apontar para baixo
            if rsi_info.state in [RsiCondition.OVERBOUGHT, RsiCondition.EXTREME_OVERBOUGHT]:
                return False
            if rsi_info.position_to_ema == RsiMomentum.EMA_BELOW and rsi_info.momentum == RsiMomentum.COOLING:
                return False
            return True

        # --- ABRIR SHORT ---
        if action == MarketAction.OPEN_SHORT:
            # Não abrir Short se estivermos em extremos de sobrevenda ou se o momentum estiver fortemente aquecido para cima
            if rsi_info.state in [RsiCondition.OVERSOLD, RsiCondition.EXTREME_OVERSOLD]:
                return False
            if rsi_info.position_to_ema == RsiMomentum.EMA_ABOVE and rsi_info.momentum == RsiMomentum.HEATING:
                return False
            return True

        # --- CONTINUAR COM O LONG ---
        if action == MarketAction.CONTINUE_LONG:
            # Queremos manter o Long enquanto o RSI não cruzar abaixo da EMA de forma agressiva
            if rsi_info.rsi_crossed_ema_down and rsi_info.state in [RsiCondition.OVERBOUGHT,
                                                                    RsiCondition.EXTREME_OVERBOUGHT]:
                logging.info("🛑 [Sinal de Saída Long] RSI cruzou abaixo da EMA em zona alta. Sugere fechar Long.")
                return False  # Indica que não deve continuar (deve fechar)
            return True

        # --- CONTINUAR COM O SHORT ---
        if action == MarketAction.CONTINUE_SHORT:
            # Queremos manter o Short enquanto o RSI não cruzar acima da EMA de forma agressiva
            if rsi_info.rsi_crossed_ema_up and rsi_info.state in [RsiCondition.OVERSOLD, RsiCondition.EXTREME_OVERSOLD]:
                logging.info(
                    "🛑 [Sinal de Saída Short] RSI cruzou abaixo/acima da EMA em zona baixa. Sugere fechar Short.")
                return False  # Indica que não deve continuar (deve fechar)
            return True

        return True

    async def calculate_open_balance(self, price_token: float) -> tuple[float, float]:
        capital_para_hedge = self.total_usdc_capital / 2
        _, usdc_hl_leg = await self.hl_client.adjust_balance(capital_para_hedge, price_token)
        usdc_meteora = usdc_hl_leg * 2

        return usdc_meteora, usdc_hl_leg

    async def open_position_dex(self, current_price: float, range_width: float) -> bool:
        try:
            logging.info("A abrir posição na Orca")
            usdc_meteora, usdc_hl_leg = await self.calculate_open_balance(current_price)
            logging.info(
                f"🚀 [BALANCEAMENTO] Orca: {usdc_meteora:.4f} | HL: {usdc_hl_leg:.4f} (Ratio: {usdc_meteora / usdc_hl_leg:.2f}x)")
            is_open = await self.meteora_client.open_position(usdc_meteora, current_price, range_width)
            logging.info(f"Posição aberta na Meteora?: {is_open}")
            if not is_open:
                return False
            return True

        except Exception as e:
            logging.error(f"❌ Falha ao abrir na Meteora: {e}")
            return False

    async def open_position_hl(self, current_price: float) -> bool:
        try:
            logging.info("A abrir posição na Hyperliquid")
            usdc_meteora, usdc_hl_leg = await self.calculate_open_balance(current_price)
            logging.info(
                f"🚀 [BALANCEAMENTO] Meteora: {usdc_meteora:.4f} | HL: {usdc_hl_leg:.4f} (Ratio: {usdc_meteora / usdc_hl_leg:.2f}x)")
            is_open = await self.hl_client.open_position(usdc_hl_leg)
            logging.info(f"Posição aberta na Hyperliquid?: {is_open}")
            if not is_open:
                return False
            return True
        except Exception as e:
            logging.error(f"❌ Falha no Hedge HL: {e}.")
            return False

    async def close_position_dex(self) -> bool:
        try:
            is_closed_meteora = await self.meteora_client.close_all()
            if is_closed_meteora:
                logging.info("⏳ Posição de Orca fechado com sucesso.")
                return True
            return False
        except Exception as e:
            logging.error(f"❌ Falha ao fechar posição na Orca: {e}.")
            return False

    async def close_position_hl(self) -> bool:
        try:
            is_closed_hl = await self.hl_client.close_position()
            if is_closed_hl:
                logging.info("✅ Posição de Hyperliquid fechado com sucesso.")
                return True
            return False
        except Exception as e:
            logging.error(f"❌ Falha ao fechar posição na Hyperliquid: {e}.")
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

    async def is_price_outside_range_sustained(self, min_price: float, max_price: float) -> tuple[bool, ActionType]:
        """
        Retorna (should_action, action_type)
        action_type pode ser: 'TAKE_PROFIT_ORCA', 'STOP_LOSS_REVERSE_TO_HL', 'OUT_OF_RANGE_CLOSE'
        """
        hl_pnl, _, _ = await self.hl_client.get_balance()
        hl_pnl = hl_pnl if hl_pnl is not None else 0.0
        position_data = await self.meteora_client.get_position()
        orca_pnl = position_data.pnlUsd if position_data is not None else 0.0

        total_pnl = (hl_pnl + orca_pnl - self.hyperliquid_fees)

        # Alvos baseados apenas na Orca
        profit_target = self.total_usdc_capital * self.profit_target_pct

        # Exemplo de limiar de prejuízo para inversão (metade do target positivo ou valor fixo ex: -0.03)
        loss_trigger_limit = -(profit_target * 0.5)  # Ou podes fixar ex: -0.03

        # 1. TAKE PROFIT: Atingiu a meta de lucro na Orca
        should_close_orca, self.peak_pnl_orca = await self._evaluate_position_exit(
            total_pnl, self.peak_pnl_orca, profit_target, MarketAction.CONTINUE_LONG, "Orca", False
        )
        if should_close_orca:
            return True, ActionType.TAKE_PROFIT_ORCA

        # 2. STOP LOSS / INVERSÃO: O prejuízo atingiu o limite de tolerância (ex: -0.03)
        # is_turbulent, direction = await self.hl_client.is_market_turbulent(threshold=0.003)
        if orca_pnl <= loss_trigger_limit:
            logging.warning(
                f"🚨 Prejuízo limite atingido na Orca (${orca_pnl:.2f} <= ${loss_trigger_limit:.2f}). A abrir Short na Hyperliquid!")
            self.out_of_range_since = None
            return True, ActionType.STOP_LOSS_REVERSE_TO_HL

        # 3. Verificar o estado atual do range de preços
        status = await self.hl_client.check_range_status(min_price, max_price, self.range_margin_pct)

        if status == RangeStatus.INSIDE:
            if self.out_of_range_since is not None:
                logging.info("✅ Preço voltou para dentro do range. Timer resetado.")
                self.out_of_range_since = None
            return False, ActionType.NONE

        # 4. SE ESTIVER FORA DO RANGE
        if status in [RangeStatus.OUT_UPPER, RangeStatus.OUT_LOWER]:
            if orca_pnl > 0:
                logging.warning(f"🚨 Preço fora do range com PnL positivo (${orca_pnl:.2f}), fecho preventivo...")
                self.out_of_range_since = None
                return True, ActionType.OUT_OF_RANGE_CLOSE

            # Lógica de timer dinâmico para saída por tempo fora do range
            distance_pct = await self.hl_client.get_range_distance_percentage(min_price, max_price)

            if distance_pct >= 0.15:
                dynamic_duration = 600
            elif distance_pct >= 0.10:
                dynamic_duration = 300
            elif distance_pct >= 0.05:
                dynamic_duration = 60
            else:
                dynamic_duration = 0

            if dynamic_duration == 0:
                logging.warning(f"🚨 Preço ultrapassou limiar crítico ({distance_pct * 100:.1f}%). Fecho imediato!")
                self.out_of_range_since = None
                return True, ActionType.OUT_OF_RANGE_CLOSE

            if self.out_of_range_since is None:
                self.out_of_range_since = time.time()
                logging.info(f"⚠️ Preço {status} ({distance_pct * 100:.1f}%). Timer de {dynamic_duration}s iniciado...")
                return False, ActionType.NONE

            elapsed = time.time() - self.out_of_range_since
            if elapsed >= dynamic_duration:
                logging.info(f"🚨 Timer dinâmico de {dynamic_duration}s atingido fora do range.")
                self.out_of_range_since = None
                return True, ActionType.OUT_OF_RANGE_CLOSE

        return False, ActionType.NONE

    async def check_and_close_management(self, position: PositionStatus | None) -> PositionStatus | None:
        # CASO RESIDUAL: Apenas HL aberta (Orca já fechou)
        if position is None:
            position_hl = await self.hl_client.get_position()
            if position_hl is not None:
                hl_pnl, _, _ = await self.hl_client.get_balance()
                if hl_pnl >= 0.0:  # Fecha se estiver no break-even ou lucro
                    logging.info("🧹 Fechando Short residual na HL...")
                    await self.close_position_hl()
            return None

        # CASO NORMAL OU HÍBRIDO (Orca aberta)
        lower_price, upper_price = position.lowerPrice, position.upperPrice
        is_outside, action_type = await self.is_price_outside_range_sustained(lower_price, upper_price)

        # 1. Proteção: Se prejuízo na Orca, abre HL (se não estiver aberta)
        if is_outside and action_type == ActionType.STOP_LOSS_REVERSE_TO_HL:
            if await self.hl_client.get_position() is None:
                logging.warning("🚨 Prejuízo crítico. Ativando Hedge na Hyperliquid...")
                await self.open_position_hl((await self.meteora_client.get_status()).raw_price)
            return position

        # 2. Fecho: Se sinal de saída, fecha tudo
        if is_outside:
            logging.warning(f"🚨 AÇÃO [{action_type}]! Encerrando todas as posições...")

            # Fecha a DEX
            if await self.close_position_dex():
                logging.info("✅ Orca fechada.")
                # Fecha a HL se existir
                if await self.hl_client.get_position() is not None:
                    await self.close_position_hl()

                self.peak_pnl_orca = 0.0
                self.out_of_range_since = None
                self.cooldown_until = time.time() + self.cooldown_duration_5m
                return None

        return position

    """
    async def check_and_close_hl_management(self, position_hl) -> None:
        hl_pnl, hl_balance, _ = await self.hl_client.get_balance()
        short_profit_target = self.total_usdc_capital * self.profit_target_pct * 1.2

        logging.info(
            f"🐻 [Modo Short Ativo] PnL atual da Hyperliquid: ${hl_pnl:.2f} (Alvo: +${short_profit_target:.2f})")

        # Condição normal de Trailing Take Profit
        should_close_hl, self.peak_pnl_hl = await self._evaluate_position_exit(
            hl_pnl, self.peak_pnl_hl, short_profit_target, MarketAction.CONTINUE_SHORT, "Short HL", True
        )

        if should_close_hl:
            is_closed = await self.close_position_hl()
            if is_closed:
                logging.info("✅ Short fechado no topo com sucesso. A voltar para a Orca.")
                self.peak_pnl_hl = 0.0
                self.cooldown_until = time.time() + self.cooldown_duration_15m
            return None
        return None
    """

    async def loop_management(self) -> None | PositionStatus:
        position_dex = await self.meteora_client.get_position()

        # O loop só precisa de saber se temos algo a gerir: ou Orca, ou HL (se não houver Orca)
        if position_dex is not None:
            return await self.check_and_close_management(position_dex)

        # Se não há Orca, verifica se ainda resta algo na HL que precise de fecho residual
        position_hl = await self.hl_client.get_position()
        if position_hl is not None:
            await self.check_and_close_management(None)  # Passamos None para tratar o caso residual
            return None

        # Se tudo vazio, tenta abrir
        if not await self.should_wait_for_market():
            return await self.open_position_management(None)

        await asyncio.sleep(10)
        return None

    async def open_position_management(self, position: PositionStatus | None) -> PositionStatus | None:
        if position is None:
            logging.info("A efetuar a abertura de posição...")
            market_status = await self.meteora_client.get_status()
            range_percentage_raw = await self.hl_client.calculate_dynamic_range_width(limit=self.lookback_limit,
                                                                                      lookback=self.lookback_range,
                                                                                      buffer=self.range_margin_pct)
            logging.info(f"Range calculado Original: {range_percentage_raw}")
            await self.open_position_dex(market_status.raw_price, range_percentage_raw)
            position = await self.meteora_client.get_position()
        return position

    async def heartbeat_log(self, position_data: PositionStatus | None, last_heartbeat: float, heartbeat_interval: int):
        now = time.time()
        if now - last_heartbeat >= heartbeat_interval:
            formated_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            msg = f"💚 [SINAL DE VIDA] {formated_time}"

            position_hl = await self.hl_client.get_position()

            if position_data:
                wallet_balance = await self.get_balance(position_data)
                hl_pnl, hl_balance, _ = await self.hl_client.get_balance()
                total_pnl = (hl_pnl + position_data.pnlUsd - self.hyperliquid_fees)
                msg += f" | Orca Ativa: {position_data.address[:6]}... | Range: [{position_data.lowerPrice} - {position_data.upperPrice}] | Pnl: [Orca: {position_data.pnlUsd:.2f}, HL: {hl_pnl:.2f}, Total: {total_pnl:.2f}] | Balanço: [Wallet: {wallet_balance}, HL: {hl_balance}]"
            elif position_hl:
                hl_pnl, hl_balance, _ = await self.hl_client.get_balance()
                msg += f" | 🐻 [Modo Short HL Ativo] | Pnl Short: ${hl_pnl:.2f} | Balanço HL: {hl_balance}"
            else:
                msg += " | Sem posição ativa (À procura de mercado)."

            logging.info(msg)
            return now
        return last_heartbeat

    async def should_wait_for_market(self) -> bool:
        current_time = time.time()
        MAX_RANGE_PCT = 0.05
        CALC_INTERVAL = 100
        TURBULENCE_THRESHOLD = 0.005

        # 1. Cooldown Geral
        if self.cooldown_until and current_time < self.cooldown_until:
            return True

        # 2. Turbulência Geral (já integrada no engine, mas mantemos o check rápido ou delegamos)
        is_turbulent, _ = await self.hl_client.is_market_turbulent(threshold=TURBULENCE_THRESHOLD)
        if is_turbulent:
            self.cooldown_until = current_time + 60
            return True

        # 3. 🧠 FILTRO INTELIGENTE DE RSI USANDO O MOTOR CENTRAL
        can_open_long = await self.evaluate_market_condition(MarketAction.OPEN_LONG)
        if not can_open_long:
            self.cooldown_until = current_time + 900
            logging.warning(
                "⚠️ [Filtro RSI Central] Mercado desfavorável para abrir posição (Extremos ou Momentum oposto). Cooldown de 15m.")
            return True

        # 4. Verificação de Range da Hyperliquid
        if current_time - self.last_calculation_time >= CALC_INTERVAL:
            self.last_known_range = await self.hl_client.calculate_dynamic_range_width(
                limit=self.lookback_limit, lookback=self.lookback_range, buffer=self.range_margin_pct
            )
            self.last_calculation_time = current_time

        if self.last_known_range > MAX_RANGE_PCT:
            self.cooldown_until = current_time + self.cooldown_duration_5m
            return True

        return False

    """
    async def handle_retrace(self) -> bool:
        if not self.waiting_for_retrace:
            return False

        current_time = time.time()

        # A. Timeout de 15 minutos
        if current_time - self.retrace_start_time > 900:
            logging.warning("⚠️ [Retrace Timeout] 15 minutos esgotados. A cancelar retrace e ativar castigo de 1h.")
            self.waiting_for_retrace = False
            self.cooldown_until = current_time + 3600
            return False

        market_status = await self.meteora_client.get_status()
        current_price = market_status.raw_price

        # B. Se ainda não chegou ao preço alvo, continua focado no retrace
        if current_price < self.target_short_price:
            return True

        # C. 🧠 Validação global através do Motor de Decisão para Short
        can_open_short = await self.evaluate_market_condition(MarketAction.OPEN_SHORT)
        if not can_open_short:
            logging.warning(
                "🚨 [Abortar Short] Condições técnicas desfavoráveis no alvo de retrace (Turbulência ou RSI inadequado). A cancelar Short.")
            self.waiting_for_retrace = False
            self.cooldown_until = current_time + 300
            return False

        # D. Tudo aprovado! Dispara o Short
        logging.info("🎯 [Retrace Bem-Sucedido] Condições validadas pelo motor central. A abrir Short na Hyperliquid.")
        self.waiting_for_retrace = False
        await self.open_position_hl(current_price)
        return True
    """

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
    bot = DeltaNeutralSniperAggressiveBot(usdc_min_hl=15, total_usdc_capital=30, profit_target_pct=0.004,
                                          sdk_file_path="orca_bot.js")
    # bot.meteora_client.get_last_position()

    asyncio.run(bot.start_sniper_cycle())

    # asyncio.run(bot.meteora_client.open_position(24.0, 867.88697, 0.04))
    # asyncio.run(bot.hl_client.calculate_dynamic_range_width(limit=100, lookback=96, buffer=0))
