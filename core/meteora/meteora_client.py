import json
import subprocess

from core.meteora.dclass import MarketStatus, PositionStatus, CalculateRange


class MeteoraClient:
    def __init__(self, script_path):
        self.script_path = script_path

    def _execute(self, args):
        try:
            full_command = ["node", self.script_path] + args
            # Capturamos tudo para garantir que não perdemos nada
            result = subprocess.run(full_command, capture_output=True, text=True)
            # Combinamos stdout e stderr apenas para garantir
            output_completo = result.stdout + result.stderr

            return self.extract_json_response(output_completo)

        except Exception as e:
            return {"status": "ERROR", "message": f"Erro crítico de execução: {str(e)}"}

    def extract_json_response(self, raw_output):
        if not raw_output:
            return {"status": "ERROR", "message": "Output vazio"}
        for line in raw_output.splitlines():
            line = line.strip()
            # Procuramos a primeira linha que seja um JSON válido
            if line.startswith('{') and line.endswith('}'):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue

        return {"status": "ERROR", "message": "Nenhum JSON encontrado nas linhas recebidas"}

    # Mapeamento dos métodos
    def get_status(self) -> MarketStatus:
        data = self._execute(["status"])
        status = MarketStatus(
            sol_balance=float(data["balances"]["SOL"]),
            usdc_balance=float(data["balances"]["USDC"]),
            raw_price=float(data["pool"]["rawPrice"]),
            wallet=data["wallet"]
        )
        print(f"✅ Status carregado com sucesso!")
        print(f"💰 Saldo SOL: {status.sol_balance}")
        print(f"💵 Saldo USDC: {status.usdc_balance}")
        print(f"📊 Preço: {status.raw_price}")

        return status

    def check_position(self) -> (PositionStatus | None):
        data = self._execute(["check"])
        if not data.get("exists"):
            return None
        status_data = {
            "exists": data.get("exists"),
            "address": data.get("address"),
            "inRange": data.get("inRange"),
            "activeBin": int(data.get("activeBin", 0)),
            "lowerBin": int(data.get("lowerBin", 0)),
            "upperBin": int(data.get("upperBin", 0)),
            "lowerPrice": float(data.get("lowerPrice", 0.0)),
            "upperPrice": float(data.get("upperPrice", 0.0))
        }
        return PositionStatus(**status_data)

    def open_position(self, usdc: float, price: float, width: float):
        data = self._execute(["open", str(usdc), str(price), str(width)])
        print("AQUIIIIII", data)
        return data.get("status") == "SUCCESS"

    def rebalance_position(self, pos_address: str, usdc: float, price: float, width: float):
        data = self._execute(["rebalance", pos_address, str(usdc), str(price), str(width)])
        return data.get("status") == "SUCCESS"

    def close_all(self):
        data = self._execute(["close"])
        return data.get("status") == "SUCCESS"

    def calculate_range(self, current_price: float, range_width_dollars: float) -> CalculateRange:
        data = self._execute(["calculate", str(current_price), str(range_width_dollars)])

        result = CalculateRange(
            status=data["status"],
            bins_offset=float(data["binsOffset"]),
            total_bins_width=float(data["totalBinsWidth"]),
            capital_multiplier=float(data["capitalMultiplier"]),
            active_bin_id=float(data["activeBinId"]))

        return result
