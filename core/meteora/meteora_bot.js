const { Connection, Keypair, PublicKey, VersionedTransaction, Transaction } = require('@solana/web3.js');
const { createJupiterApiClient } = require('@jup-ag/api');
const AnchorProvider = require('@coral-xyz/anchor').AnchorProvider;
const Wallet = require('@coral-xyz/anchor').Wallet;
const anchor = require('@coral-xyz/anchor');
const path = require('path');
const fs = require('fs');
const { BN } = require('@coral-xyz/anchor');

// =====================================================================
// 1. CARREGAMENTO ROBUSTO DO .ENV & IMPORTAÇÕES DINÂMICAS
// =====================================================================
const envPath = path.resolve(__dirname, '../../.env');
if (fs.existsSync(envPath)) {
    require('dotenv').config({ path: envPath });
} else {
    require('dotenv').config({ path: path.resolve(__dirname, '.env') });
}

const dlmmModule = require('@meteora-ag/dlmm');
const DLMMClass = dlmmModule.default || dlmmModule.DLMM || dlmmModule;
const { getPriceOfBinByBinId, StrategyType } = dlmmModule;

// =====================================================================
// 2. INFRASTRUCTURE & CONFIGURATION
// =====================================================================
const RPC_URL = "https://api.mainnet-beta.solana.com";
const connection = new Connection(RPC_URL, 'confirmed');
//console.error("DEBUG: O bot Node.js foi iniciado com sucesso.");
let walletKeypair;
try {
    const privateKeyStr = process.env.PRIVATE_KEY_WALLET_SOLANA;
    if (!privateKeyStr) throw new Error("A variável PRIVATE_KEY_WALLET_SOLANA não foi encontrada no .env");

    const bs58Module = require('bs58');
    let decodeFn = typeof bs58Module === 'function' ? bs58Module : (bs58Module.decode || bs58Module.default?.decode);
    if (!decodeFn) decodeFn = anchor.utils.bytes.bs58.decode;

    walletKeypair = Keypair.fromSecretKey(decodeFn(privateKeyStr.trim()));
} catch (e) {
    console.error(`❌ [Setup] Erro crítico ao carregar a carteira: ${e.message}`);
    process.exit(1);
}

const wallet = new Wallet(walletKeypair);
const provider = new AnchorProvider(connection, wallet, AnchorProvider.defaultOptions());
const jupiterQuoteApi = createJupiterApiClient();

const POOL_CONFIG = {
    address: "5rCf1DM8LjKTw4YqhnoLcngyZYeNnQqztScTogYHAS6",
    binStep: 4,
    feePct: 0.0020,
    tokenX: { symbol: "SOL", decimals: 9 },
    tokenY: { symbol: "USDC", decimals: 6 }
};

const USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
const WSOL_MINT = "So11111111111111111111111111111111111111112";

// =====================================================================
// 3. JUPITER SWAP ENGINE & GAS SAFETY TRACKER
// =====================================================================

async function executeJupiterSwap(inputMint, outputMint, amountInDecimals) {
    try {
        if (amountInDecimals <= 0) return false;

        const quote = await jupiterQuoteApi.quoteGet({
            inputMint: inputMint,
            outputMint: outputMint,
            amount: Math.round(amountInDecimals),
            slippageBps: 50,
        });

        if (!quote) throw new Error("A Jupiter não conseguiu encontrar uma rota válida.");

        const swapResult = await jupiterQuoteApi.swapPost({
            swapRequest: {
                quoteResponse: quote,
                userPublicKey: wallet.publicKey.toBase58(),
                wrapAndUnwrapSol: true,
            },
        });

        const swapTransactionBuf = Buffer.from(swapResult.swapTransaction, 'base64');
        const transaction = VersionedTransaction.deserialize(swapTransactionBuf);
        transaction.sign([walletKeypair]);

        const txid = await connection.sendTransaction(transaction, {
            skipPreflight: true,
            maxRetries: 2
        });

        const latestBlockHash = await connection.getLatestBlockhash();
        await connection.confirmTransaction({
            blockhash: latestBlockHash.blockhash,
            lastValidBlockHeight: latestBlockHash.lastValidBlockHeight,
            signature: txid
        }, 'confirmed');

        console.log(`🔄 [SDK Jupiter] Swap Concluído! TX: ${txid}`);
        return true;
    } catch (error) {
        console.error(`❌ [SDK Jupiter] Falha ao executar o swap: ${error.message}`);
        throw error;
    }
}

