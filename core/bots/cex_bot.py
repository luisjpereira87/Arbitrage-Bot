import asyncio
import logging
import os
import time
from datetime import datetime
from typing import Dict

import ccxt.pro as ccxtpro

from core.bots.cex_bot_utils import CexBotUtils
from core.bots.exchanges.exchange_client import ExchangeClient
from core.config.properties_multi import PropertiesMulti
from core.dclass.cex_active_position_dclass import CexActivePosition
from core.dclass.cex_opportunity_dclass import CexOpportunity
from core.dclass.cex_type_enum import CexType
from core.dclass.signal_enum import Signal
from core.utils.cex_trade_position import CexTradePosition


class CexBot:
    def __init__(self):
        properties = PropertiesMulti()

        # Descobrir a pasta do script (/core/bots)
        bots_dir = os.path.dirname(os.path.abspath(__file__))

        # Subir duas pastas para chegar à raiz do projeto (/arbitrage_bot)
        project_root = os.path.abspath(os.path.join(bots_dir, "../../"))

        # Monta o caminho apontando para a raiz do projeto
        library_path = os.path.join(project_root, properties.LIGHTER_SIGNER_FILE)

        print("\n--- 🔍 NOVO DIAGNÓSTICO DE CAMINHO ---")
        print(f"📂 Raiz do Projeto: {project_root}")
        print(f"🎯 A procurar em: {library_path}")

        if not os.path.exists(library_path):
            print(f"🚨 [ERRO] Ainda não encontrei o ficheiro na raiz do projeto!")
            # Se falhar na raiz, mantemos a procura na pasta local para não quebrar o CCXT caso o movas para lá
            library_path = os.path.join(bots_dir, properties.LIGHTER_SIGNER_FILE)
        else:
            print("✅ Ficheiro detetado na raiz do projeto!")
        print("---------------------------------\n")

        hl = ccxtpro.hyperliquid({
            "walletAddress": properties.WALLET_ADDRESS_HL,
            "privateKey": properties.PRIVATE_KEY_WALLET_HL,
            "enableRateLimit": True,
            "timeout": 10000,
            "testnet": False,
            "options": {"defaultSlippage": 0.01},
        })

        lighter = ccxtpro.lighter({
            "walletAddress": properties.WALLET_ADDRESS_HL,
            "privateKey": properties.PRIVATE_KEY_WALLET_HL,
            "enableRateLimit": True,
            "timeout": 10000,
            "testnet": False,
            "options": {"defaultSlippage": 0.01,
                        "libraryPath": library_path,
                        "integrator_account_index": 0,  # ✨ A CHAVE EM FALTA AQUI!
                        "adjustForTimeDifference": True,
                        'accountIndex': 729593,  # 🟢 O teu ID real de Mainnet!
                        'apiKeyIndex': 254,  # 🟢 O teu ID real de Mainnet!
                        },
        })

        self.watchlist = [
            "BTC/USDC:USDC",
            "ETH/USDC:USDC",
            "SOL/USDC:USDC",
            "AVAX/USDC:USDC",
            "POPCAT/USDC:USDC",
            "FARTCOIN/USDC:USDC",
            "HYPE/USDC:USDC",
            "TRUMP/USDC:USDC",
            "ZEC/USDC:USDC",
            "AAVE/USDC:USDC",
            "BNB/USDC:USDC",
            "UNI/USDC:USDC",
            "WIF/USDC:USDC"
        ]

        self.blacklist = set()

        self.hl_exchange = ExchangeClient(hl, properties.WALLET_ADDRESS_HL)
        self.lighter_exchange = ExchangeClient(lighter, properties.WALLET_ADDRESS_HL)

        self.active_positions = CexTradePosition.load_all_positions()

        # Cache de saldos e controlo de tempo
        self.hl_balance = 0.0
        self.lighter_balance = 0.0
        self.last_balance_update = 0.0
        self.BALANCE_UPDATE_INTERVAL = 10.0  # Segundos

        self.min_capital = 11.0

    def calculate_spread(
            self,
            pair: str,
            hl_prices,
            lighter_prices,
            hl_balance: float,
            lighter_balance: float,
            lighter_gas_cost: float = 0.05,
    ) -> CexOpportunity | None:
        """Calculates the real net spread by unifying arbitrage routes and logs current market state."""
        try:
            if not hl_prices or not lighter_prices:
                return None

            capital_usdc = min(hl_balance, lighter_balance, self.min_capital)
            if capital_usdc <= 0:
                return None

            hl_fee, lighter_fee = 0.00035, 0.00000

            routes = {
                CexType.LIGHTER_TO_HL: {
                    "name": "Lighter -> HL",
                    "buy_price": lighter_prices.ask, "sell_price": hl_prices.bid,
                    "buy_fee": lighter_fee, "sell_fee": hl_fee
                },
                CexType.HL_TO_LIGHTER: {
                    "name": "HL -> Lighter",
                    "buy_price": hl_prices.ask, "sell_price": lighter_prices.bid,
                    "buy_fee": hl_fee, "sell_fee": lighter_fee
                }
            }

            # Variáveis para fazermos o log do estado atual do mercado
            best_route_name = ""
            best_pct = -999.0
            best_lucro = -999.0

            for route_type, r in routes.items():
                if not r["buy_price"] or not r["sell_price"]:
                    continue

                # 🛠️ Matemática Unificada
                effective_capital = capital_usdc / (1 + r["buy_fee"])
                asset_qty = effective_capital / r["buy_price"]
                net_return = (asset_qty * r["sell_price"]) * (1 - r["sell_fee"]) - lighter_gas_cost

                net_profit_usdc = net_return - capital_usdc
                profit_pct = (net_profit_usdc / capital_usdc) * 100

                # Guardar a melhor rota deste ciclo para o log de monitorização
                if profit_pct > best_pct:
                    best_pct = profit_pct
                    best_lucro = net_profit_usdc
                    best_route_name = r["name"]

                is_viable = CexBotUtils.check_viability_dynamic(
                    pair=pair,
                    net_profit=net_profit_usdc,
                    amount_usdc=capital_usdc,
                    is_exit=False,
                    spread_percent=profit_pct,
                    entry_timestamp=None
                )

                # 🔥 SE DER LUCRO REAL: Quebra a linha (\n) para o log fixar no terminal e devolve a oportunidade
                if is_viable:
                    print(
                        f"\n🔥 [OPORTUNIDADE DETETADA] {pair} | {r['name']} | Lucro: +{net_profit_usdc:.4f} USDC ({profit_pct:.4f}%)")
                    return CexOpportunity(
                        pair, r["name"], route_type, capital_usdc,
                        r["buy_price"], r["sell_price"], asset_qty, net_profit_usdc, profit_pct, hl_balance,
                        lighter_balance
                    )

            # 📊 RADAR EM TEMPO REAL (Se nenhuma rota deu lucro):
            # Imprime no terminal o estado da melhor rota atual sem fazer scroll (substitui a mesma linha)
            """
            if best_route_name:
                print(
                    f"📡 [RADAR] {pair:<15} | Melhor Rota: {best_route_name:<15} | Spread Líquido: {best_pct:+.4f}% ({best_lucro:+.4f} USDC)",
                    end="\r"
                )
            """
            return None

        except Exception as e:
            print(f"\n⚠️ Erro interno no método calcular_spread: {e}")
            return None

    async def get_all_market_prices(self) -> Dict[str, dict]:
        """Recolhe os preços via Websocket apenas para os pares permitidos."""

        # 🎯 FILTRO ATIVO: Só cria tarefas para moedas fora da blacklist
        pares_validos = [pair for pair in self.watchlist if pair not in self.blacklist]

        tasks = [self.fetch_exchange_prices(pair) for pair in pares_validos]

        # Se todos os tokens da watchlist forem parar à blacklist, evita o crash do gather
        if not tasks:
            return {}

        results = await asyncio.gather(*tasks, return_exceptions=True)

        market_data = {}
        for pair, result in zip(pares_validos, results):
            # 🎯 CAPTURA REAL DA BLACKLIST: Se o resultado for uma exceção lançada pelo CCXT
            if isinstance(result, Exception):
                # Se for um erro de falta de dados (IndexError ou similar vindo do CCXT)
                if "index out of range" in str(result).lower() or isinstance(result, IndexError):
                    print(f"\n🚨 [LÍQUIDEZ CRÍTICA] {pair} enviado para a BLACKLIST por livro vazio.")
                    self.blacklist.add(pair)
                else:
                    # Outros erros (ex: timeouts temporários ou desconexões 1006)
                    logging.error(f"⚠️ Erro temporário ao obter preços para {pair}: {result}")
                continue

            if not result:
                continue

            hl_prices, lighter_prices = result
            if hl_prices and lighter_prices:
                market_data[pair] = {"hl": hl_prices, "lighter": lighter_prices}

        return market_data

    async def fetch_exchange_prices(self, pair: str) -> tuple:
        """Método auxiliar para disparar a recolha assíncrona de um par."""
        # Removemos o try/except daqui, pois o asyncio.gather com return_exceptions=True
        # já vai capturar e entregar o erro mastigado no get_all_market_prices
        hl_task = self.hl_exchange.watch_prices(pair)
        lighter_task = self.lighter_exchange.watch_prices(pair)
        return await asyncio.gather(hl_task, lighter_task)

    def print_log(self, par: str, op: CexOpportunity):
        """Centraliza a forma como mostras os lucros no terminal."""
        print(f"\n🚨 [OPORTUNIDADE EM {par}!]")
        print(f"   Rota:         {op.route}")
        print(f"   Capital:      {op.capital_to_trade:.2f} USDC")
        print(
            f"   Execução:     Comprar a {op.buy_price} -> Vender a {op.sell_price}"
        )
        print(
            f"   🔥 Lucro:     +{op.profit_usdc:.4f} USDC ({op.profit_percent:.4f}%)"
        )
        print("-" * 40)

    async def open_trade(self, cex_opportunity: CexOpportunity):
        symbol = cex_opportunity.symbol
        capital_to_trade = cex_opportunity.capital_to_trade
        qty = cex_opportunity.qtd_pair
        leverage = 1.0

        if not await self.lighter_exchange.validate_lighter_client():
            logging.error("❌ Abortando trade: Lighter falhou a validação de cliente.")
            return False

        # Configuração de sinais
        if cex_opportunity.type == CexType.HL_TO_LIGHTER:
            hl_signal, lighter_signal = Signal.BUY, Signal.SELL
            hl_price, lighter_price = cex_opportunity.buy_price, cex_opportunity.sell_price
        elif cex_opportunity.type == CexType.LIGHTER_TO_HL:
            hl_signal, lighter_signal = Signal.SELL, Signal.BUY
            hl_price, lighter_price = cex_opportunity.sell_price, cex_opportunity.buy_price
        else:
            return False

        print(f"🚀 [EXECUTOR] Iniciando execução sequencial para {symbol} | Qtd: {qty}...")

        # 1. Executa PRIMEIRO a perna mais instável (Lighter)
        res_lighter = await self.lighter_exchange.open_new_position(symbol, leverage, lighter_signal, capital_to_trade,
                                                                    lighter_price)
        lighter_success = res_lighter is not None

        if not lighter_success:
            print(f"❌ [FALHA PRIORITÁRIA] Lighter rejeitou a ordem. Nenhum risco gerado.")
            return False

        # 2. Executa a perna da Hyperliquid (apenas se a Lighter tiver sucesso)
        res_hl = await self.hl_exchange.open_new_position(symbol, leverage, hl_signal, capital_to_trade, hl_price)
        hl_success = res_hl is not None

        # Caso A: Tudo perfeito
        if hl_success:
            print(f"✅ [ARBITRAGEM SUCESSO] Posições abertas com sucesso!")

            entry_price_hl = entry_price_lighter = 0.0
            if cex_opportunity.type == CexType.HL_TO_LIGHTER:
                entry_price_hl = cex_opportunity.buy_price
                entry_price_lighter = cex_opportunity.sell_price
            elif cex_opportunity.type == CexType.LIGHTER_TO_HL:
                entry_price_hl = cex_opportunity.sell_price
                entry_price_lighter = cex_opportunity.buy_price

            CexTradePosition.save_position(CexActivePosition(
                status='OPEN',
                symbol=symbol,
                type=cex_opportunity.type,
                qty_pair=qty,
                initial_balance_lighter_usd=cex_opportunity.lighter_balance,
                initial_balance_hl_usd=cex_opportunity.hl_balance,
                capital_to_trade_usd=cex_opportunity.capital_to_trade,
                entry_price_hl=entry_price_hl,
                entry_price_lighter=entry_price_lighter,
                timestamp=datetime.now().isoformat()
            ))
            self.active_positions = CexTradePosition.load_all_positions()
            return True

        # Caso C: Lighter executou, mas HL FALHOU (Rollback necessário)
        if lighter_success and not hl_success:
            print(f"🚨 [FALHA PARCIAL] Ordem executada na Lighter, mas FALHOU na Hyperliquid! Erro: {res_hl}")
            print(f"⚡ [CONTINGÊNCIA] A acionar rollback na Lighter...")

            inverse_lighter_signal = Signal.SELL if lighter_signal == Signal.BUY else Signal.BUY
            try:
                await self.lighter_exchange.close_position(symbol, qty, inverse_lighter_signal)
                print(f"🛡️ [ROLLBACK CONCLUÍDO] Risco mitigado.")
            except Exception as e:
                print(f"☠️ [ALERTA MÁXIMO] Falha catastrófica no rollback: {e}")
            return False

        return False

    """
    async def open_trade(self, cex_opportunity: CexOpportunity):
        symbol = cex_opportunity.symbol
        capital_to_trade = cex_opportunity.capital_to_trade
        qty = cex_opportunity.qtd_pair
        leverage = 1.0

        if not await self.lighter_exchange.validate_lighter_client():
            logging.error("❌ Abortando trade: Lighter falhou a validação de cliente.")
            return False

        if cex_opportunity.type == CexType.HL_TO_LIGHTER:
            hl_signal, lighter_signal = Signal.BUY, Signal.SELL
            hl_price, lighter_price = cex_opportunity.buy_price, cex_opportunity.sell_price
        elif cex_opportunity.type == CexType.LIGHTER_TO_HL:
            hl_signal, lighter_signal = Signal.SELL, Signal.BUY
            hl_price, lighter_price = cex_opportunity.sell_price, cex_opportunity.buy_price
        else:
            return False

        # 1. Prepare tasks to run in parallel
        hl_task = self.hl_exchange.open_new_position(symbol, leverage, hl_signal, capital_to_trade, hl_price)
        lighter_task = self.lighter_exchange.open_new_position(symbol, leverage, lighter_signal, capital_to_trade,
                                                               lighter_price)

        print(f"🚀 [EXECUTOR] A enviar ordens em paralelo para {symbol} | Qtd: {qty}...")

        # 2. Fire both tasks simultaneously
        tasks_results = await asyncio.gather(hl_task, lighter_task, return_exceptions=True)
        res_hl, res_lighter = tasks_results

        # 3. Evaluate the execution success of each leg
        hl_success = not isinstance(res_hl, Exception) and res_hl is not None
        lighter_success = not isinstance(res_lighter, Exception) and res_lighter is not None

        # Caso A: Tudo perfeito
        if hl_success and lighter_success:
            print(f"✅ [ARBITRAGEM SUCESSO] Posições abertas em simultâneo nas duas DEXs!")

            entry_price_hl = entry_price_lighter = 0.0
            if cex_opportunity.type == CexType.HL_TO_LIGHTER:
                entry_price_hl = cex_opportunity.buy_price
                entry_price_lighter = cex_opportunity.sell_price
            elif cex_opportunity.type == CexType.LIGHTER_TO_HL:
                entry_price_hl = cex_opportunity.sell_price
                entry_price_lighter = cex_opportunity.buy_price

            CexTradePosition.save_position(CexActivePosition(
                status='OPEN',
                symbol=symbol,
                type=cex_opportunity.type,
                qty_pair=qty,
                initial_balance_lighter_usd=cex_opportunity.lighter_balance,
                initial_balance_hl_usd=cex_opportunity.hl_balance,
                capital_to_trade_usd=cex_opportunity.capital_to_trade,
                entry_price_hl=entry_price_hl,
                entry_price_lighter=entry_price_lighter,
                timestamp=datetime.now().isoformat()
            ))
            self.active_positions = CexTradePosition.load_all_positions()
            return True

        # Caso B: Ambas falharam (Azar na rede, mas capital está seguro)
        if not hl_success and not lighter_success:
            print(
                f"❌ [FALHA DUPLA] Ambas as exchanges rejeitaram as ordens. Nenhum risco gerado. Erros: HL({res_hl}) | Lighter({res_lighter})")
            return False

        # 🚨 CASO CRÍTICO 1: HL executou, mas Lighter FALHOU
        if hl_success and not lighter_success:
            print(f"🚨 [FALHA PARCIAL] Ordem executada na Hyperliquid, mas FALHOU na Lighter! Erro: {res_lighter}")
            print(f"⚡ [CONTINGÊNCIA] A acionar ordem de compensação imediata na Hyperliquid...")

            inverse_hl_signal = Signal.SELL if hl_signal == Signal.BUY else Signal.BUY

            try:
                # Executa a mercado imediatamente para limpar a exposição usando a quantidade certa
                await self.hl_exchange.close_position(symbol, qty, inverse_hl_signal)
                print(f"🛡️ [ROLLBACK CONCLUÍDO] Posição na Hyperliquid fechada com sucesso. Risco mitigado.")
            except Exception as e:
                print(f"☠️ [ALERTA MÁXIMO] Falha catastrófica! Não consegui reverter a ordem na Hyperliquid: {e}")
            return False
        # 🚨 CASO CRÍTICO 2: Lighter executou, mas HL FALHOU
        if lighter_success and not hl_success:
            print(f"🚨 [FALHA PARCIAL] Ordem executada na Lighter, mas FALHOU na Hyperliquid! Erro: {res_hl}")
            print(f"⚡ [CONTINGÊNCIA] A acionar ordem de compensação imediata na Lighter...")

            inverse_lighter_signal = Signal.SELL if lighter_signal == Signal.BUY else Signal.BUY

            try:
                await self.lighter_exchange.close_position(symbol, qty, inverse_lighter_signal)
                print(f"🛡️ [ROLLBACK CONCLUÍDO] Posição na Lighter fechada com sucesso. Risco mitigado.")
            except Exception as e:
                print(f"☠️ [ALERTA MÁXIMO] Falha catastrófica! Não consegui reverter a ordem na Lighter: {e}")
            return False
    """

    async def execute_parallel_close(self, pos: CexActivePosition) -> bool:
        """Executa o fecho simultâneo e em paralelo de ambas as pernas da arbitragem."""
        symbol = pos.symbol
        capital = pos.capital_to_trade_usd  # Ou o capital equivalente registado no teu DClass
        qty = pos.qty_pair
        leverage = 1.0

        # 1. Determinar os sinais inversos para encerrar as posições

        if pos.type == CexType.LIGHTER_TO_HL:
            # Entrada foi: Compra Lighter, Venda HL
            # Fecho é: Venda Lighter, Compra HL
            hl_close_signal = Signal.BUY
            lighter_close_signal = Signal.SELL
        elif pos.type == CexType.HL_TO_LIGHTER:
            # Entrada foi: Compra HL, Venda Lighter
            # Fecho é: Venda HL, Compra Lighter
            hl_close_signal = Signal.SELL
            lighter_close_signal = Signal.BUY
        else:
            print(f"⚠️ [FECHO] Tipo de posição inválido no JSON: {pos.type}")
            return False

        print(f"🚀 [FECHO PARALELO] A enviar ordens de encerramento para {symbol} | Qtd: {qty}...")

        # 2. Desenhar as tarefas de fecho (close_position ou open_new_position com sinal inverso)
        # Certifica-te de que o teu método close_position aceita estes argumentos
        hl_task = self.hl_exchange.close_position(symbol, qty, hl_close_signal)
        lighter_task = self.lighter_exchange.close_position(symbol, qty, lighter_close_signal)

        try:
            # 3. Disparar ambas as ordens no mesmo microssegundo
            tasks_results = await asyncio.gather(hl_task, lighter_task, return_exceptions=True)
            res_hl, res_lighter = tasks_results

            # 4. Avaliar o sucesso do fecho de cada perna
            hl_success = not isinstance(res_hl, Exception)
            lighter_success = not isinstance(res_lighter, Exception)

            # Cenário Ideal: Tudo fechado no alvo!
            if hl_success and lighter_success:
                print(f"🎉 [SUCESSO] Arbitragem liquidada com sucesso em ambas as plataformas para {symbol}!")
                CexTradePosition.clear_position(symbol)
                self.active_positions = CexTradePosition.load_all_positions()
                return True

            # Cenário de Desastre no Fecho (Legging Risk no Fecho)
            if hl_success and not lighter_success:
                print(f"🚨 [ERRO CRÍTICO FECHO] HL fechou, mas Lighter FALHOU! Erro: {res_lighter}")
                print(f"🔄 [RETRIA] A iniciar loop de emergência para forçar o fecho na Lighter...")
                # Aqui podes chamar aquele método de retribuição que vimos antes para não ficares "manco"
                await self.lighter_exchange.close_position(symbol, qty, lighter_close_signal)
                return True  # Retornamos True se a contingência assumir o controlo, ou geres conforme preferires

            if lighter_success and not hl_success:
                print(f"🚨 [ERRO CRÍTICO FECHO] Lighter fechou, mas HL FALHOU! Erro: {res_hl}")
                print(f"🔄 [RETRIA] A iniciar loop de emergência para forçar o fecho na Hyperliquid...")
                await self.hl_exchange.close_position(symbol, qty, hl_close_signal)
                return True

            # Se ambas falharem por erro de rede simultâneo
            print(f"❌ [FALHA DUPLA NO FECHO] Nenhuma ordem de fecho foi aceite: HL({res_hl}) | Lighter({res_lighter})")
            return False

        except Exception as e:
            print(f"⚠️ Erro catastrófico ao executar execute_parallel_close: {e}")
            return False

    async def monitor_active_trades(self, current_market_prices: dict):
        """Monitoriza as posições guardadas nos ficheiros JSON usando a classe TradePositionMulti."""
        # 1. Carrega todas as posições que estão na pasta de armazenamento

        # active_positions = CexTradePosition.load_all_positions()
        if not self.active_positions:
            return

        for base_symbol, pos in self.active_positions.items():
            pair = pos.symbol  # Ex: "BTC/USDC:USDC"

            # Se não temos dados de mercado em tempo real via websocket para este par, salta
            if pair not in current_market_prices:
                continue

            hl_prices = current_market_prices[pair]["hl"]
            lighter_prices = current_market_prices[pair]["lighter"]

            # Dependendo da rota, usamos o Bid ou Ask correto para simular o fecho a mercado

            if pos.type == CexType.LIGHTER_TO_HL:
                current_hl_target = hl_prices.ask  # Preço para fechar o short na HL
                current_lighter_target = lighter_prices.bid  # Preço para vender o long na Lighter
            else:
                current_hl_target = hl_prices.bid  # Preço para vender o long na HL
                current_lighter_target = lighter_prices.ask  # Preço para fechar o short na Lighter

            if not current_hl_target or not current_lighter_target:
                continue

            # 2. Calcula o lucro combinado simulado usando o teu método estático
            net_profit_usdc = CexTradePosition.check_exit_profitability(
                pos, current_hl_target, current_lighter_target
            )

            if net_profit_usdc > -1:
                logging.info(f"💰 [GATILHO LUCRATIVO] {pair} está positivo em +{net_profit_usdc:.4f} USDC. A fechar!")

            should_close = CexBotUtils.check_viability_dynamic(
                pair=pair,
                net_profit=net_profit_usdc,
                amount_usdc=pos.capital_to_trade_usd,
                is_exit=True,
                spread_percent=(net_profit_usdc / pos.capital_to_trade_usd) * 100,
                entry_timestamp=pos.timestamp
            )

            # 💡 Podes definir um alvo de lucro na tua config (ex: 0.20 USDC ou percentual)
            if should_close:
                logging.info(f"💰 [GATILHO LUCRATIVO] {pair} está positivo em +{net_profit_usdc:.4f} USDC. A fechar!")

                # 3. Executa o fecho paralelo em mercado
                # (Aqui crias um CexOpportunity inverso e chamas o teu método open_trade que já faz o parallel gather)
                success = await self.execute_parallel_close(pos)

                if success:
                    # 4. Limpa o ficheiro JSON apenas se as ordens de fecho correrem bem
                    CexTradePosition.clear_position(pos.symbol)
            else:
                # Print discreto apenas para debug no terminal
                print(f"📊 [MONITOR] {pair} PnL Combinado: {net_profit_usdc:.4f} USDC", end="\r")

    async def _update_balances(self, force: bool = False):
        """Atualiza os saldos em cache respeitando o intervalo de tempo ou de forma

        forçada.
        """
        current_time = asyncio.get_event_loop().time()

        # Só atualiza se for forçado OU se já tiver passado o intervalo de 10 segundos
        if force or (current_time - self.last_balance_update > self.BALANCE_UPDATE_INTERVAL):
            try:
                # Faz as chamadas REST em paralelo para ser ultra rápido
                self.hl_balance, self.lighter_balance = await asyncio.gather(
                    self.hl_exchange.get_available_balance(),
                    self.lighter_exchange.get_available_balance()
                )
                self.last_balance_update = current_time
            except Exception as e:
                # Se a API falhar por instabilidade, o bot não para; mantém a cache anterior
                logging.error(f"\n⚠️ Erro temporário ao atualizar saldos via REST: {e}")

    async def is_active_positions(self, symbol: str) -> bool:
        """Valida se existe alguma posição aberta em qualquer uma das DEXs (REST/Cache)."""
        try:
            hl_pos = await self.hl_exchange.get_open_position(symbol)
            lighter_pos = await self.lighter_exchange.get_open_position(symbol)

            return hl_pos is not None or lighter_pos is not None

        except Exception as e:
            logging.error(f"⚠️ [API] Erro ao checar posições em tempo real para {symbol}: {e}")
            return True

    async def test_spread_loop(self):

        logging.info("🔍 [SCANNER] A carregar mercados da Hyperliquid e Lighter...")
        await self.hl_exchange.load_markets()
        await self.lighter_exchange.load_markets()

        logging.info("🔍 [SCANNER] A ligar aos Websockets da Hyperliquid e Lighter...")

        # Forçar a primeira carga de saldos antes de entrar no loop
        await self._update_balances(force=True)
        heartbeat_interval = 120
        last_heartbeat = time.time()
        while True:
            try:
                # 1. Chamar o atualizador de saldos
                await self._update_balances()

                # 2. Capturar os preços em tempo real do Websocket
                market = await self.get_all_market_prices()

                # 🎯 3. MÓDULO DE ACOMPANHAMENTO (JSON):
                await self.monitor_active_trades(market)

                # 🚀 4. MÓDULO DE SCANNING:
                if not self.active_positions:
                    for pair, prices in market.items():
                        opportunity = self.calculate_spread(
                            pair,
                            prices["hl"],
                            prices["lighter"],
                            hl_balance=self.hl_balance,
                            lighter_balance=self.lighter_balance,
                        )

                        if opportunity:
                            logging.info(f"\n👀 [VALIDAÇÃO] Spread detetado em {pair}. A verificar exchanges...")
                            if not await self.is_active_positions(pair):
                                self.print_log(pair, opportunity)
                                await self.open_trade(opportunity)
                                await asyncio.sleep(0.1)
                                await self._update_balances(force=True)
                            else:
                                logging.info(
                                    f"🚨 [BLOQUEIO] Par {pair} ignorado. Ainda existem posições abertas na Exchange!")

                now = time.time()
                if now - last_heartbeat >= heartbeat_interval:
                    formated_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    # O \n no início garante que se houver um print com \r ativo (Radar),
                    # o sinal de vida salta para uma linha nova e limpa sem estragar o layout.
                    logging.info(
                        f"\n💚 [SINAL DE VIDA] {formated_time} | {len(market)} pares monitorizados | "
                        f"Saldos -> HL: {self.hl_balance:.2f} USDC | Lighter: {self.lighter_balance:.2f} USDC"
                    )

                    last_heartbeat = now  # Faz o reset do temporizador
                await asyncio.sleep(0.001)

            except Exception as e:
                logging.error(f"\n⚠️ Erro na captura dos dados: {e}")
                await asyncio.sleep(2)

    async def run_live_test(self):

        print("🚀 Iniciando teste real de venda...")

        try:
            # 3. Executa a ordem de venda pequena
            # Ajusta o symbol e amount para algo seguro
            order = await self.lighter_exchange.open_new_position(
                "BTC/USDC:USDC",
                1.0,
                Signal.SELL,
                20.0  # Preço muito alto para a ordem não ser executada imediatamente
            )
            print(f"✅ Ordem enviada com sucesso! ID: {order.id}")

        except Exception as e:
            print(f"❌ Ocorreu um erro no teste real:")
            # Imprime o erro completo para sabermos se é o tal ponteiro ou a chave
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    try:
        cex_bot = CexBot()
        asyncio.run(cex_bot.test_spread_loop())
    except KeyboardInterrupt:
        print("\n🛑 Scanner interrompido pelo utilizador.")
