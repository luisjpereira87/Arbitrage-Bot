import enum
from dataclasses import dataclass

import numpy as np
import pandas as pd
from numpy import ndarray
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.trend import MACD, ADXIndicator, PSARIndicator
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

    @staticmethod
    def ema_list(data, period) -> np.ndarray:
        """
        Calcula a Média Móvel Exponencial (EMA) sobre qualquer array de dados.
        Fórmula: EMA = (Preço - EMA_anterior) * Multiplicador + EMA_anterior
        """
        data = np.array(data)
        n = len(data)
        ema = np.full(n, np.nan)  # Começamos tudo com nan em vez de 0

        # Encontrar o primeiro índice que não é nan e não é zero
        start_idx = 0
        for i in range(n):
            if not np.isnan(data[i]) and data[i] != 0:
                start_idx = i
                break

        if start_idx >= n:
            return ema

        alpha = 2 / (period + 1)

        # O primeiro valor válido da EMA é o primeiro valor real do dado
        ema[start_idx] = data[start_idx]

        # Cálculo iterativo a partir do ponto de dados real
        for i in range(start_idx + 1, n):
            # Se o dado atual for nan, mantemos o anterior
            if np.isnan(data[i]):
                ema[i] = ema[i - 1]
            else:
                ema[i] = (data[i] - ema[i - 1]) * alpha + ema[i - 1]

        return ema

    @staticmethod
    def calculate_super_score(ohlcv: pd.DataFrame, smooth_period=5) -> tuple[
        ndarray, ndarray]:
        # --- 1. RSI ---
        rsi14 = RSIIndicator(close=ohlcv["close"], window=14).rsi()
        rsi8 = RSIIndicator(close=ohlcv["close"], window=8).rsi()

        # --- 2. MACD ---
        # O MACD da biblioteca 'ta' devolve a linha MACD, a linha de sinal e o histograma separadamente
        macd_indicator = MACD(close=ohlcv["close"], window_slow=26, window_fast=12, window_sign=9)
        macd = macd_indicator.macd()
        macd_signal = macd_indicator.macd_signal()
        macd_hist = macd_indicator.macd_diff()  # Equivalente ao histograma

        # --- 3. STOCHASTIC ---
        stoch = StochasticOscillator(high=ohlcv['high'], low=ohlcv['low'], close=ohlcv["close"], window=14,
                                     smooth_window=3)
        stoch_k = stoch.stoch()
        stoch_d = stoch.stoch_signal()

        # --- 4. ADX ---
        adx_indicator = ADXIndicator(high=ohlcv['high'], low=ohlcv['low'], close=ohlcv["close"], window=14)
        adx = adx_indicator.adx()

        # --- 5. PARABOLIC SAR ---
        # O PSAR na biblioteca 'ta' tem parâmetros padrão de passo 0.02 e máximo 0.2
        psar_indicator = PSARIndicator(high=ohlcv['high'], low=ohlcv['low'], close=ohlcv["close"], step=0.02,
                                       max_step=0.2)
        psar = psar_indicator.psar()  # Atenção: na biblioteca 'ta', o método chama-se .psar() ou .psar_down()/.psar_up() dependendo da versão, mas o .psar() geral dá a série completa.

        n = len(ohlcv["close"])
        final_scores = np.zeros(n)

        # Definimos os pesos máximos para podermos normalizar depois
        # Peso total = 15+15 (RSIs) + 20+20 (MACD) + 30 (Stoch) = 100
        for i in range(1, n):
            raw_score = 0

            # --- RSI CONFLUENCE (30 pts) ---
            if rsi14[i] > 50:
                raw_score += 10
            elif rsi14[i] < 50:
                raw_score -= 10

            if rsi8[i] > 50:
                raw_score += 10
            elif rsi8[i] < 50:
                raw_score -= 10

            # --- MACD CONFLUENCE (40 pts) ---
            if macd[i] > macd_signal[i]:
                raw_score += 15
            elif macd[i] < macd_signal[i]:
                raw_score -= 15

            if macd_hist[i] > 0:
                raw_score += 15
            elif macd_hist[i] < 0:
                raw_score -= 15

            # --- STOCHASTIC (30 pts) ---
            if stoch_k[i] > stoch_d[i]:
                raw_score += 25
            elif stoch_k[i] < stoch_d[i]:
                raw_score -= 25

            # --- NOVO: PARABOLIC SAR (Peso: 25 pts) ---
            # Se o PSAR está ABAIXO do preço (Tendência de Alta)
            if psar[i] < ohlcv["close"].iloc[i]:
                raw_score += 25
            # Se o PSAR está ACIMA do preço (Tendência de Baixa)
            else:
                raw_score -= 25

            # --- FILTRO DE ADX (A "SAÚDE" DO SINAL) ---
            # Se o ADX for baixo (<20), o sinal é fraco por falta de tendência.
            # Reduzimos o score em 50% para evitar entradas em "choppy market"
            if adx[i] < 20:
                raw_score *= 0.8

            # Como o nosso raw_score máximo possível é 100 (15+15+20+20+30),
            # ele já está na escala de -100 a 100.
            final_scores[i] = raw_score

        smooth_scores = IndicatorsUtils.ema_list(final_scores, smooth_period)

        return final_scores, smooth_scores
