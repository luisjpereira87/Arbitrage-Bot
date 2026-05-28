import asyncio
import logging
import socket
import time
from typing import Optional

import aiohttp

from core.dclass.dex_quote_dclass import DexQuote


class JupiterClient:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.last_jup_call = 0.0

        self.jupiter_urls = [
            "https://api.jup.ag/quote",          # <-- 1ª Opção: O URL correto da doc (Sem o v6 no meio para evitar o 404)
            "https://quote-api.jup.ag/v6/quote"  # <-- 2ª Opção: Mantém-se como fallback caso o IP do Railway seja libertado
        ]

    async def init_session(self) -> aiohttp.ClientSession:
        """Garante a inicialização e devolve a sessão ativa para o type checker."""
        if self.session is None or self.session.closed:
            connector = aiohttp.TCPConnector(family=socket.AF_INET, ttl_dns_cache=300)
            self.session = aiohttp.ClientSession(connector=connector)
        return self.session

    async def close(self):
        """Fecha a sessão de forma limpa ao desligar o bot."""
        if self.session and not self.session.closed:
            await self.session.close()

    async def _rate_limiter_buffer(self):
        """A tua barreira estática de 400ms para evitar bans de IP."""
        now = time.time()
        elapsed_time = now - self.last_jup_call
        if elapsed_time < 0.4:
            await asyncio.sleep(0.4 - elapsed_time)
        self.last_jup_call = time.time()

    async def get_quote(self, addr_in: str, addr_out: str, amount_in_human: float, decimals_in: int,
                        decimals_out: int, exclude_direct_route=False,
                        restrict_intermediate_tokens=False) -> Optional[DexQuote]:
        """
        Consulta a API da Jupiter tratando internamente Rate Limits, Timeouts e parsing de rotas.
        """
        if amount_in_human <= 0:
            return None

        session = await self.init_session()
        await self._rate_limiter_buffer()

        # Conversão para unidade base da blockchain (inteiro em string)
        amount_in_base = int(amount_in_human * (10 ** decimals_in))

        params = {
            "inputMint": addr_in,
            "outputMint": addr_out,
            "amount": str(amount_in_base),
            "slippageBps": "10",
            # "excludeDirectRoute": "false"
            "excludeDirectRoute": "true" if exclude_direct_route else "false",
            "restrictIntermediateTokens": "true" if restrict_intermediate_tokens else "false"
        }
        headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}

        # Loop de Failover nativo
        for url in self.jupiter_urls:
            try:

                async with session.get(url, params=params, headers=headers, timeout=4) as resp:

                    if resp.status == 429:
                        logging.warning(f"⚠️ Jupiter Rate Limit (429) em: {url}. Tentando failover imediato...")
                        continue

                    if resp.status != 200:
                        logging.warning(f"⚠️ Jupiter HTTP {resp.status} em: {url}. Pulando...")
                        continue

                    data = await resp.json()

                    # 1. Parsing do Preço Líquido (outAmount total)
                    out_raw = int(data['outAmount'])
                    amount_out_human = out_raw / (10 ** decimals_out)
                    raw_price_dex_net = amount_out_human / amount_in_human

                    # 2. Parsing Cirúrgico do Preço Bruto (Último Passo da Rota)
                    try:
                        route_plan = data.get('routePlan', [])
                        if route_plan:
                            last_step = route_plan[-1]
                            out_raw_gross = int(last_step.get('swapInfo', {}).get('outAmount', 0))
                        else:
                            out_raw_gross = 0

                        if out_raw_gross > 0:
                            amount_out_human_gross = out_raw_gross / (10 ** decimals_out)
                            raw_price_dex_gross = amount_out_human_gross / amount_in_human
                        else:
                            raw_price_dex_gross = raw_price_dex_net
                    except Exception:
                        raw_price_dex_gross = raw_price_dex_net

                    return DexQuote(
                        price_dex_gross=raw_price_dex_gross,
                        price_dex_net=raw_price_dex_net,
                        direction=True,
                        fee_dex_ppm=1000,  # Padrão estatístico mapeado por ti
                        data_quote=data
                    )

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logging.warning(f"🌐 Falha de conexão na URL {url}: {type(e).__name__}. Tentando failover...")
                continue

        logging.error("❌ Todas as URLs de cotação da Jupiter falharam neste ciclo.")
        return None
