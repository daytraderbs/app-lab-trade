import pandas as pd
import numpy as np
from typing import List, Dict

def calculate_indicators(candles: List[Dict]) -> Dict:
    if len(candles) < 200:
        return {"error": "É necessário um mínimo de 200 candles para calcular a EMA 200 corretamente."}
        
    df = pd.DataFrame(candles)
    df = df.sort_values('timestamp').reset_index(drop=True)
    
    close = df['close'].astype(float)
    high = df['high'].astype(float)
    low = df['low'].astype(float)
    
    results = {}
    results['last_close'] = float(close.iloc[-1])
    
    # EMAs
    results['ema_9'] = close.ewm(span=9, adjust=False).mean().iloc[-1]
    results['ema_21'] = close.ewm(span=21, adjust=False).mean().iloc[-1]
    results['ema_200'] = close.ewm(span=200, adjust=False).mean().iloc[-1]
    
    # RSI 14
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / np.where(loss == 0, 0.00001, loss)
    results['rsi_14'] = (100 - (100 / (1 + rs))).iloc[-1]
    
    # MACD
    ema_12 = close.ewm(span=12, adjust=False).mean()
    ema_26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema_12 - ema_26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    
    results['macd_line'] = macd_line.iloc[-1]
    results['macd_signal'] = signal_line.iloc[-1]
    results['macd_histogram'] = (macd_line - signal_line).iloc[-1]
    
    # ATR 14
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    results['atr_14'] = tr.ewm(alpha=1/14, adjust=False).mean().iloc[-1]
    
    return {k: float(v) for k, v in results.items()}
    
