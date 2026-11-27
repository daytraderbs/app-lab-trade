from collections import defaultdict
from math import sqrt

MIN_AMOSTRAS = 20
MAX_VIZINHOS = 50

ESCALAS = (2.0, 5.0, 0.2, 2.0, 2.0, 0.01)


def numero(v):
    try:
        n = float(v)
        return n if n == n and abs(n) != float("inf") else None
    except (TypeError, ValueError):
        return None


def extrair_features(row):
    e9 = numero(row.get("ema_9"))
    e21 = numero(row.get("ema_21"))
    e200 = numero(row.get("ema_200"))
    rsi = numero(row.get("rsi_14"))
    macd = numero(row.get("macd_histogram"))
    atr = numero(row.get("atr_14"))
    preco = numero(row.get("preco"))
    dist = numero(row.get("distancia_ema9_atr"))

    if any(v is None for v in (e9, e21, e200, rsi, macd, atr, preco)):
        return None
    if atr <= 0 or preco <= 0:
        return None

    if dist is None:
        dist = (preco - e9) / atr

    return (
        (e9 - e21) / atr,
        (e21 - e200) / atr,
        rsi / 100,
        macd / atr,
        dist,
        (atr / preco) * 100,
    )


def analyze_history(rows):
    grupos = defaultdict(list)
    finalizados = []

    for r in rows:
        if r.get("status") != "FINALIZADO":
            continue
        if r.get("resultado") not in ("ACERTO", "ERRO", "EMPATE"):
            continue

        retorno = numero(r.get("retorno_pct"))
        if retorno is None:
            continue

        item = {
            "sinal": r.get("sinal", "INDEFINIDO"),
            "regime": r.get("regime") or "NAO_CLASSIFICADO",
            "resultado": r["resultado"],
            "retorno": retorno,
            "features": extrair_features(r),
        }
        finalizados.append(item)
        grupos[(item["regime"], item["sinal"])].append(item)

    acertos = sum(x["resultado"] == "ACERTO" for x in finalizados)
    erros = sum(x["resultado"] == "ERRO" for x in finalizados)

    desempenho = {}
    for (regime, sinal), grupo in grupos.items():
        validos = [x for x in grupo if x["resultado"] != "EMPATE"]
        wins = sum(x["resultado"] == "ACERTO" for x in validos)
        desempenho.setdefault(regime, {})[sinal] = {
            "amostras": len(grupo),
            "acertos": wins,
            "erros": len(validos) - wins,
            "taxa_acerto": round(wins / len(validos) * 100, 2)
            if validos else 0,
            "retorno_medio_pct": round(
                sum(x["retorno"] for x in grupo) / len(grupo), 6
            ),
            "amostra_suficiente": len(grupo) >= MIN_AMOSTRAS,
        }

    return {
        "status": "APRENDIZADO_POR_PADROES",
        "amostras_avaliadas": len(finalizados),
        "amostras_com_features": sum(
            x["features"] is not None for x in finalizados
        ),
        "acertos": acertos,
        "erros": erros,
        "taxa_acerto": round(acertos / (acertos + erros) * 100, 2)
        if acertos + erros else 0,
        "desempenho_por_regime": desempenho,
        "recalibracao_automatica": True,
        "execucao_automatica": False,
    }


def distancia(a, b):
    return sqrt(sum(
        ((x - y) / escala) ** 2
        for x, y, escala in zip(a, b, ESCALAS)
    ) / len(ESCALAS))


