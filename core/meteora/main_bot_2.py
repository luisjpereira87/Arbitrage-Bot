import asyncio
import enum
import logging
import os
import sys
import time
from datetime import datetime

from core.meteora.dclass import PositionStatus, RangeStatus
from core.meteora.hl_client import HlClient
from core.meteora.meteora_client import MeteoraClient
from core.meteora.pool_manager_dclass import PoolManager


class ActionType(enum.Enum):
    TAKE_PROFIT_ORCA = "TAKE_PROFIT_ORCA"
    STOP_LOSS_REVERSE_TO_HL = "STOP_LOSS_REVERSE_TO_HL"
    OUT_OF_RANGE_CLOSE = "OUT_OF_RANGE_CLOSE"
    NONE = "NONE"


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

    def _evaluate_trailing_profit(self, current_pnl: float, current_peak: float, target_profit: float, label: str,
                                  is_stop_loss=False) -> \
            tuple[bool, float]:
        """
        Método helper genérico para gerir o Trailing Take Profit (Orca ou Hyperliquid).
        Retorna: (should_close: bool, updated_peak: float)
        """

        # 1. Validação de Stop Loss (Apenas tiver ativo)
        if is_stop_loss:
            stop_loss_limit = -(target_profit / 2.0)
            if current_pnl <= stop_loss_limit:
                logging.warning(
                    f"🚨 [{label}] Stop-Loss acionado! PnL atual: ${current_pnl:.2f} (Limite: ${stop_loss_limit:.2f})")
                return True, 0.0

        # 2. Se ainda não atingiu o alvo de lucro
        if current_pnl < target_profit:
            # Se por acaso estava em pico mas o PnL caiu abaixo do alvo, resetamos o pico
            return False, 0.0

        # 3. Se atingiu o alvo e o pico ainda é 0 (acabou de entrar em zona de lucro)
        if current_peak == 0.0:
            logging.info(f"🎯 [{label}] Alvo de lucro atingido (${current_pnl:.2f}). A iniciar rastreio de pico...")
            return False, current_pnl

        # 4. Se o PnL atual é maior que o pico, atualizamos o pico
        if current_pnl > current_peak:
            logging.info(f"📈 [{label}] Novo pico de lucro: ${current_pnl:.2f}")
            return False, current_pnl

        # 5. Se o PnL atual é menor ou igual ao pico, significa que começou a recuar
        if current_pnl <= current_peak:
            logging.info(
                f"📉 [{label}] Lucro a estabilizar/cair do pico (${current_peak:.2f} -> ${current_pnl:.2f}). A fechar!")
            return True, 0.0  # Reset do pico e autoriza o fecho

        return False, current_peak

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
        position_data = await self.meteora_client.get_position()
        orca_pnl = position_data.pnlUsd if position_data is not None else 0.0

        # Alvos baseados apenas na Orca
        profit_target = self.total_usdc_capital * self.profit_target_pct

        # Exemplo de limiar de prejuízo para inversão (metade do target positivo ou valor fixo ex: -0.03)
        loss_trigger_limit = -(profit_target * 0.5)  # Ou podes fixar ex: -0.03

        # 1. TAKE PROFIT: Atingiu a meta de lucro na Orca
        should_close_orca, self.peak_pnl_orca = self._evaluate_trailing_profit(
            orca_pnl, self.peak_pnl_orca, profit_target, "Orca", False
        )
        if should_close_orca:
            return True, ActionType.TAKE_PROFIT_ORCA

        # 2. STOP LOSS / INVERSÃO: O prejuízo atingiu o limite de tolerância (ex: -0.03)
        is_turbulent = await self.hl_client.is_market_turbulent(threshold=0.005)
        if orca_pnl <= loss_trigger_limit and is_turbulent:
            logging.warning(
                f"🚨 Prejuízo limite atingido na Orca (${orca_pnl:.2f} <= ${loss_trigger_limit:.2f}) e mercado turbulento. A inverter para Short na Hyperliquid!")
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

    async def check_and_close_management(self, position: PositionStatus) -> PositionStatus | None:
        if position is None or position.size != 1:
            return position

        lower_price = position.lowerPrice
        upper_price = position.upperPrice

        # Valida as condições de saída
        is_outside, action_type = await self.is_price_outside_range_sustained(lower_price, upper_price)

        if not is_outside:
            return position

        logging.warning(f"🚨 AÇÃO ACIONADA [{action_type}]! A processar fecho...")

        # Fecha a posição na Orca em qualquer um dos cenários de saída
        is_closed_orca = await self.close_position_dex()

        if is_closed_orca:
            logging.info("✅ Posição da Orca fechada com sucesso.")

            self.peak_pnl_orca = 0.0

            # SE O MOTIVO FOI O PREJUÍZO LIMITE, ABRIMOS O SHORT NA HYPERLIQUID
            if action_type == action_type.STOP_LOSS_REVERSE_TO_HL:
                logging.info("🔄 A executar estratégia de inversão: Abrir Short na Hyperliquid...")
                market_status = await self.meteora_client.get_status()
                await self.open_position_hl(market_status.raw_price)

            self.out_of_range_since = None
            return None  # Retorna None para recomeçar o ciclo limpo
        else:
            logging.error("❌ Falha ao fechar a posição na Orca neste ciclo.")
            return position

    async def check_and_close_hl_management(self, position_hl) -> None:
        """
        Monitoriza a posição de Short na Hyperliquid.
        Quando atingir o lucro desejado ou o mercado estabilizar, fecha o short e reinicia a Orca.
        """
        hl_pnl, hl_balance, _ = await self.hl_client.get_balance()

        # Define a tua meta de lucro para o short (ex: o mesmo target ou adaptado à queda)
        short_profit_target = self.total_usdc_capital * self.profit_target_pct * 1.2  # Exemplo: alvo ligeiramente maior na queda

        logging.info(
            f"🐻 [Modo Short Ativo] PnL atual da Hyperliquid: ${hl_pnl:.2f} (Alvo: +${short_profit_target:.2f})")

        # CONDIÇÃO 1: Atingiu o lucro no short
        should_close_hl, self.peak_pnl_hl = self._evaluate_trailing_profit(
            hl_pnl, self.peak_pnl_hl, short_profit_target, "Short HL", True
        )

        if should_close_hl:
            is_closed = await self.close_position_hl()
            if is_closed:
                logging.info("✅ Short fechado no topo com sucesso. A voltar para a Orca.")
                self.peak_pnl_hl = 0.0
            return None
        return None

    async def loop_management(self) -> None | PositionStatus:
        position_dex = await self.meteora_client.get_position()
        position_hl = await self.hl_client.get_position()

        # CASO 1: Tudo vazio -> Abre nova posição na Orca
        if position_dex is None and position_hl is None:
            if not await self.should_wait_for_market():
                return await self.open_position_management(position_dex)
            else:
                await asyncio.sleep(10)
            return None

        # CASO 2: Apenas a Orca está ativa -> Gestão normal de range/lucro/prejuízo
        if position_dex is not None and position_hl is None:
            return await self.check_and_close_management(position_dex)

        # CASO 3: Apenas o Short da Hyperliquid está ativo -> Monitorizar para fechar e voltar à Orca!
        if position_dex is None and position_hl is not None:
            return await self.check_and_close_hl_management(position_hl)

        # CASO DE SEGURANÇA: Se por algum erro houver ambos ativos ao mesmo tempo, limpa a DEX para alinhar
        if position_dex is not None and position_hl is not None:
            logging.warning("⚠️ Estado anómalo: Orca e Hyperliquid ativas em simultâneo. A fechar Orca...")
            await self.close_position_dex()
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
    bot = DeltaNeutralSniperAggressiveBot(usdc_min_hl=15, total_usdc_capital=30, profit_target_pct=0.004,
                                          sdk_file_path="orca_bot.js")
    # bot.meteora_client.get_last_position()

    asyncio.run(bot.start_sniper_cycle())

    # asyncio.run(bot.meteora_client.open_position(24.0, 867.88697, 0.04))
    # asyncio.run(bot.hl_client.calculate_dynamic_range_width(limit=100, lookback=96, buffer=0))
