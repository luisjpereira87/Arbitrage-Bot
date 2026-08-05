const { Connection, Keypair, PublicKey, VersionedTransaction } = require('@solana/web3.js');
const { createJupiterApiClient } = require('@jup-ag/api');
const AnchorProvider = require('@coral-xyz/anchor').AnchorProvider;
const Wallet = require('@coral-xyz/anchor').Wallet;
const anchor = require('@coral-xyz/anchor');
const path = require('path');
const fs = require('fs');
const Decimal = require('decimal.js');
const { buildAndSendTransaction } = require("@orca-so/tx-sender");

// Importações do SDK da Orca
const {
    openConcentratedPosition,
    closePosition,
    WhirlpoolDeployment,
    fetchPositionsForOwner,
    PoolUtil,
    harvestPosition,
    setRpc,
    setDefaultFunder,
    setPayerFromBytes
} = require("@orca-so/whirlpools");

const { fetchWhirlpool } = require( "@orca-so/whirlpools-client");

const { address, createSolanaRpc, createKeyPairSignerFromBytes } = require("@solana/kit");

const { PriceMath } = require("@orca-so/whirlpools-core");

// --- INTERCEPTOR GLOBAL DE ERROS (Colocar no topo absoluto) ---
process.on('uncaughtException', (err) => {
    console.log(JSON.stringify({
        status: "ERROR",
        message: `Uncaught Exception: ${err.message}`
    }));
    process.exit(1);
});

process.on('unhandledRejection', (reason, promise) => {
    console.log(JSON.stringify({
        status: "ERROR",
        message: `Unhandled Rejection: ${reason.message || reason}`
    }));
    process.exit(1);
});

// =====================================================================
// 1. CARREGAMENTO ROBUSTO DO .ENV & IMPORTAÇÕES DINÂMICAS
// =====================================================================
const envPath = path.resolve(__dirname, '../../.env');
if (fs.existsSync(envPath)) {
    require('dotenv').config({ path: envPath });
} else {
    require('dotenv').config({ path: path.resolve(__dirname, '.env') });
}

// =====================================================================
// 2. INFRASTRUCTURE & CONFIGURATION
// =====================================================================
const RPC_URL = "https://api.mainnet-beta.solana.com";
const connection = new Connection(RPC_URL, 'confirmed');
const rpc = createSolanaRpc(RPC_URL);
const devnetRpc = createSolanaRpc("https://api.mainnet-beta.solana.com");

let walletKeypair;
try {
    const privateKeyStr = process.env.PRIVATE_KEY_WALLET_SOLANA;
    if (!privateKeyStr) throw new Error("A variável PRIVATE_KEY_WALLET_SOLANA não foi encontrada no .env");

    const bs58Module = require('bs58');
    let decodeFn = typeof bs58Module === 'function' ? bs58Module : (bs58Module.decode || bs58Module.default?.decode);
    if (!decodeFn) decodeFn = anchor.utils.bytes.bs58.decode;

    walletKeypair = Keypair.fromSecretKey(decodeFn(privateKeyStr.trim()));

    const secretKeyBytes = decodeFn(privateKeyStr.trim());
    //setPayerFromBytes(new Uint8Array(secretKeyBytes));
} catch (e) {
    console.error(`❌ [Setup] Erro crítico ao carregar a carteira: ${e.message}`);
    process.exit(1);
}

async function loadWallet() {
    const bs58Module = require('bs58');
    let decodeFn = typeof bs58Module === 'function' ? bs58Module : (bs58Module.decode || bs58Module.default?.decode);
    if (!decodeFn) decodeFn = anchor.utils.bytes.bs58.decode;

    const privateKeyStr = process.env.PRIVATE_KEY_WALLET_SOLANA;
    if (!privateKeyStr) {
        throw new Error('PRIVATE_KEY_WALLET_SOLANA not set in .env');
    }

    const bytes = new Uint8Array(decodeFn(privateKeyStr.trim()));

    // Se o createKeyPairSignerFromBytes está a retornar um formato incompatível com a v8,
    // podes usar diretamente os bytes ou garantir que o import do createKeyPairSignerFromBytes
    // vem exatamente da mesma versão do @solana/kit que a Orca está a usar nas dependências.
    return await createKeyPairSignerFromBytes(bytes);
}

