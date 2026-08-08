const { Connection } = require("@solana/web3.js");
const path = require('path');
const fs = require('fs');

const envPath = path.resolve(__dirname, '../../.env');
if (fs.existsSync(envPath)) {
    require('dotenv').config({ path: envPath });
} else {
    require('dotenv').config({ path: path.resolve(__dirname, '.env') });
}

const RPC_ENDPOINTS = [
    process.env.RPC_HELIUS_SOLANA_URL,
    process.env.RPC_ALCHEMY_SOLANA_URL,
    "https://api.mainnet-beta.solana.com"
].filter(Boolean);

let currentRpcIndex = 0;

function getActiveRpcUrl() {
    return RPC_ENDPOINTS[currentRpcIndex];
}

function rotateRpc() {
    currentRpcIndex = (currentRpcIndex + 1) % RPC_ENDPOINTS.length;
    console.warn(`🔄 [RPC Manager] A mudar para o próximo RPC da lista: ${RPC_ENDPOINTS[currentRpcIndex]}`);
}

async function executeWithRpcFallback(asyncFn) {
    const maxTries = RPC_ENDPOINTS.length;
    let attempts = 0;

    while (attempts < maxTries) {
        try {
            return await asyncFn(getActiveRpcUrl());
        } catch (error) {
            const errMessage = error.message || String(error);

            if (errMessage.includes("429") || errMessage.includes("Too Many Requests") || errMessage.includes("timeout") || errMessage.includes("fetch failed")) {
                attempts++;
                console.warn(`⚠️ [RPC Manager] Erro no RPC atual (${getActiveRpcUrl()}). Motivo: ${errMessage}`);
                rotateRpc();

                if (attempts >= maxTries) {
                    throw new Error(`❌ [RPC Manager] Todos os ${maxTries} RPCs falharam.`);
                }
                await new Promise(r => setTimeout(r, 1000));
            } else {
                throw error;
            }
        }
    }
}

module.exports = {
    getActiveRpcUrl,
    executeWithRpcFallback
};