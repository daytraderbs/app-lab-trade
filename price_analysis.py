import numpy as np
import pandas as pd

from feature_engineering import build_features
from market_regime import classify_market


def analyze_recent_price(candles):
    """
    Analisa os últimos 10 candles fechados.
    Não gera sinais de compra ou venda.
    """

    df = pd.DataFrame(candles).copy()

    required = ["open", "high", "low", "close"]
    if not all(col in df.columns for col in required):
        raise ValueError("Histórico precisa conter OHLC.")

    if len(df) < 30:
        return {
            "status": "INSUFICIENTE",
            "reason": "São necessários pelo menos 30 candles."
        }

    if "timestamp" in df.columns:
        df = df.sort_values("timestamp").reset_index(drop=True)

    for col in required:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    if df[required].isna().any().any():
        raise ValueError("Histórico contém preços inválidos.")

    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("Histórico contém preços não positivos.")

    if (
        (df["high"] < df[["open", "close", "low"]].max(axis=1)).any()
        or (df["low"] > df[["open", "close", "high"]].min(axis=1)).any()
    ):
        raise ValueError("Há candles com OHLC inconsistente.")

    recent = df.tail(10).copy()
    last = recent.iloc[-1]

    candle_range = float(last["high"] - last["low"])
    body = float(last["close"] - last["open"])

    if candle_range <= 0:
        candle_type = "INDEFINIDO"
        body_ratio = 0.0
        upper_wick_ratio = 0.0
        lower_wick_ratio = 0.0
    else:
        body_ratio = abs(body) / candle_range
        upper_wick_ratio = (
            last["high"] - max(last["open"], last["close"])
        ) / candle_range
        lower_wick_ratio = (
            min(last["open"], last["close"]) - last["low"]
        ) / candle_range

        if body_ratio <= 0.10:
            candle_type = "DOJI"
        elif body > 0:
            candle_type = "ALTA"
        else:
            candle_type = "BAIXA"

    bullish = int((recent["close"] > recent["open"]).sum())
    bearish = int((recent["close"] < recent["open"]).sum())
    neutral = 10 - bullish - bearish

    first_close = float(recent["close"].iloc[0])
    last_close = float(recent["close"].iloc[-1])

    variation = (last_close / first_close) - 1

    features = build_features(df)
    latest_features = features.iloc[-1].replace(
        [np.inf, -np.inf], np.nan
    )

    feature_values = {
        key: float(value)
        for key, value in latest_features.items()
        if pd.notna(value)
    }

    regime = classify_market(df)

    return {
        "status": "OK",
        "candles_analyzed": 10,
        "recent_price_action": {
            "bullish_candles": bullish,
            "bearish_candles": bearish,
            "neutral_candles": neutral,
            "variation_10_candles": float(variation),
            "direction": (
                "ALTA" if variation > 0
                else "BAIXA" if variation < 0
                else "NEUTRA"
            )
        },
        "latest_candle": {
            "type": candle_type,
            "body_ratio": float(body_ratio),
            "upper_wick_ratio": float(upper_wick_ratio),
            "lower_wick_ratio": float(lower_wick_ratio)
        },
        "market_regime": regime,
        "features": feature_values,
        "decision": "AGUARDAR",
        "execution": False
    }
