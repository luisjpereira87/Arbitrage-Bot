# This is a sample Python script.
import asyncio
import logging

from core.bots.multi_chain_bot import MultiChainBot
from core.config.properties_multi import PropertiesMulti

"""
def load_abi():
    with open("abi.json", "r") as f:
        return json.load(f)
"""

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)

if __name__ == '__main__':
    properties_multi = PropertiesMulti()
    # abi = load_abi()

    # --- EXECUÇÃO ---
    # arbitrum_bot = ArbitrumBot(config)
    # scanner.run_triangular()

    # arbitrum_bot.run_parallel()

    multi_chain_bot = MultiChainBot(properties_multi)
    asyncio.run(multi_chain_bot.run())

    # t = WalletManager( Web3Manager())
    # t.check_and_approve_executor(100.0)

    # t.forcar_execucao_teste()