async function ensureGasTracker(currentPrice, totalNeededLamports) {
    try {
        const balanceLamports = await connection.getBalance(wallet.publicKey);
        const MARGEM_SEGURANCA = 0.02 * 1_000_000_000;
        const totalExigido = totalNeededLamports + MARGEM_SEGURANCA;

        if (balanceLamports < totalExigido) {
            console.warn(`⚠️ [Gas Tracker] Saldo insuficiente. Faltam ${(totalExigido - balanceLamports) / 1e9} SOL.`);

            // 1. Calcular valor necessário em SOL e USDC
            const solFaltante = (totalExigido - balanceLamports) / 1_000_000_000;
            const usdcToSpend = solFaltante * currentPrice * 1.05; // 5% margem

            // 2. Verificar se tens USDC suficiente
            const usdcAccounts = await connection.getParsedTokenAccountsByOwner(wallet.publicKey, { mint: new PublicKey(USDC_MINT) });
            const usdcBalance = usdcAccounts.value.length > 0 ? usdcAccounts.value[0].account.data.parsed.info.tokenAmount.uiAmount : 0;

            if (usdcToSpend > usdcBalance) {
                console.error(`🚨 [Gas Tracker] USDC insuficiente! Precisas de ${usdcToSpend.toFixed(4)} USDC, mas tens apenas ${usdcBalance.toFixed(4)}.`);
                return false;
            }

            // 3. Executar Swap
            console.log(`🔄 Comprando ${solFaltante.toFixed(4)} SOL com ${usdcToSpend.toFixed(4)} USDC...`);
            await executeJupiterSwap(USDC_MINT, WSOL_MINT, Math.round(usdcToSpend * 1_000_000));
            await new Promise(r => setTimeout(r, 5000));
        }
        return true;
    } catch (error) {
        console.error(`❌ [Gas Tracker] Erro: ${error.message}`);
        return false;
    }
}

