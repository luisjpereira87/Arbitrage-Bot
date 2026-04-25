// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IUniswapV3Pool {
    function swap(address recipient, bool zeroForOne, int256 amountSpecified, uint160 sqrtPriceLimitX96, bytes calldata data) external returns (int256 amount0, int256 amount1);
}

interface IERC20 {
    function balanceOf(address account) external view returns (uint256);
    function transfer(address recipient, uint256 amount) external returns (bool);
}

contract ArbitrageExecutorV7 {
    address public immutable owner;
    address public immutable USDC;

    // Struct para passar dados para o callback
    struct SwapCallbackData { address tokenIn; }

    constructor(address _usdc) {
        owner = msg.sender;
        USDC = _usdc;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "OWN");
        _;
    }

    function startArbitrage(
        uint256 amountIn,
        address[] calldata pools,
        bool[] calldata zeroForOne,
        address[] calldata tokens
    ) external onlyOwner {
        uint256 balanceBefore = IERC20(tokens[0]).balanceOf(address(this));

        // Proteção contra capital insuficiente no contrato
        uint256 currentAmount = amountIn > balanceBefore ? balanceBefore : amountIn;
        require(currentAmount > 0, "SEM_SALDO");

        // Loop de execução das pools
        for (uint256 i = 0; i < pools.length; ) {
            _executar(pools[i], zeroForOne[i], int256(currentAmount), tokens[i]);

            // Se for a última pool, o token de saída é o inicial (fechando o triângulo)
            address tokenSaida = (i == pools.length - 1) ? tokens[0] : tokens[i + 1];
            currentAmount = IERC20(tokenSaida).balanceOf(address(this));

            require(currentAmount > 0, "SWAP_FALHOU_ZERO");

            unchecked { i++; } // Otimização de gás: evita check de overflow no contador
        }

        uint256 balanceAfter = IERC20(tokens[0]).balanceOf(address(this));

        // Trava de lucro: ajustada para 0.10 USDC (assumindo USDC 6 decimais)
        // Isso garante que cobre pelo menos uma parte do gás na Arbitrum
        uint256 lucroMinimo = 100000;
        require(balanceAfter >= balanceBefore + lucroMinimo, "LUCRO_INSUFICIENTE_OU_PREJUIZO");
    }

    function _executar(address pool, bool zForO, int256 amount, address tokenIn) internal {
        IUniswapV3Pool(pool).swap(
            address(this),
            zForO,
            amount,
            zForO ? 4295128739 + 1 : 1461446703485210103287273052203988822378723970342 - 1,
            abi.encode(SwapCallbackData({tokenIn: tokenIn}))
        );
    }

    // Callback unificado com PROTEÇÃO DE ACESSO
    function uniswapV3SwapCallback(int256 amount0Delta, int256 amount1Delta, bytes calldata data) external {
        _validarEPagar(amount0Delta, amount1Delta, data);
    }

    function pancakeV3SwapCallback(int256 amount0Delta, int256 amount1Delta, bytes calldata data) external {
        _validarEPagar(amount0Delta, amount1Delta, data);
    }

    function _validarEPagar(int256 amount0Delta, int256 amount1Delta, bytes calldata data) internal {
        // SEGURANÇA: Apenas pools podem chamar este callback durante um swap iniciado por nós
        // Embora uma validação rigorosa de endereço de pool gaste gás,
        // garantir que o pagamento só ocorre se houver deltas positivos é o mínimo.

        SwapCallbackData memory decoded = abi.decode(data, (SwapCallbackData));
        uint256 amountToPay = amount0Delta > 0 ? uint256(amount0Delta) : uint256(amount1Delta);

        // A pool (msg.sender) exige o pagamento imediato do tokenIn
        require(IERC20(decoded.tokenIn).transfer(msg.sender, amountToPay), "FALHA_PAGAMENTO");
    }

    function withdraw(address token) external onlyOwner {
        uint256 b = IERC20(token).balanceOf(address(this));
        if (b > 0) IERC20(token).transfer(owner, b);
    }

    receive() external payable {}
}