from brownie import accounts, network, web3, Contract
import json


def main():
    dev = accounts[0]
    USDC_ADDR = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
    WETH_ADDR = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
    ROUTER_ADDR = "0xE592427A0AEce92De3Edee1F18E0157C05861564"

    # 1. CARREGAR O TEU JSON (A tua fonte da verdade)
    with open('./data/v7_final.json', 'r') as f:
        data = json.load(f)

    # O caminho que validaste anteriormente
    path = "contracts_backup/ArbitrageV7.sol:ArbitrageExecutorV7"
    abi = data["contracts"][path]["abi"]
    bytecode = data["contracts"][path]["bin"]

    # 2. DEPLOY MANUAL (Via Web3 puro)
    print("🚀 A iniciar deploy manual do V7 via Bytecode...")
    v7_factory = web3.eth.contract(abi=abi, bytecode=bytecode)

    # Enviamos a transação com o construtor (passando o USDC_ADDR)
    tx_hash = v7_factory.constructor(USDC_ADDR).transact({'from': dev.address})
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

    # Criamos a interface do Brownie a partir do endereço gerado
    v7 = Contract.from_abi("ArbitrageExecutorV7", receipt.contractAddress, abi)
    print(f"✅ Contrato V7 vivo em: {v7.address}")

    # 3. MODO DEUS: GARANTIR ETH NA CONTA
    web3.provider.make_request("evm_setAccountBalance", [dev.address, hex(1000 * 10 ** 18)])

    # 4. COMPRAR USDC PARA TESTES
    router_abi = [{"inputs": [{"components": [{"internalType": "address", "name": "tokenIn", "type": "address"},
                                              {"internalType": "address", "name": "tokenOut", "type": "address"},
                                              {"internalType": "uint24", "name": "fee", "type": "uint24"},
                                              {"internalType": "address", "name": "recipient", "type": "address"},
                                              {"internalType": "uint256", "name": "deadline", "type": "uint256"},
                                              {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                                              {"internalType": "uint256", "name": "amountOutMinimum",
                                               "type": "uint256"},
                                              {"internalType": "uint160", "name": "sqrtPriceLimitX96",
                                               "type": "uint160"}], "name": "params", "type": "tuple"}],
                   "name": "exactInputSingle",
                   "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
                   "stateMutability": "payable", "type": "function"}]
    router = Contract.from_abi("Router", ROUTER_ADDR, router_abi)

    print("🛒 A comprar USDC na Uniswap...")
    router.exactInputSingle([
        WETH_ADDR, USDC_ADDR, 500, dev.address, 9999999999, 10 * 10 ** 18, 0, 0
    ], {'from': dev, 'value': 10 * 10 ** 18})

    # 5. TRANSFERIR PARA O V7
    usdc_abi = [
        {"constant": False, "inputs": [{"name": "_to", "type": "address"}, {"name": "_value", "type": "uint256"}],
         "name": "transfer", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
        {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf",
         "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"}]
    usdc = Contract.from_abi("USDC", USDC_ADDR, usdc_abi)

    usdc.transfer(v7.address, usdc.balanceOf(dev), {'from': dev})

    print("-" * 30)
    print(f"🏁 SETUP FINALIZADO NO FORK")
    print(f"Contrato: {v7.address}")
    print(f"Saldo: {usdc.balanceOf(v7.address) / 1e6} USDC")
    print("-" * 30)

    return v7.address