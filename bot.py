import time
import os
import requests
from iqoptionapi.stable_api import IQ_Option

# ================= CONFIGURAÇÕES DO ROBÔ =================
IQ_USER = os.environ["IQ_USER"]  # Substitua pelo seu e-mail da IQ Option
IQ_PASS = os.environ["IQ_PASS"]                    # Substitua pela sua senha da IQ Option
CONTA = "PRACTICE"                                 # Configurado para Conta de Treinamento (PRACTICE)
ATIVO = "EURUSD"                               # Par de moedas
TIMEFRAME = 15                                 # Tempo gráfico M15 (em minutos)
CANDLES_COUNT = 205                            # Puxa um pouco mais de 200 por segurança

# Configurações do seu servidor Render
RENDER_URL = "https://app-lab-trade-1.onrender.com/api/v1/analyze-indicators"
TOKEN_SEGURANCA = os.environ["TOKEN_SEGURANCA"]
# =========================================================

def conectar_iq():
    print("🔄 Conectando na IQ Option...")
    api = IQ_Option(IQ_USER, IQ_PASS)

    # Tenta conectar
    status, mensagem = api.connect()
    if status:
        api.change_balance(CONTA)
        print(f"✅ Conectado com sucesso na Conta {CONTA}!")
        return api
    else:
        print(f"❌ Erro ao conectar: {mensagem}")
        return None

def puxar_e_analisar(api):
    print(f"📊 Buscando os últimos {CANDLES_COUNT} candles de M15 para {ATIVO}...")

    # Puxa o histórico de velas da IQ Option
    # O timeframe na API da IQ é em segundos (15 minutos = 900 segundos)
    candles_raw = api.get_candles(ATIVO, TIMEFRAME * 60, CANDLES_COUNT, time.time())

    # Descarta candles que ainda não terminaram
    if candles_raw:
        agora = time.time()
        candles_raw = [c for c in candles_raw if float(c["to"]) <= agora - 1]

    if not candles_raw or len(candles_raw) < 200:
        print("❌ Não foi possível recuperar a quantidade mínima de 200 candles.")
        return

    # Formata os dados para o padrão que o seu main.py exige
    lista_candles = []
    for c in candles_raw:
        lista_candles.append({
            "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(c['from'])),
            "open": float(c['open']),
            "high": float(c['max']),
            "low": float(c['min']),
            "close": float(c['close']),
            "volume": int(c['volume']),
            "abertura": float(c['open']),
            "maxima": float(c['max']),
            "minima": float(c['min']),
            "fechamento": float(c['close'])
        })

    payload = {"candles": lista_candles}
    headers = {"X-Trader-Token": TOKEN_SEGURANCA}

    # Envia os dados para o Render fazer a análise matemática
    try:
        print("🚀 Enviando dados para processamento no Render...")
        resposta = requests.post(RENDER_URL, json=payload, headers=headers)

        if resposta.status_code == 200:
            resultado = resposta.json()
            print(f"📥 Resposta do Robô: {resultado}")

            # TODO: Aqui podemos programar para executar a ordem real na IQ Option
            # Exemplo: se o resultado disser 'COMPRAR', o script executa api.buy(...)

        else:
            print(f"⚠️ Servidor respondeu com erro {resposta.status_code}: {resposta.text}")

    except Exception as e:
        print(f"❌ Erro de comunicação com o Render: {e}")

if __name__ == "__main__":
    api = conectar_iq()
    if api:
        # Loop para rodar a análise a cada fechamento de vela de 15 minutos
        while True:
            puxar_e_analisar(api)
            print(f"⏳ Aguardando a próxima checagem em M15...")
            time.sleep(60) # Verifica o mercado a cada minuto
