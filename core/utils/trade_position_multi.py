import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from core.dclass.active_position_dclass import ActivePosition

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


class TradePositionMulti:
    @staticmethod
    def get_file_path(symbol: str):
        # Transforma "ARB/USDC" em "ARB_USDC.json"
        clean_symbol = symbol.replace("/", "_")
        return Path(os.path.join(STORAGE_PATH, f'pos_{clean_symbol}.json'))

    @staticmethod
    def load_all_positions():
        """Varre a pasta e carrega todas as posições ativas num dicionário."""
        positions = {}
        if not os.path.exists(STORAGE_PATH):
            os.makedirs(STORAGE_PATH)
            return positions

        for file in os.listdir(STORAGE_PATH):
            if file.startswith("pos_") and file.endswith(".json"):
                try:
                    file_path = Path(os.path.join(STORAGE_PATH, file))
                    with file_path.open("r", encoding="utf-8") as f:
                        data = json.load(f)

                    pos = ActivePosition(**data)
                    # Usamos o símbolo base (ex: ARB) como chave do dicionário
                    base_symbol = pos.symbol.split('/')[0]
                    positions[base_symbol] = pos
                except Exception as e:
                    logging.error(f"❌ Erro ao carregar posição do ficheiro {file}: {e}")

        if positions:
            logging.info(f"📂 {len(positions)} posições carregadas da storage: {list(positions.keys())}")
        return positions

    @staticmethod
    def save_position(position: ActivePosition):
        """Guarda a posição num ficheiro específico para o seu símbolo."""
        file_path = TradePositionMulti.get_file_path(position.symbol)
        with file_path.open("w", encoding="utf-8") as f:
            f.write(position.model_dump_json(indent=4))
        logging.info(f"💾 Posição de {position.symbol} guardada em {file_path.name}")

    @staticmethod
    def clear_position(symbol: str):
        """Elimina o ficheiro específico de um ativo após o fecho do trade."""
        file_path = TradePositionMulti.get_file_path(symbol)
        if file_path.exists():
            file_path.unlink()
            logging.info(f"🧹 Arquivo {file_path.name} removido (Trade concluído).")

    @staticmethod
    def check_exit_profitability(pos: ActivePosition, current_dex_price: float, current_hl_price: float):
        # Lógica de lucro (mantém-se igual, apenas certifica que os nomes batem com o ActivePosition)
        current_dex_value = (pos.units_dex * current_dex_price) * 0.997

        # PnL HL usando o ratio de unidades reais na entrada
        units_hl = pos.initial_balance_hl_usd / pos.entry_price_hl
        pnl_hl = (pos.entry_price_hl - current_hl_price) * units_hl
        current_hl_value = pos.initial_balance_hl_usd + pnl_hl

        total_now = current_dex_value + current_hl_value
        return total_now - pos.total_balance_before_usd
