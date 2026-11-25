from collections import defaultdict
from statistics import mean


MIN_AMOSTRAS = 30


def analyze_history(rows):
    grupos = defaultdict(list)
    total = []

    for row in rows:
        if row.get("status") != "FINALIZADO":
            continue

        if row.get("resultado") not in ("ACERTO", "ERRO", "EMPATE"):
            continue

        try:
            retorno = float(row.get("retorno_pct") or 0)
        except (TypeError, ValueError):
            continue

        registro = {
            "resultado": row["resultado"],
            "retorno": retorno,
            "sinal": row.get("sinal", "INDEFINIDO"),
        }

        total.append(registro)
        regime = row.get("regime") or "NAO_CLASSIFICADO"
        grupos[(regime, registro["sinal"])].append(registro)

    acertos = sum(x["resultado"] == "ACERTO" for x in total)
    erros = sum(x["resultado"] == "ERRO" for x in total)

    desempenho = {}

    for (regime, sinal), amostras in grupos.items():
        quantidade = len(amostras)
        wins = sum(x["resultado"] == "ACERTO" for x in amostras)
        losses = sum(x["resultado"] == "ERRO" for x in amostras)

        desempenho.setdefault(regime, {})[sinal] = {
            "amostras": quantidade,
            "acertos": wins,
            "erros": losses,
            "taxa_acerto": round(wins / quantidade * 100, 2),
            "retorno_medio_pct": round(
                mean(x["retorno"] for x in amostras), 6
            ),
            "amostra_suficiente": quantidade >= MIN_AMOSTRAS,
        }

    return {
        "status": "APRENDIZADO_CONTINUO",
        "amostras_avaliadas": len(total),
        "acertos": acertos,
        "erros": erros,
        "taxa_acerto": round(acertos / len(total) * 100, 2)
        if total else 0,
        "desempenho_por_regime": desempenho,
        "recalibracao_automatica": False,
        "execucao_automatica": False,
    }


def evaluate_decision(rows, regime, sinal_base, resumo_adaptativo=None):
    if (
        isinstance(resumo_adaptativo, dict)
        and isinstance(
            resumo_adaptativo.get('desempenho_por_regime'), dict
        )
    ):
        historico = resumo_adaptativo
    else:
        historico = analyze_history(rows)

    resultado = {
        "participacao": True,
        "status": "ATIVA",
        "regime": regime,
        "sinal_base": sinal_base,
        "decisao": sinal_base if sinal_base in ("COMPRA", "VENDA")
        else "AGUARDAR",
        "influencia": "NEUTRA",
        "amostras": 0,
        "taxa_acerto": None,
        "retorno_medio_pct": None,
        "motivo": "",
        "execucao_automatica": False,
    }

    if sinal_base not in ("COMPRA", "VENDA"):
        resultado["motivo"] = "Nenhum sinal direcional de base."
        resultado["decisao"] = "AGUARDAR"
        resultado["historico"] = historico
        return resultado

    metricas = (
        historico["desempenho_por_regime"]
        .get(regime, {})
        .get(sinal_base)
    )

    if not metricas:
        resultado["motivo"] = (
            "Coletando resultados para este regime e direção."
        )
        resultado["historico"] = historico
        return resultado

    resultado["amostras"] = metricas["amostras"]
    resultado["taxa_acerto"] = metricas["taxa_acerto"]
    resultado["retorno_medio_pct"] = metricas["retorno_medio_pct"]

    if metricas["amostras"] < MIN_AMOSTRAS:
        resultado["motivo"] = (
            "Amostra ainda insuficiente. Sinal original preservado."
        )
        resultado["historico"] = historico
        return resultado

    taxa = metricas["taxa_acerto"]
    retorno = metricas["retorno_medio_pct"]

    if taxa < 45 or retorno < 0:
        resultado["decisao"] = "AGUARDAR"
        resultado["influencia"] = "BLOQUEIO_ADAPTATIVO"
        resultado["motivo"] = (
            "Histórico desfavorável neste regime e direção."
        )

    elif taxa >= 55 and retorno > 0:
        resultado["influencia"] = "APOIO_ADAPTATIVO"
        resultado["motivo"] = (
            "Histórico favorável neste regime e direção."
        )

    else:
        resultado["motivo"] = (
            "Desempenho histórico inconclusivo. "
            "Sinal original preservado."
        )

    resultado["historico"] = historico
    return resultado
