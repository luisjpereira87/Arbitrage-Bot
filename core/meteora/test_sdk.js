require('dotenv').config({ path: require('path').resolve(__dirname, '../../.env') })
const { Connection, PublicKey } = require('@solana/web3.js');
const dlmmModule = require('@meteora-ag/dlmm');
const { createJupiterApiClient } = require('@jup-ag/api');
const fs = require('fs');
const { decode } = require('bs58');

// =====================================================================
// CONFIGURAÇÃO DE TESTE
// =====================================================================
const RPC_URL = "https://api.mainnet-beta.solana.com"; // O teu RPC
const connection = new Connection(RPC_URL, 'confirmed');

if (!process.env.PRIVATE_KEY_WALLET_SOLANA) {
    console.error("❌ Erro: A variável SOLANA_PRIVATE_KEY não está definida no ficheiro .env");
    process.exit(1);
}

// Endereço da Pool Oficial SOL/USDC (0.03% fee, binStep = 2)
const POOL_ADDRESS = "5rCf1DM8LjKTw4YqhnoLcngyZYeNnQqztScTogYHAS6";
const USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
const WSOL_MINT = "So11111111111111111111111111111111111111112";

async function runSDKTests() {
    console.log("🔍 --- INICIANDO DIAGNÓSTICO DOS SDKs (MAINNET) ---\n");

    // -----------------------------------------------------------------
    // TESTE 1: Verificar Chave Privada e Conexão Básica
    // -----------------------------------------------------------------
    let publicKey;
    try {
        if (!process.env.PRIVATE_KEY_WALLET_SOLANA) {
            throw new Error("A variável PRIVATE_KEY_WALLET_SOLANA não foi encontrada no process.env");
        }

        // Importamos a biblioteca inteira para inspecionar o objeto
        const bs58Module = require('bs58');
        const { Keypair } = require('@solana/web3.js');

        // Resolução dinâmica de empacotamento (Mapeia se a função está no root, no .default ou no .bs58)
        let decodeFn = typeof bs58Module === 'function' ? bs58Module : (bs58Module.decode || bs58Module.default?.decode);

        // Se mesmo assim o Node.js for teimoso, usamos o fallback do Anchor que usa outra árvore de dependências
        if (!decodeFn) {
            const anchor = require('@coral-xyz/anchor');
            decodeFn = anchor.utils.bytes.bs58.decode;
        }

        if (!decodeFn) {
            throw new Error("Não foi possível mapear a função de descodificação Base58 no teu ambiente Node.js.");
        }

        // Descodifica a tua string limpa de espaços
        const secretKeyArray = decodeFn(process.env.PRIVATE_KEY_WALLET_SOLANA.trim());
        const walletKeypair = Keypair.fromSecretKey(secretKeyArray);
        publicKey = walletKeypair.publicKey;

        console.log(`✅ [Wallet] Carteira carregada com sucesso!`);
        console.log(`   Endereço Público: ${publicKey.toBase58()}`);
    } catch (error) {
        console.error(`❌ [Wallet] Erro ao processar a PRIVATE_KEY_WALLET_SOLANA do .env: ${error.message}`);
        return;
    }

    // -----------------------------------------------------------------
    // TESTE 2: Obter Saldos Reais da Carteira (Via RPC nativo)
    // -----------------------------------------------------------------
    try {
        const solBalance = await connection.getBalance(publicKey);
        console.log(`✅ [RPC Solana] Conexão estabelecida.`);
        console.log(`   Saldo de SOL: ${(solBalance / 1_000_000_000).toFixed(4)} SOL`);

        // Procurar saldo de USDC da carteira
        const tokenAccounts = await connection.getParsedTokenAccountsByOwner(publicKey, {
            mint: new PublicKey(USDC_MINT)
        });

        let usdcBalance = 0;
        if (tokenAccounts.value.length > 0) {
            usdcBalance = tokenAccounts.value[0].account.data.parsed.info.tokenAmount.uiAmount;
        }
        console.log(`   Saldo de USDC: ${usdcBalance.toFixed(2)} USDC`);
    } catch (error) {
        console.error(`❌ [RPC Solana] Erro ao buscar saldos: ${error.message}`);
    }

    // -----------------------------------------------------------------
    // TESTE 3: Validar SDK da Meteora DLMM (Leitura da Pool)
    // -----------------------------------------------------------------
    // =====================================================================
    try {
        console.log(`\n🔄 [SDK Meteora] A tentar conectar à pool: ${POOL_ADDRESS}...`);

        const DLMMClass = dlmmModule.default || dlmmModule.DLMM || dlmmModule;
        const dlmmPool = await DLMMClass.create(connection, new PublicKey(POOL_ADDRESS));

        // Obter a Bin Ativa no segundo atual
        const activeBin = await dlmmPool.getActiveBin();

        // Se o SDK não te der o preço direto, extraímos o ID da bin ativa
        const binId = activeBin.binId;

        console.log(`✅ [SDK Meteora] Conectado com sucesso!`);
        console.log(`   ID da Bin Ativa atual: ${binId}`);

        // Fallback inteligente para mostrar o preço se ele existir no objeto,
        // caso contrário mostra apenas que a ligação foi bem-sucedida.
        if (activeBin.price) {
            console.log(`   Preço bruto da bin ativa: ${activeBin.price}`);
        } else {
            console.log(`   Dados da Bin obtidos com sucesso (Bin estruturada e comunicável).`);
        }
    } catch (error) {
        console.error(`❌ [SDK Meteora] Erro ao interagir com o SDK DLMM: ${error.message}`);
    }

    // -----------------------------------------------------------------
    // TESTE 4: Validar SDK da Jupiter (Simulação de Rota)
    // -----------------------------------------------------------------
    try {
        console.log(`\n🔄 [SDK Jupiter] A testar o motor de cotações (Quote API)...`);
        const { createJupiterApiClient } = require('@jup-ag/api');
        const jupiterQuoteApi = createJupiterApiClient();

        // Pedir uma rota fictícia de 10 USDC para SOL
        const amountToTest = 10 * 1_000_000; // 10 USDC em 6 decimais
        const quote = await jupiterQuoteApi.quoteGet({
            inputMint: USDC_MINT,
            outputMint: WSOL_MINT,
            amount: amountToTest,
            slippageBps: 50,
        });

        if (quote) {
            const outAmountSol = quote.outAmount / 1_000_000_000;
            console.log(`✅ [SDK Jupiter] Rota encontrada com sucesso!`);
            console.log(`   Cotação simulada: $10 USDC daria aproximadamente ${outAmountSol.toFixed(4)} SOL`);
        } else {
            console.log(`❌ [SDK Jupiter] O SDK respondeu mas não encontrou rotas.`);
        }
    } catch (error) {
        console.error(`❌ [SDK Jupiter] Erro ao comunicar com a API da Jupiter: ${error.message}`);
    }

    console.log("\n🏁 --- DIAGNÓSTICO CONCLUÍDO ---");
}

runSDKTests();