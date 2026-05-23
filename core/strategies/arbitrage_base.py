import logging
import time

from core.dclass.chains_enum import Chains
from core.dclass.config_json import Config
from core.dclass.dex_opportunity_dclass import DexOpportunity
from core.dclass.dex_quote_dclass import DexQuote
from core.dclass.watched_pair_dclass import WatchedPair
from core.strategies.watched_pair_builder import WatchedPairBuilder
from core.web3.jupiter_client import JupiterClient
from core.web3.uniswap_client import UniswapClient


class ArbitrageBase:
    def __init__(self, web3_manager, config: Config):
        self.web3_manager = web3_manager
        self.config = config

        self.jupiter_client = JupiterClient()

        # self.pool_static_cache = {}

        self.tokens = self.config.tokens

        self.session = None
        self.pool_blacklist: dict = {}
        # self.last_batch_results = {}

        self.watched_pairs: list[WatchedPair] = []

        self.watched_pairs, all_pools_for_cache, all_pool_addrs = WatchedPairBuilder(web3_manager, self.config).build(
            Chains.ARBITRUM)

        self.uniswap_client = UniswapClient(self.web3_manager, self.config, all_pools_for_cache, all_pool_addrs)

        self.name_map = {
            info.address.lower(): name
            for name, info in self.config.tokens.items()
        }

    def get_pool_static_cache(self):
        return self.uniswap_client.pool_static_cache

    def get_low_liquidity_cache(self):
        return self.uniswap_client.low_liquidity_cache

    # --- 1. MÉTODOS DE CÁLCULO CENTRALIZADOS ---

    def calculate_net_metrics(self, price_hl, price_dex_gross, price_dex_net, amount_usdc, fee_ppm, gas_usdc):
        """
        A 'Fórmula Única' para ROI e Spread.
        fee_ppm: fee da DEX em partes por milhão (ex: 3000 para 0.3%)
        """
        fee_dex_percent = fee_ppm / 1_000_000

        # 1. Quantos tokens compro na DEX com o capital disponível
        # tokens_bought = (amount_usdc * (1 - fee_dex_percent)) / price_dex_gross

        # 1. Quantos tokens compro na DEX com o capital disponível?
        if price_dex_net is not None:
            # CASO SOLANA: O preço líquido já engole o impacto e as taxas da Jupiter
            tokens_bought = amount_usdc / price_dex_net
            # O custo de reversão já está implícito no preço líquido, logo é 0 para o cálculo de saída
            custo_reverter_dex = 0.0
        else:
            # CASO ARBITRUM: Cálculo manual tradicional usando o preço bruto e a taxa da pool
            tokens_bought = (amount_usdc * (1 - fee_dex_percent)) / price_dex_gross
            custo_reverter_dex = (amount_usdc * fee_dex_percent)

        # 2. Valor bruto da venda na Hyperliquid (já com taxas de lá: 0.035% * 2)
        total_recebido_hl = (tokens_bought * price_hl) * (1 - 0.00070)

        # 3. Custos adicionais
        # custo_reverter_dex = (tokens_bought * price_dex_gross) * fee_dex_percent
        total_gas = gas_usdc * 2  # Abertura + Fecho (será 0 na Solana)

        # 4. LUCRO REAL LÍQUIDO
        net_profit = total_recebido_hl - amount_usdc - custo_reverter_dex - total_gas
        spread_percent = ((price_hl / price_dex_gross) - 1) * 100

        return net_profit, spread_percent

    # --- 2. CONSULTA DE PREÇOS AGNÓSTICA ---

    async def fetch_dex_price(self, pair: WatchedPair, pool_addr, usdc_balance_to_trade: float) -> (DexQuote | None):
        """
        Decide se consulta o cache do Multicall (ARB) ou a API da Jupiter (SOL).
        """
        if pair.chain == Chains.SOLANA:
            return await self.jupiter_client.get_quote(addr_in=pair.addr_a,
                                                       addr_out=pair.addr_b,
                                                       amount_in_human=usdc_balance_to_trade,
                                                       decimals_in=pair.decimal_a,
                                                       decimals_out=pair.decimal_b)
        else:
            return self.uniswap_client.calculate_quote_local(pool_addr, pair.addr_a, pair.addr_b)
        return None

    # --- 3. O NOVO LOCALIZADOR DE OPORTUNIDADES (Refatorado do teu original) ---

    async def find_best_dex_opportunity(self, pair: WatchedPair, price_hl: float, usdc_balance_to_trade: float,
                                        gas_cost_usdc: float):
        best_opportunity = None

        # Filtro inicial de blacklist por par/pool
        for dex_name, pool_addr in pair.pools_map.items():
            p_addr_l = pool_addr.lower()
            if p_addr_l in self.pool_blacklist:
                if time.time() < self.pool_blacklist[p_addr_l]:
                    continue
                else:
                    del self.pool_blacklist[p_addr_l]

            # Obter cotação (Agnóstico)
            quote = await self.fetch_dex_price(pair, p_addr_l, usdc_balance_to_trade)

            if not quote: continue

            raw_price_dex_gross = quote.price_dex_gross
            raw_price_dex_net = quote.price_dex_net
            direction = quote.direction
            fee_dex_ppm = quote.fee_dex_ppm
            data_quote = quote.data_quote

            # raw_price_dex, direction, fee_dex_ppm, data_quote = quote
            # price_dex_gross = 1 / raw_price_dex_gross
            price_dex_gross = 1 / raw_price_dex_gross if raw_price_dex_gross > 0 else 0.0
            price_dex_net = None
            if raw_price_dex_net is not None and raw_price_dex_net > 0:
                price_dex_net = 1 / raw_price_dex_net
            # price_dex = raw_price_dex

            """
            logging.info(
                f"Dex: {dex_name}, Pair: {pair.symbol_a}/{pair.symbol_b}, Price Dex: {price_dex_gross}, Price HL: {price_hl}")
            """

            logging.info(
                f"Dex: {dex_name}, Pair: {pair.symbol_a}/{pair.symbol_b} | "
                f"Bruto: {price_dex_gross:.4f} | Líquido: {f'{price_dex_net:.4f}' if price_dex_net else 'N/A'} | "
                f"HL: {price_hl:.4f}"
            )

            # Determinar custo de gás baseado na rede
            current_gas = 0.05 if pair.chain == Chains.SOLANA else gas_cost_usdc

            # Cálculo de Métricas Centralizado
            net_profit, spread_percent = self.calculate_net_metrics(
                price_hl, price_dex_gross, price_dex_net, usdc_balance_to_trade, fee_dex_ppm, current_gas
            )

            # Criar objeto de oportunidade (DexOpportunity)
            current_opp = DexOpportunity(
                chain=pair.chain,
                strategy='MULTI_CHAIN',
                profit=net_profit,
                spread=spread_percent,
                symbol=pair.symbol_b,
                price_dex=price_dex_gross,
                price_hl=price_hl,
                pool_addr=pool_addr,
                dex_name=dex_name,
                dex_fee=fee_dex_ppm,
                direction=direction,
                data_quote=data_quote
            )

            if best_opportunity is None or current_opp.profit > best_opportunity.profit:
                best_opportunity = current_opp
        return best_opportunity
