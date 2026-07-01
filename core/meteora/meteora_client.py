import asyncio
import json

from core.meteora.dclass import MarketStatus, PositionStatus, CalculateRange
from core.meteora.pool_manager_dclass import PoolConfig


class MeteoraClient:
    def __init__(self, script_path, pool_config: PoolConfig):
        self.script_path = script_path
        self.pool_config = pool_config

    async def _execute_async(self, args):
        try:
            full_command = ["node", self.script_path] + args
            process = await asyncio.create_subprocess_exec(
                *full_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            # Lê o output e espera o processo acabar, mas de forma mais direta
            stdout, stderr = await process.communicate()

            print(stdout.decode())
            print(stderr.decode())
            # Se o Node.js estiver a enviar logs inúteis, isso pode estar a atrasar.
            # Garante que só tens o JSON na saída.
            return self.extract_json_response(stdout.decode())

        except Exception as e:
            return {"status": "ERROR", "message": str(e)}

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
    async def get_status(self) -> MarketStatus:
        data = await self._execute_async(["status", self.pool_config.address])
        print(f"DEBUG: JSON recebido do Node.js: {data}")
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

    async def get_position(self) -> (PositionStatus | None):
        data = await self._execute_async(["get_position", self.pool_config.address])
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
            "upperPrice": float(data.get("upperPrice", 0.0)),
            "size": float(data.get("size", 0.0)),
            "totalXAmount": float(data.get("totalXAmount", 0.0)),
            "totalYAmount": float(data.get("totalYAmount", 0.0)),
        }
        return PositionStatus(**status_data)

    async def open_position(self, usdc: float, price: float, width: float):
        data = await self._execute_async(["open", self.pool_config.address, str(usdc), str(price), str(width)])
        print(f"Position object {data}")
        return data.get("status") == "SUCCESS_OPEN_BALANCE_POSITION"

    async def rebalance_position(self, usdc: float, price: float, width: float):
        data = await self._execute_async(["rebalance", self.pool_config.address, str(usdc), str(price), str(width)])
        return data.get("status") == "SUCCESS_REBALANCE_POSITION"

    async def close_all(self):
        data = await self._execute_async(["close", self.pool_config.address])
        return data.get("status") == "SUCCESS_CLOSE_ALL"

    async def calculate_range(self, current_price: float, range_width_dollars: float) -> CalculateRange:
        data = await self._execute_async(["calculate", str(current_price), str(range_width_dollars)])

        result = CalculateRange(
            status=data["status"],
            bins_offset=float(data["binsOffset"]),
            total_bins_width=float(data["totalBinsWidth"]),
            capital_multiplier=float(data["capitalMultiplier"]),
            active_bin_id=float(data["activeBinId"]))

        return result