async function cleanupAndSettle_old(poolAddress, reserveUsdAmount = 3.0) {
    try {
        console.log(`🧹 [Cleaner] Iniciando consolidação (Reserva: $${reserveUsdAmount} USD)...`);

        const dlmmPool = await DLMMClass.create(connection, new PublicKey(poolAddress));
        const activeBin = await dlmmPool.getActiveBin();
        const precoRealMeteora = dlmmPool.fromPricePerLamport(parseFloat(activeBin.price));

        let currentSolPrice = precoRealMeteora;
        if (dlmmPool.tokenX.decimal < dlmmPool.tokenY.decimal) {
            currentSolPrice = 1 / precoRealMeteora;
        }

        // 1. LIMPEZA DE SOL NATIVO
        const solBalance = await connection.getBalance(wallet.publicKey);
        const solBalanceUi = solBalance / 1_000_000_000;

        // Calcula a reserva necessária em SOL
        const reserveSol = reserveUsdAmount / currentSolPrice;

        // Verifica se o saldo é maior que a reserva (com uma pequena margem de segurança de 0.05 SOL para taxas)
        if (solBalanceUi > (reserveSol + 0.05)) {
            const excessoSol = solBalanceUi - reserveSol - 0.02; // Deixa um extra de 0.02 para garantir execução

            console.log(`🔄 [Cleaner] Consolidando excedente de SOL: ${excessoSol.toFixed(4)}`);

            // Usa o WSOL_MINT para o swap de SOL nativo
            await executeJupiterSwap("So11111111111111111111111111111111111111112", USDC_MINT, Math.round(excessoSol * 1_000_000_000));
            await new Promise(r => setTimeout(r, 5000)); // Aumentei para 5s para maior estabilidade
        }

        // 2. LIMPEZA DE TOKENS
        const tokenAccounts = await connection.getParsedTokenAccountsByOwner(wallet.publicKey, {
            programId: new PublicKey("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
        });

        for (const account of tokenAccounts.value) {
            const parsedInfo = account.account.data.parsed.info;
            const mint = parsedInfo.mint;
            const amountRaw = parsedInfo.tokenAmount.amount;
            const balance = parsedInfo.tokenAmount.uiAmount;

            // Filtros básicos para evitar erros
            if (balance <= 0 || mint === USDC_MINT || mint === "So11111111111111111111111111111111111111112") continue;

            console.log(`🔄 [Cleaner] Consolidando token ${mint}: ${balance}...`);
            await executeJupiterSwap(mint, USDC_MINT, parseInt(amountRaw));
            await new Promise(r => setTimeout(r, 5000));
        }

        return true;
    } catch (error) {
        console.error(`❌ [Cleaner] Erro: ${error.message}`);
        return false;
    }
}

async function cleanupAndSettle(poolAddress, targetSolAmount = 0.02) {
    try {
        console.log(`🧹 [Cleaner] Iniciando consolidação (Alvo de SOL fixo: ${targetSolAmount} SOL)...`);

        const dlmmPool = await DLMMClass.create(connection, new PublicKey(poolAddress));

        // 1. LIMPEZA DE SOL NATIVO PARA OBTER QUANTIDADE EXATA
        const solBalance = await connection.getBalance(wallet.publicKey);
        const solBalanceUi = solBalance / 1_000_000_000;

        // Margem de segurança para garantir que a transação de swap não falha por falta de gás (ex: 0.005 SOL)
        const gasMargin = 0.005;

        // Se o saldo for superior ao alvo + margem de gás, limpamos o excesso
        if (solBalanceUi > (targetSolAmount + gasMargin)) {
            // O excesso a enviar para swap é o saldo total menos o alvo que queremos reter, menos a margem de segurança
            const excessoSol = solBalanceUi - targetSolAmount - gasMargin;

            console.log(`🔄 [Cleaner] Consolidando excedente de SOL. Atual: ${solBalanceUi.toFixed(4)} SOL | A reter: ${targetSolAmount} SOL | A trocar: ${excessoSol.toFixed(4)} SOL`);

            // Usa o WSOL_MINT para o swap de SOL nativo para USDC
            await executeJupiterSwap("So11111111111111111111111111111111111111112", USDC_MINT, Math.round(excessoSol * 1_000_000_000));
            await new Promise(r => setTimeout(r, 5000)); // Aguarda estabilização
        } else {
            console.log(`ℹ️ [Cleaner] Saldo de SOL (${solBalanceUi.toFixed(4)}) está dentro ou abaixo do alvo (${targetSolAmount} SOL). Nenhum swap de SOL necessário.`);
        }

        // 2. LIMPEZA DE TOKENS (Mantém-se igual)
        const tokenAccounts = await connection.getParsedTokenAccountsByOwner(wallet.publicKey, {
            programId: new PublicKey("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
        });

        for (const account of tokenAccounts.value) {
            const parsedInfo = account.account.data.parsed.info;
            const mint = parsedInfo.mint;
            const amountRaw = parsedInfo.tokenAmount.amount;
            const balance = parsedInfo.tokenAmount.uiAmount;

            // Filtros básicos para evitar erros
            if (balance <= 0 || mint === USDC_MINT || mint === "So11111111111111111111111111111111111111112") continue;

            console.log(`🔄 [Cleaner] Consolidando token ${mint}: ${balance}...`);
            await executeJupiterSwap(mint, USDC_MINT, parseInt(amountRaw));
            await new Promise(r => setTimeout(r, 5000));
        }

        return true;
    } catch (error) {
        console.error(`❌ [Cleaner] Erro: ${error.message}`);
        return false;
    }
}

// =====================================================================
// 4. MATHEMATICS & RANGE INTELLIGENCE
// =====================================================================
async function calculateRangeMetrics__(currentPrice, rangePercent) {
    const dlmmPool = await DLMMClass.create(connection, new PublicKey(POOL_CONFIG.address));
    const activeBin = await dlmmPool.getActiveBin();

    // 1. Calcula a largura total em USD com base na percentagem (ex: 0.10 = 10% do preço)
    const rangeWidthDollars = currentPrice * rangePercent;

    // 2. O resto da lógica mantém-se, mas agora é dinâmico
    const pctPerBin = POOL_CONFIG.binStep / 10000;
    const dollarValuePerBin = currentPrice * pctPerBin;

    // Ajusta o número de bins baseado na nova largura dinâmica
    const binsHalfSide = Math.round((rangeWidthDollars / 2) / dollarValuePerBin);
    const totalBinsWidth = binsHalfSide * 2;

    const priceMin = currentPrice - (rangeWidthDollars / 2);
    const priceMax = currentPrice + (rangeWidthDollars / 2);
    const capitalMultiplier = 1 / (1 - Math.sqrt(priceMin / priceMax));

    const result = {
        status: "SUCCESS",
        binsOffset: binsHalfSide,
        totalBinsWidth: totalBinsWidth,
        capitalMultiplier: capitalMultiplier,
        activeBinId: activeBin.binId,
        // É bom devolver os preços calculados para o Python confirmar
        priceMin: priceMin,
        priceMax: priceMax
    };

    //console.log(JSON.stringify(result));
    return result;
}

async function calculateRangeMetrics(currentPrice, rangePercent, skew = 0.5) {
    const dlmmPool = await DLMMClass.create(connection, new PublicKey(POOL_CONFIG.address));
    const activeBin = await dlmmPool.getActiveBin();

    // 1. Calcula a largura total em USD
    const rangeWidthDollars = currentPrice * rangePercent;

    // 2. Calcula quanto representa cada bin
    const pctPerBin = POOL_CONFIG.binStep / 10000;
    const dollarValuePerBin = currentPrice * pctPerBin;

    // 3. Aplica o skew:
    // Se skew for 0.5, o range é igual para ambos os lados.
    // Se skew for 0.3, ficas com 30% do range abaixo e 70% acima.
    const binsDown = Math.round((rangeWidthDollars * skew) / dollarValuePerBin);
    const binsUp = Math.round((rangeWidthDollars * (1 - skew)) / dollarValuePerBin);

    // Como o offset na Meteora é simétrico, para teres um skew tens de usar o maior lado
    // para definir o "raio" de bins que o protocolo vai criar
    const maxOffset = Math.max(binsDown, binsUp);

    const priceMin = currentPrice - (rangeWidthDollars * skew);
    const priceMax = currentPrice + (rangeWidthDollars * (1 - skew));

    const result = {
        status: "SUCCESS",
        binsOffset: maxOffset, // O valor que precisas para o protocolo
        totalBinsWidth: binsDown + binsUp,
        activeBinId: activeBin.binId,
        priceMin: priceMin,
        priceMax: priceMax,
        // Opcional: devolve os lados individuais para saberes o teu desvio
        binsDown: binsDown,
        binsUp: binsUp
    };

    return result;
}


// =====================================================================
// 5. CORE EXECUTION FUNCTIONS
// =====================================================================
async function openBalancedPosition(poolAddress, totalUsdcCapital, currentPrice, rangeWidthDollars) {
    const dlmmPool = await DLMMClass.create(connection, new PublicKey(poolAddress));
    console.log(`🚀 [Meteora] A iniciar ciclo dinâmico para capital de $${totalUsdcCapital} USDC...`);

    // 1. Calcular o capital de injeção (30% SOL / 70% USDC)
    const solPercent = 0.50;
    const usdcPercent = 1 - solPercent;

    const totalSolCapital = totalUsdcCapital * solPercent; // Valor em USD do SOL que queres
    const totalUsdcCapitalInjetar = totalUsdcCapital * usdcPercent; // Valor em USD do USDC que queres

    const solFinalAInjetar = totalSolCapital / currentPrice;

    // DEFINIÇÃO DOS BNs (Corrigidos para 30/70)
    const totalXAmount = new anchor.BN(Math.floor(solFinalAInjetar * 1_000_000_000));
    const totalYAmount = new anchor.BN(Math.floor(totalUsdcCapitalInjetar * 1_000_000));

    // 2. Obter Metrics e Quote
    const metrics = await calculateRangeMetrics(currentPrice, rangeWidthDollars, solPercent);
    const quote = await dlmmPool.quoteCreatePosition({
        strategy: {
            minBinId: metrics.activeBinId - metrics.binsOffset,
            maxBinId: metrics.activeBinId + metrics.binsOffset,
            strategyType: StrategyType.Curve,
        },
    });

    // 3. Conversão segura de SOL (float) para Lamports (BN)
    // O quote devolve valores em SOL (ex: 0.0574...)
    const positionRentSOL = quote.positionCost || 0;
    const binArrayCostSOL = quote.binArrayCost || 0;
    const reallocCostSOL = quote.positionReallocCost || 0;
    const bitmapExtensionCostSOL = quote.bitmapExtensionCost || 0;

    const totalRentSOL = positionRentSOL + binArrayCostSOL + reallocCostSOL + bitmapExtensionCostSOL;
    const totalRentLamports = new anchor.BN(Math.ceil(totalRentSOL * 1_000_000_000));

    // Adiciona Buffer de 0.01 SOL (10M lamports) para garantir margem contra simulação
    const BUFFER_EXTRA = new anchor.BN(10_000_000);
    const totalNeeded = totalXAmount.add(totalRentLamports).add(BUFFER_EXTRA);

    // 4. Gas Tracker (Agora com o valor exato calculado)
    const gasOk = await ensureGasTracker(currentPrice, totalNeeded.toNumber());
    if (!gasOk) throw new Error("Falha no reabastecimento de gás/rent.");

    // 5. Balanceamento (Swap se necessário)
    const usdcAccounts = await connection.getParsedTokenAccountsByOwner(wallet.publicKey, { mint: new PublicKey(USDC_MINT) });
    const solAccounts = await connection.getParsedTokenAccountsByOwner(wallet.publicKey, { mint: new PublicKey(WSOL_MINT) });

    const usdcBalance = usdcAccounts.value[0]?.account.data.parsed.info.tokenAmount.uiAmount || 0;
    const solBalance = solAccounts.value[0]?.account.data.parsed.info.tokenAmount.uiAmount || 0;
    const solNativoBalance = await connection.getBalance(wallet.publicKey) / 1_000_000_000;

    // Total de SOL disponível (WSOL + Nativo)
    const totalSolBalance = solBalance + solNativoBalance;
    const currentTotalValue = usdcBalance + (totalSolBalance * currentPrice);

    const targetUsdc = currentTotalValue * usdcPercent;

    // A diferença real para atingir o alvo de 70% USDC
    const diffUsdc = targetUsdc - usdcBalance;

    if (Math.abs(diffUsdc) > 1.0) { // Tolerância de 1$
        if (diffUsdc > 0) {
            // Precisamos de mais USDC: Vendes SOL (prioriza vender WSOL ou nativo)
            const solParaVender = diffUsdc / currentPrice;
            console.log(`🔄 [Swap] Vendendo SOL para obter $${diffUsdc.toFixed(2)} USDC...`);
            await executeJupiterSwap(WSOL_MINT, USDC_MINT, Math.round(solParaVender * 1_000_000_000));
        } else {
            // Tens USDC a mais: Compras SOL
            const solParaComprar = Math.abs(diffUsdc) / currentPrice;
            console.log(`🔄 [Swap] Comprando SOL usando $${Math.abs(diffUsdc).toFixed(2)} USDC...`);
            await executeJupiterSwap(USDC_MINT, WSOL_MINT, Math.round(Math.abs(diffUsdc) * 1_000_000));
        }
        await new Promise(r => setTimeout(r, 5000)); // Espera extra para confirmação da blockchain
    }

    // 6. Injeção
    console.log(`⚡ A injetar X:${totalXAmount.toString()} Y:${totalYAmount.toString()}...`);
    const positionKeypair = Keypair.generate();


    const tx = await dlmmPool.initializePositionAndAddLiquidityByStrategy({
        positionPubKey: positionKeypair.publicKey,
        user: wallet.publicKey,
        baseKeyPair: positionKeypair,
        lbPair: dlmmPool.pubkey,
        //totalXAmount: new anchor.BN(Math.floor(totalXAmount.toNumber() * 0.995)),
        //totalYAmount: new anchor.BN(Math.floor(totalYAmount.toNumber() * 0.995)),
        totalXAmount: new anchor.BN(Math.floor(totalXAmount.toNumber())),
        totalYAmount: new anchor.BN(Math.floor(totalYAmount.toNumber())),
        strategy: {
            minBinId: metrics.activeBinId - metrics.binsOffset,
            maxBinId: metrics.activeBinId + metrics.binsOffset,
            strategyType: StrategyType.Curve
        },
    });

    if (Array.isArray(tx)) {
        for (const t of tx) await provider.sendAndConfirm(t, [positionKeypair]);
    } else {
        await provider.sendAndConfirm(tx, [positionKeypair]);
    }

    console.log(`✅ Posição injetada com sucesso!`);
    return true;
}

async function closeAllPoolPositionsAndSettle(poolAddress) {

    console.log(`🛑 [Meteora] Protocolo de fecho e liquidação total acionado...`);
    const dlmmPool = await DLMMClass.create(connection, new PublicKey(poolAddress));

    // CORREÇÃO 1: Usar o método correto de busca de posições
    const result = await dlmmPool.getPositionsByUserAndLbPair(wallet.publicKey, dlmmPool.pubkey);
    //const userPositions = await dlmmPool.getPositionsByUserAndLbPair(wallet.publicKey, dlmmPool.pubkey);

    if (result.userPositions.length === 0) {
        //endScript("SUCCESS", { message: "Nenhuma posição para fechar." });
        console.log("Nenhuma posição para fechar.");
        return true;
    }

    for (const position of result.userPositions) {
        console.log(`🧹 A remover liquidez da posição: ${position.publicKey.toBase58()}`);

        const lowerBinId = position.positionData.lowerBinId;
        const upperBinId = position.positionData.upperBinId;

        // CORREÇÃO 2: A v1.9.10 exige a lista de Bins e Liquidez para o removeLiquidity
        // O objeto 'position' na 1.9.10 tem 'positionData.binData'
        const removeLiquidityTx = await dlmmPool.removeLiquidity({
            user: wallet.publicKey,
            position: position.publicKey,
            //binIds: position.positionData.binData.map(b => b.binId),
            //liquidities: position.positionData.binData.map(b => b.liquidity),
            fromBinId: lowerBinId,
            toBinId: upperBinId,
            bps: new anchor.BN(10_000),
            shouldClaimAndClose: true, // Substituindo 'shouldClosePosition' que é legado
        });

        if (Array.isArray(removeLiquidityTx)) {
            for (const t of removeLiquidityTx) {
                await provider.sendAndConfirm(t);
            }
        } else {
            await provider.sendAndConfirm(removeLiquidityTx);
        }

        //await provider.sendAndConfirm(removeLiquidityTx, [wallet.payer]);

    }

    console.log(`✅ Liquidez removida. Aguardando confirmação...`);
    //await new Promise(resolve => setTimeout(resolve, 5000));

    /**
    const success = await (poolAddress, 3.0);

    if (success) {
        console.log("🎉 Ciclo de fecho e liquidação finalizado com sucesso.");
    }
    **/
    // ... (resto do teu código de liquidação de SOL permanece igual)
    //endScript("SUCCESS_CLOSE_ALL");
    return true;
}

// =====================================================================
// 6. CONSULTATION & DIAGNOSTIC METHODS (Read-Only)
// =====================================================================

async function getMarketStatus(poolAddress) {
    try {
        const solBalanceLamports = await connection.getBalance(wallet.publicKey);
        const solBalance = solBalanceLamports / 1_000_000_000;

        let usdcBalance = 0;
        try {
            const tokenAccounts = await connection.getParsedTokenAccountsByOwner(wallet.publicKey, {
                mint: new PublicKey(USDC_MINT)
            });
            if (tokenAccounts.value.length > 0) {
                usdcBalance = tokenAccounts.value[0].account.data.parsed.info.tokenAmount.uiAmount;
            }
        } catch (e) {}

        const dlmmPool = await DLMMClass.create(connection, new PublicKey(poolAddress));
        const activeBin = await dlmmPool.getActiveBin();
        const precoRealMeteora = dlmmPool.fromPricePerLamport(parseFloat(activeBin.price));

        let precoFinalPython = precoRealMeteora;
        if (dlmmPool.tokenX.decimal < dlmmPool.tokenY.decimal) {
            precoFinalPython = 1 / precoRealMeteora;
        }

        const statusReport = {
            status: "SUCCESS",
            wallet: wallet.publicKey.toBase58(),
            balances: {
                SOL: solBalance,
                USDC: usdcBalance
            },
            pool: {
                address: poolAddress,
                activeBinId: activeBin.binId,
                rawPrice: precoFinalPython
            }
        };

        console.log(JSON.stringify(statusReport));
        process.exit(0);

    } catch (error) {
        const errorReport = {
            status: "ERROR",
            message: error.message
        };
        console.log(JSON.stringify(errorReport));
        process.exit(1);
    }
}

async function rebalancePositionByStrategy(poolAddress, totalUsdcCapital, currentPrice, rangeWidthDollars) {
    console.log("DEBUG: Executando estratégia de Rebalanceamento via Fecho/Abertura...");

    try {
        // 1. Fecha a posição atual
        console.log("🧹 A fechar posição antiga...");
        await closeAllPoolPositionsAndSettle(poolAddress);

        // 2. Abre nova posição no novo range (Preço atualizado)
        console.log("🚀 A abrir nova posição no novo range...");
        await openBalancedPosition(poolAddress, totalUsdcCapital, currentPrice, rangeWidthDollars);

        console.log("✅ Rebalanceamento concluído com sucesso (via Close/Open).");
        return true;
    } catch (error) {
        console.error("❌ Erro no Rebalanceamento:", error.message);
        throw error; // O router irá apanhar isto e imprimir o status: ERROR
    }
}

async function getPositionPnL(poolAddress, positionAddress) {
    try {
        const dlmmPool = await DLMMClass.create(connection, new PublicKey(poolAddress));
        await dlmmPool.refetchStates();

        // 1. Preço atual do bin na Meteora
        const activeBin = await dlmmPool.getActiveBin();
        const precoMeteora = dlmmPool.fromPricePerLamport(parseFloat(activeBin.price));

        // 2. Consulta os dados da posição
        const response = await fetch(`https://dlmm.datapi.meteora.ag/positions/${poolAddress}/pnl?user=${wallet.publicKey.toBase58()}`);
        const data = await response.json();
        const targetPosition = data.positions.find(pos => pos.positionAddress === positionAddress);

        if (!targetPosition) return 0;

        // 3. Extrai saldos e TAXAS
        const balanceX = parseFloat(targetPosition.unrealizedPnl.balanceTokenX.amount);
        const balanceY = parseFloat(targetPosition.unrealizedPnl.balanceTokenY.amount);
        const feeX = parseFloat(targetPosition.unrealizedPnl.unclaimedFeeTokenX.amount);
        const feeY = parseFloat(targetPosition.unrealizedPnl.unclaimedFeeTokenY.amount);
        const depositUsd = parseFloat(targetPosition.allTimeDeposits.total.usd);

        // 4. Calcula o valor atual somando os saldos E as taxas
        // Convertemos as taxas de X para USD usando o mesmo precoMeteora
        const valorAtual = ((balanceX + feeX) * precoMeteora) + (balanceY + feeY);

        return valorAtual - depositUsd;

    } catch (error) {
        console.error("Erro ao calcular PnL com taxas:", error);
        return 0;
    }
}

async function getLastPositionFromPnL(poolAddress) {
    try {
        // 1. Consulta a API para listar todas as posições do utilizador nesse pool
        const response = await fetch(`https://dlmm.datapi.meteora.ag/positions/${poolAddress}/pnl?user=${wallet.publicKey.toBase58()}`);

        if (!response.ok) throw new Error(`Erro API PnL: ${response.statusText}`);

        const data = await response.json();

        // 2. Verifica se existem posições
        if (!data.positions || data.positions.length === 0) {
            console.log(JSON.stringify({ exists: false }));
            return;
        }

        // 3. Retorna a primeira posição (a mais recente ou a que o endpoint prioriza)
        const lastPosition = data.positions[0];

        // Retornamos os dados cruciais que precisas para o teu range (lower/upper)
        // A API de PnL geralmente devolve estes campos:

        console.log(JSON.stringify({
            exists: true,
            address: lastPosition.positionAddress,
            //inRange: inRange,
            //activeBin: activeBinId,
            //lowerBin: lowerBinId,
            //upperBin: upperBinId,
            lowerPrice: parseFloat(lastPosition.minPrice),
            upperPrice: parseFloat(lastPosition.maxPrice),
            size: 1,
            //totalXAmount: parseFloat(lastPosition.unrealizedPnl.balanceTokenX.amount),
            //totalYAmount: parseFloat(lastPosition.unrealizedPnl.balanceTokenY.amount),
            //pnlUsd: pnlUsd
        }));

    } catch (error) {
        //console.error("❌ Erro ao recuperar última posição do PnL:", error);
        console.log(JSON.stringify({ status: "ERROR", message: error.message }));
        //return null;
    }
}

async function getPosition(poolAddress) {
    try {
        const dlmmPool = await DLMMClass.create(connection, new PublicKey(poolAddress));
        await dlmmPool.refetchStates();

        // 1. Extração correta baseada no JSON que enviaste
        const result = await dlmmPool.getPositionsByUserAndLbPair(wallet.publicKey, dlmmPool.pubkey);

        // O activeBinId está em: data.activeBin.binId
        const activeBinId = result.activeBin.binId;

        if (!result.userPositions || result.userPositions.length === 0) {
            console.log(JSON.stringify({ exists: false }));
            return;
        }

        const p = result.userPositions[0];
        const lowerBinId = p.positionData.lowerBinId;
        const upperBinId = p.positionData.upperBinId;

        const positionAddress = p.publicKey.toBase58();

        // 2. Validação do range
        const inRange = activeBinId >= lowerBinId && activeBinId <= upperBinId;

        const binStep = dlmmPool.lbPair.binStep;

        const rawLower = getPriceOfBinByBinId(lowerBinId, binStep);
        const rawUpper = getPriceOfBinByBinId(upperBinId, binStep);

        const lowerPrice = dlmmPool.fromPricePerLamport(rawLower);
        const upperPrice = dlmmPool.fromPricePerLamport(rawUpper);

        const totalXAmount = p.positionData.totalXAmount;
        const totalYAmount = p.positionData.totalYAmount;

        const pnlUsd = await getPositionPnL(poolAddress, positionAddress)

        console.log(JSON.stringify({
            exists: true,
            address: p.publicKey,
            inRange: inRange,
            activeBin: activeBinId,
            lowerBin: lowerBinId,
            upperBin: upperBinId,
            lowerPrice: lowerPrice,
            upperPrice: upperPrice,
            size: result.userPositions.length,
            totalXAmount: totalXAmount,
            totalYAmount: totalYAmount,
            pnlUsd: pnlUsd
        }));

    } catch (error) {
        console.log(JSON.stringify({ status: "ERROR", message: error.message }));
    }
}
// =====================================================================
// 7. TERMINAL ROUTER (CLI INTERFACE)
// =====================================================================
const args = process.argv.slice(2);
const command = args[0];

async function handleAction(promise, poolAddress, successStatus) {
    try {
        await promise;
        // Tenta limpar, mas não deixa o sucesso da operação depender da limpeza
        await new Promise(resolve => setTimeout(resolve, 5000));
        try {
            const success = await cleanupAndSettle(poolAddress, 0.02);

            if (success) {
                console.log("🎉 Ciclo de fecho e liquidação finalizado com sucesso.");
            }
        } catch (cleanupErr) {
            console.error(JSON.stringify({ status: "WARNING_CLEANUP_FAILED", message: cleanupErr.message }));
        }
        console.log(JSON.stringify({ status: successStatus }));
        process.exit(0);
    } catch (err) {
        console.error(JSON.stringify({ status: "ERROR", message: err.message }));
        process.exit(1);
    }
}

async function handleActionSimple(promise, successStatus = "SUCCESS") {
    try {
        await promise;
        console.log(JSON.stringify({ status: successStatus }));
        process.exit(0);
    } catch (err) {
        console.error(JSON.stringify({ status: "ERROR", message: err.message }));
        process.exit(1);
    }
}

if (command === "open") {
    handleAction(openBalancedPosition(args[1], parseFloat(args[2]), parseFloat(args[3]), parseFloat(args[4])), args[1], "SUCCESS_OPEN_BALANCE_POSITION");
} else if (command === "close") {
    handleAction(closeAllPoolPositionsAndSettle(args[1]), args[1], "SUCCESS_CLOSE_ALL");
} else if (command === "status") {
    //handleActionSimple(getMarketStatus(args[1]), "SUCCESS_STATUS")
    //const poolAddress = args[1];
    //getMarketStatus(poolAddress);
    //process.exit(0);
    (async () => {
        await getMarketStatus(args[1]);
        process.exit(0);
    })();
} else if (command === "rebalance") {
    handleAction(rebalancePositionByStrategy(args[1], parseFloat(args[2]), parseFloat(args[3]), parseFloat(args[4])), args[1], "SUCCESS_REBALANCE_POSITION");
} else if (command === "get_position") {
    //handleActionSimple(getPosition(args[1]), "SUCCESS_GET_POSITION")
    //const poolAddress = args[1];
    //getPosition(poolAddress);
    //process.exit(0);
    (async () => {
        await getPosition(args[1]);
        process.exit(0);
    })();
} else if (command === "get_last_position") {
    //handleActionSimple(getPosition(args[1]), "SUCCESS_GET_POSITION")
    //const poolAddress = args[1];
    //getPosition(poolAddress);
    //process.exit(0);
    (async () => {
        await getLastPositionFromPnL(args[1]);
        process.exit(0);
    })();
} else if (command === "calculate") {
    //handleActionSimple(calculateRangeMetrics(parseFloat(args[1]), parseFloat(args[2])), "SUCCESS_CALCULATE")
    //const currentPrice = parseFloat(args[1]);
    //const rangeWidthDollars = parseFloat(args[2]);
    //calculateRangeMetrics(currentPrice, rangeWidthDollars);
    //process.exit(0);
    (async () => {
        await calculateRangeMetrics(parseFloat(args[1]), parseFloat(args[2]));
        process.exit(0);
    })();
} else {
    console.log(JSON.stringify({ status: "ERROR", message: "Comando inválido. Usa 'open', 'close', 'status' ou 'rebalance'." }));
    process.exit(1);
}
