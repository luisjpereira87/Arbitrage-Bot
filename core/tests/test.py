def simular_v3_final(sqrtPriceX96, d0, d1, token_in_is_t0):
    # 1. Preço em termos de "unidades mínimas" (wei de T1 por wei de T0)
    # P = sqrt^2 / 2^192
    price_wei = (sqrtPriceX96 ** 2) / (2 ** 192)

    # 2. Ajuste para "unidades humanas" (Ex: ETH por USDC)
    # Esta é a fórmula infalível:
    price_human_t0_em_t1 = price_wei * (10 ** d0 / 10 ** d1)

    # 3. Direção do Swap
    if token_in_is_t0:
        # Entra T0 -> Sai T1 (Ex: WETH -> USDC)
        return price_human_t0_em_t1
    else:
        # Entra T1 -> Sai T0 (Ex: USDC -> WETH)
        return 1 / price_human_t0_em_t1


# --- VALORES REAIS ARBITRUM ---
# Pool WETH/USDC: WETH é T0 (18), USDC é T1 (6)
sqrt_exemplo = 143125824240755452243456

print("🧪 TESTE FINAL (SEM INVERSÕES ESTRANHAS)")
# Queremos saber: Entrando com 1 USDC (T1), quanto WETH (T0) recebo?
# Portanto: token_in_is_t0 = False
resultado = simular_v3_final(sqrt_exemplo, 18, 6, False)

print(f"Com 1 USDC recebo: {resultado:.10f} WETH")
print(f"Preço do ETH: ${1 / resultado:.2f}")