def avaliar_padroes(rows, regime, sinal, atuais):
    candidatos = []

    for r in rows:
        if r.get("status") != "FINALIZADO":
            continue
        if r.get("sinal") != sinal:
            continue
        if r.get("resultado") not in ("ACERTO", "ERRO", "EMPATE"):
            continue

        features = extrair_features(r)
        retorno = numero(r.get("retorno_pct"))
        if features is None or retorno is None:
            continue

        mesmo_regime = (r.get("regime") or "NAO_CLASSIFICADO") == regime
        candidatos.append((features, r["resultado"], retorno, mesmo_regime))

    do_regime = [x for x in candidatos if x[3]]
    usar_regime = len(do_regime) >= MIN_AMOSTRAS
    base = do_regime if usar_regime else candidatos

    vizinhos = sorted(
        [(distancia(atuais, x[0]), x) for x in base],
        key=lambda x: x[0]
    )[:MAX_VIZINHOS]

    if len(vizinhos) < MIN_AMOSTRAS:
        return None

    pesos = [1 / (0.2 + d) for d, _ in vizinhos]
    total_peso = sum(pesos)

    wins = sum(
        p for p, (_, x) in zip(pesos, vizinhos)
        if x[1] == "ACERTO"
    )
    perdas = sum(
        p for p, (_, x) in zip(pesos, vizinhos)
        if x[1] == "ERRO"
    )

    if wins + perdas == 0:
        return None

    retorno = sum(
        p * x[2] for p, (_, x) in zip(pesos, vizinhos)
    ) / total_peso

    return {
        "amostras_semelhantes": len(vizinhos),
        "probabilidade_estimada_pct": round(
            wins / (wins + perdas) * 100, 2
        ),
        "retorno_medio_pct": round(retorno, 6),
        "regime_correspondente": usar_regime,
        "distancia_media": round(
            sum(p * d for p, (d, _) in zip(pesos, vizinhos))
            / total_peso, 4
        ),
    }


def evaluate_decision(
    rows, regime, sinal_base, resumo_adaptativo=None,
    current_features=None
):
    historico = analyze_history(rows)

    resultado = {
        "participacao": True,
        "status": "ATIVA",
        "metodo": "SIMILARIDADE_DE_PADROES",
        "regime": regime,
        "sinal_base": sinal_base,
        "decisao": "AGUARDAR",
        "influencia": "SEM_EVIDENCIA",
        "amostras": 0,
        "probabilidade_estimada_pct": None,
        "taxa_acerto": None,
        "retorno_medio_pct": None,
        "motivo": "",
        "historico": historico,
        "execucao_automatica": False,
    }

    atuais = extrair_features(current_features or {})
    if atuais is None:
        resultado["motivo"] = "Indicadores atuais incompletos."
        return resultado

    direcoes = (
        [sinal_base]
        if sinal_base in ("COMPRA", "VENDA")
        else ["COMPRA", "VENDA"]
    )

    avaliadas = []

    for direcao in direcoes:
        padrao = avaliar_padroes(rows, regime, direcao, atuais)
        if padrao is not None:
            avaliadas.append((direcao, padrao))

    if not avaliadas:
        resultado["motivo"] = (
            "Histórico semelhante insuficiente para avaliar as direções."
        )
        return resultado

    melhor_direcao, melhor_padrao = max(
        avaliadas,
        key=lambda item: (
            item[1]["probabilidade_estimada_pct"],
            item[1]["retorno_medio_pct"]
        )
    )

    prob = melhor_padrao["probabilidade_estimada_pct"]
    retorno = melhor_padrao["retorno_medio_pct"]

    resultado.update({
        "amostras": melhor_padrao["amostras_semelhantes"],
        "probabilidade_estimada_pct": prob,
        "taxa_acerto": prob,
        "retorno_medio_pct": retorno,
        "distancia_media": melhor_padrao["distancia_media"],
        "regime_correspondente": melhor_padrao["regime_correspondente"],
        "direcao_avaliada": melhor_direcao,
    })

    if prob >= 55 and retorno > 0:
        resultado["decisao"] = melhor_direcao
        resultado["influencia"] = "APOIO_PREDITIVO"
        resultado["motivo"] = (
            "Direção selecionada por padrões históricos semelhantes "
            "com probabilidade mínima e retorno médio positivo."
        )
    elif prob < 45 or retorno < 0:
        resultado["influencia"] = "BLOQUEIO_PREDITIVO"
        resultado["motivo"] = (
            "Padrões semelhantes historicamente desfavoráveis."
        )
    else:
        resultado["motivo"] = "Evidência histórica inconclusiva."

    resultado["execucao_automatica"] = False
    return resultado


def avaliar_duas_direcoes(rows, regime, current_features):
    atuais = extrair_features(current_features or {})
    if atuais is None:
        return {
            "status": "SEM_DADOS",
            "motivo": "Indicadores atuais incompletos.",
            "compra": None,
            "venda": None,
        }

    saida = {}
    for sinal in ("COMPRA", "VENDA"):
        saida[sinal.lower()] = avaliar_padroes(
            rows, regime, sinal, atuais
        )

    return {
        "status": "ATIVA" if any(saida.values()) else "SEM_EVIDENCIA",
        "motivo": "Comparação histórica independente por direção.",
        "compra": saida["compra"],
        "venda": saida["venda"],
    }
