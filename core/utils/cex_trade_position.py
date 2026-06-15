import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from core.dclass.cex_active_position_dclass import CexActivePosition
from core.dclass.cex_type_enum import CexType

load_dotenv()

# 1. Encontrar a Raiz do Projeto de forma robusta
# Sobe 3 níveis a partir de commons/utils/trade_position.py para chegar à raiz
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 2. Definir o Diretório de Modelos
# No Railway, vamos definir uma variável STORAGE_PATH = /data/ no painel
# Se não existir, ele cria uma pasta 'models_storage' na raiz do projeto
STORAGE_PATH = os.getenv("STORAGE_PATH", os.path.join(BASE_DIR, "models_storage"))

# 3. Criar a pasta se não existir (evita erros de FileNotFoundError)
if not os.path.exists(STORAGE_PATH):
    os.makedirs(STORAGE_PATH, exist_ok=True)


class CexTradePosition:
    @staticmethod
    def get_file_path(symbol: str):
        # Transforma "ARB/USDC" em "ARB_USDC.json"
        clean_symbol = symbol.replace("/", "_")
        return Path(os.path.join(STORAGE_PATH, f'cex_pos_{clean_symbol}.json'))

    @staticmethod
    def load_all_positions():
        """Varre a pasta e carrega todas as posições ativas num dicionário."""
        positions = {}
        if not os.path.exists(STORAGE_PATH):
            os.makedirs(STORAGE_PATH)
            return positions

        for file in os.listdir(STORAGE_PATH):
            if file.startswith("cex_pos_") and file.endswith(".json"):
                try:
                    file_path = Path(os.path.join(STORAGE_PATH, file))
                    with file_path.open("r", encoding="utf-8") as f:
                        data = json.load(f)

                    pos = CexActivePosition(**data)
                    # Usamos o símbolo base (ex: ARB) como chave do dicionário
                    base_symbol = pos.symbol.split('/')[0]
                    positions[base_symbol] = pos
                except Exception as e:
                    logging.error(f"❌ Erro ao carregar posição do ficheiro {file}: {e}")

        if positions:
            logging.info(f"📂 {len(positions)} posições carregadas da storage: {list(positions.keys())}")
        return positions

    @staticmethod
    def save_position(position: CexActivePosition):
        """Guarda a posição num ficheiro específico para o seu símbolo."""
        file_path = CexTradePosition.get_file_path(position.symbol)
        with file_path.open("w", encoding="utf-8") as f:
            f.write(position.model_dump_json(indent=4))
        logging.info(f"💾 Posição de {position.symbol} guardada em {file_path.name}")

    @staticmethod
    def clear_position(symbol: str):
        """Elimina o ficheiro específico de um ativo após o fecho do trade."""
        file_path = CexTradePosition.get_file_path(symbol)
        if file_path.exists():
            file_path.unlink()
            logging.info(f"🧹 Arquivo {file_path.name} removido (Trade concluído).")

    @staticmethod
    def check_exit_profitability_(pos: CexActivePosition, current_hl_price: float,
                                  current_lighter_price: float) -> float:
        """Calcula o lucro líquido real simulando o fecho simultâneo baseado no saldo combinado."""
        # Taxas padrão (HL: 0.035%, Lighter: 0.00%, Gas estimado: 0.05 USDC)
        hl_fee = 0.00035
        lighter_gas_cost = 0.05
        qty = pos.qty_pair  # Garante que mapeias a quantidade do ativo guardada no teu DClass

        # 1. Determinar o retorno de fecho baseado no tipo de arbitragem
        if pos.type == CexType.LIGHTER_TO_HL:
            # Entrámos: Compra Lighter (Long), Venda HL (Short)
            # Fecho: Venda Lighter (Bid) e Compra HL (Ask) para fechar o Short
            simulated_lighter_return = (qty * current_lighter_price) - lighter_gas_cost
            simulated_hl_cost = (qty * current_hl_price) * (1 + hl_fee)

            # Balanço final simulado de cada lado
            final_hl_balance = pos.initial_balance_hl_usd - simulated_hl_cost
            final_lighter_balance = pos.initial_balance_lighter_usd + simulated_lighter_return

        elif pos.type == CexType.HL_TO_LIGHTER:
            # Entrámos: Compra HL (Long), Venda Lighter (Short)
            # Fecho: Venda HL (Bid) e Compra Lighter (Ask) para fechar o Short
            simulated_hl_return = (qty * current_hl_price) * (1 - hl_fee)
            simulated_lighter_cost = (qty * current_lighter_price) + lighter_gas_cost

            final_hl_balance = pos.initial_balance_hl_usd + simulated_hl_return
            final_lighter_balance = pos.initial_balance_lighter_usd - simulated_lighter_cost
        else:
            logging.error(f"⚠️ Tipo de posição desconhecido: {pos.type}")
            return 0.0

        # 2. Soma Soberana: Quanto capital teríamos no total após o fecho
        total_now = final_hl_balance + final_lighter_balance

        # 3. Capital total que tínhamos quando o trade foi registado
        total_investido_entrada = pos.initial_balance_lighter_usd + pos.initial_balance_hl_usd

        # Retorna o lucro líquido puro (positivo = lucro, negativo = prejuízo)
        return total_now - total_investido_entrada

    @staticmethod
    def check_exit_profitability(pos: CexActivePosition, current_hl_price: float,
                                 current_lighter_price: float) -> float:
        """Calcula o lucro líquido real simulando o fecho simultâneo baseado no PNL das pernas."""
        # Taxas padrão (HL: 0.035%, Lighter: 0.00%, Gas estimado: 0.05 USDC)
        hl_fee_rate = 0.00035
        lighter_gas_cost = 0.05
        qty = pos.qty_pair

        pnl_lighter = 0.0
        pnl_hl = 0.0

        # 1. Calcular o PNL Bruto de cada perna isolada com base no lado
        if pos.type == CexType.LIGHTER_TO_HL:
            # Entrada: Compra Lighter (Long) | Venda HL (Short)
            # Preços de Entrada originais guardados na dclass
            pnl_lighter = (current_lighter_price - pos.entry_price_lighter) * qty
            pnl_hl = (pos.entry_price_hl - current_hl_price) * qty  # Short: Preço Entrada - Preço Atual

            # Custos de Fecho
            fee_hl = (qty * current_hl_price) * hl_fee_rate
            cost_lighter = lighter_gas_cost

        elif pos.type == CexType.HL_TO_LIGHTER:
            # Entrada: Compra HL (Long) | Venda Lighter (Short)
            pnl_hl = (current_hl_price - pos.entry_price_hl) * qty
            pnl_lighter = (pos.entry_price_lighter - current_lighter_price) * qty  # Short: Preço Entrada - Preço Atual

            # Custos de Fecho
            fee_hl = (qty * current_hl_price) * hl_fee_rate
            cost_lighter = lighter_gas_cost
        else:
            logging.error(f"⚠️ Tipo de posição desconhecido: {pos.type}")
            return 0.0

        # 2. Lucro Líquido Combinado = (PNL A + PNL B) - Taxas de Saída
        return (pnl_lighter + pnl_hl) - (fee_hl + cost_lighter)
