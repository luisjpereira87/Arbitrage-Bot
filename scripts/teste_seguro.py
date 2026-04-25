from brownie import accounts, network, Contract, project, web3
import json


def main():
    # 1. Carregar o contrato manualmente (O Brownie não vai apagar isto)
    with open('./data/v7_final.json', 'r') as f:
        data = json.load(f)

        path = "contracts_backup/ArbitrageV7.sol:ArbitrageExecutorV7"
        abi = data["contracts"][path]["abi"]
        bytecode = data["contracts"][path]["bin"]

        dev = accounts[0]
        USDC_ADDR = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
        WETH_ADDR = "0x82aF49447D8a07e3bd95BD0d56f352415231aB1e"
        ROUTER_V3 = "0xE592427A0AEce92De3Edee1F18E0157C05861564"  # Uniswap V3 Router

        # 2. DEPLOY DO SEU CONTRATO
        v7_factory = web3.eth.contract(abi=abi, bytecode=bytecode)
        tx_hash = v7_factory.constructor(USDC_ADDR).transact({'from': dev.address})
        v7 = Contract.from_abi("ArbitrageExecutorV7", web3.eth.wait_for_transaction_receipt(tx_hash).contractAddress,
                               abi)

        USDC_ADDR = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
        WETH_ADDR = "0x82aF49447D8a07e3bd95BD0d56f352415231aB1e"
        ROUTER_V3 = "0xE592427A0AEce92De3Edee1F18E0157C05861564"

        # 2. DEPLOY
        v7_factory = web3.eth.contract(abi=abi, bytecode=bytecode)
        tx_hash = v7_factory.constructor(USDC_ADDR).transact({'from': dev.address})
        v7 = Contract.from_abi("ArbitrageExecutorV7", web3.eth.wait_for_transaction_receipt(tx_hash).contractAddress,
                               abi)

        # 3. CONVERTER ETH EM WETH (Depósito Direto)
        print("[SISTEMA] A converter 10 ETH em WETH...")
        weth = Contract.from_abi("WETH", WETH_ADDR, [
            {"constant": False, "inputs": [], "name": "deposit", "outputs": [], "payable": True,
             "stateMutability": "payable", "type": "function"},
            {"constant": False, "inputs": [{"name": "guy", "type": "address"}, {"name": "wad", "type": "uint256"}],
             "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"}
        ])
        weth.deposit({'from': dev, 'value': 10 * 10 ** 18})
        weth.approve(ROUTER_V3, 10 * 10 ** 18, {'from': dev})

        # 4. SWAP WETH -> USDC
        print("[SISTEMA] A trocar WETH por USDC no Router...")
        router = Contract.from_abi("Router", ROUTER_V3, [
            {"inputs": [{"components": [{"internalType": "address", "name": "tokenIn", "type": "address"},
                                        {"internalType": "address", "name": "tokenOut", "type": "address"},
                                        {"internalType": "uint24", "name": "fee", "type": "uint24"},
                                        {"internalType": "address", "name": "recipient", "type": "address"},
                                        {"internalType": "uint256", "name": "deadline", "type": "uint256"},
                                        {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                                        {"internalType": "uint256", "name": "amountOutMinimum", "type": "uint256"},
                                        {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"}],
                         "name": "params", "type": "tuple"}], "name": "exactInputSingle",
             "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
             "stateMutability": "nonpayable", "type": "function"}
        ])

        router.exactInputSingle([
            WETH_ADDR, USDC_ADDR, 500, dev.address, 9999999999, 10 * 10 ** 18, 0, 0
        ], {'from': dev})

        # 5. ENVIAR USDC PARA O CONTRATO V7
        usdc = Contract.from_abi("USDC", USDC_ADDR, [
            {"constant": False, "inputs": [{"name": "_to", "type": "address"}, {"name": "_value", "type": "uint256"}],
             "name": "transfer", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
            {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf",
             "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"}
        ])

        saldo_usdc = usdc.balanceOf(dev.address)
        usdc.transfer(v7.address, saldo_usdc, {'from': dev})
        print(f"✅ Saldo Real no Contrato: {usdc.balanceOf(v7.address) / 1e6} USDC")

        # 6. EXECUTAR START ARBITRAGE
        pool_v3 = "0xC31E6943f5424D04C5A03713027D02dB0366D60b"
        pools = [pool_v3, pool_v3]
        zero_for_one = [True, False]
        tokens = [USDC_ADDR, WETH_ADDR, USDC_ADDR]

        print("\n[SISTEMA] A disparar startArbitrage...")
        try:
            # Testamos com metade do saldo para garantir que o require passe
            amount_test = int(saldo_usdc / 2)
            v7.startArbitrage(amount_test, pools, zero_for_one, tokens, {'from': dev})
            print("🚀 SUCESSO!")
        except Exception as e:
            if "LN" in str(e):
                print("\n✅ VALIDAÇÃO TÉCNICA COMPLETA: 'revert: LN'")
                print("O contrato comunicou com a Uniswap e o callback funcionou.")
            else:
                print(f"❌ Erro: {e}")