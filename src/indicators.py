import numpy as np
from typing import List, Dict, Any, Optional

def calculate_ema(prices: np.ndarray, period: int) -> np.ndarray:
    """Calculate Exponential Moving Average."""
    if len(prices) == 0:
        return np.array([])
    ema = np.zeros_like(prices, dtype=float)
    if len(prices) < period:
        ema[:] = np.nan
        return ema
    
    alpha = 2.0 / (period + 1.0)
    # Start with simple average for first window
    ema[period - 1] = np.mean(prices[:period])
    ema[:period - 1] = np.nan
    
    for i in range(period, len(prices)):
        ema[i] = alpha * prices[i] + (1 - alpha) * ema[i - 1]
    return ema

def calculate_sma(prices: np.ndarray, period: int) -> np.ndarray:
    """Calculate Simple Moving Average."""
    if len(prices) < period:
        res = np.empty_like(prices, dtype=float)
        res[:] = np.nan
        return res
    cumsum = np.cumsum(np.insert(prices, 0, 0))
    sma = np.empty_like(prices, dtype=float)
    sma[:period - 1] = np.nan
    sma[period - 1:] = (cumsum[period:] - cumsum[:-period]) / period
    return sma

def calculate_rsi(prices: np.ndarray, period: int = 14) -> np.ndarray:
    """Calculate Relative Strength Index (Wilder's smoothing)."""
    rsi = np.zeros_like(prices, dtype=float)
    if len(prices) <= period:
        rsi[:] = 50.0
        return rsi

    deltas = np.diff(prices)
    gains = np.maximum(deltas, 0)
    losses = np.maximum(-deltas, 0)

    avg_gain = np.zeros_like(prices, dtype=float)
    avg_loss = np.zeros_like(prices, dtype=float)

    avg_gain[period] = np.mean(gains[:period])
    avg_loss[period] = np.mean(losses[:period])

    for i in range(period + 1, len(prices)):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gains[i - 1]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + losses[i - 1]) / period

    rsi[:period] = np.nan
    for i in range(period, len(prices)):
        if avg_loss[i] == 0:
            rsi[i] = 100.0 if avg_gain[i] > 0 else 50.0
        else:
            rs = avg_gain[i] / avg_loss[i]
            rsi[i] = 100.0 - (100.0 / (1.0 + rs))

    return rsi

def calculate_macd(prices: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9) -> Dict[str, np.ndarray]:
    """Calculate MACD line, signal line, and histogram."""
    fast_ema = calculate_ema(prices, fast)
    slow_ema = calculate_ema(prices, slow)
    macd_line = fast_ema - slow_ema

    # Valid values for macd_line start after slow
    signal_line = np.zeros_like(prices, dtype=float)
    signal_line[:] = np.nan
    
    valid_idx = np.where(~np.isnan(macd_line))[0]
    if len(valid_idx) >= signal:
        valid_macd = macd_line[valid_idx]
        valid_signal = calculate_ema(valid_macd, signal)
        signal_line[valid_idx] = valid_signal

    histogram = macd_line - signal_line
    return {
        "macd": macd_line,
        "signal": signal_line,
        "histogram": histogram
    }

def calculate_bollinger_bands(prices: np.ndarray, period: int = 20, num_std: float = 2.0) -> Dict[str, np.ndarray]:
    """Calculate Bollinger Bands (middle, upper, lower, bandwidth, %b)."""
    middle = calculate_sma(prices, period)
    upper = np.zeros_like(prices, dtype=float)
    lower = np.zeros_like(prices, dtype=float)
    bandwidth = np.zeros_like(prices, dtype=float)
    pct_b = np.zeros_like(prices, dtype=float)

    upper[:] = np.nan
    lower[:] = np.nan
    bandwidth[:] = np.nan
    pct_b[:] = np.nan

    for i in range(period - 1, len(prices)):
        window = prices[i - period + 1 : i + 1]
        std = np.std(window)
        upper[i] = middle[i] + num_std * std
        lower[i] = middle[i] - num_std * std
        
        bw_denom = middle[i] if middle[i] != 0 else 1e-8
        bandwidth[i] = ((upper[i] - lower[i]) / bw_denom) * 100.0
        
        diff = upper[i] - lower[i]
        pct_b[i] = (prices[i] - lower[i]) / (diff if diff != 0 else 1e-8)

    return {
        "middle": middle,
        "upper": upper,
        "lower": lower,
        "bandwidth": bandwidth,
        "pct_b": pct_b
    }

