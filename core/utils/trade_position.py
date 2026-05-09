import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError

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


class TradePosition:

    @staticmethod
    def empty_position():
        # Garante que MODEL_STORAGE é o caminho do volume (ex: /app/data)
        file_path = Path(os.path.join(STORAGE_PATH, 'active_position.json'))

        if not file_path.exists():
            logging.info(f"📁 Ficheiro não encontrado em {file_path}. Criando posição inicial de ARB...")

            data = {
                "status": "OPEN",
                "symbol": "ARB/USDC",
                "units_dex": 110.00758388805156,
                "initial_balance_dex_usd": 12.00,
                "initial_balance_hl_usd": 13.00,
                "total_initial_usd": 25.00,
                "entry_price_hl": 0.11866,
                "timestamp": "2026-05-04T13:40:00"
            }

            try:
                # USAR "w" para criar/escrever, não "r"
                with file_path.open("w", encoding="utf-8") as f:
                    json.dump(data, f, indent=4)
                logging.info("✅ Posição inicial ARB injetada com sucesso!")
            except Exception as e:
                logging.error(f"❌ Erro ao criar ficheiro no volume: {e}")
        else:
            logging.info("💾 Ficheiro de posição já existe. Seguindo monitorização.")

    @staticmethod
    def get_position():
        filename = 'active_position.json'
        file_path = Path(os.path.join(STORAGE_PATH, 'active_position.json'))
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
        file_path = Path(os.path.join(STORAGE_PATH, 'active_position.json'))
        with file_path.open("w", encoding="utf-8") as f:
            f.write(position.model_dump_json(indent=4))
        logging.info(f"💾 Posição de {position.symbol} guardada no JSON.")
        return position

    @staticmethod
    def clear_position():
        file_path = Path(os.path.join(STORAGE_PATH, 'active_position.json'))
        if file_path.exists():
            file_path.unlink()
            logging.info("🧹 JSON de posição ativa limpo (Trade concluído).")

    @staticmethod
    def check_exit_profitability(pos: ActivePosition, current_dex_price: float, current_hl_price: float):
        # 1. Valor na DEX: Quanto USDC terias se vendesses os teus tokens agora
        # Aplicamos 0.3% de taxa (0.997) para simular o custo do swap de saída
        current_dex_value = (pos.units_dex * current_dex_price) * 0.997

        # 2. Valor na HL:
        # O PnL de um Short é (Preço Entrada - Preço Atual) * Unidades
        # Somamos isso ao saldo que foi alocado para a margem na HL
        pnl_hl = (pos.entry_price_hl - current_hl_price) * (pos.initial_balance_hl_usd / pos.entry_price_hl)
        current_hl_value = pos.initial_balance_hl_usd + pnl_hl

        # 3. Resultado Final Comparativo
        total_now = current_dex_value + current_hl_value

        # IMPORTANTE: Usar o nome exato que definiste no execute_entry_sequence:
        # total_balance_before_usd (O snapshot da banca antes do trade)
        return total_now - pos.total_balance_before_usd
