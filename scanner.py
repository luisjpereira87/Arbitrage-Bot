import os
import time

from dotenv import load_dotenv
from web3 import Web3

from pool_finder import PoolFinder
from wallet_manager import WalletManager

# --- CONFIGURAÇÃO INICIAL ---
# Usa os teus endereços mascarados aqui
WETH = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
USDC = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
ARB = "0x912CE59144191C1204E64559FE8253a0e49E6548"
LINK = "0xf97f4df75117a78c1A5a0DBb814Af92458539FB4"
WBTC = "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f"


class ArbitrageScanner:
    def __init__(self, rpc_url, wallet_private_key, config_file):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.finder = PoolFinder(self.w3)  # A classe que criámos
        self.config_file = config_file
        self.decimal_map = {info["addr"].lower(): info["dec"] for info in self.config_file["tokens"].values()}

        self.wallet = WalletManager(self.w3)

        # ABI mínima para ler o preço (slot0)
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

        self.erc20_abi = [{"inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf",
                      "outputs": [{"name": "balance", "type": "uint256"}], "stateMutability": "view",
                      "type": "function"}]

    def get_price(self, pool_address, token_base, token_cotacao):
        try:
            pool_address = self.w3.to_checksum_address(pool_address)
            contract = self.w3.eth.contract(address=pool_address, abi=self.pool_abi)

            slot0_data = contract.functions.slot0().call()
            sqrtPriceX96 = slot0_data[0] if isinstance(slot0_data, (list, tuple)) else slot0_data

            t0 = contract.functions.token0().call().lower()
            t1 = contract.functions.token1().call().lower()

            # 1. Identificar decimais (USDC=6, resto=18)
            d0 = 6 if t0 == USDC.lower() else 18
            d1 = 6 if t1 == USDC.lower() else 18

            # 2. Cálculo do Preço Real de Token1 em relação a Token0
            # Preço = (sqrtPriceX96 / 2^96)^2 * (10^d0 / 10^d1)
            price_t1_em_t0 = ((sqrtPriceX96 / (2 ** 96)) ** 2) * (10 ** d0 / 10 ** d1)

            # 3. Lógica de Retorno
            if token_base.lower() == t1:
                # Tu queres o preço do Token1 (ex: ARB) expresso em Token0 (ex: WETH)
                return price_t1_em_t0
            else:
                # Tu queres o preço do Token0 (ex: WETH) expresso em Token1 (ex: USDC)
                # Invertemos o rácio
                return 1 / price_t1_em_t0

        except Exception as e:
            return None

    def get_quote(self, pool_address, token_in, token_out):
        try:
            pool_address = self.w3.to_checksum_address(pool_address)
            pool_contract = self.w3.eth.contract(address=pool_address, abi=self.pool_abi)

            # Busca a fee diretamente da pool (ex: 3000 significa 0.3%)
            fee = pool_contract.functions.fee().call()

            # 1. VALIDAÇÃO REAL: A pool contém os dois tokens que eu quero trocar?
            t0 = pool_contract.functions.token0().call().lower()
            t1 = pool_contract.functions.token1().call().lower()

            # 2. DEFINIR OS DECIMAIS (Garante que estas linhas existem e não estão comentadas!)
            #d0 = self.get_token_decimals(t0)
            #d1 = self.get_token_decimals(t1)

            tokens_na_pool = [t0, t1]
            if token_in.lower() not in tokens_na_pool or token_out.lower() not in tokens_na_pool:
                # Se um dos tokens não pertence à pool, o swap é impossível.
                return None

            # 2. Filtros de Liquidez e Saldo (Como já tinhas)
            liquidity = pool_contract.functions.liquidity().call()
            if liquidity < 10 ** 12: return None

            #print(f"Liquidez da Pool: {liquidity}")
            # 3. Cálculo do Preço (Ajustado para evitar o erro de subscriptable)
            slot0_data = pool_contract.functions.slot0().call()

            # Se vier uma lista/tuplo, pegamos o primeiro elemento.
            # Se vier um inteiro, usamos o valor diretamente.
            if isinstance(slot0_data, (list, tuple)):
                sqrtPriceX96 = slot0_data[0]
            else:
                sqrtPriceX96 = slot0_data

            # 1. Busca os decimais de forma segura
            d0 = self.get_token_decimals(t0)
            d1 = self.get_token_decimals(t1)

            # 2. Preço Teórico (Sem decimais)
            price_raw = (sqrtPriceX96 / (2 ** 96)) ** 2

            # 3. Preço Real (Ajustado)
            # Preço de 1 unidade de T0 expressa em T1
            price_t0_em_t1 = price_raw * (10 ** d0 / 10 ** d1)

            # 4. Lógica de Saída
            if token_in.lower() == t0:
                # Entra T0 -> Sai T1
                direcao_v3 = True
                preco_final = price_t0_em_t1
            else:
                # Entra T1 -> Sai T0
                direcao_v3 = False
                preco_final = 1 / price_t0_em_t1

            print(f"DEBUG POOL {pool_address}: T0_Dec: {d0}, T1_Dec: {d1}, Price_Raw: {price_raw}")

            return preco_final, direcao_v3, fee

        except Exception as e:
            print(f"❌ Erro no get_quote!")
            print(f"   Pool: {pool_address}")
            print(f"   Token In: {token_in} (Tipo: {type(token_in)})")
            print(f"   Token Out: {token_out} (Tipo: {type(token_out)})")
            print(f"   Mensagem: {e}")
            return None

    def calcular_triangular(self, q1, f1, q2, f2, q3, f3, capital=100.0):
        """
        q1: USDC -> WETH
        q2: WETH -> ARB
        q3: ARB -> USDC
        """
        # Usamos 0.9995 para simular a taxa de 0.05% da Uniswap/Pancake
        t1 = (1000000 - f1) / 1000000
        t2 = (1000000 - f2) / 1000000
        t3 = (1000000 - f3) / 1000000

        margem_seguranca = 0.995

        valor_final = (capital * (q1 * t1) * (q2 * t2) * (q3 * t3)) * margem_seguranca
        lucro_liquido = valor_final - capital - 0.25  # Desconto de $0.25 de Gas
        #if lucro_liquido > -0.10:
        #print(f"🔥 ALERTA DE LUCRO: ${lucro_liquido:.2f}")
        #print(f"   1. USDC -> ARB @ {q1}")
        #print(f"   2. ARB  -> GMX @ {q2}")
        #print(f"   3. GMX  -> USDC @ {q3}")
        if lucro_liquido > -0.25:
            print(f"DEBUG: Q1: {q1} | Q2: {q2} | Q3: {q3} | Final: {valor_final}")
            print(f"👀 Oportunidade Próxima: ${lucro_liquido:.4f} | Rota: {q1:.6f}->{q2:.2f}->{q3:.4f}")
        return lucro_liquido

    def log_sucesso_triangular(self, lucro, detalhes=""):
        # Cria (ou abre) um ficheiro CSV para ser fácil de abrir no Excel depois
        ficheiro = "oportunidades_triangulares.txt"
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')

        msg = f"[{timestamp}] ✅ LUCRO: ${lucro:.4f} | {detalhes}\n"

        with open(ficheiro, "a", encoding="utf-8") as f:
            f.write(msg)

        print(f"💾 Oportunidade guardada em {ficheiro}")

    def analisar_rotas(self, p1_dict, p2_dict, p3_dict, t1_addr, t2_addr, t3_addr):
        """
            Analisa todas as combinações de DEXs para um triângulo específico.
            t1_addr: Endereço do Token Inicial (ex: USDC)
            t2_addr: Endereço do Token Intermédio 1 (ex: WETH)
            t3_addr: Endereço do Token Intermédio 2 (ex: ARB)
            """
        melhor_lucro = -999.0
        melhor_rota_nome = ""

        # Itera por todas as DEXs/Pools encontradas para cada perna
        for dex1, addr1 in p1_dict.items():
            for dex2, addr2 in p2_dict.items():
                for dex3, addr3 in p3_dict.items():
                    # 1. Busca as cotações (Quotes) na direção certa
                    # P1: Token 1 -> Token 2
                    res1 = self.get_quote(addr1, t1_addr, t2_addr)
                    # P2: Token 2 -> Token 3
                    res2 = self.get_quote(addr2, t2_addr, t3_addr)
                    # P3: Token 3 -> Token 1
                    res3 = self.get_quote(addr3, t3_addr, t1_addr)

                    #print("AQUIII")
                    if res1 and res2 and res3:
                        q1, d1, f1 = res1  # Preço e Direção (bool)
                        q2, d2, f2 = res2
                        q3, d3, f3 = res3

                        # 2. Calcula o lucro (baseado em $100 de capital)
                        lucro = self.calcular_triangular(q1, f1, q2, f2, q3, f3, 30.0)

                        if lucro > melhor_lucro:
                            melhor_lucro = lucro
                            melhor_rota_nome = f"{dex1} | {dex2} | {dex3}"

                            # 3. GUARDAMOS A "RECEITA" PARA O CONTRATO
                            # Se este for o melhor lucro até agora, salvamos as direções
                            self.melhor_config = {
                                "pools": [addr1, addr2, addr3],
                                "direcoes": [d1, d2, d3],
                                "tokens": [t1_addr, t2_addr, t3_addr]
                            }
                    else:
                        # Isto vai dizer-te qual falhou
                        falhas = []
                        if not res1: falhas.append("P1")
                        if not res2: falhas.append("P2")
                        if not res3: falhas.append("P3")
                        #print(f"DEBUG: Salto em {dex1}|{dex2}|{dex3} por falta de quote em: {falhas}")

        return melhor_rota_nome, melhor_lucro

    def setup_triangulo(self):
        self.tokens = self.config_file["tokens"]
        self.fees = self.config_file["fees"]
        rotas_configuradas = []

        print(f"⚙️ Configurando {len(self.config_file['triangulos'])} triângulos em {len(self.fees)} faixas de taxa...")

        for tri in self.config_file["triangulos"]:
            t1, t2, t3 = tri
            addr1, addr2, addr3 = self.tokens[t1]["addr"], self.tokens[t2]["addr"], self.tokens[t3]["addr"]

            print(f"🔍 Mapeando: {t1} -> {t2} -> {t3}")

            # Estrutura para armazenar as pools de cada perna do triângulo
            pool_data = {
                "nome": f"{t1}-{t2}-{t3}",
                "tokens": [addr1, addr2, addr3],
                "p1": {}, "p2": {}, "p3": {}
            }

            for f in self.fees:
                # P1: Token1 -> Token2
                pool_data["p1"].update(self.finder.get_pools(addr1, addr2, f))
                # P2: Token2 -> Token3
                pool_data["p2"].update(self.finder.get_pools(addr2, addr3, f))
                # P3: Token3 -> Token1
                pool_data["p3"].update(self.finder.get_pools(addr3, addr1, f))

            rotas_configuradas.append(pool_data)

        return rotas_configuradas

    def get_token_decimals(self, token_address):
        return self.decimal_map.get(token_address.lower(), 18)

    def run_triangular(self):
        rotas = self.setup_triangulo()

        while True:
            for rota in rotas:
                try:
                    # Extrai os endereços para facilitar a leitura no get_quote
                    t1_addr, t2_addr, t3_addr = rota["tokens"]

                    # analisa_rotas agora recebe o dicionário de pools específico daquela rota
                    nome_exec, lucro = self.analisar_rotas(rota["p1"], rota["p2"], rota["p3"], t1_addr, t2_addr,
                                                              t3_addr)

                    # GATILHO DE EXECUÇÃO REAL
                    if lucro > 0.15 and self.melhor_config:
                        print(f"🚀 EXECUTANDO: {rota['nome']} | Lucro Est.: ${lucro:.2f}")


                        tx_hash = self.wallet.executar_arbitragem(
                            self.melhor_config["pools"],
                            self.melhor_config["direcoes"],
                            self.melhor_config["tokens"],
                            30.0
                        )

                        if tx_hash:
                            print(f"💰 Transação enviada! Hash: {tx_hash}")
                            time.sleep(15)  # Espera a rede processar antes de procurar a próxima
                            self.melhor_config = None  # Limpa para a próxima busca

                except Exception as e:
                    print(f"⚠️ Erro na rota {rota['nome']}: {e}")

            time.sleep(0.5)  # Pausa pequena entre ciclos completos