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
const { getPriceOfBinByBinId } = dlmmModule;

// =====================================================================
// 2. INFRASTRUCTURE & CONFIGURATION
// =====================================================================
const RPC_URL = "https://api.mainnet-beta.solana.com";
const connection = new Connection(RPC_URL, 'confirmed');
console.error("DEBUG: O bot Node.js foi iniciado com sucesso.");
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

function endScript(status, data = {}) {
    console.log(JSON.stringify({ status, ...data }));
    process.exit(status === "SUCCESS" ? 0 : 1);
}

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

        console.error(`🔄 [SDK Jupiter] Swap Concluído! TX: ${txid}`);
        return true;
    } catch (error) {
        console.error(`❌ [SDK Jupiter] Falha ao executar o swap: ${error.message}`);
        throw error;
    }
}

async function ensureGasTracker(currentPrice) {
    try {
        const solBalanceLamports = await connection.getBalance(wallet.publicKey);
        const solBalance = solBalanceLamports / 1_000_000_000;
        const requiredSolForGas = 10.0 / currentPrice; // Teto rígido de $10 protegido

        console.error(`⛽ [Gas Tracker] Saldo atual: ${solBalance.toFixed(4)} SOL (Mínimo Gas Protegido: ${requiredSolForGas.toFixed(4)} SOL)`);

        if (solBalance < requiredSolForGas) {
            const solNeeded = requiredSolForGas - solBalance;
            const usdcToSpend = solNeeded * currentPrice * 1.05;

            console.error(`⚠️ [Gas Tracker] Saldo baixo! A converter $${usdcToSpend.toFixed(2)} USDC para colateral de Gas...`);
            await executeJupiterSwap(USDC_MINT, WSOL_MINT, Math.round(usdcToSpend * 1_000_000));
        } else {
            console.error(`✅ [Gas Tracker] Colchão de Gas OK. Mantendo ${requiredSolForGas.toFixed(4)} SOL intactos para taxas.`);
        }

        const solBalanceLamports = await connection.getBalance(wallet.publicKey);
        if (solBalanceLamports < 5000000) { // Menos de 0.005 SOL
            throw new Error("Saldo de Gas insuficiente após tentativa de reabastecimento.");
        }
    } catch (error) {
        console.error(`❌ [Gas Tracker] Erro na gestão de taxas: ${error.message}`);
    }
}

// =====================================================================
// 4. MATHEMATICS & RANGE INTELLIGENCE
// =====================================================================

async function calculateRangeMetrics__(currentPrice, rangeWidthDollars) {
    const dlmmPool = await DLMMClass.create(connection, new PublicKey(POOL_CONFIG.address));
    const activeBin = await dlmmPool.getActiveBin();

    const pctPerBin = POOL_CONFIG.binStep / 10000;
    const dollarValuePerBin = currentPrice * pctPerBin;

    const binsHalfSide = Math.round((rangeWidthDollars / 2) / dollarValuePerBin);
    const totalBinsWidth = binsHalfSide * 2;

    const priceMin = currentPrice - (rangeWidthDollars / 2);
    const priceMax = currentPrice + (rangeWidthDollars / 2);
    const capitalMultiplier = 1 / (1 - Math.sqrt(priceMin / priceMax));

    result = {
        status: "SUCCESS",
        binsOffset: binsHalfSide,
        totalBinsWidth: totalBinsWidth,
        capitalMultiplier: capitalMultiplier,
        activeBinId: activeBin.binId
    };

    console.log(JSON.stringify(result));

    return result;
}

async function calculateRangeMetrics(currentPrice, rangePercent) {
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

    console.log(JSON.stringify(result));
    return result;
}


