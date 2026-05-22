from dataclasses import dataclass


@dataclass
class DexQuote:
    price_dex_gross: float
    price_dex_net: (float | None)
    direction: bool
    fee_dex_ppm: float
    data_quote: (dict | None)
