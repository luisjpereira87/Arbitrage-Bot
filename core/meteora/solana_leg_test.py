import json
import os
import subprocess
import time

from core.meteora.meteora_client import MeteoraBot

# 1. Descobrir a pasta exata onde ESTE script Python está a correr
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

# 2. Como o meteora_bot.js está na MESMA pasta, o caminho é direto!
JS_SCRIPT_PATH = os.path.join(CURRENT_DIR, "meteora_bot.js")

# =====================================================================
# CONFIGURAÇÃO DO TESTE
# =====================================================================
CAPITAL_TESTE_USDC = 10.0  # Valor baixo e seguro para o teste
LARGURA_RANGE_DOLLARS = 4.0  # Range de $4 dólares em redor do preço atual
TEMPO_DE_ESPERA_SEGUNDOS = 10  # Tempo que a posição vai ficar aberta no teste


def executar_comando_node(args):
    """Auxiliar para rodar o Node.js e capturar o stdout/stderr"""
    try:
        resultado = subprocess.run(
            ["node", JS_SCRIPT_PATH] + args,
            capture_output=True,
            text=True,
            check=True
        )
        return resultado.stdout.strip()
    except subprocess.CalledProcessError as e:
        # AGORA VAMOS VER O ERRO REAL SE O NODE FALHAR
        print(f"\n❌ [Erro Crítico Node.js] Comando {args} falhou!")
        print(f"   STDOUT: {e.stdout}")
        print(f"   STDERR: {e.stderr}")
        return None


def extrair_json_da_resposta(raw_output):
    """Filtra as linhas decorativas do terminal e extrai o JSON real"""
    if not raw_output:
        return None

    # DEBUG: Mostra o que o Python recebeu do Node antes de filtrar
    print(f"🔍 [Debug Raw Output] Recebido do Node:\n{raw_output}\n")

    for line in raw_output.splitlines():
        if line.strip().startswith('{"status"'):
            try:
                return json.loads(line.strip())
            except json.JSONDecodeError as e:
                print(f"⚠️ Erro ao decodificar JSON na linha: {e}")
                pass
    return None