def calculate_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """Calculate Average True Range (Wilder's ATR)."""
    n = len(close)
    atr = np.zeros(n, dtype=float)
    if n <= 1:
        return atr

    tr = np.zeros(n, dtype=float)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        hl = high[i] - low[i]
        hc = abs(high[i] - close[i - 1])
        lc = abs(low[i] - close[i - 1])
        tr[i] = max(hl, hc, lc)

    if n <= period:
        atr[:] = np.mean(tr)
        return atr

    atr[:period - 1] = np.nan
    atr[period - 1] = np.mean(tr[:period])
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    return atr

def find_support_resistance(highs: np.ndarray, lows: np.ndarray, window: int = 10) -> Dict[str, float]:
    """Find key recent swing highs (resistance) and swing lows (support)."""
    n = len(highs)
    if n < window * 2:
        return {"support": float(np.min(lows)), "resistance": float(np.max(highs))}

    recent_lows = lows[-window * 2:]
    recent_highs = highs[-window * 2:]

    support = float(np.min(recent_lows))
    resistance = float(np.max(recent_highs))
    return {"support": support, "resistance": resistance}


def calculate_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> Dict[str, np.ndarray]:
    """
    Calculate Average Directional Index (ADX), +DI, and -DI using Wilder's smoothing.
    """
    n = len(close)
    adx = np.full(n, 20.0, dtype=float)
    plus_di = np.full(n, 20.0, dtype=float)
    minus_di = np.full(n, 20.0, dtype=float)

    if n <= period:
        return {"adx": adx, "plus_di": plus_di, "minus_di": minus_di}

    tr = np.zeros(n, dtype=float)
    plus_dm = np.zeros(n, dtype=float)
    minus_dm = np.zeros(n, dtype=float)

    tr[0] = high[0] - low[0]
    for i in range(1, n):
        hl = high[i] - low[i]
        hc = abs(high[i] - close[i - 1])
        lc = abs(low[i] - close[i - 1])
        tr[i] = max(hl, hc, lc)

        up_move = high[i] - high[i - 1]
        down_move = low[i - 1] - low[i]

        if up_move > down_move and up_move > 0:
            plus_dm[i] = up_move
        else:
            plus_dm[i] = 0.0

        if down_move > up_move and down_move > 0:
            minus_dm[i] = down_move
        else:
            minus_dm[i] = 0.0

    # Wilder's smoothing
    smoothed_tr = np.zeros(n, dtype=float)
    smoothed_plus_dm = np.zeros(n, dtype=float)
    smoothed_minus_dm = np.zeros(n, dtype=float)

    smoothed_tr[period] = np.sum(tr[1 : period + 1])
    smoothed_plus_dm[period] = np.sum(plus_dm[1 : period + 1])
    smoothed_minus_dm[period] = np.sum(minus_dm[1 : period + 1])

    for i in range(period + 1, n):
        smoothed_tr[i] = smoothed_tr[i - 1] - (smoothed_tr[i - 1] / period) + tr[i]
        smoothed_plus_dm[i] = smoothed_plus_dm[i - 1] - (smoothed_plus_dm[i - 1] / period) + plus_dm[i]
        smoothed_minus_dm[i] = smoothed_minus_dm[i - 1] - (smoothed_minus_dm[i - 1] / period) + minus_dm[i]

    dx = np.zeros(n, dtype=float)
    for i in range(period, n):
        tr_val = smoothed_tr[i] if smoothed_tr[i] > 1e-8 else 1e-8
        plus_di[i] = 100.0 * (smoothed_plus_dm[i] / tr_val)
        minus_di[i] = 100.0 * (smoothed_minus_dm[i] / tr_val)
        di_sum = plus_di[i] + minus_di[i]
        dx[i] = 100.0 * (abs(plus_di[i] - minus_di[i]) / (di_sum if di_sum > 1e-8 else 1e-8))

    # Calculate ADX as Wilder's smoothed DX
    first_adx_idx = 2 * period
    if n > first_adx_idx:
        adx[first_adx_idx] = np.mean(dx[period : first_adx_idx + 1])
        for i in range(first_adx_idx + 1, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
    else:
        adx[period:] = dx[period:]

    # Clean NaNs
    adx[np.isnan(adx)] = 20.0
    plus_di[np.isnan(plus_di)] = 20.0
    minus_di[np.isnan(minus_di)] = 20.0

    return {"adx": adx, "plus_di": plus_di, "minus_di": minus_di}


def calculate_keltner_channels(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 20, atr_mult: float = 1.5
) -> Dict[str, np.ndarray]:
    """Calculate Keltner Channels (EMA middle +/- ATR multiplier)."""
    middle = calculate_ema(close, period)
    atr = calculate_atr(high, low, close, period)
    upper = middle + (atr_mult * atr)
    lower = middle - (atr_mult * atr)
    return {"middle": middle, "upper": upper, "lower": lower}


def calculate_ttm_squeeze(
    bb_upper: np.ndarray, bb_lower: np.ndarray, kc_upper: np.ndarray, kc_lower: np.ndarray
) -> Dict[str, Any]:
    """
    Evaluate Bollinger Bands inside Keltner Channel to detect TTM Squeeze.
    """
    n = len(bb_upper)
    if n == 0 or np.isnan(bb_upper[-1]) or np.isnan(kc_upper[-1]):
        return {"is_squeeze": False, "status": "NO_SQUEEZE"}

    is_squeeze = bool(bb_upper[-1] < kc_upper[-1] and bb_lower[-1] > kc_lower[-1])
    prev_squeeze = bool(n > 1 and not np.isnan(bb_upper[-2]) and not np.isnan(kc_upper[-2]) and bb_upper[-2] < kc_upper[-2] and bb_lower[-2] > kc_lower[-2])

    if is_squeeze:
        status = "SQUEEZE_ON"
    elif prev_squeeze and not is_squeeze:
        status = "SQUEEZE_FIRED"
    else:
        status = "EXPANDING"

    return {"is_squeeze": is_squeeze, "status": status}


def compute_all_indicators(klines: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Compute full technical indicator suite on klines.
    Each kline expected format: {'time': int, 'open': float, 'high': float, 'low': float, 'close': float, 'volume': float, ...}
    Returns decorated candles and current latest indicator telemetry.
    """
    if not klines:
        return {"series": [], "latest": {}}

    closes = np.array([k["close"] for k in klines], dtype=float)
    highs = np.array([k["high"] for k in klines], dtype=float)
    lows = np.array([k["low"] for k in klines], dtype=float)
    opens = np.array([k["open"] for k in klines], dtype=float)
    volumes = np.array([k.get("volume", 0.0) for k in klines], dtype=float)

    # Compute Core Indicators
    ema9 = calculate_ema(closes, 9)
    ema21 = calculate_ema(closes, 21)
    ema50 = calculate_ema(closes, 50)
    ema200 = calculate_ema(closes, 200)

    rsi = calculate_rsi(closes, 14)
    macd_res = calculate_macd(closes, 12, 26, 9)
    bb_res = calculate_bollinger_bands(closes, 20, 2.0)
    atr = calculate_atr(highs, lows, closes, 14)
    vol_sma = calculate_sma(volumes, 20)

    # Advanced Quant & Regime Indicators
    adx_res = calculate_adx(highs, lows, closes, 14)
    kc_res = calculate_keltner_channels(highs, lows, closes, 20, 1.5)
    squeeze_res = calculate_ttm_squeeze(bb_res["upper"], bb_res["lower"], kc_res["upper"], kc_res["lower"])

    # Decorate klines with indicators for frontend lightweight charts
    decorated_series = []
    n = len(klines)
    for i in range(n):
        k = klines[i]
        item = {
            "time": k["time"],
            "open": float(opens[i]),
            "high": float(highs[i]),
            "low": float(lows[i]),
            "close": float(closes[i]),
            "volume": float(volumes[i]),
            "ema9": None if np.isnan(ema9[i]) else round(float(ema9[i]), 8),
            "ema21": None if np.isnan(ema21[i]) else round(float(ema21[i]), 8),
            "ema50": None if np.isnan(ema50[i]) else round(float(ema50[i]), 8),
            "ema200": None if np.isnan(ema200[i]) else round(float(ema200[i]), 8),
            "rsi": None if np.isnan(rsi[i]) else round(float(rsi[i]), 2),
            "macd": None if np.isnan(macd_res["macd"][i]) else round(float(macd_res["macd"][i]), 8),
            "macd_signal": None if np.isnan(macd_res["signal"][i]) else round(float(macd_res["signal"][i]), 8),
            "macd_hist": None if np.isnan(macd_res["histogram"][i]) else round(float(macd_res["histogram"][i]), 8),
            "bb_upper": None if np.isnan(bb_res["upper"][i]) else round(float(bb_res["upper"][i]), 8),
            "bb_middle": None if np.isnan(bb_res["middle"][i]) else round(float(bb_res["middle"][i]), 8),
            "bb_lower": None if np.isnan(bb_res["lower"][i]) else round(float(bb_res["lower"][i]), 8),
            "atr": None if np.isnan(atr[i]) else round(float(atr[i]), 8),
            "adx": round(float(adx_res["adx"][i]), 1),
        }
        decorated_series.append(item)

    # Latest bar metrics
    last_idx = -1
    cur_price = closes[last_idx]
    prev_price = closes[last_idx - 1] if n > 1 else cur_price
    price_change_pct = ((cur_price - prev_price) / prev_price) * 100 if prev_price else 0.0

    cur_vol = volumes[last_idx]
    avg_vol = vol_sma[last_idx] if not np.isnan(vol_sma[last_idx]) else cur_vol
    vol_spike = (cur_vol / avg_vol) if avg_vol > 0 else 1.0

    levels = find_support_resistance(highs, lows)
    cur_atr = atr[last_idx] if not np.isnan(atr[last_idx]) else (cur_price * 0.01)

    # Trend categorization
    trend_state = "NEUTRAL"
    if not np.isnan(ema9[last_idx]) and not np.isnan(ema21[last_idx]):
        if cur_price > ema9[last_idx] > ema21[last_idx]:
            trend_state = "STRONG_BULLISH" if (not np.isnan(ema50[last_idx]) and ema21[last_idx] > ema50[last_idx]) else "BULLISH"
        elif cur_price < ema9[last_idx] < ema21[last_idx]:
            trend_state = "STRONG_BEARISH" if (not np.isnan(ema50[last_idx]) and ema21[last_idx] < ema50[last_idx]) else "BEARISH"

    latest_adx = float(adx_res["adx"][last_idx])
    market_regime = "STRONG_TREND" if latest_adx >= 25 else ("CHOP_RANGE" if latest_adx < 20 else "MODERATE_TREND")

    latest_telemetry = {
        "price": float(cur_price),
        "change_pct": round(float(price_change_pct), 3),
        "rsi": round(float(rsi[last_idx]), 2) if not np.isnan(rsi[last_idx]) else 50.0,
        "macd": round(float(macd_res["macd"][last_idx]), 8) if not np.isnan(macd_res["macd"][last_idx]) else 0.0,
        "macd_signal": round(float(macd_res["signal"][last_idx]), 8) if not np.isnan(macd_res["signal"][last_idx]) else 0.0,
        "macd_hist": round(float(macd_res["histogram"][last_idx]), 8) if not np.isnan(macd_res["histogram"][last_idx]) else 0.0,
        "bb_upper": round(float(bb_res["upper"][last_idx]), 8) if not np.isnan(bb_res["upper"][last_idx]) else float(cur_price),
        "bb_middle": round(float(bb_res["middle"][last_idx]), 8) if not np.isnan(bb_res["middle"][last_idx]) else float(cur_price),
        "bb_lower": round(float(bb_res["lower"][last_idx]), 8) if not np.isnan(bb_res["lower"][last_idx]) else float(cur_price),
        "bb_pct_b": round(float(bb_res["pct_b"][last_idx]), 3) if not np.isnan(bb_res["pct_b"][last_idx]) else 0.5,
        "atr": round(float(cur_atr), 8),
        "atr_pct": round((float(cur_atr) / float(cur_price)) * 100, 2) if cur_price else 1.0,
        "ema9": round(float(ema9[last_idx]), 8) if not np.isnan(ema9[last_idx]) else None,
        "ema21": round(float(ema21[last_idx]), 8) if not np.isnan(ema21[last_idx]) else None,
        "ema50": round(float(ema50[last_idx]), 8) if not np.isnan(ema50[last_idx]) else None,
        "ema200": round(float(ema200[last_idx]), 8) if not np.isnan(ema200[last_idx]) else None,
        "volume": float(cur_vol),
        "vol_spike": round(float(vol_spike), 2),
        "rvol": round(float(vol_spike), 2),
        "adx": round(latest_adx, 1),
        "plus_di": round(float(adx_res["plus_di"][last_idx]), 1),
        "minus_di": round(float(adx_res["minus_di"][last_idx]), 1),
        "market_regime": market_regime,
        "squeeze_status": squeeze_res["status"],
        "is_squeeze": squeeze_res["is_squeeze"],
        "support": levels["support"],
        "resistance": levels["resistance"],
        "trend_state": trend_state
    }

    return {
        "series": decorated_series,
        "latest": latest_telemetry
    }
