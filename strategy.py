import math

def generate_signal(metrics):
    required = [
        "ema_9", "ema_21", "ema_200",
        "rsi_14", "macd_histogram", "atr_14"
    ]

    if not all(key in metrics for key in required):
        return {"error": "Indicadores incompletos para análise."}

    values = [float(metrics[key]) for key in required]

    if not all(math.isfinite(value) for value in values):
        return {"error": "Valores inválidos nos indicadores."}

    ema9 = float(metrics["ema_9"])
    ema21 = float(metrics["ema_21"])
    ema200 = float(metrics["ema_200"])
    rsi = float(metrics["rsi_14"])
    macd = float(metrics["macd_histogram"])
    atr = float(metrics["atr_14"])

    score = 0
    reasons = []

    # Tendência pelas médias móveis
    if ema9 > ema21 > ema200:
        score += 2
        reasons.append("Tendência de alta confirmada pelas EMAs.")
    elif ema9 < ema21 < ema200:
        score -= 2
        reasons.append("Tendência de baixa confirmada pelas EMAs.")
    else:
        reasons.append("EMAs sem alinhamento claro.")

    # Momentum pelo MACD
    if macd > 0:
        score += 1
        reasons.append("MACD com momentum comprador.")
    elif macd < 0:
        score -= 1
        reasons.append("MACD com momentum vendedor.")
    else:
        reasons.append("MACD sem direção definida.")

    # Força relativa pelo RSI
    if rsi > 55:
        score += 1
        reasons.append("RSI favorece os compradores.")
    elif rsi < 45:
        score -= 1
        reasons.append("RSI favorece os vendedores.")
    else:
        reasons.append("RSI em zona intermediária.")

    # Classificação
    if score >= 3:
        signal = "COMPRA"
    elif score <= -3:
        signal = "VENDA"
    else:
        signal = "AGUARDAR"

    return {
        "signal": signal,
        "score": score,
        "maximum_score": 4,
        "reasons": reasons,
        "rsi": rsi,
        "atr": atr,
        "execution": False
    }
