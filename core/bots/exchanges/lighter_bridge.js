const { LighterClient, ORDER_TYPES, TIME_IN_FORCE } = require('lighter-node-client');

async function executeOrder() {
    const args = process.argv.slice(2);
    const side = args[0]; // 'buy' ou 'sell'
    const amount = parseFloat(args[1]);
    const price = parseFloat(args[2]);
    const marketIndex = parseInt(args[3] || '0');

    const baseUrl = process.env.LIGHTER_BASE_URL || 'https://mainnet.zklighter.elliot.ai';
    const accountIndex = parseInt(process.env.LIGHTER_ACCOUNT_INDEX || '729593');
    const apiKeyIndex = parseInt(process.env.LIGHTER_API_KEY_INDEX || '0');
    //const marketIndex = parseInt(process.env.LIGHTER_MARKET_INDEX || '0');

    const privateKey = "3fda2e91f61cf3c242bcbd3bfbf4b29bc3a43334771fedd69aa6a12b263b46c599a592100e07025e";

    const client = new LighterClient(baseUrl, privateKey, apiKeyIndex, accountIndex);
    await client.initialize();

    const isAsk = (side.toLowerCase() === 'sell');
    const myClientOrderIndex = Date.now(); // <--- Guardamos numa variável

    try {
        const result = client.createOrder({
            marketIndex: 2,
            clientOrderIndex: myClientOrderIndex,
            baseAmount: Math.round(amount * 1e8),
            price: Math.round(price * 100),
            isAsk: isAsk,
            orderType: 0,
            timeInForce: 0,
            orderExpiry: 0,
            nonce: myClientOrderIndex
        });

        // Devolvemos explicitamente o clientOrderIndex para o Python apanhar sem falhas
        console.log(JSON.stringify({
            success: true,
            clientOrderIndex: myClientOrderIndex,
            result
        }));
    } catch (error) {
        console.error(JSON.stringify({ success: false, error: error.message }));
        process.exit(1);
    }
}

executeOrder();