import numpy as np
import pandas as pd


def build_features(candles):
    if isinstance(candles, pd.DataFrame):
        df = candles.copy()
    else:
        df = pd.DataFrame(candles)

    required = ["open", "high", "low", "close"]

    if not all(col in df.columns for col in required):
        raise ValueError("Candles precisam conter OHLC.")

    if len(df) < 30:
        raise ValueError("São necessários pelo menos 30 candles.")

    if "timestamp" in df.columns:
        df = df.sort_values("timestamp").reset_index(drop=True)

    for col in required:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    if df[required].isna().any().any():
        raise ValueError("Existem valores OHLC inválidos.")

    if (df["close"] <= 0).any() or (df["open"] <= 0).any():
        raise ValueError("Preço inválido.")

    o, h, l, c = (
        df["open"], df["high"], df["low"], df["close"]
    )

    features = pd.DataFrame(index=df.index)

    # Retornos
    for period in [1, 3, 5, 10]:
        features[f"return_{period}"] = c.pct_change(period)

    # Geometria dos candles
    candle_range = (h - l).replace(0, np.nan)

    features["body_ratio"] = (c - o) / candle_range
    features["upper_wick_ratio"] = (
        h - pd.concat([o, c], axis=1).max(axis=1)
    ) / candle_range
    features["lower_wick_ratio"] = (
        pd.concat([o, c], axis=1).min(axis=1) - l
    ) / candle_range

    features["range_pct"] = (h - l) / c
    features["body_pct"] = (c - o) / o

    # Volatilidade
    features["volatility_5"] = c.pct_change().rolling(5).std()
    features["volatility_10"] = c.pct_change().rolling(10).std()
    features["volatility_20"] = c.pct_change().rolling(20).std()

    previous_close = c.shift(1)

    true_range = pd.concat([
        h - l,
        (h - previous_close).abs(),
        (l - previous_close).abs()
    ], axis=1).max(axis=1)

    atr = true_range.ewm(
        alpha=1 / 14,
        adjust=False,
        min_periods=14
    ).mean()

    features["atr_pct"] = atr / c

    # Tendência e momentum
    ema9 = c.ewm(span=9, adjust=False).mean()
    ema21 = c.ewm(span=21, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()

    features["ema_gap_9_21"] = (ema9 - ema21) / c
    features["ema_gap_21_50"] = (ema21 - ema50) / c
    features["price_gap_ema9"] = (c - ema9) / c
    features["momentum_5"] = c / c.shift(5) - 1
    features["momentum_10"] = c / c.shift(10) - 1

    # Volume opcional
    if "volume" in df.columns:
        v = pd.to_numeric(df["volume"], errors="coerce")
        volume_mean = v.rolling(20).mean()
        features["volume_relative"] = (
            v / volume_mean.replace(0, np.nan)
        )

    return features.replace([np.inf, -np.inf], np.nan)