def iniciar_teste_isolado__():
    print("🔬 --- INICIANDO TESTE ISOLADO DA PERNA DA SOLANA ---")
    print(f"📂 Script JS Alvo: {JS_SCRIPT_PATH}\n")

    # -----------------------------------------------------------------
    # PASSO 1: CONSULTAR STATUS E CALCULAR SALDO DISPONÍVEL REAL
    # -----------------------------------------------------------------
    print("🔄 [Passo 1] A consultar o estado atual do mercado...")
    raw_status = executar_comando_node(["status"])
    data_status = extrair_json_da_resposta(raw_status)

    if not data_status or data_status.get("status") != "SUCCESS":
        print("❌ Não foi possível obter o status inicial. Teste abortado.")
        return

    saldo_sol = data_status["balances"]["SOL"]
    saldo_usdc = data_status["balances"]["USDC"]
    preco_sol = float(data_status["pool"]["rawPrice"])

    # 🧮 CÁLCULO PATRIMONIAL INTELIGENTE
    valor_sol_usd = saldo_sol * preco_sol
    patrimonio_total_usd = valor_sol_usd + saldo_usdc

    # A tua regra rígida de segurança: guardar sempre $10 em SOL para o gas
    RESERVA_GAS_USD = 10.0
    saldo_disponivel_usd = patrimonio_total_usd - RESERVA_GAS_USD

    print(f"   💳 Carteira: {data_status['wallet']}")
    print(f"   🪙 Saldo Atual: {saldo_sol:.4f} SOL (${valor_sol_usd:.2f}) | {saldo_usdc:.2f} USDC")
    print(f"   💰 Património Combinado Total: ${patrimonio_total_usd:.2f} USDC")
    print(f"   🛡️ Saldo Comercializável Livre (Descontando $10 de Gas): ${saldo_disponivel_usd:.2f} USDC")
    print(f"   🏷️ Preço de Mercado da SOL: ${preco_sol:.2f} USDC")

    # 🚨 NOVA GUARDA INTELIGENTE MULTI-MOEDA
    if saldo_disponivel_usd < CAPITAL_TESTE_USDC:
        print(
            f"❌ Saldo Insuficiente! Tens apenas ${saldo_disponivel_usd:.2f} livres para operar, mas o alvo do teste é ${CAPITAL_TESTE_USDC:.2f} USDC.")
        return

    # -----------------------------------------------------------------
    # PASSO 2: EXECUTAR ABERTURA DA POSIÇÃO (OPEN)
    # -----------------------------------------------------------------
    print(f"\n🚀 [Passo 2] A abrir posição DLMM com ${CAPITAL_TESTE_USDC} USDC...")
    print(f"   📊 Parâmetros: Preço=${preco_sol:.2f} | Range=+/-${LARGURA_RANGE_DOLLARS}")

    args_open = [
        "open",
        str(CAPITAL_TESTE_USDC),
        str(preco_sol),
        str(LARGURA_RANGE_DOLLARS)
    ]

    output_open = executar_comando_node(args_open)

    if output_open:
        print(output_open)
    else:
        print("⚠️ [Aviso] O script Node não devolveu nenhuma resposta no STDOUT.")
        output_open = ""

    if "SUCCESS_OPEN" not in output_open:
        print("❌ Falha crítica ao abrir a posição na Solana. O script de fecho não será chamado por segurança.")
        return

    # -----------------------------------------------------------------
    # PASSO 3: JANELA DE ESPERA (POSIÇÃO ATIVA)
    # -----------------------------------------------------------------
    print(f"\n⏳ Posição aberta com sucesso! Aguardando {TEMPO_DE_ESPERA_SEGUNDOS} segundos em mercado aberto...")
    time.sleep(TEMPO_DE_ESPERA_SEGUNDOS)

    # -----------------------------------------------------------------
    # PASSO 4: EXECUTAR FECHO E LIQUIDAÇÃO TOTAL (CLOSE)
    # -----------------------------------------------------------------
    print("\n🛑 [Passo 4] Acionando protocolo de fecho e liquidação total...")
    output_close = executar_comando_node(["close"])
    print(output_close)

    if "SUCCESS_CLOSE" in output_close:
        print("\n🏆 --- TESTE CONCLUÍDO COM SUCESSO ---")
        print("✅ A perna da Solana está 100% calibrada, rápida e segura para produção!")
    else:
        print("\n⚠️ O fecho foi executado, mas detetou avisos ou falhas na liquidação final.")


