from brownie import accounts, Contract, history
import json
import scripts.run_fork as setup


def main():
    # 1. EXECUTAR O SETUP AUTOMATICAMENTE
    # Isto faz o deploy e carrega os 24k USDC sempre que começas
    print("🛠️  Iniciando Setup do Ambiente...")
    v7_address = setup.main()

    # 1. Configurações Iniciais
    JSON_PATH = './data/v7_final.json'
    CONTRACT_PATH = "contracts_backup/ArbitrageV7.sol:ArbitrageExecutorV7"

    # Endereços dos Tokens (Arbitrum)
    USDC = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
    WETH = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
    WBTC = "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f"
    LINK = "0xf97f4df75117a78c1A5a0DBb814Af92458539FB4"

    usdc_abi = [
        {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf",
         "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
        {"constant": False, "inputs": [{"name": "_spender", "type": "address"}, {"name": "_value", "type": "uint256"}],
         "name": "approve", "outputs": [{"name": "success", "type": "bool"}], "type": "function"}
    ]

    usdc = Contract.from_abi("USDC", USDC, usdc_abi)

    # Endereços das Pools Uniswap V3
    p1 = "0xc473e2aEE3441BF9240Be85eb122aBB059A3B57c"  # USDC -> WETH (0.05%)
    p2 = "0x468b88941e7Cc0B88c1869d68ab6b570bCEF62Ff"  # WETH -> WBTC (0.3%)
    p3 = "0xbBe36e6f0331C6a36AB44Bc8421E28E1a1871C1e"  # WBTC -> USDC (0.05%)

    # 2. Carregar ABI e Instanciar Contrato
    with open(JSON_PATH, 'r') as f:
        data = json.load(f)
    abi = data["contracts"][CONTRACT_PATH]["abi"]

    # Procurar o último deploy feito no fork

    if not len(history) or not history[-1].contract_address:
        # Se não encontrares no histórico, podes colar o endereço do console aqui:
        contract_address = "0x635AE4690b27372709cA2F6a4a700869f91A64D7"
    else:
        contract_address = history[-1].contract_address

    v7 = Contract.from_abi("ArbitrageExecutorV7", v7_address, abi)
    dev = accounts[0]

    # 3. Parâmetros da Arbitragem
    #amount_in = 5000 * 10 ** 6  # 5000 USDC
    amount_in = 100 * 10**6
    pools = [p1, p2, p3]
    # zeroForOne:
    # T1 -> T2 (se T1 > T2 no endereço, True)
    # USDC(af88...) > WETH(82af...) -> True
    # WETH(82af...) < WBTC(2f2a...) -> False
    # WBTC(2f2a...) < USDC(af88...) -> False
    zero_for_one = [False, True, False]
    tokens = [USDC, WETH, LINK, USDC]

    print("-" * 50)
    print(f"🚀 Iniciando Teste Triangular no Contrato: {v7.address}")
    print(f"💰 Montante: {amount_in / 1e6} USDC")
    print(f"🛣️  Rota: USDC -> WETH -> WBTC -> USDC")
    print("-" * 50)

    saldo_v7 = usdc.balanceOf(v7.address)
    print(f"💰 Saldo confirmado no contrato: {saldo_v7 / 1e6} USDC")

    #pools = ["0xC6962004f452bE9203591991D15f6b388e09E8D0"]  # Pool USDC/WETH (0.05%)
    """
    pools = [
        "0xC6962004f452bE9203591991D15f6b388e09E8D0",  # Swap 1: USDC -> WETH
        "0xC6962004f452bE9203591991D15f6b388e09E8D0"  # Swap 2: WETH -> USDC
    ]
    """
    #zero_for_one = [False, True]  # USDC (T1) -> WETH (T0) é 1 para 0, logo False
    #tokens = [USDC, WETH, USDC]  # Apenas dois tokens

    # 4. Execução
    try:
        tx = v7.startArbitrage(
            amount_in,
            pools,
            zero_for_one,
            tokens,
            {'from': dev, 'gas_limit': 1500000, 'allow_revert': True}
        )

        saldo_v7_final = usdc.balanceOf(v7.address)
        print(f"Saldo Final: {saldo_v7_final / 1e6} USDC")

        print(f"Status da Transação: {tx.status}")
        if tx.status == 0:
            # Em vez de tx.revert_msg (que causa o crash), tentamos ver o evento
            print("❌ Transação Reverteu.")
    except Exception as e:
        print(f"⚠️ Erro capturado: {e}")


if __name__ == "__main__":
    main()