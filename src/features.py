"""Model features for each closed 5m bar, shared by training and live inference.

Every value uses only candles closed at or before the bar's close: 5m features
use rolling and recursive indicators over past bars, and a 15m/1h/4h candle is
joined only once its close time is at or before the 5m bar's close.
"""

from typing import Dict, List

import numpy as np
import pandas as pd

from .indicators import (
    calculate_adx,
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_keltner_channels,
    calculate_macd,
    calculate_rsi,
)

STEP_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
HTF_PREFIX = {"15m": "m15", "1h": "h1", "4h": "h4"}

BASE_COLUMNS = [
    "ret_1", "ret_3", "ret_12", "ret_48", "vol_12", "vol_48", "atr_pct", "rsi", "macd_hist",
    "bb_pct_b", "adx", "di_diff", "ema_dist_9", "ema_dist_21", "ema_dist_50", "ema_dist_200",
    "range_pos_20", "dist_high_20", "dist_low_20", "vol_z", "taker_ratio", "squeeze",
    "hour_sin", "hour_cos",
]
HTF_COLUMNS = ["rsi", "ret_1", "ret_4", "ema_dist_21", "ema_dist_50", "adx", "atr_pct"]
FEATURE_COLUMNS: List[str] = BASE_COLUMNS + [
    f"{HTF_PREFIX[tf]}_{name}" for tf in ("15m", "1h", "4h") for name in HTF_COLUMNS
]


def _ema_dist(close: np.ndarray, period: int) -> np.ndarray:
    return (close - calculate_ema(close, period)) / close


def _htf_features(df: pd.DataFrame) -> pd.DataFrame:
    c, h, l = (df[k].to_numpy(float) for k in ("close", "high", "low"))
    logc = pd.Series(np.log(c))
    return pd.DataFrame({
        "rsi": calculate_rsi(c),
        "ret_1": logc.diff(1).to_numpy(),
        "ret_4": logc.diff(4).to_numpy(),
        "ema_dist_21": _ema_dist(c, 21),
        "ema_dist_50": _ema_dist(c, 50),
        "adx": calculate_adx(h, l, c)["adx"],
        "atr_pct": calculate_atr(h, l, c) / c,
    })


def _base_features(df: pd.DataFrame) -> pd.DataFrame:
    h, l, c, v = (df[k].to_numpy(float) for k in ("high", "low", "close", "volume"))
    logc = pd.Series(np.log(c))
    r1 = logc.diff()
    high20 = pd.Series(h).rolling(20).max().to_numpy()
    low20 = pd.Series(l).rolling(20).min().to_numpy()
    span = np.where(high20 - low20 > 0, high20 - low20, np.nan)
    vol = pd.Series(v)
    vol_std = vol.rolling(20).std().to_numpy()
    taker = df["taker_buy_volume"].to_numpy(float) if "taker_buy_volume" in df else np.full(len(df), np.nan)
    bb = calculate_bollinger_bands(c)
    kc = calculate_keltner_channels(h, l, c)
    adx = calculate_adx(h, l, c)
    hour = (df["time"].to_numpy(np.int64) % 86400) / 3600.0
    return pd.DataFrame({
        "ret_1": r1.to_numpy(),
        "ret_3": logc.diff(3).to_numpy(),
        "ret_12": logc.diff(12).to_numpy(),
        "ret_48": logc.diff(48).to_numpy(),
        "vol_12": r1.rolling(12).std().to_numpy(),
        "vol_48": r1.rolling(48).std().to_numpy(),
        "atr_pct": calculate_atr(h, l, c) / c,
        "rsi": calculate_rsi(c),
        "macd_hist": calculate_macd(c)["histogram"] / c,
        "bb_pct_b": bb["pct_b"],
        "adx": adx["adx"],
        "di_diff": adx["plus_di"] - adx["minus_di"],
        "ema_dist_9": _ema_dist(c, 9),
        "ema_dist_21": _ema_dist(c, 21),
        "ema_dist_50": _ema_dist(c, 50),
        "ema_dist_200": _ema_dist(c, 200),
        "range_pos_20": (c - low20) / span,
        "dist_high_20": (high20 - c) / c,
        "dist_low_20": (c - low20) / c,
        "vol_z": (v - vol.rolling(20).mean().to_numpy()) / np.where(vol_std > 0, vol_std, np.nan),
        "taker_ratio": np.divide(taker, v, out=np.full(len(v), np.nan), where=v > 0),
        "squeeze": ((bb["upper"] < kc["upper"]) & (bb["lower"] > kc["lower"])).astype(float),
        "hour_sin": np.sin(2 * np.pi * hour / 24),
        "hour_cos": np.cos(2 * np.pi * hour / 24),
    })


def build_features(frames: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per 5m candle in ``frames["5m"]``: ``time`` plus FEATURE_COLUMNS (float32).

    ``frames`` maps "5m", "15m", "1h", "4h" to candles with columns time (open,
    seconds), open, high, low, close, volume and optionally taker_buy_volume,
    sorted oldest first. Higher-timeframe rows that have not closed are ignored.
    """
    base = frames["5m"].reset_index(drop=True)
    out = pd.concat([base[["time"]].astype(np.int64), _base_features(base)], axis=1)
    out["bar_close"] = out["time"] + STEP_SECONDS["5m"]
    for tf in ("15m", "1h", "4h"):
        htf = frames[tf].reset_index(drop=True)
        feats = _htf_features(htf).add_prefix(HTF_PREFIX[tf] + "_")
        feats["htf_close"] = htf["time"].astype(np.int64) + STEP_SECONDS[tf]
        # tolerance: a gap in higher-timeframe history leaves NaN instead of reusing an old candle
        out = pd.merge_asof(out, feats, left_on="bar_close", right_on="htf_close", direction="backward",
                            tolerance=STEP_SECONDS[tf])
        out = out.drop(columns="htf_close")
    out = out.drop(columns="bar_close")
    out[FEATURE_COLUMNS] = out[FEATURE_COLUMNS].astype(np.float32)
    return out[["time", *FEATURE_COLUMNS]]
