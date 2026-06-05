import asyncio
import base64
import logging
from abc import ABC
from typing import Optional

import aiohttp
from solana.rpc.commitment import Commitment
from solana.rpc.types import TxOpts, TokenAccountOpts
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction
from spl.token.instructions import get_associated_token_address, create_associated_token_account

from core.config.properties_base import PropertiesBase
from core.config.properties_multi import PropertiesMulti
from core.dclass.chains_enum import Chains
from core.web3.executors.executor_base import ExecutorBase
from core.web3.rpcs.solana_manager import SolanaManager


class SolanaExecutor(ExecutorBase, ABC):
    def __init__(self, solana_manager: SolanaManager, properties: PropertiesBase):
        self.solana_manager = solana_manager

        if properties.PRIVATE_KEY_WALLET_SOLANA is None:
            return

        self.wallet = Keypair.from_base58_string(properties.PRIVATE_KEY_WALLET_SOLANA)
        self.config = properties.CONFIG
        self.session: Optional[aiohttp.ClientSession] = None
        # self.priority_fee = 50000  # Lamports (~$0.01)
        self.priority_fee = 1500000

        asyncio.run(self.__mapear_e_preparar_tokens())

    @property
    def w3(self):
        """Acesso dinâmico ao RPC ativo no Manager"""
        return self.solana_manager.solana

    def _get_session(self) -> aiohttp.ClientSession:
        # Cria a sessão apenas quando for necessária, já dentro do ambiente assíncrono
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def get_token_balance(self, token_address: str, chain: Chains) -> int:
        try:
            if token_address == "So11111111111111111111111111111111111111112":
                res_sol = await self.solana_manager.solana.get_balance(self.wallet.pubkey())
                return res_sol.value
            else:
                pubkey_token = Pubkey.from_string(token_address)
                opts = TokenAccountOpts(mint=pubkey_token)

                res_token = await  self.solana_manager.solana.get_token_accounts_by_owner(
                    self.wallet.pubkey(),
                    opts=opts,
                    commitment=Commitment("processed")
                )

                if not res_token.value:
                    return 0

                # Pegamos nos dados brutos (account.data)
                account_data = res_token.value[0].account.data

                # Se o RPC não parseou, account_data será tratado como bytes
                # O layout de um SPL Token Account é fixo na Solana:
                # Bytes 0-32: Mint
                # Bytes 32-64: Owner
                # Bytes 64-72: Amount (u64, Little Endian) <--- É ISTO QUE QUEREMOS

                import struct
                raw_bytes = bytes(account_data)

                if len(raw_bytes) >= 72:
                    # Extraímos os 8 bytes do offset 64 ao 72
                    amount = struct.unpack("<Q", raw_bytes[64:72])[0]
                    return int(amount)

                return 0

        except Exception as e:
            if any(x in str(e) for x in ["401", "429", "403", "500", "503", "timeout", "unauthorized"]):
                self.solana_manager.rotate_rpc()
                return await self.get_token_balance(token_address, chain)  # Tenta de novo com novo RPC
            logging.error(f"❌ Erro ao ler saldo na Solana: {e}")
            return 0

    async def send_transaction(self, pools_list: list[str], dir_list: list[bool], tokens_list: list[str],
                               amount_usd: float, chain: Chains, quote_data: dict | None):

        # 1. TRATAMENTO DO VALOR E SALDO
        val_in_raw = int(amount_usd)
        t_address = tokens_list[0]
        w_address = str(self.wallet.pubkey())

        try:
            contract_balance = await self.get_token_balance(t_address, chain)
        except Exception as e:
            print(f"❌ [PROD] Falha ao ler saldo no nó: {e}")
            return None

        if contract_balance < val_in_raw:
            diff = val_in_raw - contract_balance
            if diff < 100000:  # Tolerância de poeira (micro-ajuste)
                val_in_raw = contract_balance
            else:
                print(f"❌ [PROD] Saldo insuficiente abortado: {contract_balance} < {val_in_raw}")
                return None

        # Endpoints de contingência
        jupiter_endpoints = [
            "https://public.jupiterapi.com/swap",
            "https://api.jup.ag/swap/v6/swap"  # Endpoint principal v6 oficial
        ]

        max_jupiter_retries = 3
        for attempt in range(max_jupiter_retries):
            try:
                if not quote_data:
                    print("❌ [PROD] Sem dados de Quote válidos.")
                    return None

                # --- 2. CONSTRUIR SWAP JUPITER (SESSÃO PROTEGIDA) ---
                swap_url = jupiter_endpoints[attempt % len(jupiter_endpoints)]
                payload = {
                    "quoteResponse": quote_data,
                    "userPublicKey": w_address,
                    "wrapAndUnwrapSol": True,
                    "dynamicComputeUnitLimit": True,
                    "prioritizationFeeLamports": self.priority_fee,
                    "feeAccount": w_address
                }

                # 🛠️ RESOLVIDO: 'async with' limpa as conexões da memória automaticamente e evita o erro de Unclosed Session
                session = self._get_session()
                async with session.post(swap_url, json=payload, timeout=2.0) as resp:
                    if resp.status != 200:
                        print(f"⚠️ [JUPITER] Erro HTTP {resp.status} em {swap_url}")
                        if resp.status in [429, 500, 503]:
                            raise Exception(f"HTTP_{resp.status}")
                        return None
                    swap_res = await resp.json()
                    tx_base64 = swap_res['swapTransaction']

                # --- 3. ASSINATURA NATIVA (MÉTODO COMPILADO EM RUST) ---
                raw_tx = base64.b64decode(tx_base64)
                v_tx_jupiter = VersionedTransaction.from_bytes(raw_tx)

                # Compilação e fusão de assinaturas em baixo nível (Rust)
                v_tx = VersionedTransaction(v_tx_jupiter.message, [self.wallet])

                # --- 4. ENVIO DE ALTA VELOCIDADE (MÓDULO PROD) ---
                # 🚀 OPTIMIZAÇÃO: skip_preflight=True desliga a simulação e ganha centenas de milissegundos críticos
                opts = TxOpts(skip_preflight=True, preflight_commitment=Commitment("processed"))
                try:
                    res = await self.solana_manager.solana.send_raw_transaction(bytes(v_tx), opts=opts)
                    tx_hash = str(res.value)
                except Exception as node_error:
                    print(f"🚨 [Heliux] Transação rejeitada no pipeline de entrada: {node_error}")
                    return None

                # --- 5. POLLING DE CONFIRMAÇÃO OTIMIZADO ---
                confirmed = False
                for check_attempt in range(30):  # 15 Segundos de tolerância agressiva para arbitragem
                    await asyncio.sleep(0.5)
                    try:
                        status_resp = await self.solana_manager.solana.get_signature_statuses([res.value])
                        if status_resp.value and status_resp.value[0] is not None:
                            status = status_resp.value[0]

                            if status.err is not None:
                                print(f"❌ [REVERT] Transação falhou internamente na chain: {status.err}")
                                return None

                            if status.confirmation_status is not None:
                                confirmed = True
                                break
                    except Exception:
                        continue  # Suporta falhas temporárias de rede do RPC

                if not confirmed:
                    print(f"⚠️ [DROP] Transação expirou sem entrar no bloco. Abortando Hedge.")
                    return None

                return tx_hash

            except Exception as e:
                error_str = str(e).lower()
                is_network_error = any(x in error_str for x in [
                    "clientconnectorerror", "dns", "timeout", "cannot connect",
                    "401", "429", "403", "500", "503", "unauthorized", "http_"
                ])

                if is_network_error and attempt < max_jupiter_retries - 1:
                    await asyncio.sleep(0.1)  # Pequeno respiro antes de mudar de rota
                    continue

                elif is_network_error:
                    # 🛠️ RESOLVIDO: Em vez de recursão infinita, rotacionamos o RPC e devolvemos None
                    # para o loop principal reiniciar de forma limpa, preservando a Stack de memória.
                    print(f"🚨 [PROD] Falha total de rede. Rotacionando RPC para a próxima oportunidade.")
                    self.solana_manager.rotate_rpc()
                    return None

                print(f"❌ [CRÍTICO] Erro de lógica não contornável: {e}")
                return None

    async def send_transaction_old(self, pools_list: list[str], dir_list: list[bool], tokens_list: list[str],
                                   amount_usd: float, chain: Chains, quote_data: dict | None):

        # 1. TRATAMENTO DO VALOR
        val_in_raw = int(amount_usd)

        # --- DEBUG/REPORTE ESTILO MAINNET ---
        t_address = tokens_list[0]
        w_address = str(self.wallet.pubkey())

        contract_balance = await self.get_token_balance(t_address, chain)

        print(f"\n--- 🕵️ RELATÓRIO DE EXECUÇÃO SOLANA ---")
        print(f"📍 Wallet: {w_address}")
        print(f"🪙 Token: {t_address}")
        print(f"🔢 Saldo Bruto: {contract_balance}")
        print(f"💰 Saldo Formatado: {contract_balance / 10 ** 6:.4f}")
        print(f"📉 Pedido p/ Swap: {val_in_raw}")

        if contract_balance < val_in_raw:
            diff = val_in_raw - contract_balance
            if diff < 100000:
                print(f"⚠️ Diferença mínima ({diff}). Ajustando para saldo total.")
                val_in_raw = contract_balance
            else:
                print(f"❌ ERRO: Saldo insuficiente real! ({contract_balance} < {val_in_raw})")
                return None
        print(f"---------------------------------\n")

        # Lista de endpoints de contingência da Jupiter (Plano A e Plano B)
        jupiter_endpoints = [
            "https://public.jupiterapi.com/swap",
            "https://api.jup.ag/swap/v6/swap"
        ]

        max_jupiter_retries = 3
        for attempt in range(max_jupiter_retries):
            try:
                if not quote_data:
                    print("❌ ERRO: quote_data da Jupiter é necessário para execução na Solana.")
                    return None

                swap_url = jupiter_endpoints[attempt % len(jupiter_endpoints)]

                payload = {
                    "quoteResponse": quote_data,
                    "userPublicKey": w_address,
                    "wrapAndUnwrapSol": True,
                    "dynamicComputeUnitLimit": True,
                    "prioritizationFeeLamports": self.priority_fee,
                    "feeAccount": w_address,
                    "slippageBps": 150
                }

                session = self._get_session()
                async with session.post(swap_url, json=payload, timeout=2.0) as resp:
                    if resp.status != 200:
                        print(f"⚠️ Falha ao construir swap Jupiter em {swap_url}: {await resp.text()}")
                        if resp.status in [429, 500, 503]:
                            raise Exception(f"HTTP_{resp.status}")
                        return None
                    swap_res = await resp.json()
                    tx_base64 = swap_res['swapTransaction']

                # 3. Assinatura (Corrigido para passar bytes puros)
                raw_tx = base64.b64decode(tx_base64)
                v_tx = VersionedTransaction.from_bytes(raw_tx)

                # Injetar blockhash fresco contornando a propriedade read-only
                try:
                    recent_blockhash_resp = await self.solana_manager.solana.get_latest_blockhash()
                    fresh_blockhash = recent_blockhash_resp.value.blockhash

                    # Acedemos ao campo diretamente através do objeto de mensagem subjacente
                    # que permite a alteração antes da serialização final
                    if hasattr(v_tx.message, 'recent_blockhash'):
                        # Para Message (Legacy)
                        object.__setattr__(v_tx.message, 'recent_blockhash', fresh_blockhash)
                    else:
                        # Para MessageV0
                        object.__setattr__(v_tx.message, 'recent_blockhash', fresh_blockhash)

                    print(f"🔄 Blockhash forçado via setattr com sucesso: {fresh_blockhash}")
                except Exception as bh_error:
                    print(f"⚠️ Não foi possível atualizar o blockhash: {bh_error}")

                # Assinar os bytes corrigidos
                signature = self.wallet.sign_message(bytes(v_tx.message))
                v_tx.signatures = [signature]

                # 4. Envio (MODIFICADO COM TRY/EXCEPT PARA CAPTURAR O VEREDICTO DA HELIUS)
                opts = TxOpts(skip_preflight=True, preflight_commitment=Commitment("processed"))
                try:
                    res = await self.solana_manager.solana.send_raw_transaction(bytes(v_tx), opts=opts)
                    # Se o nó aceitar, extraímos a hash normalmente
                    tx_hash = str(res.value)
                    print(f"🚀 Enviado Solana! Hash: {tx_hash}")
                except Exception as node_error:
                    # Se a Helius rejeitar os bytes na hora por saldo de SOL insuficiente ou Blockhash velho
                    print(f"🚨 O nó da Helius rejeitou a transação imediatamente! Erro: {node_error}")
                    return None

                print(f"⏳ Aguardando confirmation no bloco da Solana...")

                # 🔒 SEGURANÇA CRÍTICA: Validação ativa por Polling
                confirmed = False
                for check_attempt in range(40):
                    await asyncio.sleep(0.5)
                    try:
                        status_resp = await self.solana_manager.solana.get_signature_statuses([res.value])

                        if status_resp.value and status_resp.value[0] is not None:
                            status = status_resp.value[0]

                            if status.err is not None:
                                print(f"❌ Transação fez REVERT na Solana! Erro interno: {status.err}")
                                return None

                            if status.confirmation_status is not None:
                                print(f"✅ Transação CONFIRMADA no bloco! Status: {status.confirmation_status}")
                                confirmed = True
                                break
                    except Exception as status_error:
                        continue

                if not confirmed:
                    print(f"⚠️ Transação não apareceu no bloco a tempo (Dropada/Expirada). Abortando Hedge na HL.")
                    return None

                return tx_hash

            except Exception as e:
                error_str = str(e).lower()

                is_network_error = any(x in error_str for x in [
                    "clientconnectorerror", "dns", "timeout", "cannot connect",
                    "401", "429", "403", "500", "503", "unauthorized", "http_"
                ])

                if is_network_error and attempt < max_jupiter_retries - 1:
                    next_url = jupiter_endpoints[(attempt + 1) % len(jupiter_endpoints)]
                    print(f"⚠️ [TENTATIVA {attempt + 1}/{max_jupiter_retries}] Falha de rede/DNS na Jupiter: {e}.")
                    print(f"🔄 Alternando rota para: {next_url} em 150ms...")
                    await asyncio.sleep(0.15)
                    continue

                elif is_network_error:
                    print(f"🚨 Esgotadas as rotas de contingência da Jupiter. Rotacionando RPC e reiniciando envio...")
                    self.solana_manager.rotate_rpc()
                    return await self.send_transaction(pools_list, dir_list, tokens_list, amount_usd, chain, quote_data)

                print(f"❌ Erro crítico no envio Solana (Não corrigível automaticamente): {e}")
                return None

    async def send_transaction_(self, pools_list: list[str], dir_list: list[bool], tokens_list: list[str],
                                amount_usd: float, chain: Chains, quote_data: dict | None):

        # 1. TRATAMENTO DO VALOR
        val_in_raw = int(amount_usd)

        # --- DEBUG/REPORTE ESTILO MAINNET ---
        t_address = tokens_list[0]
        w_address = str(self.wallet.pubkey())

        contract_balance = await self.get_token_balance(t_address, chain)

        print(f"\n--- 🕵️ RELATÓRIO DE EXECUÇÃO SOLANA ---")
        print(f"📍 Wallet: {w_address}")
        print(f"🪙 Token: {t_address}")
        print(f"🔢 Saldo Bruto: {contract_balance}")
        print(f"💰 Saldo Formatado: {contract_balance / 10 ** 6:.4f}")
        print(f"📉 Pedido p/ Swap: {val_in_raw}")

        if contract_balance < val_in_raw:
            diff = val_in_raw - contract_balance
            if diff < 100000:
                print(f"⚠️ Diferença mínima ({diff}). Ajustando para saldo total.")
                val_in_raw = contract_balance
            else:
                print(f"❌ ERRO: Saldo insuficiente real! ({contract_balance} < {val_in_raw})")
                return None
        print(f"---------------------------------\n")

        try:
            if not quote_data:
                print("❌ ERRO: quote_data da Jupiter é necessário para execução na Solana.")
                return None

            # 2. Construção da Transação via Jupiter
            swap_url = "https://quote-api.jup.ag/v6/swap"
            payload = {
                "quoteResponse": quote_data,
                "userPublicKey": w_address,
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
                "prioritizationFeeLamports": self.priority_fee,

                "skipUserAccountsRpcCalls": False,
                "asLegacyTransaction": False
            }

            session = self._get_session()
            async with session.post(swap_url, json=payload) as resp:
                if resp.status != 200:
                    print(f"⚠️ Falha ao construir swap Jupiter: {await resp.text()}")
                    return None
                swap_res = await resp.json()
                tx_base64 = swap_res['swapTransaction']

            # 3. Assinatura (Corrigido para passar bytes puros)
            raw_tx = base64.b64decode(tx_base64)
            v_tx = VersionedTransaction.from_bytes(raw_tx)

            # Passamos a mensagem convertida em bytes puros
            signature = self.wallet.sign_message(bytes(v_tx.message))
            v_tx.signatures = [signature]

            # 4. Envio
            opts = TxOpts(skip_preflight=False, preflight_commitment=Commitment("processed"))
            res = await  self.solana_manager.solana.send_raw_transaction(bytes(v_tx), opts=opts)

            tx_hash = str(res.value)
            print(f"🚀 Enviado Solana! Hash: {tx_hash}")

            return tx_hash

        except Exception as e:
            if any(x in str(e) for x in ["401", "429", "403", "500", "503", "timeout", "unauthorized"]):
                self.solana_manager.rotate_rpc()
                return await self.send_transaction(pools_list, dir_list, tokens_list, amount_usd, chain, quote_data)

            print(f"❌ Erro crítico no envio Solana: {e}")
            return None

    async def is_swap_viable(self, token_in: str, token_out: str, amount_in_usd: float, expected_out_units: float,
                             fee: int, tolerance: float, chain: Chains, quote_data: dict | None) -> tuple[bool, float]:
        try:
            t_in_info = self.config.tokens_by_address.get(token_in.lower())
            t_out_info = self.config.tokens_by_address.get(token_out.lower())

            dec_in = t_in_info.decimals if t_in_info else 9
            dec_out = t_out_info.decimals if t_out_info else 6

            amount_in_raw = int(amount_in_usd * 10 ** dec_in)
            balance_raw = await self.get_token_balance(token_in, chain)

            if balance_raw < amount_in_raw:
                logging.warning(f"❌ [SOLANA] Saldo insuficiente: {balance_raw} < {amount_in_raw}")
                return False, 0

            if not quote_data:
                logging.error("❌ Erro: quote_data da Jupiter é obrigatório para validação na Solana")
                return False, 0

            # O que a Jupiter nos garante dar na blockchain real
            amount_out_raw = int(quote_data['outAmount'])
            amount_out_real = amount_out_raw / 10 ** dec_out

            # Aplicamos a tolerância passada (ex: 0.003 para 0.3% de folga)
            # Se o Short pede 12.825, com 0.3% aceitamos até 12.787
            min_acceptable = expected_out_units * (1 - tolerance)

            if amount_out_real < min_acceptable:
                # IMPRIMIMOS O MIN_ACCEPTABLE REAL COM DESCONTO PARA SABERES O LIMITE VERDADEIRO
                logging.warning(
                    f"⚠️ Swap REJEITADO (SOLANA): Real {amount_out_real:.6f} < Min Tolerável {min_acceptable:.6f} (HL pedia: {expected_out_units:.6f})")
                return False, amount_out_real

            logging.info(
                f"✅ Swap validado (SOLANA): Receberás aprox. {amount_out_real:.6f} {t_out_info.symbol if t_out_info else ''} (Min aceitável era: {min_acceptable:.6f})")
            return True, amount_out_real

        except Exception as e:
            logging.error(f"❌ Erro na validação Solana: {e}")
            return False, 0

    async def is_swap_viable_(self, token_in: str, token_out: str, amount_in_usd: float, expected_out_units: float,
                              fee: int, tolerance: float, chain: Chains, quote_data: dict | None) -> tuple[bool, float]:
        try:
            t_in_info = self.config.tokens_by_address.get(token_in.lower())
            t_out_info = self.config.tokens_by_address.get(token_out.lower())

            dec_in = t_in_info.decimals if t_in_info else 9
            dec_out = t_out_info.decimals if t_out_info else 6

            amount_in_raw = int(amount_in_usd * 10 ** dec_in)
            balance_raw = await self.get_token_balance(token_in, chain)

            if balance_raw < amount_in_raw:
                logging.warning(f"❌ [SOLANA] Saldo insuficiente: {balance_raw} < {amount_in_raw}")
                return False, 0

            if not quote_data:
                logging.error("❌ Erro: quote_data da Jupiter é obrigatório para validação na Solana")
                return False, 0

            amount_out_raw = int(quote_data['outAmount'])
            amount_out_real = amount_out_raw / 10 ** dec_out

            min_acceptable = expected_out_units * (1 - tolerance)

            if amount_out_real < min_acceptable:
                logging.warning(
                    f"⚠️ Swap REJEITADO (SOLANA): Real {amount_out_real:.6f} < Min {min_acceptable:.6f}")
                return False, amount_out_real

            logging.info(
                f"✅ Swap validado (SOLANA): Receberás aprox. {amount_out_real:.6f} {t_out_info.symbol if t_out_info else ''}")
            return True, amount_out_real

        except Exception as e:
            logging.error(f"❌ Erro na validação Solana: {e}")
            return False, 0

    async def check_and_approve_executor(self, amount_usd: float, chain: Chains) -> bool:
        return False

    async def get_usdc_balance(self, chain: Chains) -> int:
        return await self.get_token_balance("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", chain)

    async def get_gas_cost_usd(self, eth_price: (float | None), chain: Chains) -> float:
        return 0.0

    async def __mapear_e_preparar_tokens(self):
        """
        MÉTODO PRIVADO: Filtra a configuração multi-chain, encontra os tokens da Solana,
        mapeia as suas chaves públicas e garante que as ATAs existem na blockchain.
        """
        carteira_pubkey = self.wallet.pubkey()

        # 1. Usamos um dicionário (ou set) para garantir que apenas guardamos tokens ÚNICOS
        # Evita verificar o mesmo token várias vezes se ele estiver em múltiplos pares
        tokens_solana_unicos = {}

        for symbol_a, symbol_b, hl_pair, chain in self.config.multi_chain:
            # Só nos interessam os pares que rodam na Solana
            if chain == 'solana':
                token_a_data = self.config.tokens.get(symbol_a)
                token_b_data = self.config.tokens.get(symbol_b)

                if token_a_data is None or token_b_data is None:
                    print(f"⚠️ [AVISO] Dados em falta na config para o par {symbol_a}-{symbol_b}")
                    continue

                # Assumindo que na tua config guardas o endereço no campo 'address' ou 'mint'
                # Ajusta o .get('address') para bater com o formato do teu JSON/Dicionário
                if symbol_a not in tokens_solana_unicos:
                    tokens_solana_unicos[symbol_a] = token_a_data.address
                if symbol_b not in tokens_solana_unicos:
                    tokens_solana_unicos[symbol_b] = token_b_data.address

        if not tokens_solana_unicos:
            print("🟩 [INICIALIZAÇÃO] Nenhum par de Solana encontrado na configuração multi-chain.")
            return

        print(
            f"\n🛠️  [INICIALIZAÇÃO] Detetados {len(tokens_solana_unicos)} tokens Solana únicos. A verificar gavetas...")

        # 2. Agora sim, corremos o processo de validação/criação apenas para os tokens únicos filtrados
        for simbolo, mint_str in tokens_solana_unicos.items():
            if not mint_str:
                continue

            mint_pubkey = Pubkey.from_string(mint_str)
            ata_teorica = get_associated_token_address(carteira_pubkey, mint_pubkey)
            # self.atas_mapeadas[simbolo] = ata_teorica

            try:
                resposta = await self.solana_manager.solana.get_account_info(ata_teorica)

                if resposta.value is None:
                    print(f"⚠️  [ATA] {simbolo} não tem conta ativa. A abrir gaveta na blockchain...")

                    instrucao_criar_ata = create_associated_token_account(
                        payer=carteira_pubkey,
                        owner=carteira_pubkey,
                        mint=mint_pubkey
                    )

                    recent_blockhash_resp = await self.solana_manager.solana.get_latest_blockhash()
                    blockhash = recent_blockhash_resp.value.blockhash

                    mensagem = Message.new_with_blockhash(
                        [instrucao_criar_ata],
                        carteira_pubkey,
                        blockhash
                    )

                    tx = VersionedTransaction(mensagem, [self.wallet])

                    response = await self.solana_manager.solana.send_raw_transaction(bytes(tx))
                    await self.solana_manager.solana.confirm_transaction(response.value)
                    print(f"✅ [ATA] Conta para {simbolo} criada com sucesso!")

                    await asyncio.sleep(1)
                else:
                    print(f"🟩 [ATA] {simbolo} já tem conta ativa. Endereço: {ata_teorica}")

            except Exception as e:
                print(f"❌ Erro ao verificar ou criar a conta para {simbolo}: {e}")
                continue

        print("🟩 [INICIALIZAÇÃO] Filtro e preparação de tokens concluídos com sucesso!\n")