async function setEnvAndLoadWallet(){
        // 1. Configurar o ambiente global exatamente como o exemplo faz
        const rpcEndpoint = process.env.RPC_ENDPOINT_URL || "https://api.mainnet-beta.solana.com";
        const rpc = createSolanaRpc(rpcEndpoint);

        await setRpc(rpcEndpoint);

        // Carregar a private key em bytes a partir da tua variável de ambiente
        const bs58 = require('bs58');
        const decodeFn = bs58.decode || bs58.default?.decode;

        if (!decodeFn) {
            throw new Error('Não foi possível carregar a função de decode do bs58');
        }
        const privateKeyBytes = new Uint8Array(decodeFn(process.env.PRIVATE_KEY_WALLET_SOLANA.trim()));

        const signer = await setPayerFromBytes(privateKeyBytes);
        //setDefaultFunder(signer.address);
        console.log('Signer configurado:', signer.address);

        return signer
}

//const ownerAddress = "DpUwFAAarUjQGzAKFviSvvbtoVz28Pg2bTZ5kb17SBuJ";
//const ownerAddress = walletKeypair.publicKey.toBase58();
//const owner = address(ownerAddress);

// Inicialização global única
//setRpc(RPC_URL);
//setDefaultFunder(ownerAddress);


const wallet = new Wallet(walletKeypair);
const provider = new AnchorProvider(connection, wallet, AnchorProvider.defaultOptions());
//const ctx = WhirlpoolContext.from(connection, wallet, anchor.web3.PublicKey.default); // O SDK deteta o programa Orca automaticamente
//const client = buildWhirlpoolClient(ctx);
const jupiterQuoteApi = createJupiterApiClient();

const POOL_CONFIG = {
    address: "CeaZcxBNLpJWtxzt58qQmfMBtJY8pQLvursXTJYGQpbN", // Substitui pelo endereço da pool na Orca
    tokenX: { symbol: "SOL", decimals: 9 },
    tokenY: { symbol: "USDC", decimals: 6 }
};

const USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
const WSOL_MINT = "So11111111111111111111111111111111111111112";

// =====================================================================
// 3. JUPITER SWAP ENGINE & GAS SAFETY TRACKER (Mantém-se idêntico)
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
            const solFaltante = (totalExigido - balanceLamports) / 1_000_000_000;
            const usdcToSpend = solFaltante * currentPrice * 1.05;

            const usdcAccounts = await connection.getParsedTokenAccountsByOwner(wallet.publicKey, { mint: new PublicKey(USDC_MINT) });
            const usdcBalance = usdcAccounts.value.length > 0 ? usdcAccounts.value[0].account.data.parsed.info.tokenAmount.uiAmount : 0;

            if (usdcToSpend > usdcBalance) {
                console.error(`🚨 [Gas Tracker] USDC insuficiente! Precisas de ${usdcToSpend.toFixed(4)} USDC.`);
                return false;
            }

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

