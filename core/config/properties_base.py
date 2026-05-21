import os
from abc import ABC

from dotenv import load_dotenv

from core.dclass.config_json import Config

load_dotenv()


class PropertiesBase(ABC):
    def __init__(self):
        pass

    base_path = os.path.dirname(os.path.abspath(__file__))

    # 2. Se o config.json está NA MESMA PASTA que este ficheiro:
    config_path = os.path.join(base_path, 'config.json')

    CONFIG = Config(config_path)

    USDC_ADDRESS_ARBITRUM = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"

    ERC20_ABI = [
        {"constant": False,
         "inputs": [{"name": "_spender", "type": "address"}, {"name": "_value", "type": "uint256"}],
         "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
        {"constant": True,
         "inputs": [{"name": "_owner", "type": "address"}, {"name": "_spender", "type": "address"}],
         "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"}
    ]

    PRIVATE_KEY: str | None
    WALLET_ADDRESS: str | None
    CONTRACT_ADDRESS: str | None
    CONTRACT_ABI: list | None
    PRIVATE_KEY_WALLET_HL: str | None
    WALLET_ADDRESS_HL: str | None

    PRIVATE_KEY_WALLET_SOLANA: str | None
