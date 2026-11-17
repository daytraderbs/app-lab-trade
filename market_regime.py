import numpy as np
import pandas as pd


def classify_market(candles):
    """
    Classifica o mercado usando tendência, volatilidade
    e força direcional.

    Retorna: TENDENCIA_ALTA, TENDENCIA_BAIXA,
    LATERAL, ALTA_VOLATILIDADE ou INDEFINIDO.
    """

    if isinstance(candles, pd.DataFrame):
        df = candles.copy()
    else:
        df = pd.DataFrame(candles)

    required = ["high", "low", "close"]

    if not all(col in df.columns for col in required):
        raise ValueError("Dados precisam conter high, low e close.")

    if len(df) < 50:
        return {
            "regime": "INDEFINIDO",
            "reason": "Histórico insuficiente."
        }

    for col in required:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    if df[required].isna().any().any():
        raise ValueError("Existem preços inválidos.")

    close = df["close"]
    high = df["high"]
    low = df["low"]

    if (close <= 0).any():
        raise ValueError("Preço de fechamento inválido.")

    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()

    previous_close = close.shift(1)

    true_range = pd.concat([
        high - low,
        (high - previous_close).abs(),
        (low - previous_close).abs()
    ], axis=1).max(axis=1)

    atr = true_range.ewm(
        alpha=1 / 14,
        adjust=False,
        min_periods=14
    ).mean()

    volatility = atr / close
    current_volatility = float(volatility.iloc[-1])

    ema_gap = float((ema20.iloc[-1] - ema50.iloc[-1]) / close.iloc[-1])

    recent_return = float(
        close.iloc[-1] / close.iloc[-11] - 1
    )

    volatility_reference = float(
        volatility.tail(100).median()
    )

    if not all(np.isfinite(x) for x in [
        current_volatility,
        ema_gap,
        recent_return,
        volatility_reference
    ]):
        return {
            "regime": "INDEFINIDO",
            "reason": "Não foi possível calcular as métricas."
        }

    # Volatilidade excepcional em relação ao histórico recente
    if (
        volatility_reference > 0
        and current_volatility > volatility_reference * 2
    ):
        regime = "ALTA_VOLATILIDADE"
        reason = "Volatilidade acima de duas vezes a mediana recente."

    elif ema_gap > 0.0002 and recent_return > 0:
        regime = "TENDENCIA_ALTA"
        reason = "EMAs e retorno recente apontam alta."

    elif ema_gap < -0.0002 and recent_return < 0:
        regime = "TENDENCIA_BAIXA"
        reason = "EMAs e retorno recente apontam baixa."

    else:
        regime = "LATERAL"
        reason = "Ausência de alinhamento direcional suficiente."

    return {
        "regime": regime,
        "reason": reason,
        "ema_gap": ema_gap,
        "recent_return": recent_return,
        "atr_percent": current_volatility,
        "volatility_reference": volatility_reference
    }
