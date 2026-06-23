import pandas as pd
from ta.volatility import AverageTrueRange


class IndicatorsUtils():
    def __init__(self):
        pass

    @staticmethod
    def atr(ohlcv: pd.DataFrame, length=14):
        return AverageTrueRange(ohlcv["high"], ohlcv["low"], ohlcv["close"], window=length).average_true_range()

        # ohlcv.ta.atr(length=length, append=True)
        # col_name = f"ATRr_{length}"
        # print("aquiii", ohlcv)
        # return ohlcv[col_name]

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
        print("AQUIII", range_width_percent, atr_series)
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
        print("AQUII", range_percent, channel_high, channel_low)
        return range_percent
