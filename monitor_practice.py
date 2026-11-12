import os
import time
import csv
import requests
from pathlib import Path
from datetime import datetime, timezone

from iqoptionapi.stable_api import IQ_Option
from indicators import calculate_indicators
from strategy import generate_signal

IQ_USER = os.environ["IQ_USER"]
IQ_PASS = os.environ["IQ_PASS"]

ATIVO = "EURUSD"
TIMEFRAME = 60
CANDLES_COUNT = 205
CONTA = "PRACTICE"

ARQUIVO = Path("monitor_practice.csv")
RENDER_URL = "https://app-lab-trade-1.onrender.com/api/v1/monitor/update"
COMMANDS_URL = "https://app-lab-trade-1.onrender.com/api/v1/monitor/commands"
COMMAND_RESULT_URL = "https://app-lab-trade-1.onrender.com/api/v1/monitor/command-result"
ACCOUNT_STATE_URL = "https://app-lab-trade-1.onrender.com/api/v1/monitor/account-state"
TOKEN_SEGURANCA = os.environ.get("TOKEN_SEGURANCA")

COLUNAS = [
    "timestamp",
    "data_hora",
    "sinal",
    "score",
    "preco",
    "ema_9",
    "ema_21",
    "ema_200",
    "rsi_14",
    "macd_histogram",
    "atr_14",
    "distancia_ema9_atr",
    "status",
    "resultado",
    "retorno_pct"
]


def conectar():
    print("Conectando à IQ Option...")
    api = IQ_Option(IQ_USER, IQ_PASS)

    try:
        status, mensagem = api.connect()

        if not status:
            raise ConnectionError(str(mensagem))

        api.change_balance(CONTA)

        modo_confirmado = api.get_balance_mode()

        if modo_confirmado != "PRACTICE":
            raise RuntimeError(
                f"Conta não confirmada como PRACTICE. "
                f"Modalidade retornada: {modo_confirmado!r}"
            )

        print("Conta PRACTICE confirmada pela IQ Option.")
        return api

    except Exception:
        try:
            conexao = getattr(api, "api", None)
            if conexao is not None:
                conexao.close()
                print("Conexão encerrada por segurança.")
        except Exception as erro:
            print(f"Aviso ao encerrar conexão: {erro}")

        raise


def carregar_historico():
    if not ARQUIVO.exists():
        with ARQUIVO.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=COLUNAS).writeheader()
        return []

    with ARQUIVO.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def salvar_historico(rows):
    temporario = ARQUIVO.with_name(ARQUIVO.name + ".tmp")

    try:
        with temporario.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=COLUNAS)
            writer.writeheader()
            writer.writerows(rows)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temporario, ARQUIVO)

    except Exception:
        if temporario.exists():
            temporario.unlink()
        raise


