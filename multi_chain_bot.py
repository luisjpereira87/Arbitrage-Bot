import time

from ccxt import hyperliquid
from eth_abi import decode
from lazy_object_proxy.utils import await_
from web3 import Web3

from pool_finder import PoolFinder
from web3_manager import Web3Manager

# Endereços das Pools ETH/USDC (0.05% ou similar)
POOLS = {
    'Uniswap_V3': '0xC6962004f452bE9203591991D15f6b388e09E8D0',
    'Pancakeswap_V3': '0xd9e2a1a61B6E61b275cEc326465d417e52C1b95c', # Exemplo
    'Sushi_V3':   '0xf3Eb87C1F6020982173C908E7eB31aA66c1f0296'  # Exemplo
}

# Endereço do Contrato Multicall3
MULTICALL_ADDRESS = '0xcA11bde05977b3631167028862bE2a173976CA11'

# ABI simplificada para o Multicall3 (Função 'aggregate')
MULTICALL_ABI = '[{"inputs":[{"internalType":"bool","name":"requireSuccess","type":"bool"},{"components":[{"internalType":"address","name":"target","type":"address"},{"internalType":"bytes","name":"callData","type":"bytes"}],"internalType":"struct Multicall3.Call[]","name":"calls","type":"tuple[]"}],"name":"tryAggregate","outputs":[{"components":[{"internalType":"bool","name":"success","type":"bool"},{"internalType":"bytes","name":"returnData","type":"bytes"}],"internalType":"struct Multicall3.Result[]","name":"returnData","type":"tuple[]"}],"stateMutability":"view","type":"function"}]'

# Selector da função slot0() -> keccak256("slot0()")[:10]
SLOT0_SELECTOR = "0x3850c988"


class MultiChainBot():
    def __init__(self):
        self.web3_manager = Web3Manager()

        self.hl = hyperliquid({
            "enableRateLimit": True,
            "timeout": 10000,
            "testnet": False
        })
        self.pool_abi = [
            {
                "inputs": [],
                "name": "slot0",
                "outputs": [
                    {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"}
                ],
                "stateMutability": "view",
                "type": "function"
            },
            {
                "inputs": [],
                "name": "token0",
                "outputs": [{"internalType": "address", "name": "", "type": "address"}],
                "stateMutability": "view",
                "type": "function"
            },
            {
                "inputs": [],
                "name": "token1",
                "outputs": [{"internalType": "address", "name": "", "type": "address"}],
                "stateMutability": "view",
                "type": "function"
            },
            {
                "inputs": [],
                "name": "fee",
                "outputs": [{"internalType": "uint24", "name": "", "type": "uint24"}],
                "stateMutability": "view",
                "type": "function"
            },
            {
                "inputs": [],
                "name": "liquidity",
                "outputs": [{"internalType": "uint128", "name": "", "type": "uint128"}],
                "stateMutability": "view",
                "type": "function"
            }
        ]

        self.finder = PoolFinder(self.web3_manager)

        print("🔍 Localizando pools com liquidez...")
        # Procuramos pools de 0.05% (500)
        self.active_pools = self.finder.get_pools('0xaf88d065e77c8cC2239327C5EDb3A432268e5831', '0x82aF49447D8a07e3bd95BD0d56f35241523fBab1', 500)
        print(f"🚀 Monitorando {len(self.active_pools)} pools encontradas.")

    @property
    def w3(self):
        """Retorna o Web3 atualizado do manager sempre que o bot precisar dele"""
        return self.web3_manager.w3

    def get_multiple_prices(self):
        prices = {}
        for name, addr in self.active_pools.items():
            try:

                pool_contract = self.w3.eth.contract(
                    address=self.w3.to_checksum_address(addr),
                    abi=self.pool_abi
                )

                # Multi-call or batching would be better, but keeping it simple for now
                slot0 = pool_contract.functions.slot0().call()
                sqrt_price_x96 = slot0[0] if isinstance(slot0, (list, tuple)) else slot0

                prices[name] = self.decode_v3_price_from_uint(sqrt_price_x96)
            except:
                prices[name] = None
        return prices

    def decode_v3_price_from_uint(self, sqrtPriceX96):
        """Nova função de decode que recebe diretamente o número do Slot0"""
        price = (sqrtPriceX96 / (2 ** 96)) ** 2
        return price * 10 ** 12  # Ajuste ETH/USDC

    def get_hl_prices(self):
        try:
            # fetch_ticker dá o preço ATUAL, não o da vela de 1min
            ticker = self.hl.fetch_ticker("ETH/USDC:USDC")
            return ticker['last']  # O último preço negociado
        except Exception as e:
            print(f"⚠️ Erro HL: {e}")
            return None


    def run(self):
        print(f"Conectado ao RPC: {self.w3.is_connected()}")
        print(f"Monitor Ativo. Bloco: {self.w3.eth.block_number}")
        while True:
            try:
                data = self.get_multiple_prices()
                data1 = self.get_hl_prices()
                print(data1)
                for dex, price in data.items():
                    if price is not None:
                        print(f"{dex:<15} | ${price:,.2f}")
                    else:
                        print(f"{dex:<15} | N/A (Erro na Pool)")

                print("-" * 35)
                time.sleep(1)
            except Exception as e:
                print(f"Erro no loop: {e}")
                time.sleep(2)

