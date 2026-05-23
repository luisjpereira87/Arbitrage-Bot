from typing import Any

from core.dclass.chains_enum import Chains
from core.dclass.config_json import Config
from core.dclass.watched_pair_dclass import WatchedPair
from core.pools.pool_finder import PoolFinder
from core.web3.rpcs.web3_manager import Web3Manager


class WatchedPairBuilder:
    def __init__(self, web3_manager: Web3Manager, config: Config):
        self.web3_manager = web3_manager
        self.finder = PoolFinder(self.web3_manager)
        self.config = config

    def build(self, stop_chain: Chains | None) -> tuple[list[WatchedPair], list[Any], list[str]] | tuple[
        list[Any], None, list[str]]:
        all_pools_for_cache = set()
        watched_pairs = []
        fee_tiers = self.config.fees

        for symbol_a, symbol_b, hl_pair, chain in self.config.multi_chain:

            if stop_chain is not None and chain == stop_chain.value:
                continue

            # 1. Obter dados do token (mantendo case-sensitive para Solana)
            token_a_data = self.config.tokens.get(symbol_a)
            token_b_data = self.config.tokens.get(symbol_b)

            if token_a_data is None or token_b_data is None:
                continue

            addr_a = token_a_data.address
            addr_b = token_b_data.address
            dec_a = token_a_data.decimals
            dec_b = token_b_data.decimals

            pair_pools = {}
            z4o = True  # Default

            # --- LÓGICA POR REDE ---
            if chain == 'solana':
                # Na Solana/Jupiter, a "pool" é a própria API.
                # Podemos colocar um placeholder ou o Mint do token para manter a estrutura.
                pair_pools["JUPITER"] = addr_b
                # z4o não é usado na Solana, mas preenchemos para não quebrar a DClass
                z4o = True

            else:
                # LÓGICA ARBITRUM (EVM)
                addr_a_l = addr_a.lower()
                addr_b_l = addr_b.lower()

                # Ordenar para a Uniswap (t0 é o menor hexadecimal)
                t0, t1 = sorted([addr_a_l, addr_b_l])

                for fee in fee_tiers:
                    pool_found = self.finder.get_pools(t0, t1, fee)
                    if pool_found:
                        for dex_name, addr in pool_found.items():
                            unique_key = f"{dex_name}_{fee}"
                            pair_pools[unique_key] = addr.lower()
                            all_pools_for_cache.add(addr.lower())

                # Cálculo real do zeroForOne para EVM
                z4o = int(addr_a, 16) < int(addr_b, 16)

            if not pair_pools and chain != 'solana':
                print(f"⚠️ Nenhuma pool encontrada para {symbol_a}/{symbol_b}")

            # 4. Adicionar à lista global de pares vigiados
            watched_pairs.append(
                WatchedPair(
                    addr_a=addr_a,
                    addr_b=addr_b,
                    symbol_a=symbol_a,
                    symbol_b=symbol_b,
                    decimal_a=dec_a,
                    decimal_b=dec_b,
                    hl_pair=hl_pair,
                    pools_map=pair_pools,
                    z4o=z4o,
                    chain=Chains.from_str(chain),
                ))

        all_pool_addrs = [
            addr for p in watched_pairs
            if p.chain == Chains.ARBITRUM and getattr(p, 'pools_map', None)
            for addr in p.pools_map.values()
        ]

        # O build_pool_cache só precisa de rodar para as pools EVM (Arbitrum)
        if all_pools_for_cache:
            return watched_pairs, list(all_pools_for_cache), all_pool_addrs

        return watched_pairs, None, all_pool_addrs
