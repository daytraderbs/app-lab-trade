import math

def generate_signal(metrics):
    required = [
        "ema_9", "ema_21", "ema_200",
        "rsi_14", "macd_histogram", "atr_14",
        "last_close"
    ]

    if not all(key in metrics for key in required):
        return {"error": "Indicadores incompletos para análise."}

    try:
        values = {key: float(metrics[key]) for key in required}
    except (TypeError, ValueError):
        return {"error": "Valores inválidos nos indicadores."}

    if not all(math.isfinite(v) for v in values.values()):
        return {"error": "Valores não finitos nos indicadores."}

    ema9 = values["ema_9"]
    ema21 = values["ema_21"]
    ema200 = values["ema_200"]
    rsi = values["rsi_14"]
    macd = values["macd_histogram"]
    atr = values["atr_14"]
    price = values["last_close"]

    if atr <= 0 or price <= 0 or not 0 <= rsi <= 100:
        return {"error": "Indicadores fora dos limites válidos."}

    score = 0
    reasons = []

    if ema9 > ema21 > ema200:
        score += 2
        reasons.append("Tendência de alta pelas EMAs.")
    elif ema9 < ema21 < ema200:
        score -= 2
        reasons.append("Tendência de baixa pelas EMAs.")
    else:
        reasons.append("EMAs sem alinhamento claro.")

    if macd > 0:
        score += 1
        reasons.append("MACD comprador.")
    elif macd < 0:
        score -= 1
        reasons.append("MACD vendedor.")

    if rsi > 55:
        score += 1
        reasons.append("RSI favorece compradores.")
    elif rsi < 45:
        score -= 1
        reasons.append("RSI favorece vendedores.")

    distance_atr = abs(price - ema9) / atr
    signal = "AGUARDAR"

    if score >= 3:
        signal = "COMPRA"
    elif score <= -3:
        signal = "VENDA"

    if distance_atr > 1.5:
        signal = "AGUARDAR"
        reasons.append("Movimento muito distante da EMA 9.")

    if signal == "COMPRA" and rsi >= 70:
        signal = "AGUARDAR"
        reasons.append("RSI elevado: evitar compra esticada.")

    if signal == "VENDA" and rsi <= 30:
        signal = "AGUARDAR"
        reasons.append("RSI muito baixo: evitar venda esticada.")

    return {
        "signal": signal,
        "score": score,
        "maximum_score": 4,
        "reasons": reasons,
        "rsi": rsi,
        "atr": atr,
        "distance_from_ema9_atr": round(distance_atr, 2),
        "execution": False
    }
