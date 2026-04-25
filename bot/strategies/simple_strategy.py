from bot.strategies.arbitrage_base import ArbitrageBase
from wallet_manager import WalletManager


class SimpleStrategy(ArbitrageBase):
    def __init__(self, web3_manager, config, pool_finder, wallet, capital_amount):
        super().__init__(web3_manager, config)
        self.finder = pool_finder
        self.min_profit = 0.20  # Minimum $ profit to trigger
        self.wallet = wallet
        self.capital = capital_amount

        self.pairs_to_watch = [
            ["USDC", "WETH"],
            ["WETH", "WBTC"],
            ["USDC", "ARB"]
        ]

        # --- NOVO: CACHE INICIAL ---
        # Mapeamos logo todas as pools possíveis para os pares que queres vigiar
        unique_pools = set()
        self.watched_pairs = []

        for symbol_a, symbol_b in self.pairs_to_watch:
            addr_a = self.config["tokens"][symbol_a]["addr"]
            addr_b = self.config["tokens"][symbol_b]["addr"]

            pools_map = self.finder.get_pools(addr_a, addr_b)
            for addr in pools_map.values():
                unique_pools.add(addr.lower())

            # Guardamos os endereços para evitar lookups repetidos no config
            self.watched_pairs.append({
                "t_in": addr_a,
                "t_out": addr_b,
                "pools": pools_map
            })

        self.build_pool_cache(list(unique_pools))

    def analyze_pairs(self, pair_list):
        """
        pair_list example: [['USDC', 'WETH'], ['WETH', 'WBTC']]
        """
        for symbol_a, symbol_b in pair_list:
            addr_a = self.config["tokens"][symbol_a]["addr"]
            addr_b = self.config["tokens"][symbol_b]["addr"]

            # Get all pools for this specific pair
            pools_map = self.finder.get_pools(addr_a, addr_b)

            opportunity = self.find_cross_dex_spread(addr_a, addr_b, pools_map)
            if opportunity:
                self._execute_trade(opportunity)
                return True
        return False

    def analyze_all_pairs(self):
        """
        Analisa todos os pares configurados num único ciclo de alta velocidade.
        """
        # 1. Recolhe todas as pools ativas para o Batch
        all_pool_addrs = []
        for pair in self.watched_pairs:
            all_pool_addrs.extend(list(pair["pools"].values()))

        # 2. ÚNICO Pedido RPC para todos os preços (com filtro de liquidez e cache interno)
        current_prices = self.get_quotes_batch(all_pool_addrs)

        # 3. Processamento Local (Ultra Rápido)
        for pair in self.watched_pairs:
            opportunity = self.find_cross_dex_spread(
                pair["t_in"],
                pair["t_out"],
                pair["pools"],
                current_prices
            )
            if opportunity:
                self._execute_trade(opportunity)
                return True
        return False

    def find_cross_dex_spread(self, token_in, token_out, pools_map, current_prices):
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

                    gas_cost = 0.25
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

                        return {
                            "strategy": "SIMPLE",
                            "profit": net_profit,
                            "payload": {
                                "amount_in": int(self.capital * 10 ** 6),
                                "pools": [pool_buy, pool_sell],
                                "zero_for_one": [dir1, dir2],
                                "tokens": [token_in, token_out, token_in]
                            }
                        }
        return None

    def find_cross_dex_spread_old(self, token_in, token_out, pools_map):
        for dex_buy, pool_buy in pools_map.items():
            for dex_sell, pool_sell in pools_map.items():
                if pool_buy == pool_sell: continue

                q1 = self.get_quote(pool_buy, token_in, token_out)
                q2 = self.get_quote(pool_sell, token_out, token_in)

                if q1 and q2:
                    price1, dir1, fee1 = q1
                    price2, dir2, fee2 = q2

                    # --- Calculation Steps ---
                    # Step 1: Buying token_out
                    step1_res = self.capital * price1 * ((1e6 - fee1) / 1e6) * 0.997
                    # Step 2: Selling token_out back to token_in
                    final_amount = step1_res * price2 * ((1e6 - fee2) / 1e6) * 0.997

                    gas_cost = 0.30  # Ajusta conforme vires o custo real no Arbiscan
                    net_profit = (final_amount - self.capital) - gas_cost

                    # Info for logs
                    dex_info = [
                        {"dex": dex_buy, "addr": pool_buy},
                        {"dex": dex_sell, "addr": pool_sell}
                    ]

                    max_profit_allowed = self.capital * 0.20  # Limite de 20%
                    if net_profit > max_profit_allowed:
                        # print(f"⚠️ Rota descartada: Lucro irreal detectado (${net_profit:.2f})")
                        continue

                    # Show logs if profit is interesting (even if slightly negative)
                    if net_profit > self.min_profit:
                        # We pass [step1_res, final_amount] as our steps
                        self._display_simple_logs(
                            [token_in, token_out],
                            [price1, price2],
                            [step1_res, final_amount],
                            [dir1, dir2],
                            dex_info,
                            net_profit
                        )

                        return {
                            "strategy": "SIMPLE",
                            "profit": net_profit,
                            "route": f"{dex_buy} -> {dex_sell}",
                            "payload": {
                                "amount_in": int(self.capital * 10 ** 6),
                                "pools": [pool_buy, pool_sell],
                                "zero_for_one": [dir1, dir2],
                                "tokens": [token_in, token_out, token_in]
                            }
                        }
        return None

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


    def _execute_trade(self, opportunity):
        """
        Receives the standardized payload and sends it to the Blockchain
        """

        # 1. Check for real profit threshold again before sending
        if opportunity["profit"] > 0:  # Only execute if profit > $0.50
            print(f"💰 [EXECUTION] Sending {opportunity['strategy']} trade to Contract!")

            # Here you call your WalletManager
            tx_hash = self.wallet.executar_arbitragem(opportunity['payload']["pools"], opportunity['payload']["zero_for_one"], opportunity['payload']["tokens"], opportunity['payload']["amount_in"])
            # print(f"✅ Tx Sent: {tx_hash}")