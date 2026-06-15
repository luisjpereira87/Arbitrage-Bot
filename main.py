# This is a sample Python script.
import asyncio
import logging

from dotenv import load_dotenv

from core.bots.cex_bot import CexBot
from core.config.properties_dex import PropertiesDex
from core.config.properties_multi import PropertiesMulti

"""
def load_abi():
    with open("abi.json", "r") as f:
        return json.load(f)
"""

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("solana").setLevel(logging.WARNING)

if __name__ == '__main__':
    properties_multi = PropertiesMulti()
    properties_dex = PropertiesDex()
    # abi = load_abi()

    # --- EXECUÇÃO -
    # solana_bot = SolanaBot()
    # asyncio.run(solana_bot.scan_jupiter_triangles())
    cex_bot = CexBot()
    asyncio.run(cex_bot.test_spread_loop())
    # TradePosition.empty_position()
    # multi_chain_bot = MultiChainBot(properties_multi)
    # asyncio.run(multi_chain_bot.run())

    # test = TestSolana()
    # asyncio.run(test.test_execute())

    # multi_chain_bot.test_manual_quote()

    # t = WalletManager( Web3Manager())
    # t.check_and_approve_executor(100.0)

    # t.forcar_execucao_teste()
