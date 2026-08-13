import enum
from dataclasses import dataclass

import pandas as pd
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange


class RsiCondition(enum.Enum):
    OVERBOUGHT = "OVERBOUGHT"
    EXTREME_OVERBOUGHT = "EXTREME_OVERBOUGHT"
    OVERSOLD = "OVERSOLD"
    NEUTRAL = "NEUTRAL"
    EXTREME_OVERSOLD = "EXTREME_OVERSOLD"


class RsiMomentum(enum.Enum):
    COOLING = "COOLING"
    HEATING = "HEATING"
    EMA_ABOVE = "EMA_ABOVE"
    EMA_BELOW = "EMA_BELOW"


@dataclass
class RsiResponse:
    value: float
    state: RsiCondition
    ema: float
    momentum: RsiMomentum
    crossed_overbought: bool
    crossed_oversold: bool
    rsi_crossed_ema_up: bool
    rsi_crossed_ema_down: bool
    position_to_ema: RsiMomentum


class IndicatorsUtils():
    def __init__(self):
        pass

    @staticmethod
    def atr(ohlcv: pd.DataFrame, length=14):
        return AverageTrueRange(ohlcv["high"], ohlcv["low"], ohlcv["close"], window=length).average_true_range()

    @staticmethod
    def rsi(ohlcv: pd.DataFrame, length=14):
        return RSIIndicator(close=ohlcv["close"], window=length).rsi()

    @staticmethod
    def calculate_dynamic_range_width__(ohlcv: pd.DataFrame, length=14, multiplier=1.5):
        """
        ohlcv: DataFrame com colunas ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        """

        # Verificação robusta para DataFrame vazio
        if ohlcv.empty or len(ohlcv) < length:  # Garante que temos dados suficientes
            return 0.01

        # 1. Obtém a Series com os últimos 14 valores de ATR
        atr_series = IndicatorsUtils.atr(ohlcv, length=length)

        # 2. Pegamos apenas o último valor para o cálculo atual
        current_atr = atr_series.iloc[-1]

        # 3. Cálculo da percentagem
        last_close = ohlcv['close'].iloc[-1]
        range_width_percent = (current_atr * multiplier) / last_close
        print("AQUIII", range_width_percent)
        return range_width_percent

    @staticmethod
    def calculate_dynamic_range_width(ohlcv: pd.DataFrame, length=14, multiplier=1.5):
        # 1. Validação
        if ohlcv.empty or len(ohlcv) <= length:
            return 0.01

        # 2. Obtém a série completa de ATRs
        atr_series = IndicatorsUtils.atr(ohlcv, length=length)

        # 3. Em vez de pegar só no último, tira a média dos últimos 'length' valores
        # Isso dá-te uma medida muito mais resiliente de volatilidade
        smoothed_atr = atr_series.tail(length).mean()

        if pd.isna(smoothed_atr) or smoothed_atr <= 0:
            return 0.01

        last_close = ohlcv['close'].iloc[-1]
        range_width_percent = (smoothed_atr * multiplier) / last_close
        return range_width_percent

    @staticmethod
    def calculate_channel_width(ohlcv: pd.DataFrame, lookback=14):
        """
        Calcula a largura do canal baseada no máximo e mínimo dos últimos N candles.
        """
        if ohlcv.empty or len(ohlcv) < lookback:
            return 0.01

        # Pega nos últimos N candles
        last_n = ohlcv.tail(lookback)

        # O "Range" é a diferença entre o ponto mais alto e o mais baixo desse período
        channel_high = last_n['high'].max()
        channel_low = last_n['low'].min()

        channel_width = channel_high - channel_low

        # Converte para percentagem do preço atual
        current_price = ohlcv['close'].iloc[-1]
        range_percent = channel_width / current_price
        return range_percent

    @staticmethod
    def check_rsi_condition(ohlcv: pd.DataFrame, period=14, ema_period=9) -> RsiResponse:
        """
        Calcula o RSI atual e avalia o contexto técnico para decisões de trading.
        Retorna um dicionário com o valor, estado e indicação de cruzamento/momentum.
        """
        rsi_series = IndicatorsUtils.rsi(ohlcv, length=period)

        rsi_ema_series = rsi_series.ewm(span=ema_period, adjust=False).mean()

        # Obter os dois últimos valores para avaliar a direção do momentum
        current_rsi = float(rsi_series.iloc[-1])
        previous_rsi = float(rsi_series.iloc[-2])

        current_ema = float(rsi_ema_series.iloc[-1])
        previous_ema = float(rsi_ema_series.iloc[-2])

        # Determinar o estado base
        if current_rsi >= 75:
            state = RsiCondition.EXTREME_OVERBOUGHT
        elif current_rsi >= 70:
            state = RsiCondition.OVERBOUGHT
        elif current_rsi <= 25:
            state = RsiCondition.EXTREME_OVERSOLD
        elif current_rsi <= 30:
            state = RsiCondition.OVERSOLD
        else:
            state = RsiCondition.NEUTRAL

        # Validar momentum (se está a arrefecer ou a intensificar-se)
        momentum = RsiMomentum.COOLING if current_rsi < previous_rsi else RsiMomentum.HEATING

        # Cruzamentos da EMA do RSI
        rsi_crossed_ema_up = (previous_rsi <= previous_ema) and (current_rsi > current_ema)
        rsi_crossed_ema_down = (previous_rsi >= previous_ema) and (current_rsi < current_ema)

        position_to_ema = RsiMomentum.EMA_ABOVE if current_rsi > current_ema else RsiMomentum.EMA_BELOW

        return RsiResponse(
            value=current_rsi,
            ema=current_ema,
            state=state,
            momentum=momentum,
            crossed_overbought=(previous_rsi >= 70 and current_rsi < 70),
            crossed_oversold=(previous_rsi <= 30 and current_rsi > 30),
            rsi_crossed_ema_up=rsi_crossed_ema_up,
            rsi_crossed_ema_down=rsi_crossed_ema_down,
            position_to_ema=position_to_ema
        )