async function cleanupAndSettle(poolAddress, targetSolAmount = 0.02) {
    try {
        console.log(`🧹 [Cleaner] Iniciando consolidação (Alvo de SOL fixo: ${targetSolAmount} SOL)...`);
        const solBalance = await connection.getBalance(wallet.publicKey);
        const solBalanceUi = solBalance / 1_000_000_000;
        const gasMargin = 0.005;

        if (solBalanceUi > (targetSolAmount + gasMargin)) {
            const excessoSol = solBalanceUi - targetSolAmount - gasMargin;
            console.log(`🔄 [Cleaner] Consolidando excedente de SOL: ${excessoSol.toFixed(4)} SOL`);
            await executeJupiterSwap("So11111111111111111111111111111111111111112", USDC_MINT, Math.round(excessoSol * 1_000_000_000));
            await new Promise(r => setTimeout(r, 5000));
        }

        const tokenAccounts = await connection.getParsedTokenAccountsByOwner(wallet.publicKey, {
            programId: new PublicKey("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
        });

        for (const account of tokenAccounts.value) {
            const parsedInfo = account.account.data.parsed.info;
            const mint = parsedInfo.mint;
            const amountRaw = parsedInfo.tokenAmount.amount;
            const balance = parsedInfo.tokenAmount.uiAmount;

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
// 4. MATHEMATICS & RANGE INTELLIGENCE (Convertido para Ticks da Orca)
// =====================================================================
async function calculateOrcaRangeMetrics(currentPrice, rangePercent, whirlpoolData) {
    const tickSpacing = whirlpoolData.tickSpacing;

    const rangeWidthDollars = currentPrice * rangePercent;
    const priceMin = currentPrice - (rangeWidthDollars / 2);
    const priceMax = currentPrice + (rangeWidthDollars / 2);

    // Conversão matemática direta de preço para índice de tick compatível com a pool
    let tickLowerIndex = Math.floor(Math.log(priceMin) / Math.log(1.0001));
    let tickUpperIndex = Math.floor(Math.log(priceMax) / Math.log(1.0001));

    // Alinhamento obrigatório ao tick spacing da pool
    tickLowerIndex = Math.floor(tickLowerIndex / tickSpacing) * tickSpacing;
    tickUpperIndex = Math.floor(tickUpperIndex / tickSpacing) * tickSpacing;

    // Converter ticks finais para preços reais limpios
    const validatedPriceMin = Math.pow(1.0001, tickLowerIndex);
    const validatedPriceMax = Math.pow(1.0001, tickUpperIndex);

    return {
        priceMin: validatedPriceMin,
        priceMax: validatedPriceMax,
        tickLowerIndex,
        tickUpperIndex
    };
}

// =====================================================================
// 5. CORE EXECUTION FUNCTIONS (Orca Implementation)
// =====================================================================
async function openBalancedPositionOrca(poolAddress, totalUsdcCapital, currentPrice, rangeWidthPercent) {

    //position = await getPositionOrca(poolAddress);
   /** position = await withRetry(() => getPositionOrca(poolAddress), 3, 2000);

    if (position){
        throw new Error("Existe uma posição aberta...");
    }**/

    const signer = await setEnvAndLoadWallet();

    const whirlpool = await fetchWhirlpool(rpc, poolAddress);
    const whirlpoolData = whirlpool.data;
    const tokenMintA = whirlpoolData.tokenMintA;
    const tokenMintB = whirlpoolData.tokenMintB;

    const tokenInfo = await getTokenInfo(tokenMintA, tokenMintB);
    const decimalsTokenA = tokenInfo.decimalsTokenA;
    const decimalsTokenB = tokenInfo.decimalsTokenB;

    // Cálculo de métricas baseado na mesma estrutura validada
    const metrics = await calculateOrcaRangeMetrics(currentPrice, rangeWidthPercent, whirlpoolData);
    console.log(metrics)
    console.log(`🚀 [Orca] A calcular e abrir posição concentrada para $${totalUsdcCapital} USDC...`);

    // Definir montantes (Ex: 50% Token A / 50% Token B)
    const tokenBVal = totalUsdcCapital * 0.5;
    const tokenAVal = totalUsdcCapital * 0.5;
    const tokenAmountA = tokenAVal / currentPrice;

    // Conversão rigorosa para BigInt com os decimais corretos
    const tokenMaxA = BigInt(Math.floor(tokenAmountA * Math.pow(10, decimalsTokenA)));
    const tokenMaxB = BigInt(Math.floor(tokenBVal * Math.pow(10, decimalsTokenB)));

    // Garantir Gás/Rent usando o ensureGasTracker (caso o Token A seja SOL/WSOL, usamos o valor em lamports dele; caso contrário, estimamos uma base segura para taxas e alugueres)
    const estimatedLamportsNeeded = whirlpoolData.tokenMintA === WSOL_MINT || whirlpoolData.tokenMintA === "So11111111111111111111111111111111111111112"
        ? Number(tokenMaxA)
        : 10_000_000; // Buffer base para contas e metadados se o Token A não for SOL

    const gasOk = await ensureGasTracker(currentPrice, estimatedLamportsNeeded);
    console.log("⏳ A aguardar 3 segundos para propagação do saldo de SOL...");
    await new Promise(resolve => setTimeout(resolve, 3000));
    if (!gasOk) {
        throw new Error("Falha no reabastecimento de gás/rent (ensureGasTracker falhou na Orca).");
    }

    // Chamada oficial da API da Orca com o mesmo padrão de execução
    const {
        instructions,
        initializationCost,
        positionMint,
        callback: sendTx,
    } = await openConcentratedPosition(
        address(poolAddress),
        {
            tokenMaxA: tokenMaxA,
            tokenMaxB: tokenMaxB,
        },
        metrics.priceMin,
        metrics.priceMax,
        {
            slippageToleranceBps: 100, // 1% de slippage
            withTokenMetadataExtension: true,
            whirlpoolDeployment: WhirlpoolDeployment.mainnet,
            funder: signer

        }
    );

    const txId = await sendTx();

    console.log(`✅ [Orca] Posição aberta com sucesso! Address: ${positionMint} | TX: ${txId}`);
    return true;
}

async function closeAllPoolPositionsAndSettleOrca(poolAddress) {
    console.log(`🛑 [Orca] A fechar posições na Whirlpool...`);

    const signer = await setEnvAndLoadWallet();

    const positions = await fetchPositionsForOwner(
        rpc,
        signer.address,
        WhirlpoolDeployment.mainnet,
    );

    const poolPositions = positions.filter(p => p.data && p.data.whirlpool === poolAddress);

    if (poolPositions.length === 0) {
        console.log("Nenhuma posição da Orca para fechar.");
        return true;
    }

    for (const position of poolPositions) {
        const positionMint = position.data.positionMint;
        const positionPubkeyStr = position.address;
        console.log(`🧹 A fechar posição Orca: ${positionPubkeyStr}`);

        const liquidity = position.data.liquidity;
        if (liquidity <= 0) continue;

        // Utilização da função moderna de fecho/recolha integrada no ecossistema
        const { callback: closeTx } = await closePosition(
            address(positionMint),
            {
                whirlpoolDeployment: WhirlpoolDeployment.mainnet,
                slippageToleranceBps: 100,
                funder: signer,
                authority: signer
            }
        );

        const txid = await closeTx();
        console.log(`✅ Posição ${positionPubkeyStr} fechada com sucesso! TX: ${txid}`);
    }

    console.log(`✅ Todas as posições da Orca foram fechadas e liquidadas.`);
    return true;
}

async function rebalancePositionByStrategy(poolAddress, totalUsdcCapital, currentPrice, rangeWidthDollars) {
    console.log("DEBUG: Executando estratégia de Rebalanceamento via Fecho/Abertura...");

    try {
        // 1. FECHO BLINDADO: Garante on-chain que a posição deixou de existir
        console.log("🧹 A fechar posição antiga com verificação de estado...");
        await waitForState(
            async () => await closeAllPoolPositionsAndSettleOrca(poolAddress),
            async () => {
                try {
                    return await getPositionOrca(poolAddress);
                } catch (e) {
                    return null; // Se der erro a ler, é porque já foi eliminada com sucesso
                }
            },
            false // Queremos que a posição DEIXE de existir
        );

        // Pequena pausa de segurança entre o fecho e a nova abertura para propagação de saldos
        console.log("⏳ Posição fechada confirmada. A aguardar 3s para reabertura...");
        await new Promise(resolve => setTimeout(resolve, 3000));

        // 2. ABERTURA BLINDADA: Garante on-chain que a nova posição passou a existir
        console.log("🚀 A abrir nova posição no novo range com verificação de estado...");
        await waitForState(
            async () => await openBalancedPositionOrca(poolAddress, totalUsdcCapital, currentPrice, rangeWidthDollars),
            async () => await getPositionOrca(poolAddress),
            true // Queremos que a posição PASSE a existir
        );

        console.log("✅ Rebalanceamento concluído com sucesso e validado on-chain.");
        return true;
    } catch (error) {
        console.error("❌ Erro Crítico no Rebalanceamento:", error.message);
        throw error; // O router apanha e envia o status ERROR para o Python
    }
}

async function getMarketStatusOrca(poolAddress) {
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

        const whirlpool = await fetchWhirlpool(rpc, poolAddress);
        const whirlpoolData = whirlpool.data;
        const tokenMintA = whirlpoolData.tokenMintA
        const tokenMintB = whirlpoolData.tokenMintB

        const tokenInfo = await getTokenInfo(tokenMintA, tokenMintB)
        const decimalsTokenA = tokenInfo.decimalsTokenA
        const decimalsTokenB = tokenInfo.decimalsTokenB
        const tokenPriceA = tokenInfo.priceUsdcTokenA
        const tokenPriceB = tokenInfo.priceUsdcTokenB

        const activeTickIndex = whirlpoolData.tickCurrentIndex;

        //const currentPrice = Math.pow(1.0001, activeTickIndex);
        const currentPrice = Math.pow(1.0001, activeTickIndex) * Math.pow(10, decimalsTokenA - decimalsTokenB);

        const statusReport = {
            status: "SUCCESS",
            wallet: wallet.publicKey.toBase58(),
            balances: {
                SOL: solBalance,
                USDC: usdcBalance
            },
            pool: {
                address: poolAddress,
                activeTickIndex: activeTickIndex,
                rawPrice: currentPrice
            }
        };

        //console.log(JSON.stringify(statusReport));
        //process.exit(0);
        return statusReport;
    } catch (error) {
        const errorReport = {
            status: "ERROR",
            message: error.message
        };
        //console.log(JSON.stringify(errorReport));
        throw error;
        //process.exit(1);
    }
}

async function getTokenInfo(tokenMintA, tokenMintB){
    try {

        usdcAddress = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
        usdcDecimals = 1e6

        const tokenAResponse = await fetch(`https://api.orca.so/v2/solana/tokens/${tokenMintA}`);
        const data1  = await tokenAResponse.json();
        const decimalsA = data1.data.decimals;
        const priceUsdcTokenA = data1.data.priceUsdc;
        const tokenBResponse = await fetch(`https://api.orca.so/v2/solana/tokens/${tokenMintB}`);
        const data2  = await tokenBResponse.json();
        const decimalsB = data2.data.decimals;
        const priceUsdcTokenB = data2.data.priceUsdc;

        return {
            addressTokenA: tokenMintA,
            addressTokenB: tokenMintB,
            decimalsTokenA: decimalsA,
            decimalsTokenB: decimalsB,
            priceUsdcTokenA: priceUsdcTokenA,
            priceUsdcTokenB: priceUsdcTokenB,
        };

    } catch (error) {
        //console.error("Erro ao ir buscar dados da API de informação de token:", error.message);
        throw new Error("Erro ao ir buscar dados da API de informação de token:", error.message);
        //return null;
    }
}

async function getPositionOrca(poolAddress) {
    try {
        const signer = await setEnvAndLoadWallet();

        const positions = await fetchPositionsForOwner(
            rpc,
            signer.address,
            WhirlpoolDeployment.mainnet,
        );

        if (!positions || positions.length === 0) {
            //console.log(JSON.stringify({ exists: false }));
            //return;
            throw new Error("RPC ainda não indexou a posição (lista vazia). A tentar novamente...");
        }

        const targetPosition = positions.find(p => p.data && p.data.whirlpool === poolAddress);

        if (!targetPosition) {
            //console.log(JSON.stringify({ exists: false }));
            //return;
            throw new Error(`Posição para a pool ${poolAddress} ainda não encontrada no RPC. A tentar novamente...`);
        }

        const whirlpool = await fetchWhirlpool(rpc, poolAddress);
        const whirlpoolData = whirlpool.data;
        const tokenMintA = whirlpoolData.tokenMintA
        const tokenMintB = whirlpoolData.tokenMintB

        const tokenInfo = await getTokenInfo(tokenMintA, tokenMintB)
        const decimalsTokenA = tokenInfo.decimalsTokenA
        const decimalsTokenB = tokenInfo.decimalsTokenB
        const tokenPriceA = tokenInfo.priceUsdcTokenA
        const tokenPriceB = tokenInfo.priceUsdcTokenB

        const activeTickIndex = whirlpoolData.tickCurrentIndex;
        const lowerTickIndex = targetPosition.data.tickLowerIndex;
        const upperTickIndex = targetPosition.data.tickUpperIndex;
        const positionMint = targetPosition.data.positionMint;
        const positionAddress = targetPosition.address;

        const inRange = activeTickIndex >= lowerTickIndex && activeTickIndex <= upperTickIndex;

        // 1. Obter investimento inicial e valores base da API de PnL da Orca
        let initialUsd = 0;
        let apiCurrentValueUsd = 0;
        let apiFeesUsd = 0;

        try {
            const statsUrl = `https://stats-api.mainnet.orca.so/api/pnl/summary?wallet=${signer.address}&position=${positionAddress}`;
            const statsRes = await fetch(statsUrl);
            const statsJson = await statsRes.json();

            if (statsJson.data && statsJson.data.length > 0) {
                const apiData = statsJson.data[0];
                initialUsd = apiData.total_deposits_usd || 0;

                // Se a API da Orca fornecer diretamente métricas de valor atual e fees em USD, aproveitamos
                if (apiData.current_value_usd) apiCurrentValueUsd = apiData.current_value_usd;
                if (apiData.unclaimed_fees_usd) apiFeesUsd = apiData.unclaimed_fees_usd;
            }
        } catch (apiErr) {
            // Ignora falhas da API
        }

        // Montantes base da pool calculados via liquidez
        const lowerPrice = Math.pow(1.0001, lowerTickIndex);
        const upperPrice = Math.pow(1.0001, upperTickIndex);

        let amountA = 0;
        let amountB = 0;
        const liquidity = Number(targetPosition.data.liquidity);

        if (liquidity > 0) {
            const currentPrice = Math.pow(1.0001, activeTickIndex);

            const sqrtP = Math.sqrt(currentPrice);
            const sqrtLower = Math.sqrt(lowerPrice);
            const sqrtUpper = Math.sqrt(upperPrice);

            if (activeTickIndex < lowerTickIndex) {
                amountA = liquidity * (sqrtUpper - sqrtLower) / (sqrtLower * sqrtUpper);
            } else if (activeTickIndex >= upperTickIndex) {
                amountB = liquidity * (sqrtUpper - sqrtLower);
            } else {
                amountA = liquidity * (sqrtUpper - sqrtP) / (sqrtP * sqrtUpper);
                amountB = liquidity * (sqrtP - sqrtLower);
            }
        }

        const finalAmountA = Math.max(0, amountA / Math.pow(10, decimalsTokenA));
        const finalAmountB = Math.max(0, amountB / Math.pow(10, decimalsTokenB));

        let feeA = Number(targetPosition.data.feeOwedA) / Math.pow(10, decimalsTokenA);
        let feeB = Number(targetPosition.data.feeOwedB) / Math.pow(10, decimalsTokenB);

        // Obter taxas pendentes reais via SDK
        try {
            const positionPda = address(positionMint);
            const { feesQuote } = await harvestPosition(positionPda, {
                whirlpoolDeployment: WhirlpoolDeployment.mainnet,
                funder: signer,
                authority: signer
            });
            if (feesQuote) {
                feeA = Number(feesQuote.feeOwedA) / Math.pow(10, decimalsTokenA);
                feeB = Number(feesQuote.feeOwedB) / Math.pow(10, decimalsTokenB);
            }
        } catch (e) {
            // Mantém os valores estáticos
             //console.log(JSON.stringify({ status: "ERROR", message: e.message }));
        }

        // 1. Totais reais (Tokens principais + Taxas pendentes)
        const totalTokenA = finalAmountA + feeA;
        const totalTokenB = finalAmountB + feeB;

        // 2. Cálculo do valor atual em USD puramente dinâmico
        const calculatedCurrentValueUsd = (totalTokenA * tokenPriceA) + (totalTokenB * tokenPriceB);

        // 3. Taxas totais em USD
        const totalFeesUsd = (feeA * tokenPriceA) + (feeB * tokenPriceB);

        // 4. PnL dinâmico com base no investimento inicial real e valor atual calculado
        const pnlUsd = initialUsd > 0 ? calculatedCurrentValueUsd - initialUsd : 0;
        const pnlPercentage = initialUsd > 0 && initialUsd !== 0 ? (pnlUsd / initialUsd) * 100 : 0;

        const lowerPriceFinal = Math.pow(1.0001, lowerTickIndex) * Math.pow(10, decimalsTokenA - decimalsTokenB);
        const upperPriceFinal = Math.pow(1.0001, upperTickIndex) * Math.pow(10, decimalsTokenA - decimalsTokenB);


        result = {
            exists: true,
            address: positionAddress,
            positionMint: positionMint,
            inRange: inRange,
            activeTick: activeTickIndex,
            lowerTick: lowerTickIndex,
            upperTick: upperTickIndex,
            lowerPrice: lowerPriceFinal,
            upperPrice: upperPriceFinal,
            size: 1,
            totalXAmount: finalAmountA,
            totalYAmount: finalAmountB,
            feesX: feeA,
            feesY: feeB,
            feesUsd: Number(totalFeesUsd.toFixed(2)),
            initialInvestmentUsd: initialUsd,
            currentValueUsd: Number(calculatedCurrentValueUsd.toFixed(2)),
            pnlUsd: Number(pnlUsd.toFixed(2)),
            pnlPercentage: Number(pnlPercentage.toFixed(2))
        }
        //console.log(JSON.stringify(result));
        return result;

    } catch (error) {
        //console.log(JSON.stringify({ status: "ERROR", message: error.message }));
        throw error;
        //return null;
    }
}

async function withRetry(asyncFn, maxRetries = 3, delayMs = 2000) {
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
        try {
            // CORRIGIDO: Usa parênteses para executar a função e devolve o resultado
            return await asyncFn(); 
        } catch (error) {
            if (attempt < maxRetries) {
                console.error(`⚠️ [Retry ${attempt}/${maxRetries}] Falha temporária: ${error.message}. A tentar novamente...`);
            } else {
                // Só quando esgotam todas é que enviamos o JSON estruturado fatal para o Python
                console.log(JSON.stringify({
                    status: "ERROR",
                    message: `Esgotadas as ${maxRetries} tentativas. Erro final: ${error.message}`
                }));
                throw new Error(`Esgotadas as ${maxRetries} tentativas. Erro final: ${error.message}`);
            }

            await new Promise(resolve => setTimeout(resolve, delayMs * attempt));
        }
    }
}

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
        console.log(JSON.stringify({ status: "ERROR", message: err.message }));
        process.exit(1);
    }
}