def formatar_candle(c):
    return {
        "timestamp": datetime.fromtimestamp(
            float(c["from"]), timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "open": float(c["open"]),
        "high": float(c["max"]),
        "low": float(c["min"]),
        "close": float(c["close"]),
        "volume": int(c["volume"])
    }


def analisar_candle(historico, indice):
    dados = [
        formatar_candle(c)
        for c in historico[:indice + 1]
    ]

    indicadores = calculate_indicators(dados)

    if "error" in indicadores:
        return None

    decisao = generate_signal(indicadores)

    if "error" in decisao:
        return None

    return indicadores, decisao


def atualizar_resultados(rows, candles):
    mapa = {int(c["from"]): c for c in candles}
    alterado = False

    for row in rows:
        if row["status"] != "PENDENTE":
            continue

        proximo_timestamp = int(row["timestamp"]) + TIMEFRAME
        candle = mapa.get(proximo_timestamp)

        if candle is None:
            continue

        abertura = float(candle["open"])
        fechamento = float(candle["close"])

        if abertura <= 0:
            continue

        retorno = (fechamento - abertura) / abertura * 100
        sinal = row["sinal"]

        if sinal == "COMPRA":
            resultado = (
                "ACERTO" if retorno > 0
                else "ERRO" if retorno < 0
                else "EMPATE"
            )
        else:
            retorno = -retorno
            resultado = (
                "ACERTO" if retorno > 0
                else "ERRO" if retorno < 0
                else "EMPATE"
            )

        row["status"] = "FINALIZADO"
        row["resultado"] = resultado
        row["retorno_pct"] = round(retorno, 6)
        alterado = True

        print(
            f"Resultado {sinal}: {resultado} | "
            f"Retorno: {retorno:.6f}%"
        )

    return alterado


def mostrar_estatisticas(rows):
    avaliados = [
        r for r in rows
        if r["status"] == "FINALIZADO"
    ]

    acertos = sum(r["resultado"] == "ACERTO" for r in avaliados)
    erros = sum(r["resultado"] == "ERRO" for r in avaliados)
    empates = sum(r["resultado"] == "EMPATE" for r in avaliados)

    total = acertos + erros

    taxa = (acertos / total * 100) if total else 0

    print("\n===== ESTATÍSTICAS DO MONITOR =====")
    print(f"Operações avaliadas: {len(avaliados)}")
    print(f"Acertos: {acertos}")
    print(f"Erros: {erros}")
    print(f"Empates: {empates}")
    print(f"Taxa de acerto: {taxa:.2f}%")
    print("Execução de ordens: DESATIVADA")
    print("==================================\n")



def enviar_estado_render(rows, candles):
    if not TOKEN_SEGURANCA or not rows:
        return

    finalizados = [
        r for r in rows if r["status"] == "FINALIZADO"
    ]

    acertos = sum(r["resultado"] == "ACERTO" for r in finalizados)
    erros = sum(r["resultado"] == "ERRO" for r in finalizados)
    empates = sum(r["resultado"] == "EMPATE" for r in finalizados)

    total = acertos + erros

    payload = {
        "latest": rows[-1],
        "history": rows[-300:],
        "candles": [formatar_candle(c) for c in candles[-205:]],
        "stats": {
            "avaliadas": len(finalizados),
            "acertos": acertos,
            "erros": erros,
            "empates": empates,
            "pendentes": sum(r["status"] == "PENDENTE" for r in rows),
            "taxa_acerto": round(acertos / total * 100, 2) if total else 0
        },
        "pair": ATIVO,
        "timeframe": "1m",
        "source": "monitor_practice"
    }

    try:
        resposta = requests.post(
            RENDER_URL,
            json=payload,
            headers={"X-Trader-Token": TOKEN_SEGURANCA},
            timeout=15
        )
        resposta.raise_for_status()
        print("Painel Render: dados sincronizados.")

    except requests.RequestException as erro:
        print(f"Falha ao sincronizar com Render: {erro}")



ultimo_comando_processado = None
resultado_pendente = None


def processar_comando_conta(api):
    global ultimo_comando_processado, resultado_pendente

    if not TOKEN_SEGURANCA:
        return

    headers = {"X-Trader-Token": TOKEN_SEGURANCA}

    if resultado_pendente:
        try:
            r = requests.post(
                COMMAND_RESULT_URL,
                json=resultado_pendente,
                headers=headers,
                timeout=15
            )
            if r.status_code == 409:
                print("Comando expirado ou já encerrado no Render.")
                resultado_pendente = None
            else:
                r.raise_for_status()
                print("Resultado da troca de conta confirmado no Render.")
                resultado_pendente = None
        except requests.RequestException:
            print("Resultado pendente: tentando reenviar.")
        return

    try:
        r = requests.get(COMMANDS_URL, headers=headers, timeout=10)
        r.raise_for_status()
        comando = r.json().get("command")

        if not comando:
            return

        command_id = comando.get("command_id")
        conta = comando.get("account")

        if not command_id or command_id == ultimo_comando_processado:
            return

        ultimo_comando_processado = command_id

        sucesso = False
        mensagem = ""

        try:
            criado = float(comando.get("created_at", 0))

            if time.time() - criado > 60 or criado > time.time() + 5:
                raise RuntimeError("Comando expirado.")

            if conta not in ("PRACTICE", "REAL"):
                raise RuntimeError("Conta solicitada inválida.")

            print(f"Solicitação de troca para {conta} recebida.")
            api.change_balance(conta)
            time.sleep(2)

            confirmada = api.get_balance_mode()
            sucesso = confirmada == conta

            mensagem = (
                f"Conta {conta} confirmada pela IQ Option."
                if sucesso else
                f"Falha na confirmação. IQ Option retornou: {confirmada!r}"
            )

        except Exception as erro:
            mensagem = str(erro)

        resultado_pendente = {
            "command_id": command_id,
            "success": sucesso,
            "account": conta,
            "message": mensagem
        }

        print(mensagem)

    except requests.RequestException as erro:
        print(f"Falha ao consultar comandos do Render: {erro}")


def sincronizar_estado_conta(api):
    if not TOKEN_SEGURANCA:
        print("Token ausente: estado da conta não sincronizado.")
        return

    conta = api.get_balance_mode()
    if conta not in ("PRACTICE", "REAL"):
        raise RuntimeError("IQ Option retornou modalidade inválida.")

    resposta = requests.post(
        ACCOUNT_STATE_URL,
        json={"account": conta},
        headers={"X-Trader-Token": TOKEN_SEGURANCA},
        timeout=15
    )
    resposta.raise_for_status()
    print(f"Conta {conta} sincronizada com o painel.")


def executar():
    api = conectar()
    sincronizar_estado_conta(api)
    rows = carregar_historico()
    registrados = {int(r["timestamp"]) for r in rows}

    primeiro_ciclo = not rows
    ultimo_timestamp = max(registrados, default=0)

    print("Monitor PRACTICE iniciado.")
    print("Ordens reais e operações automáticas: DESATIVADAS.")

    while True:
        try:
            processar_comando_conta(api)
            agora = time.time()

            candles = api.get_candles(
                ATIVO, TIMEFRAME, CANDLES_COUNT, agora
            )

            fechados = sorted(
                [
                    c for c in (candles or [])
                    if float(c["to"]) <= time.time() - 1
                ],
                key=lambda c: float(c["from"])
            )

            if len(fechados) < 200:
                print("Aguardando histórico suficiente...")
                time.sleep(10)
                continue

            alterado = atualizar_resultados(rows, fechados)

            if primeiro_ciclo:
                indices = [len(fechados) - 1]
                primeiro_ciclo = False
            else:
                indices = [
                    i for i, c in enumerate(fechados)
                    if int(c["from"]) not in registrados
                    and int(c["from"]) > ultimo_timestamp
                ]

            for i in indices:
                candle = fechados[i]
                timestamp = int(candle["from"])

                if timestamp in registrados or i < 199:
                    continue

                analise = analisar_candle(fechados, i)

                if analise is None:
                    continue

                indicadores, decisao = analise
                sinal = decisao["signal"]

                row = {
                    "timestamp": timestamp,
                    "data_hora": datetime.fromtimestamp(
                        timestamp, timezone.utc
                    ).isoformat(),
                    "sinal": sinal,
                    "score": decisao["score"],
                    "preco": indicadores["last_close"],
                    "ema_9": indicadores["ema_9"],
                    "ema_21": indicadores["ema_21"],
                    "ema_200": indicadores["ema_200"],
                    "rsi_14": indicadores["rsi_14"],
                    "macd_histogram": indicadores["macd_histogram"],
                    "atr_14": indicadores["atr_14"],
                    "distancia_ema9_atr": decisao[
                        "distance_from_ema9_atr"
                    ],
                    "status": (
                        "PENDENTE" if sinal in ("COMPRA", "VENDA")
                        else "SEM_OPERACAO"
                    ),
                    "resultado": "",
                    "retorno_pct": ""
                }

                rows.append(row)
                registrados.add(timestamp)
                ultimo_timestamp = timestamp

                print(
                    f"\nNovo sinal: {sinal} | "
                    f"Preço: {row['preco']} | "
                    f"Score: {row['score']}"
                )

                for motivo in decisao["reasons"]:
                    print("-", motivo)

                alterado = True

            if alterado:
                salvar_historico(rows)

            em_formacao = sorted(
                [c for c in (candles or [])
                 if float(c["to"]) > time.time() - 1],
                key=lambda c: float(c["from"])
            )
            candles_painel = fechados[-204:] + em_formacao[-1:]
            enviar_estado_render(rows, candles_painel)
            mostrar_estatisticas(rows)
            time.sleep(10)

        except KeyboardInterrupt:
            salvar_historico(rows)
            print("\nMonitor encerrado pelo usuário.")
            break

        except Exception as erro:
            print(f"Erro no monitor: {erro}")
            time.sleep(15)


if __name__ == "__main__":
    executar()