def iniciar_teste_isolado():
    meteora_bot = MeteoraBot(JS_SCRIPT_PATH)
    print("🔬 --- INICIANDO TESTE ISOLADO DA PERNA DA SOLANA ---")
    print(f"📂 Script JS Alvo: {JS_SCRIPT_PATH}\n")

    # -----------------------------------------------------------------
    # PASSO 1: CONSULTAR STATUS E CALCULAR SALDO DISPONÍVEL REAL
    # -----------------------------------------------------------------
    print("🔄 [Passo 1] A consultar o estado atual do mercado...")
    market_status = meteora_bot.get_status()
    # raw_status = executar_comando_node(["status"])
    # data_status = extrair_json_da_resposta(raw_status)

    if market_status is None:
        print("❌ Não foi possível obter o status inicial. Teste abortado.")
        return

    saldo_sol = market_status.sol_balance
    saldo_usdc = market_status.usdc_balance
    preco_sol = float(market_status.raw_price)

    # 🧮 CÁLCULO PATRIMONIAL INTELIGENTE
    valor_sol_usd = saldo_sol * preco_sol
    patrimonio_total_usd = valor_sol_usd + saldo_usdc
    RESERVA_GAS_USD = 10.0
    saldo_disponivel_usd = patrimonio_total_usd - RESERVA_GAS_USD

    print(f"   💳 Carteira: {market_status.wallet}")
    print(f"   🪙 Saldo Atual: {saldo_sol:.4f} SOL (${valor_sol_usd:.2f}) | {saldo_usdc:.2f} USDC")
    print(f"   💰 Património Combinado Total: ${patrimonio_total_usd:.2f} USDC")
    print(f"   🛡️ Saldo Comercializável Livre (Descontando $10 de Gas): ${saldo_disponivel_usd:.2f} USDC")
    print(f"   🏷️ Preço de Mercado da SOL: ${preco_sol:.2f} USDC")

    if saldo_disponivel_usd < CAPITAL_TESTE_USDC:
        print(
            f"❌ Saldo Insuficiente! Necessitas de ${CAPITAL_TESTE_USDC:.2f} mas tens ${saldo_disponivel_usd:.2f} livres.")
        return

    # -----------------------------------------------------------------
    # PASSO 2: VALIDAÇÃO E GESTÃO DE POSIÇÃO (CHECK / OPEN / REBALANCE)
    # -----------------------------------------------------------------
    print("\n🔍 [Passo 2] A verificar estado da posição na blockchain...")
    status_pos = meteora_bot.check_position()
    # status_pos = extrair_json_da_resposta(executar_comando_node(["check"]))

    if not status_pos:
        print(f"🔰 [Cold Start] Nenhuma posição encontrada. A abrir com ${CAPITAL_TESTE_USDC} USDC...")
        output = meteora_bot.open_position(CAPITAL_TESTE_USDC, preco_sol, LARGURA_RANGE_DOLLARS)
        # output = executar_comando_node(["open", str(CAPITAL_TESTE_USDC), str(preco_sol), str(LARGURA_RANGE_DOLLARS)])
        print(output)

    elif not status_pos.inRange:
        print(f"⚖️ [Maintenance] Posição {status_pos.address} FORA DO RANGE! A rebalancear...")
        output = executar_comando_node(
            ["rebalance", status_pos.address, str(CAPITAL_TESTE_USDC), str(preco_sol), str(LARGURA_RANGE_DOLLARS)])
        print(output)

    else:
        print("✅ [Status] Posição saudável e dentro do range. Nada a fazer.")

    # -----------------------------------------------------------------
    # PASSO 3: JANELA DE ESPERA
    # -----------------------------------------------------------------
    print(f"\n⏳ Aguardando {TEMPO_DE_ESPERA_SEGUNDOS} segundos para o mercado respirar...")
    time.sleep(TEMPO_DE_ESPERA_SEGUNDOS)

    # -----------------------------------------------------------------
    # PASSO 4: FECHO DE SEGURANÇA (TEST MODE)
    # -----------------------------------------------------------------
    """
    print("\n🛑 [Passo 4] Acionando protocolo de fecho...")
    output_close = executar_comando_node(["close"])
    print(output_close)

    if "SUCCESS_CLOSE" in output_close:
        print("\n🏆 --- TESTE CONCLUÍDO COM SUCESSO ---")
    else:
        print("\n⚠️ O fecho foi executado, mas detetou avisos ou falhas na liquidação.")
    """


def iniciar_teste_isolado_new():
    meteora_bot = MeteoraBot(JS_SCRIPT_PATH)
    print("🔬 --- INICIANDO TESTE ISOLADO DA PERNA DA SOLANA ---")
    print(f"📂 Script JS Alvo: {JS_SCRIPT_PATH}\n")

    # -----------------------------------------------------------------
    # PASSO 1: CONSULTAR STATUS E CALCULAR SALDO DISPONÍVEL REAL
    # -----------------------------------------------------------------
    print("🔄 [Passo 1] A consultar o estado atual do mercado...")
    market_status = meteora_bot.get_status()
    # raw_status = executar_comando_node(["status"])
    # data_status = extrair_json_da_resposta(raw_status)

    if market_status is None:
        print("❌ Não foi possível obter o status inicial. Teste abortado.")
        return

    saldo_sol = market_status.sol_balance
    saldo_usdc = market_status.usdc_balance
    preco_sol = float(market_status.raw_price)

    meteora_bot.calculate_range(preco_sol, LARGURA_RANGE_DOLLARS)


if __name__ == "__main__":
    iniciar_teste_isolado_new()
