import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError

from core.dclass.active_position_dclass import ActivePosition

load_dotenv()

# 1. Encontrar a Raiz do Projeto de forma robusta
# Sobe 3 níveis a partir de commons/utils/paths.py para chegar à raiz
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 2. Definir o Diretório de Modelos
# No Railway, vamos definir uma variável STORAGE_PATH = /data/ no painel
# Se não existir, ele cria uma pasta 'models_storage' na raiz do projeto
MODEL_STORAGE = os.getenv("STORAGE_PATH", os.path.join(BASE_DIR, "models_storage"))

# 3. Criar a pasta se não existir (evita erros de FileNotFoundError)
if not os.path.exists(MODEL_STORAGE):
    os.makedirs(MODEL_STORAGE, exist_ok=True)


class TradePosition:

    @staticmethod
    def empty_position():
        file_path = Path(os.path.join(MODEL_STORAGE, 'active_position.json'))
        if not file_path.exists():
            data = {
                "status": "OPEN",
                "symbol": "ARB/USDC",
                "units_dex": 110.00758388805156,
                "initial_balance_dex_usd": 13.03,
                "initial_balance_hl_usd": 13.43442,
                "total_initial_usd": 26.86884,
                "entry_price_hl": 0.11866,
                "timestamp": "2026-05-04T13:40:00"
            }
            with file_path.open("r", encoding="utf-8") as f:
                json.dump(data, f)

    @staticmethod
    def get_position():
        filename = 'active_position.json'
        file_path = Path(os.path.join(MODEL_STORAGE, 'active_position.json'))
        if not file_path.exists():
            logging.warning(f"⚠️ Arquivo de configuração '{filename}' não encontrado. Usando pares padrão.")
            return None
        try:
            with file_path.open("r", encoding="utf-8") as f:
                data = json.load(f)

            active_position = ActivePosition(**data)
            logging.info(f"✅ {active_position} pares carregados de '{filename}' com sucesso.")
            return active_position
        except ValidationError as e:
            logging.error(f"❌ Erro de validação nos dados de '{filename}': {e}. Usando pares padrão.")
            return None

    @staticmethod
    def save_position(position: ActivePosition):
        file_path = Path(os.path.join(MODEL_STORAGE, 'active_position.json'))
        with file_path.open("w", encoding="utf-8") as f:
            f.write(position.model_dump_json(indent=4))
        logging.info(f"💾 Posição de {position.symbol} guardada no JSON.")

    @staticmethod
    def clear_position():
        file_path = Path(os.path.join(MODEL_STORAGE, 'active_position.json'))
        if file_path.exists():
            file_path.unlink()
            logging.info("🧹 JSON de posição ativa limpo (Trade concluído).")

    @staticmethod
    def check_exit_profitability(pos: ActivePosition, current_dex_price: float, current_hl_price: float):
        # 1. Valor na DEX: (Unidades que compraste * Preço de agora) - Taxa de Swap
        # Usamos o units_dex porque o saldo em USD muda, mas os tokens que tens são fixos.
        current_dex_value = (pos.units_dex * current_dex_price) * 0.997

        # 2. Valor na HL: Saldo Inicial HL + Lucro/Prejuízo do Short
        # PnL = (Preço Entrada - Preço Atual) * Unidades
        pnl_hl = (pos.entry_price_hl - current_hl_price) * pos.units_dex
        current_hl_value = pos.initial_balance_hl_usd + pnl_hl

        # 3. Resultado Final
        total_now = current_dex_value + current_hl_value
        lucro_absoluto = total_now - pos.total_initial_usd

        return lucro_absoluto
