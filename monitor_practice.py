import os
import time
import csv
import json
import threading
import requests
from pathlib import Path
from datetime import datetime, timezone

from iqoptionapi.stable_api import IQ_Option
from iqoptionapi import stable_api
from indicators import calculate_indicators
from strategy import generate_signal

IQ_USER = os.environ["IQ_USER"]
IQ_PASS = os.environ["IQ_PASS"]

ATIVO = "EURUSD"
TIMEFRAME = 60
CANDLES_COUNT = 205

ARQUIVO = Path("monitor_practice.csv")
RENDER_URL = "https://app-lab-trade-1.onrender.com/api/v1/monitor/update"
COMMAND_URL = "https://app-lab-trade-1.onrender.com/api/v1/monitor/commands"
COMMAND_RESULT_URL = "https://app-lab-trade-1.onrender.com/api/v1/monitor/command-result"
TOKEN_SEGURANCA = os.environ.get("TOKEN_SEGURANCA")


# Observação independente dos eventos de mudança de conta da IQ Option.
evento_mudanca_conta = threading.Event()
estado_mudanca_conta = {}
lock_mudanca_conta = threading.Lock()


def observar_evento_conta(ws, mensagem, opcode, fin):
    if not fin:
        return

    try:
        dados = json.loads(mensagem)

        if dados.get("name") != "balance-changed":
            return

        saldo = dados.get("msg", {}).get("current_balance", {})

        with lock_mudanca_conta:
            estado_mudanca_conta.clear()
            estado_mudanca_conta.update({
                "id": saldo.get("id"),
                "type": saldo.get("type"),
                "amount": saldo.get("amount"),
                "received_at": time.monotonic()
            })
            evento_mudanca_conta.set()

    except Exception as erro:
        print(f"Aviso no observador de conta: {erro}")


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

        cliente_ws = api.api.websocket_client
        cliente_ws.wss.on_data = observar_evento_conta

        print("Conexão estabelecida. Observador de conta instalado.")
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



def consultar_comandos():
    if not TOKEN_SEGURANCA:
        return None
    try:
        resposta = requests.get(
            COMMAND_URL,
            headers={"X-Trader-Token": TOKEN_SEGURANCA},
            timeout=5
        )
        resposta.raise_for_status()
        dados = resposta.json()

        tf = dados.get("timeframe")
        return {
            "timeframe": tf if tf in ("1m", "15m") else None,
            "account_command": dados.get("account_command")
        }
    except requests.RequestException as erro:
        print(f"Falha ao consultar comandos: {erro}")
        return None



def comando_ainda_pendente(command_id):
    dados = consultar_comandos()
    if not dados:
        return False

    comando = dados.get("account_command")
    return (
        isinstance(comando, dict)
        and comando.get("command_id") == command_id
        and comando.get("account") == "REAL"
    )


def enviar_resultado_comando(command_id, sucesso, account_id=None, account_type=None, mensagem=""):
    payload = {
        "command_id": command_id,
        "account": "REAL",
        "success": sucesso,
        "account_id": account_id,
        "account_type": account_type,
        "message": mensagem
    }

    resposta = requests.post(
        COMMAND_RESULT_URL,
        json=payload,
        headers={"X-Trader-Token": TOKEN_SEGURANCA},
        timeout=10
    )
    resposta.raise_for_status()
    return resposta.json()