// =====================================================================
// 5. CORE EXECUTION FUNCTIONS
// =====================================================================
async function openBalancedPosition(totalUsdcCapital, currentPrice, rangeWidthDollars) {
    try {
        console.error(`🚀 [Meteora] A iniciar ciclo dinâmico para capital de $${totalUsdcCapital} USDC...`);

        // 1. Gas e Saldos
        await ensureGasTracker(currentPrice);
        const usdcTokenAccounts = await connection.getParsedTokenAccountsByOwner(wallet.publicKey, { mint: new PublicKey(USDC_MINT) });
        const usdcBalance = usdcTokenAccounts.value.length > 0 ? usdcTokenAccounts.value[0].account.data.parsed.info.tokenAmount.uiAmount : 0;

        // 2. Balanceamento
        const alvoMetadeUsdc = totalUsdcCapital / 2;
        if (Math.abs(usdcBalance - alvoMetadeUsdc) > 0.50) {
            const solParaVender = (alvoMetadeUsdc - usdcBalance) / currentPrice;
            await executeJupiterSwap(WSOL_MINT, USDC_MINT, Math.round(solParaVender * 1_000_000_000));
            await new Promise(r => setTimeout(r, 3000));
        }

        // 3. Preparação
        const dlmmPool = await DLMMClass.create(connection, new PublicKey(POOL_CONFIG.address));

        // --- DIAGNÓSTICO ATIVO ---
        //const proto = Object.getPrototypeOf(dlmmPool);
        //const methods = Object.getOwnPropertyNames(proto);
        //console.log("DEBUG: Métodos disponíveis:", methods);
        // -------------------------

        const metrics = await calculateRangeMetrics(currentPrice, rangeWidthDollars);
        const positionKeypair = Keypair.generate();
        const solFinalAInjetar = alvoMetadeUsdc / currentPrice;
        const totalXAmount = new anchor.BN(Math.floor(solFinalAInjetar * 1_000_000_000));
        const totalYAmount = new anchor.BN(Math.floor(alvoMetadeUsdc * 1_000_000));

        console.error(`⚡ A injetar X:${totalXAmount.toString()} Y:${totalYAmount.toString()}...`);

        // 4. Injeção Dinâmica (Adaptativa)
        console.error("⚡ A injetar via estratégia de Spot conforme doc...");

        // Usar os BN (BigNumbers) que já calculaste anteriormente
        // totalXAmount e totalYAmount já estão definidos no teu código

        const tx = await dlmmPool.initializePositionAndAddLiquidityByStrategy({
            positionPubKey: positionKeypair.publicKey,
            user: wallet.publicKey,
            baseKeyPair: positionKeypair,
            lbPair: dlmmPool.pubkey,
            totalXAmount: new anchor.BN(Math.floor(totalXAmount.toNumber() * 0.995)),
            totalYAmount: new anchor.BN(Math.floor(totalYAmount.toNumber() * 0.995)),
            strategy: {
                minBinId: metrics.activeBinId - metrics.binsOffset,
                maxBinId: metrics.activeBinId + metrics.binsOffset,
                strategyType: 0, // 0 = Spot
            },
        });

        // O SDK pode devolver uma transação ou um array (se for necessário criar bin arrays)
        if (Array.isArray(tx)) {
            for (const t of tx) {
                await provider.sendAndConfirm(t, [positionKeypair]);
            }
        } else {
            await provider.sendAndConfirm(tx, [positionKeypair]);
        }

        console.error(`✅ [SUCCESS_OPEN] Posição injetada com sucesso!`);
        endScript("SUCCESS");
    } catch (error) {
        console.error(`❌ [ERROR_OPEN] ${error.message}`);
        endScript("ERROR", { message: error.message });
    }
}

async function closeAllPoolPositionsAndSettle() {
    try {
        console.error(`🛑 [Meteora] Protocolo de fecho e liquidação total acionado...`);
        const dlmmPool = await DLMMClass.create(connection, new PublicKey(POOL_CONFIG.address));

        // CORREÇÃO 1: Usar o método correto de busca de posições
        const userPositions = await dlmmPool.getPositionsByUserAndLbPair(wallet.publicKey, dlmmPool.pubkey);

        if (userPositions.length === 0) {
            endScript("SUCCESS", { message: "Nenhuma posição para fechar." });
        }

        for (const position of userPositions) {
            console.error(`🧹 A remover liquidez da posição: ${position.publicKey.toBase58()}`);

            // CORREÇÃO 2: A v1.9.10 exige a lista de Bins e Liquidez para o removeLiquidity
            // O objeto 'position' na 1.9.10 tem 'positionData.binData'
            const removeLiquidityTx = await dlmmPool.removeLiquidity({
                user: wallet.publicKey,
                position: position.publicKey,
                binIds: position.positionData.binData.map(b => b.binId),
                liquidities: position.positionData.binData.map(b => b.liquidity),
                bps: new anchor.BN(10_000),
                shouldClaimAndClose: true, // Substituindo 'shouldClosePosition' que é legado
            });

            await provider.sendAndConfirm(removeLiquidityTx, [wallet.payer]);
        }

        console.error(`✅ Liquidez removida. Aguardando confirmação...`);
        await new Promise(resolve => setTimeout(resolve, 2000));

        // ... (resto do teu código de liquidação de SOL permanece igual)
        endScript("SUCCESS");
    } catch (error) {
        console.error(`❌ [ERROR_CLOSE] Falha na liquidação final: ${error.message}`);
        endScript("ERROR", { message: error.message });
    }
}

