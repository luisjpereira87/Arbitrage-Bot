import time

from bot.strategies.arbitrage_base import ArbitrageBase
from wallet_manager import WalletManager


class TriangularStrategy(ArbitrageBase):
    def __init__(self, web3_manager, config, pool_finder, wallet, capital_amount):
        super().__init__(web3_manager, config)
        self.finder = pool_finder
        self.min_profit = 0.20  # Triangular costs more gas (~$0.25)
        self.routes = self._setup_routes()
        self.wallet = wallet
        self.route_blacklist = {}
        self.capital = capital_amount

        # No final do __init__ da TriangularStrategy
        unique_pools_for_cache = set()
        for r in self.routes:
            for step in r["pool_steps"]:
                for addr in step.values():
                    unique_pools_for_cache.add(addr.lower())

        # Chama o método que criámos na ArbitrageBase
        self.build_pool_cache(list(unique_pools_for_cache))

    def _setup_routes(self):
        """
        Pre-calculates all possible triangular combinations
        including Normal and Inverse directions.
        """
        configured_routes = []
        tokens_cfg = self.config["tokens"]
        fees_cfg = self.config["fees"]

        for triangle in self.config["triangles"]:
            t1, t2, t3 = triangle
            addr1, addr2, addr3 = tokens_cfg[t1]["addr"], tokens_cfg[t2]["addr"], tokens_cfg[t3]["addr"]

            # Testamos combinações de taxas (Ex: UNI 500 -> SUSHI 3000 -> UNI 500)
            for f1 in fees_cfg:
                for f2 in fees_cfg:
                    for f3 in fees_cfg:

                        # --- 1. ROTA NORMAL (t1 -> t2 -> t3 -> t1) ---
                        # Ex: USDC -> WETH -> ARB -> USDC
                        configured_routes.append({
                            "name": f"{t1}->{t2}->{t3}",
                            "token_path": [addr1, addr2, addr3, addr1],
                            "pool_steps": [
                                self.finder.get_pools(addr1, addr2, f1),  # USDC-WETH
                                self.finder.get_pools(addr2, addr3, f2),  # WETH-ARB
                                self.finder.get_pools(addr3, addr1, f3)  # ARB-USDC
                            ]
                        })

                        # --- 2. ROTA INVERSA (t1 -> t3 -> t2 -> t1) ---
                        # Ex: USDC -> ARB -> WETH -> USDC
                        configured_routes.append({
                            "name": f"{t1}->{t3}->{t2} (INV)",
                            "token_path": [addr1, addr3, addr2, addr1],
                            "pool_steps": [
                                self.finder.get_pools(addr1, addr3),  # USDC-ARB (Era o step 3 da normal)
                                self.finder.get_pools(addr3, addr2),  # ARB-WETH  (Era o step 2 da normal)
                                self.finder.get_pools(addr2, addr1)  # WETH-USDC (Era o step 1 da normal)
                            ]
                        })

        print(f"✅ Mapeadas {len(configured_routes)} rotas (Normal e Inverso)")
        return configured_routes

    def analyze_all_triangles(self):
        for route in self.routes:
            opp = self._check_triangle_profit(route)
            if opp:
                self._execute_trade(opp)
                return True
        return False

    def _check_triangle_profit_old(self, route):
        path = route["token_path"]
        pools = route["pool_steps"]

        # Brute force through all DEX combinations for the triangle
        for dex1, p1 in pools[0].items():
            for dex2, p2 in pools[1].items():
                for dex3, p3 in pools[2].items():

                    route_id = f"{p1}-{p2}-{p3}"

                    if route_id in self.route_blacklist:
                        if time.time() < self.route_blacklist[route_id]:
                            continue  # Pula esta combinação específica
                        else:
                            del self.route_blacklist[route_id]  # Expulsa da blacklist

                    q1 = self.get_quote(p1, path[0], path[1])
                    q2 = self.get_quote(p2, path[1], path[2])
                    q3 = self.get_quote(p3, path[2], path[3])

                    if q1 and q2 and q3:
                        res1, dir1, fee1 = q1
                        res2, dir2, fee2 = q2
                        res3, dir3, fee3 = q3

                        # Step-by-step simulation
                        step1 = self.capital * res1 * ((1e6 - fee1) / 1e6) * 0.997
                        step2 = step1 * res2 * ((1e6 - fee2) / 1e6) * 0.997
                        final_amount = step2 * res3 * ((1e6 - fee3) / 1e6) * 0.997

                        gas_cost = 0.30  # Ajusta conforme vires o custo real no Arbiscan
                        net_profit = (final_amount - self.capital) - gas_cost

                        # --- FILTRO DE SANIDADE ---
                        max_profit_allowed = self.capital * 0.20  # Limite de 20%
                        if net_profit > max_profit_allowed:
                            # print(f"⚠️ Rota descartada: Lucro irreal detectado (${net_profit:.2f})")
                            continue

                        if net_profit > self.min_profit:
                            current_dex_info = [
                                {"dex": dex1, "addr": p1},
                                {"dex": dex2, "addr": p2},
                                {"dex": dex3, "addr": p3}
                            ]
                            self._display_detailed_logs(
                                route,
                                [res1, res2, res3],
                                [step1, step2, final_amount],
                                [dir1, dir2, dir3],
                                current_dex_info,  # Enviamos os dicts
                                net_profit
                            )
                            return {
                                "strategy": "TRIANGULAR",
                                "profit": net_profit,
                                "route_name": route["name"],
                                "dex_path": f"{dex1}->{dex2}->{dex3}",
                                "route_id": route_id,
                                "payload": {
                                    "amount_in": int(self.capital * 10 ** 6),
                                    "pools": [p1, p2, p3],
                                    "zero_for_one": [dir1, dir2, dir3],
                                    "tokens": path
                                }
                            }
        return None


    def _check_triangle_profit(self, route):
        path = route["token_path"]
        pools = route["pool_steps"]

        # 1. Lista TODAS as pools únicas desta rota para pedir de uma vez
        all_pools_in_route = set()
        for step in pools:
            for addr in step.values(): all_pools_in_route.add(addr.lower())

        # 2. ÚNICO pedido RPC para a rota inteira
        current_prices = self.get_quotes_batch(list(all_pools_in_route))

        # Brute force through all DEX combinations for the triangle
        for dex1, p1 in pools[0].items():
            for dex2, p2 in pools[1].items():
                for dex3, p3 in pools[2].items():

                    route_id = f"{p1}-{p2}-{p3}"

                    if route_id in self.route_blacklist:
                        if time.time() < self.route_blacklist[route_id]:
                            continue  # Pula esta combinação específica
                        else:
                            del self.route_blacklist[route_id]  # Expulsa da blacklist

                    q1 = self._calculate_quote_local(p1, path[0], path[1], current_prices.get(p1.lower()))
                    q2 = self._calculate_quote_local(p2, path[1], path[2], current_prices.get(p2.lower()))
                    q3 = self._calculate_quote_local(p3, path[2], path[3], current_prices.get(p3.lower()))

                    if q1 and q2 and q3:
                        res1, dir1, fee1 = q1
                        res2, dir2, fee2 = q2
                        res3, dir3, fee3 = q3

                        # Step-by-step simulation
                        step1 = self.capital * res1 * ((1e6 - fee1) / 1e6) * 0.997
                        step2 = step1 * res2 * ((1e6 - fee2) / 1e6) * 0.997
                        final_amount = step2 * res3 * ((1e6 - fee3) / 1e6) * 0.997

                        gas_cost = 0.30  # Ajusta conforme vires o custo real no Arbiscan
                        net_profit = (final_amount - self.capital) - gas_cost

                        # --- FILTRO DE SANIDADE ---
                        max_profit_allowed = self.capital * 0.20  # Limite de 20%
                        if net_profit > max_profit_allowed:
                            # print(f"⚠️ Rota descartada: Lucro irreal detectado (${net_profit:.2f})")
                            continue

                        if net_profit > self.min_profit:
                            current_dex_info = [
                                {"dex": dex1, "addr": p1},
                                {"dex": dex2, "addr": p2},
                                {"dex": dex3, "addr": p3}
                            ]
                            self._display_detailed_logs(
                                route,
                                [res1, res2, res3],
                                [step1, step2, final_amount],
                                [dir1, dir2, dir3],
                                current_dex_info,  # Enviamos os dicts
                                net_profit
                            )
                            return {
                                "strategy": "TRIANGULAR",
                                "profit": net_profit,
                                "route_name": route["name"],
                                "dex_path": f"{dex1}->{dex2}->{dex3}",
                                "route_id": route_id,
                                "payload": {
                                    "amount_in": int(self.capital * 10 ** 6),
                                    "pools": [p1, p2, p3],
                                    "zero_for_one": [dir1, dir2, dir3],
                                    "tokens": path
                                }
                            }
        return None

    def _display_detailed_logs(self, route, prices, steps, directions, dex_info, profit):
        path_names = [self.name_map.get(addr.lower(), addr[:6]) for addr in route["token_path"]]

        print(f"\n--- 🛰️ ROUTE DETECTED: {' -> '.join(path_names)} ---")

        # Removido o print bruto que estava a sujar o terminal

        for i in range(3):
            t_in = path_names[i]
            t_out = path_names[i + 1]
            # Agora dex_info[i]['dex'] vai funcionar porque passámos dicionários!
            print(
                f"  📍 Step {i + 1} [{dex_info[i]['dex']}]: {t_in} -> {t_out} @ {prices[i]:.8f} | Pool: {dex_info[i]['addr']}")

        print(f"💰 Investment: ${self.capital:.2f} {path_names[0]}")
        print(f"➡️ Step 1 Result: {steps[0]:.6f} {path_names[1]}")
        print(f"➡️ Step 2 Result: {steps[1]:.6f} {path_names[2]}")
        print(f"⬅️ Step 3 Result: {steps[2]:.6f} {path_names[0]} (Final)")
        print(f"⬅️ Direction: {directions}")

        status = "✅ PROFIT" if profit > 0 else "❌ LOSS"
        print(f"📊 Result: {status} of ${profit:.4f}")
        print(f"--------------------------------------------------\n")

    def _execute_trade(self, opportunity):
        """
        Receives the standardized payload and sends it to the Blockchain
        """

        # 1. Check for real profit threshold again before sending
        if opportunity["profit"] > -1:  # Only execute if profit > $0.50
            print(f"💰 [EXECUTION] Sending {opportunity['strategy']} trade to Contract!")

            # Here you call your WalletManager

            tx_hash = self.wallet.executar_arbitragem(opportunity['payload']["pools"], opportunity['payload']["zero_for_one"], opportunity['payload']["tokens"], opportunity['payload']["amount_in"])

            if tx_hash is None:
                self.route_blacklist[opportunity["route_id"]] = time.time() + 300
                print(f"🚫 Rota {opportunity['route_name']} em blacklist devido a falha.")


            # print(f"✅ Tx Sent: {tx_hash}")