async function waitForState(actionFn, checkStateFn, targetExists, maxAttempts = 5, delayMs = 6000) {
    let attempts = 0;

    while (attempts < maxAttempts) {
        attempts++;
        try {
            // 1. Executa a ação (caso ainda não tenha sido executada com sucesso)
            if (attempts === 1) {
                console.log(`🚀 [Ação] A executar transação (Tentativa ${attempts})...`);
                await actionFn();
            } else {
                console.log(`🔄 [Ação] Re-tentando transação (Tentativa ${attempts})...`);
                await actionFn();
            }
        } catch (error) {
            console.warn(`⚠️ [Aviso] A transação falhou na tentativa ${attempts}: ${error.message}`);
        }

        // 2. Valida o estado real na blockchain
        console.log(`🔍 [Estado] A verificar on-chain se a posição ${targetExists ? 'existe' : 'foi fechada'}...`);
        const position = await checkStateFn();
        const exists = position !== null && position !== undefined;

        // 3. Compara com o objetivo desejado
        if (exists === targetExists) {
            console.log(`✅ [Sucesso] Estado desejado atingido com sucesso na blockchain!`);
            return true;
        }

        console.log(`⏳ [Espera] O estado ainda não reflete o desejado. A aguardar ${delayMs / 1000}s...`);
        await new Promise(resolve => setTimeout(resolve, delayMs));
    }

    throw new Error(`❌ [Erro Crítico] O estado desejado (posição existir: ${targetExists}) não foi confirmado após ${maxAttempts} tentativas.`);
}

