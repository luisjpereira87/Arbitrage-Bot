import random

import base58
import httpx
from solders.message import Message
from solders.pubkey import Pubkey
from solders.solders import Keypair
from solders.system_program import transfer, TransferParams
from solders.transaction import VersionedTransaction


class JitoExecutor:
    def __init__(self, keypair: Keypair):
        self.keypair = keypair
        # Endereço do Block Engine do Jito em Amesterdão (mais perto de Portugal/Europa)
        self.jito_url = "https://amsterdam.mainnet.block-engine.jito.wtf/api/v1/bundles"

        self.jito_urls = [
            "https://amsterdam.mainnet.block-engine.jito.wtf/api/v1/bundles",  # Principal (Europa)
            "https://frankfurt.mainnet.block-engine.jito.wtf/api/v1/bundles",  # Secundário (Europa)
            "https://ny.mainnet.block-engine.jito.wtf/api/v1/bundles",  # Alternativo (EUA Este)
            "https://mainnet.block-engine.jito.wtf/api/v1/bundles"  # Geral (Default)
        ]

        self.jito_tip_accounts = [
            Pubkey.from_string("ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49"),
            Pubkey.from_string("Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY"),
            Pubkey.from_string("HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe"),
            Pubkey.from_string("DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh"),
            Pubkey.from_string("96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5"),
            Pubkey.from_string("ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt"),
            Pubkey.from_string("3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT"),
            Pubkey.from_string("DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL")
        ]

        # self.jito_tip_account = random.choice(self.JITO_TIP_ACCOUNTS)

    async def send_jito_bundle(self, v_tx_jupiter_signed, recent_blockhash, tip_lamports=150000):
        """
        Recebe a transação da Jupiter já assinada (v_tx), cria a transação da gorjeta,
        agrupa ambas num Bundle e envia para o Block Engine do Jito.
        """
        # jito_url = "https://amsterdam.mainnet.block-engine.jito.wtf/api/v1/bundles"
        jito_tip_account = random.choice(self.jito_tip_accounts)

        try:
            # 1. Cria a instrução de transferência para a gorjeta do Jito
            tip_instruction = transfer(
                TransferParams(
                    from_pubkey=self.keypair.pubkey(),
                    to_pubkey=jito_tip_account,
                    lamports=tip_lamports
                )
            )

            # 2. Cria e assina a transação da gorjeta (Formato Legacy clássico, sem erros)
            tip_message = Message.new_with_blockhash(
                instructions=[tip_instruction],
                payer=self.keypair.pubkey(),
                blockhash=recent_blockhash
            )
            tip_tx = VersionedTransaction(tip_message, [self.keypair])

            # 3. Serializa ambas as transações assinadas em formato Base58 (Exigência do Jito)
            # v_tx_jupiter_signed é a 'v_tx' que já assinaste no teu método principal
            serialized_jup = base58.b58encode(bytes(v_tx_jupiter_signed)).decode('utf-8')
            serialized_tip = base58.b58encode(bytes(tip_tx)).decode('utf-8')

            # 4. Monta o Payload JSON-RPC oficial do Jito
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendBundle",
                "params": [[serialized_jup, serialized_tip]]
            }

            # 5. Envia diretamente para o Block Engine de Amesterdão
            async with httpx.AsyncClient() as client:
                for url in self.jito_urls:
                    try:
                        response = await client.post(url, json=payload, timeout=10.0)
                        result = response.json()

                        if "result" in result:
                            print(f"🚀 [JITO] Bundle enviado com sucesso! ID: {result['result']}")
                            return result['result']
                        else:
                            print(f"❌ [JITO] Erro no Block Engine: {result}")
                            return None

                    except Exception as url_err:
                        print(f"⚠️ Falha de conexão com {url}: {url_err}")
                        continue

        except Exception as e:
            print(f"❌ [JITO] Falha crítica no método send_jito_bundle: {e}")
            return None
