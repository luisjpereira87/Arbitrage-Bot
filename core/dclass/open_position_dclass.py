from dataclasses import dataclass
from typing import Optional, Union


@dataclass
class OpenPosition:
    side: Optional[Union[str, None]]
    size: float
    entry_price: float
    id: str
    notional: float
    sl: (float | None)
    tp: (float | None)
    unrealizedPnl: (float | None)
    funding_rate: (float | None)