// =====================================================================
// 6. TERMINAL ROUTER (CLI INTERFACE)
// =====================================================================
const args = process.argv.slice(2);
const command = args[0];

async function main() {
    if (command === "open") {
        await handleAction(
            async () => {
                await waitForState(
                    async () => await openBalancedPositionOrca(
                        args[1],
                        parseFloat(args[2]),
                        parseFloat(args[3]),
                        parseFloat(args[4])
                    ),
                    async () => await getPositionOrca(args[1]),
                    true
                );
                return true;
            },
            args[1],
            "SUCCESS_OPEN_BALANCE_POSITION"
        );
    } else if (command === "rebalance") {
        await handleAction(
            async () => {
                // Chama a função robusta que já tem os dois waitForState lá dentro (fecho e abertura)
                await rebalancePositionByStrategy(
                    args[1],
                    parseFloat(args[2]),
                    parseFloat(args[3]),
                    parseFloat(args[4])
                );
                return true;
            },
            args[1],
            "SUCCESS_REBALANCE_POSITION"
        );
    } else if (command === "close") {
        await handleAction(
            async () => {
                await waitForState(
                    async () => await closeAllPoolPositionsAndSettleOrca(args[1]),
                    async () => {
                        try {
                            return await getPositionOrca(args[1]);
                        } catch (e) {
                            return null;
                        }
                    },
                    false
                );
                return true;
            },
            args[1],
            "SUCCESS_CLOSE_ALL"
        );
    } else if (command === "get_position") {
        const position = await withRetry(() => getPositionOrca(args[1]), 3, 2000);
        console.log(JSON.stringify(position));
        process.exit(0);
    } else if (command === "status") {
        const status = await withRetry(() => getMarketStatusOrca(args[1]), 3, 2000);
        console.log(JSON.stringify(status));
        process.exit(0);
    }
}

// Executa a função e apanha erros globais sem disparar o SyntaxError do Node.js
main().catch(err => {
    console.log(JSON.stringify({ status: "ERROR", message: err.message }));
    process.exit(1);
});