class TokenInfo:
    def __init__(self, symbol, decimals):
        self.symbol = symbol
        self.decimals = decimals


# --- FUNÇÃO PRINCIPAL DE TESTE ---
async def main():
    # ⚠️ ADICIONA AS TUAS CONFIGURAÇÕES DE TESTE AQUI ⚠️
    RPC_URL = "https://api.mainnet-beta.solana.com"  # Ou o teu link Helius/Quicknode

    USDC_SOLANA = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    SOL_NATIVA = "So11111111111111111111111111111111111111112"

    print("⚡ Inicializando sessão assíncrona...")
    async with aiohttp.ClientSession() as session:
        # Instancia o teu executor
        properties = PropertiesMulti()
        solana_manager = SolanaManager()
        executor = SolanaExecutor(
            solana_manager,
            properties
        )

        print(f"🔑 Chave Pública derivada: {executor.wallet.pubkey()}")
        print("📡 Conectando à rede para ler saldos...\n")

        # Teste 1: Buscar saldo de SOL (Nativo)
        saldo_sol_raw = await executor.get_token_balance(SOL_NATIVA)
        saldo_sol_formatado = saldo_sol_raw / 10 ** 9
        print(f" Balance de SOL: {saldo_sol_formatado:.4f} SOL ({saldo_sol_raw} lamports)")

        # Teste 2: Buscar saldo de USDC (SPL Token via descompactação binária struct)
        saldo_usdc_raw = await executor.get_token_balance(USDC_SOLANA)
        saldo_usdc_formatado = saldo_usdc_raw / 10 ** 6
        print(f" Balance de USDC: ${saldo_usdc_formatado:.2f} USDC ({saldo_usdc_raw} raw)")

        print("\n🏁 Teste de comunicação concluído.")

        # Fecha a conexão do RPC de forma limpa
        await solana_manager.solana.close()


if __name__ == "__main__":
    asyncio.run(main())