// =====================================================================
// 6. CONSULTATION & DIAGNOSTIC METHODS (Read-Only)
// =====================================================================

async function getMarketStatus() {
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

        const dlmmPool = await DLMMClass.create(connection, new PublicKey(POOL_CONFIG.address));
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
                address: POOL_CONFIG.address,
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

function getPriceFromBin(binId, dlmmPool) {
    // O SDK tem um método chamado 'fromPricePerLamport' ou 'toPrice'
    // dependendo da versão, mas a lógica padrão é esta:
    const price = dlmmPool.fromPricePerLamport(dlmmPool.getBinPrice(binId));
    return parseFloat(price);
}

async function sendTransactionSafely(provider, transaction) {
    // 1. Identificar se é uma transação versão V0 (VersionedTransaction) ou Legacy
    const isVersioned = transaction.version !== undefined;

    if (isVersioned) {
        // Para VersionedTransaction, assinamos com o wallet.signTransaction
        const signedTx = await provider.wallet.signTransaction(transaction);
        return await connection.sendRawTransaction(signedTx.serialize());
    } else {
        // Para Legacy Transaction, usamos o padrão que você já conhece
        await transaction.partialSign(wallet.payer);
        return await connection.sendRawTransaction(transaction.serialize());
    }
}

async function rebalancePositionByStrategy(positionPublicKey, totalUsdcCapital, currentPrice, rangeWidthDollars) {
    console.error("DEBUG: Implementação estrita via docs.meteora.ag...");

    try {
        const dlmmPool = await DLMMClass.create(connection, new PublicKey(POOL_CONFIG.address));
        await dlmmPool.refetchStates();


        // 1. Extração correta baseada no JSON que enviaste
        const result = await dlmmPool.getPositionsByUserAndLbPair(wallet.publicKey, dlmmPool.pubkey);

        // O activeBinId está em: data.activeBin.binId
        const activeBinId = result.activeBin.binId;

        if (!result.userPositions || result.userPositions.length === 0) {
            throw new Error("Posição não encontrada no pool.");
        }

        const p = result.userPositions[0];
        const lowerBinId = p.positionData.lowerBinId;
        const upperBinId = p.positionData.upperBinId;


        // 2. Executar conforme a docs
        const removeResult = await dlmmPool.removeLiquidity({
            position: p.publicKey,
            user: wallet.publicKey,
            fromBinId: lowerBinId,
            toBinId: upperBinId,
            bps: new BN(10000), // 100%
            shouldClaimAndClose: true,
        });

        // 3. A documentação indica que removeLiquidity retorna uma transação (ou um array)
        // Corrigimos aqui para garantir que lidamos com VersionedTransactions
        const transactions = Array.isArray(removeResult) ? removeResult : [removeResult];

        for (const txObj of transactions) {
            // Se o retorno for um objeto com a propriedade 'tx', usamos ela
            const tx = txObj.tx || txObj;

            // Usamos a assinatura do provider conforme a docs do Anchor
            const signature = await provider.sendAndConfirm(tx);
            console.error("✅ Confirmado:", signature);
        }

        console.error("✅ Posição fechada.");

        // 1. Gas e Saldos
        await ensureGasTracker(currentPrice);
        const usdcTokenAccounts = await connection.getParsedTokenAccountsByOwner(wallet.publicKey, { mint: new PublicKey(USDC_MINT) });
        const usdcBalance = usdcTokenAccounts.value.length > 0 ? usdcTokenAccounts.value[0].account.data.parsed.info.tokenAmount.uiAmount : 0;

        // 2. Balanceamento
        const alvoMetadeUsdc = totalUsdcCapital / 2;
        if (Math.abs(usdcBalance - alvoMetadeUsdc) > 0.50) {
            const solParaVender = (alvoMetadeUsdc - usdcBalance) / currentPrice;
            await executeJupiterSwap(WSOL_MINT, USDC_MINT, Math.round(solParaVender * 1_000_000_000));
            await new Promise(r => setTimeout(r, 3000));
        }

        const metrics = await calculateRangeMetrics(currentPrice, rangeWidthDollars);
        const solFinalAInjetar = alvoMetadeUsdc / currentPrice;
        const totalXAmount = new anchor.BN(Math.floor(solFinalAInjetar * 1_000_000_000));
        const totalYAmount = new anchor.BN(Math.floor(alvoMetadeUsdc * 1_000_000));

        const tx = await dlmmPool.addLiquidityByStrategy({
          positionPubKey: p.publicKey,
          user: wallet.publicKey,
          totalXAmount: new anchor.BN(Math.floor(totalXAmount.toNumber() * 0.995)),
          totalYAmount: new anchor.BN(Math.floor(totalYAmount.toNumber() * 0.995)),
          strategy: {
            minBinId: metrics.activeBinId - metrics.binsOffset,
            maxBinId: metrics.activeBinId + metrics.binsOffset,
            strategyType: 0,
          },
        });


        if (Array.isArray(tx)) {
            for (const t of tx) {
                await provider.sendAndConfirm(t, [positionKeypair]);
            }
        } else {
            await provider.sendAndConfirm(tx, [positionKeypair]);
        }

        endScript("SUCCESS");

    } catch (error) {
        console.error("❌ ERRO NA IMPLEMENTAÇÃO OFICIAL:", error);
        endScript("ERROR", { message: error.message });
    }
}

async function checkPositionStatus() {
    try {
        const dlmmPool = await DLMMClass.create(connection, new PublicKey(POOL_CONFIG.address));
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

        // 2. Validação do range
        const inRange = activeBinId >= lowerBinId && activeBinId <= upperBinId;

        const binStep = dlmmPool.lbPair.binStep;

        const rawLower = getPriceOfBinByBinId(lowerBinId, binStep);
        const rawUpper = getPriceOfBinByBinId(upperBinId, binStep);

        const lowerPrice = dlmmPool.fromPricePerLamport(rawLower);
        const upperPrice = dlmmPool.fromPricePerLamport(rawUpper);

        console.log(JSON.stringify({
            exists: true,
            address: p.publicKey,
            inRange: inRange,
            activeBin: activeBinId,
            lowerBin: lowerBinId,
            upperBin: upperBinId,
            lowerPrice: lowerPrice,
            upperPrice: upperPrice
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

if (command === "open") {
    const totalUsdc = parseFloat(args[1]);
    const currentPrice = parseFloat(args[2]);
    const rangeWidth = parseFloat(args[3]);
    openBalancedPosition(totalUsdc, currentPrice, rangeWidth);
} else if (command === "close") {
    closeAllPoolPositionsAndSettle();
} else if (command === "status") {
    getMarketStatus();
} else if (command === "rebalance") {
    const positionPublicKey = new PublicKey(args[1]);
    const totalUsdc = parseFloat(args[2]);
    const currentPrice = parseFloat(args[3]);
    const rangeWidth = parseFloat(args[4]);
    rebalancePositionByStrategy(positionPublicKey, totalUsdc, currentPrice, rangeWidth);
} else if (command === "check") {
    checkPositionStatus();
} else if (command === "calculate") {
    const currentPrice = parseFloat(args[1]);
    const rangeWidthDollars = parseFloat(args[2]);
    calculateRangeMetrics(currentPrice, rangeWidthDollars);
} else {
    console.log(JSON.stringify({ status: "ERROR", message: "Comando inválido. Usa 'open', 'close', 'status' ou 'rebalance'." }));
    process.exit(1);
}