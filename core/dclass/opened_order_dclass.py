from dataclasses import dataclass
from typing import Optional, Union


@dataclass
class OpenedOrder:
    id: str
    clientOrderId: Optional[Union[str, None]]
    timestamp: Optional[Union[int, None]]
    datetime: Optional[Union[str, None]]
    symbol: str
    type: Optional[Union[str, None]]
    side: str
    price: float
    amount: Optional[Union[float, None]]
    reduceOnly: bool
    orderType: Optional[Union[str, None]]