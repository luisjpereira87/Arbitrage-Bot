from core.dclass.config_json import Config
from core.dclass.dex_opportunity_simple_dclass import DexOpportunitySimple
from core.dclass.watched_pair_simple_dclass import WatchedPairSimple
from core.pools.pool_finder import PoolFinder
from core.strategies.arbitrage_base import ArbitrageBase
from core.web3.wallet_base import WalletBase
from core.web3.web3_manager import Web3Manager


class SimpleStrategy(ArbitrageBase):
    def __init__(self, web3_manager: Web3Manager, config: Config, pool_finder: PoolFinder, wallet: WalletBase,
                 capital_amount: int):
        super().__init__(web3_manager, config)
        self.watched_pairs: list[WatchedPairSimple] = []
        self.finder = pool_finder
        self.min_profit = 0.20  # Minimum $ profit to trigger
        self.wallet = wallet
        self.capital = capital_amount
        self.config = config

        self.watched_pairs_weth = None

        self.init_cache()

    def init_cache(self):
        # --- NOVO: CACHE INICIAL ---
        # Mapeamos logo todas as pools possíveis para os pares que queres vigiar
        unique_pools = set()
        self.watched_pairs = []

        for symbol_a, symbol_b in self.get_dynamic_routes(is_triangular=False):
            for fee in self.config.fees:

                addr_a = self.config.tokens.get(symbol_a).address
                addr_b = self.config.tokens.get(symbol_b).address

                pools_map = self.finder.get_pools(addr_a, addr_b, fee)

                for addr in pools_map.values():
                    unique_pools.add(addr.lower())

                # Guardamos os endereços para evitar lookups repetidos no config

                self.watched_pairs.append(WatchedPairSimple(addr_a, addr_b, pools_map))

        self.build_pool_cache(list(unique_pools))

    def analyze_all_pairs(self):
        """
        Analisa todos os pares configurados num único ciclo de alta velocidade.
        """
        # 1. Recolhe todas as pools ativas para o Batch
        all_pool_addrs = []

        for pair in self.watched_pairs:
            all_pool_addrs.extend(list(pair.pools_map.values()))

        # 2. ÚNICO Pedido RPC para todos os preços (com filtro de liquidez e cache interno)
        current_prices = self.get_quotes_batch(all_pool_addrs)

        # 3. Processamento Local (Ultra Rápido)
        for pair in self.watched_pairs:
            opportunity = self.find_cross_dex_spread(
                pair.addr_a,
                pair.addr_b,
                pair.pools_map,
                current_prices
            )
            if opportunity:
                self._execute_trade(opportunity)
                return True
        return False

    def find_cross_dex_spread(self, token_in, token_out, pools_map, current_prices) -> (DexOpportunitySimple | None):
        best_opportunity = None
        for dex_buy, pool_buy in pools_map.items():
            p_buy_l = pool_buy.lower()

            for dex_sell, pool_sell in pools_map.items():
                p_sell_l = pool_sell.lower()
                if p_buy_l == p_sell_l: continue

                # BUSCA NO BATCH (Zero RPC)
                q1 = self._calculate_quote_local(p_buy_l, token_in, token_out, current_prices.get(p_buy_l))
                q2 = self._calculate_quote_local(p_sell_l, token_out, token_in, current_prices.get(p_sell_l))

                if q1 and q2:
                    price1, dir1, fee1 = q1
                    price2, dir2, fee2 = q2

                    # Simulação de swap
                    step1_res = self.capital * price1 * ((1e6 - fee1) / 1e6) * 0.997
                    final_amount = step1_res * price2 * ((1e6 - fee2) / 1e6) * 0.997

                    gas_cost = self.wallet.get_gas_cost_usd(None)
                    net_profit = (final_amount - self.capital) - gas_cost

                    if net_profit > (self.capital * 0.20): continue  # Filtro de sanidade

                    if net_profit > self.min_profit:
                        self._display_simple_logs(
                            [token_in, token_out],
                            [price1, price2],
                            [step1_res, final_amount],
                            [dir1, dir2],
                            [{"dex": dex_buy, "addr": pool_buy}, {"dex": dex_sell, "addr": pool_sell}],
                            net_profit
                        )

                        current_opp = DexOpportunitySimple("SIMPLE", net_profit, int(self.capital * 10 ** 6),
                                                           [pool_buy, pool_sell], [dir1, dir2],
                                                           [token_in, token_out, token_in])

                        # Se for a primeira ou se for melhor que a anterior, guarda
                        if best_opportunity is None or current_opp.profit > best_opportunity.profit:
                            best_opportunity = current_opp

        return best_opportunity

    def _display_simple_logs(self, tokens, prices, steps, directions, dex_info, profit):
        names = [self.name_map.get(addr.lower(), addr[:6]) for addr in tokens]

        print(f"\n--- ⚡ SIMPLE ARBITRAGE: {names[0]} -> {names[1]} -> {names[0]} ---")

        print(
            f"  📍 Step 1 [{dex_info[0]['dex']}]: {names[0]} -> {names[1]} @ {prices[0]:.8f} | Pool: {dex_info[0]['addr']}")
        print(
            f"  📍 Step 2 [{dex_info[1]['dex']}]: {names[1]} -> {names[0]} @ {prices[1]:.8f} | Pool: {dex_info[1]['addr']}")

        print(f"💰 Investment: ${self.capital:.2f} {names[0]}")
        print(f"➡️ Step 1 Result: {steps[0]:.6f} {names[1]}")
        print(f"⬅️ Step 2 Result: {steps[1]:.6f} {names[0]} (Final)")
        print(f"⬅️ Direction: {directions}")

        status = "✅ PROFIT" if profit > 0 else "❌ LOSS"
        print(f"📊 Result: {status} of ${profit:.4f}")
        print(f"--------------------------------------------------\n")

    def _execute_trade(self, opportunity: DexOpportunitySimple):
        """
        Receives the standardized payload and sends it to the Blockchain
        """

        # 1. Check for real profit threshold again before sending
        if opportunity.profit > 0:  # Only execute if profit > $0.50
            print(f"💰 [EXECUTION] Sending {opportunity.strategy} trade to Contract!")

            # Here you call your WalletManager
            tx_hash = self.wallet.send_transaction(opportunity.pools,
                                                   opportunity.zero_for_one,
                                                   opportunity.tokens,
                                                   opportunity.amount_in)
            # print(f"✅ Tx Sent: {tx_hash}")
