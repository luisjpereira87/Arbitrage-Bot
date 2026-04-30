from enum import Enum


class Signal(Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    SHORT = "short"
    LONG = "long"
    CLOSE = "close"

    @staticmethod
    def from_str(value: (str|None)):
        if not value:
            raise ValueError("⚠️ Valor nulo ou vazio recebido em Signal.from_str")

        value = value.lower()
        mapping = {
            "buy": Signal.BUY,
            "sell": Signal.SELL,
            "hold": Signal.HOLD,
            "short": Signal.SHORT,
            "long": Signal.LONG,
            "close": Signal.CLOSE
        }

        if value in mapping:
            return mapping[value]

        raise NotImplementedError(f"⚠️ Valor desconhecido em Signal.from_str: {value}")