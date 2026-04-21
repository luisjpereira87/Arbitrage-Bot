import os
import time
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
from web3 import Web3

from pool_finder import PoolFinder
from wallet_manager import WalletManager
from web3_manager import Web3Manager

# --- CONFIGURAÇÃO INICIAL ---
# Usa os teus endereços mascarados aqui
WETH = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
USDC = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
ARB = "0x912CE59144191C1204E64559FE8253a0e49E6548"
LINK = "0xf97f4df75117a78c1A5a0DBb814Af92458539FB4"
WBTC = "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f"


class ArbitrageScanner:
    def __init__(self, rpc_url, wallet_private_key, config_file, capital_amount=100):

        self.web3_manager = Web3Manager()
        #self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.finder = PoolFinder(self.web3_manager)  # A classe que criámos
        self.config_file = config_file
        self.decimal_map = {info["addr"].lower(): info["dec"] for info in self.config_file["tokens"].values()}

        self.wallet = WalletManager(self.web3_manager)
        self.capital_amount  = capital_amount

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

        self.name_map = {info["addr"].lower(): nome for nome, info in self.config_file["tokens"].items()}

    @property
    def w3(self):
        """Retorna o Web3 atualizado do manager sempre que o bot precisar dele"""
        return self.web3_manager.w3

    def get_quote(self, pool_address, token_in, token_out):
        try:
            pool_contract = self.w3.eth.contract(
                address=self.w3.to_checksum_address(pool_address),
                abi=self.pool_abi
            )

            liquidez = pool_contract.functions.liquidity().call()

            t0 = pool_contract.functions.token0().call().lower()
            t1 = pool_contract.functions.token1().call().lower()
            fee = pool_contract.functions.fee().call()

            slot0 = pool_contract.functions.slot0().call()
            sqrtPriceX96 = slot0[0] if isinstance(slot0, (list, tuple)) else slot0

            d0 = self.get_token_decimals(t0)
            d1 = self.get_token_decimals(t1)

            # 1. PREÇO BASE (Unidades de T1 por 1 unidade de T0)
            # Fórmula: (sqrt / 2^96)^2 * (10^d0 / 10^d1)
            price_base = ((sqrtPriceX96 / (2 ** 96)) ** 2) * (10 ** d0 / 10 ** d1)

            # 2. LÓGICA DE DIREÇÃO
            if token_in.lower() == t0:
                # Estou a entrar com T0, vou receber T1
                # O preço já está em unidades de T1 por T0
                preco_final = price_base
                direcao_v3 = True
            else:
                # Estou a entrar com T1, vou receber T0
                # Preciso inverter o preço
                preco_final = 1 / price_base
                direcao_v3 = False

            if preco_final > 10 ** 15:
                return None

            if liquidez < 10 ** 15:
                return None

            if preco_final <= 0 or preco_final > 10 ** 12:  # Ninguém quer tokens que valham triliões
                return None

            return preco_final, direcao_v3, fee

        except Exception as e:
            if "429" in str(e) or "limit" in str(e).lower():
                self.web3_manager.rotate_rpc()
                # Opcional: tenta novamente uma vez após rodar
                return self.get_quote(pool_address, token_in, token_out)

            print(f"❌ Erro no get_quote!")
            print(f"   Pool: {pool_address}")
            print(f"   Token In: {token_in} (Tipo: {type(token_in)})")
            print(f"   Token Out: {token_out} (Tipo: {type(token_out)})")
            print(f"   Mensagem: {e}")
            return None

    def calcular_triangular(self, q1, d1, f1, q2, d2, f2, q3, d3, f3, tokens, info_pools):


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

        valor_final = (self.capital_amount * (q1 * t1) * (q2 * t2) * (q3 * t3)) * margem_seguranca
        lucro_liquido = valor_final - self.capital_amount - 0.25  # Desconto de $0.25 de Gas
        #if lucro_liquido > -0.10:
        #print(f"🔥 ALERTA DE LUCRO: ${lucro_liquido:.2f}")
        #print(f"   1. USDC -> ARB @ {q1}")
        #print(f"   2. ARB  -> GMX @ {q2}")
        #print(f"   3. GMX  -> USDC @ {q3}")

        # Passo a passo da montanha-russa de preços
        passo1 = self.capital_amount * (q1 * t1)  # USDC -> Token A
        passo2 = passo1 * (q2 * t2)  # Token A -> Token B
        passo3 = passo2 * (q3 * t3)  # Token B -> USDC final

        if lucro_liquido > -2:  # Só mostra se houver lucro interessante
            nomes = [self.name_map.get(addr.lower(), addr[:6]) for addr in tokens]

            print(f"\n--- 🛰️ ROTA DETETADA: {' -> '.join(nomes)} -> {nomes[0]} ---")

            precos = [q1, q2, q3]

            for i, pool in enumerate(info_pools):
                t_in = nomes[i]
                t_out = nomes[(i + 1) % 3]
                # Mostra: Passo [DEX]: TokenIn -> TokenOut @ Preço | Pool: 0x...
                print(f"  📍 Passo {i + 1} [{pool['dex']}]: {t_in} -> {t_out} @ {precos[i]:.8f} | Pool: {pool['addr']}")

            print(f"💰 Investimento: ${self.capital_amount:.2f} {nomes[0]}")
            print(f"➡️ Resultado Passo 1: {passo1:.6f} {nomes[1]}")
            print(f"➡️ Resultado Passo 2: {passo2:.6f} {nomes[2]}")
            print(f"⬅️ Resultado Passo 3: {passo3:.6f} {nomes[0]} (Final)")

            status = "✅ LUCRO" if lucro_liquido > 0 else "❌ PREJUÍZO"
            print(f"📊 Resultado: {status} de ${lucro_liquido:.4f}")
            print(f"--------------------------------------------------\n")

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
                        info_pools = [
                            {"dex": dex1, "addr": addr1},
                            {"dex": dex2, "addr": addr2},
                            {"dex": dex3, "addr": addr3}
                        ]


                        q1, d1, f1 = res1  # Preço e Direção (bool)
                        q2, d2, f2 = res2
                        q3, d3, f3 = res3

                        # 2. Calcula o lucro (baseado em $100 de capital)
                        lucro = self.calcular_triangular(q1, d1, f1, q2, d2, f2, q3, d3, f3, [t1_addr, t2_addr, t3_addr], info_pools)

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

        for tri in self.config_file["triangulos"]:
            t1, t2, t3 = tri
            addr1, addr2, addr3 = self.tokens[t1]["addr"], self.tokens[t2]["addr"], self.tokens[t3]["addr"]

            # 1. ROTA NORMAL (t1 -> t2 -> t3)
            pool_normal = {
                "nome": f"{t1}-{t2}-{t3}",
                "tokens": [addr1, addr2, addr3],
                "p1": {}, "p2": {}, "p3": {}
            }

            # 2. ROTA INVERSA (t1 -> t3 -> t2)
            pool_inversa = {
                "nome": f"{t1}-{t3}-{t2} (INV)",
                "tokens": [addr1, addr3, addr2],
                "p1": {}, "p2": {}, "p3": {}
            }

            for f in self.fees:
                # Obtemos as pools dos pares
                p1_pools = self.finder.get_pools(addr1, addr2, f)  # USDC-WETH
                p2_pools = self.finder.get_pools(addr2, addr3, f)  # WETH-ARB
                p3_pools = self.finder.get_pools(addr3, addr1, f)  # ARB-USDC

                # Preenche Normal (USDC -> WETH -> ARB -> USDC)
                pool_normal["p1"].update(p1_pools)
                pool_normal["p2"].update(p2_pools)
                pool_normal["p3"].update(p3_pools)

                # Preenche Inversa (USDC -> ARB -> WETH -> USDC)
                # O Passo 1 da inversa é a conexão USDC-ARB (p3_pools)
                pool_inversa["p1"].update(p3_pools)
                # O Passo 2 da inversa é a conexão ARB-WETH (p2_pools)
                pool_inversa["p2"].update(p2_pools)
                # O Passo 3 da inversa é a conexão WETH-USDC (p1_pools)
                pool_inversa["p3"].update(p1_pools)

            rotas_configuradas.append(pool_normal)
            rotas_configuradas.append(pool_inversa)

        print(f"✅ Mapeadas {len(rotas_configuradas)} rotas (Sentidos Normal e Inverso)")
        return rotas_configuradas

    def get_token_decimals(self, token_address):
        addr = token_address.lower()
        # Hardcode total: Se for este endereço, É 6 ou 8. Ponto final.
        if addr == "0xaf88d065e77c8cc2239327c5edb3a432268e5831": return 6  # USDC
        if addr == "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f": return 8  # WBTC

        return self.decimal_map.get(addr, 18)

    def processar_rota_individual(self, rota):
        """
        Esta função contém a lógica que tinhas dentro do 'for rota in rotas'
        """
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
            # Se o erro subir até aqui, também tentamos rodar
            if "429" in str(e) or "limit" in str(e).lower():
                self.web3_manager.rotate_rpc()
            else:
                print(f"⚠️ Erro na rota {rota['nome']}: {e}")

        except Exception as e:
            # Importante: prints dentro de threads podem ficar misturados,
            # mas para debug servem.
            pass


    def run_triangular(self):
        rotas = self.setup_triangulo()
        # Definimos quantos "trabalhadores" (threads) teremos.
        # 5 a 10 é um bom número para não seres bloqueado pelo RPC (Infura/Alchemy).
        max_workers = 3

        while True:
            # O ThreadPoolExecutor gere o paralelismo para nós
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Enviamos todas as rotas para serem analisadas ao mesmo tempo
                executor.map(self.processar_rota_individual, rotas)

            # Uma pausa mínima apenas para não colapsar o loop infinito
            time.sleep(0.1)


    def run_triangular_old(self):
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


                        """
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
                        """
                except Exception as e:
                    print(f"⚠️ Erro na rota {rota['nome']}: {e}")

            time.sleep(0.5)  # Pausa pequena entre ciclos completos