def processar_comando_conta(api, comando):
    if not isinstance(comando, dict):
        return

    command_id = comando.get("command_id")

    if not command_id or comando.get("account") != "REAL":
        return

    if not comando_ainda_pendente(command_id):
        print("Comando REAL cancelado ou expirado. Nenhuma alteração feita.")
        return

    try:
        # Aguarda o perfil sem permitir espera infinita.
        limite = time.monotonic() + 10
        perfil = None

        while time.monotonic() < limite:
            perfil = getattr(api.api.profile, "msg", None)
            if isinstance(perfil, dict) and perfil.get("balances"):
                break
            time.sleep(0.2)

        if not isinstance(perfil, dict):
            raise RuntimeError("Perfil da IQ Option não recebido.")

        saldos = perfil.get("balances", [])

        conta_real = next(
            (
                conta for conta in saldos
                if conta.get("type") == 1
                and conta.get("id") is not None
            ),
            None
        )

        if conta_real is None:
            raise RuntimeError("Conta REAL não encontrada na IQ Option.")

        real_id = conta_real["id"]

        if not comando_ainda_pendente(command_id):
            print("Solicitação cancelada antes da troca REAL.")
            return

        # Limpa eventos anteriores para exigir uma confirmação nova.
        with lock_mudanca_conta:
            estado_mudanca_conta.clear()
            evento_mudanca_conta.clear()
            solicitacao_em = time.monotonic()

        # Usa a sessão autenticada da IQ Option, com tempo limite.
        recurso = api.api.changebalance
        url = api.api.prepare_http_url(recurso)

        resposta = api.api.session.request(
            method="POST",
            url=url,
            data={"balance_id": real_id},
            proxies=api.api.proxies,
            timeout=(5, 10)
        )
        resposta.raise_for_status()

        # HTTP 200 não basta: exige evento novo da própria IQ Option.
        if not evento_mudanca_conta.wait(timeout=12):
            raise RuntimeError(
                "A IQ Option não confirmou a troca de conta no prazo esperado."
            )

        with lock_mudanca_conta:
            evento = dict(estado_mudanca_conta)

        if evento.get("received_at", 0) < solicitacao_em:
            raise RuntimeError("Evento de confirmação anterior à solicitação.")

        if str(evento.get("id")) != str(real_id):
            raise RuntimeError("O ID recebido não corresponde à conta REAL.")

        if evento.get("type") != 1:
            raise RuntimeError("A IQ Option não confirmou o tipo REAL (1).")

        if not comando_ainda_pendente(command_id):
            print("Solicitação cancelada. Autorização REAL permanece bloqueada.")
            return

        # Atualiza as inscrições locais somente após confirmação do servidor.
        id_anterior = stable_api.global_value.balance_id

        if id_anterior is not None and str(id_anterior) != str(real_id):
            api.position_change_all("unsubscribeMessage", id_anterior)

        api.position_change_all("subscribeMessage", real_id)
        stable_api.global_value.balance_id = real_id

        if not comando_ainda_pendente(command_id):
            print("Comando cancelado após a confirmação. REAL permanece bloqueado.")
            return

        enviar_resultado_comando(
            command_id,
            True,
            real_id,
            1,
            "IQ Option confirmou a conta REAL por evento balance-changed."
        )

        print("Conta REAL confirmada pela IQ Option. Ordens continuam bloqueadas.")

    except Exception as erro:
        print(f"Falha na confirmação da conta REAL: {erro}")

        if comando_ainda_pendente(command_id):
            try:
                enviar_resultado_comando(
                    command_id,
                    False,
                    None,
                    None,
                    str(erro)
                )
            except requests.RequestException as envio_erro:
                print(f"Falha ao comunicar o resultado: {envio_erro}")


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
        "timeframe": "1m" if TIMEFRAME == 60 else "15m",
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



def executar():
    global TIMEFRAME, ARQUIVO
    api = conectar()
    rows = carregar_historico()
    registrados = {int(r["timestamp"]) for r in rows}

    primeiro_ciclo = not rows
    ultimo_timestamp = max(registrados, default=0)

    print("Monitor de mercado iniciado — sem seleção de conta.")
    print("Ordens reais e operações automáticas: DESATIVADAS.")

    while True:
        try:
            comandos = consultar_comandos()

            if comandos:
                processar_comando_conta(
                    api,
                    comandos.get("account_command")
                )

            solicitado = comandos.get("timeframe") if comandos else None
            if solicitado:
                novo = 60 if solicitado == "1m" else 900
                if novo != TIMEFRAME:
                    TIMEFRAME = novo
                    ARQUIVO = Path("monitor_practice.csv" if novo == 60 else "monitor_practice_15m.csv")
                    rows = carregar_historico()
                    registrados = {int(r["timestamp"]) for r in rows}
                    primeiro_ciclo = not rows
                    ultimo_timestamp = max(registrados, default=0)
                    print(f"Timeframe alterado para {solicitado}: {ARQUIVO}")
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
