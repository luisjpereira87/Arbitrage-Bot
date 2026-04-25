import os
import time
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
from web3 import Web3

from bot.dclass.config_json import Config
from bot.strategies.simple_strategy import SimpleStrategy
from bot.strategies.triangular_strategy import TriangularStrategy
from pool_finder import PoolFinder
from wallet_manager import WalletManager
from web3_manager import Web3Manager


class ArbitrageScanner:
    def __init__(self, config_file: Config, capital_amount: float = 100.0):

        self.web3_manager = Web3Manager()
        #self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.finder = PoolFinder(self.web3_manager)  # A classe que criámos
        self.config_file = config_file
        self.decimal_map = {info["addr"].lower(): info["dec"] for info in self.config_file["tokens"].values()}

        self.wallet = WalletManager(self.web3_manager)
        self.capital_amount  = capital_amount

        self.simple_engine = SimpleStrategy(self.web3_manager, self.config_file, self.finder,  self.wallet, self.capital_amount)
        self.triangular_engine = TriangularStrategy(self.web3_manager, self.config_file, self.finder,  self.wallet, self.capital_amount)

        self.active_simple_pairs = [
            ["USDC", "WETH"],
            ["WETH", "WBTC"],
            ["USDC", "ARB"]
        ]



    @property
    def w3(self):
        """Retorna o Web3 atualizado do manager sempre que o bot precisar dele"""
        return self.web3_manager.w3

    def run_sync(self):
        """
        Runs the scan in a single loop (good for debugging)
        """
        print("🔍 Starting Synchronized Arbitrage Scan...")
        last_heartbeat = 0
        heartbeat_interval = 60
        while True:
            try:
                # 1. Check Simple Arbitrage
                self.simple_engine.analyze_all_pairs()

                # 2. Check Triangular Arbitrage
                self.triangular_engine.analyze_all_triangles()

                last_heartbeat = self._check_alive(last_heartbeat, heartbeat_interval)

                time.sleep(0.5)
            except Exception as e:
                print(f"⚠️ Scanner Error: {e}")
                time.sleep(2)

    def run_parallel(self):
        print("🚀 Bot em execução: Motores Simples e Triangular ativos...")

        with ThreadPoolExecutor(max_workers=2) as executor:
            # Lançamos as duas tarefas e guardamos os futures
            fut_simple = executor.submit(self._loop_simple)
            fut_triang = executor.submit(self._loop_triangular)

            # Esperamos por qualquer erro que possa ocorrer
            try:
                fut_simple.result()
                fut_triang.result()
            except Exception as e:
                print(f"🚨 Erro crítico no motor: {e}")

    def _loop_simple(self):
        last_heartbeat = 0
        heartbeat_interval = 60
        while True:  # O loop interno garante que a thread nunca morre
            try:
                self.simple_engine.analyze_all_pairs()

                last_heartbeat = self._check_alive(last_heartbeat, heartbeat_interval)
            except Exception as e:
                print(f"⚠️ Erro no Loop Simple: {e}")
            time.sleep(0.01)  # Delay mínimo para não fritar o CPU

    def _loop_triangular(self):
        last_heartbeat = 0
        heartbeat_interval = 60
        while True:  # O loop interno garante que a thread nunca morre
            try:
                self.triangular_engine.analyze_all_triangles()
                last_heartbeat = self._check_alive(last_heartbeat, heartbeat_interval)
            except Exception as e:
                print(f"⚠️ Erro no Loop Triangular: {e}")
            time.sleep(0.01)

    def _check_alive(self, last_heartbeat, heartbeat_interval):
        # 2. PRINT DE HEARTBEAT (Onde deves adicionar)
        current_time = time.time()
        if current_time - last_heartbeat > heartbeat_interval:
            # Contamos quantas pools estão ativas em todas as estratégias
            # apenas para monitorização

            static_count = len(self.simple_engine.pool_static_cache) if self.simple_engine.pool_static_cache else 0
            low_liq_count = len(self.simple_engine.low_liquidity_cache) if self.simple_engine.low_liquidity_cache else 0

            alive_pools = static_count - low_liq_count

            print(f"💓 [HEARTBEAT] {time.strftime('%H:%M:%S')} | "
                  f"Pools Ativas: {alive_pools} | "
                  f"Status: À procura de oportunidades...")

            return current_time
        return last_heartbeat
