import numpy as np
import pandas as pd

from src.features import FEATURE_COLUMNS, build_features

STEP = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}


def synthetic_frames(n5=6000, seed=3):
    rng = np.random.default_rng(seed)
    t = 1_700_000_000 - 1_700_000_000 % 14400 + np.arange(n5) * 300
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, n5)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.001, n5))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.001, n5))
    vol = rng.uniform(10, 20, n5)
    base = pd.DataFrame({"time": t, "open": open_, "high": high, "low": low, "close": close,
                         "volume": vol, "taker_buy_volume": vol * rng.uniform(0.3, 0.7, n5)})
    frames = {"5m": base}
    for tf in ("15m", "1h", "4h"):
        key = base["time"] // STEP[tf] * STEP[tf]
        frames[tf] = base.groupby(key).agg(
            open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
            volume=("volume", "sum"), taker_buy_volume=("taker_buy_volume", "sum"),
        ).reset_index(names="time")
    return frames


def truncate(frames, now):
    """What a live fetch at wall-clock `now` sees: 5m bars closed by now, HTF bars opened before now."""
    out = {"5m": frames["5m"][frames["5m"]["time"] + 300 <= now].reset_index(drop=True)}
    for tf in ("15m", "1h", "4h"):
        out[tf] = frames[tf][frames[tf]["time"] < now].reset_index(drop=True)
    return out


def test_one_row_per_5m_bar_with_all_columns():
    frames = synthetic_frames(6000)  # 125 4h candles: enough for the 4h EMA50
    feats = build_features(frames)
    assert len(feats) == 6000
    assert list(feats.columns) == ["time", *FEATURE_COLUMNS]
    assert feats[FEATURE_COLUMNS].iloc[-1].notna().all()


def test_future_bars_never_change_past_features():
    frames = synthetic_frames(3000)
    full = build_features(frames)
    now = int(frames["5m"]["time"].iloc[2500]) + 300 + 120  # mid-way through a 15m/1h/4h candle
    cut = build_features(truncate(frames, now))
    past = full.iloc[:len(cut)]
    pd.testing.assert_frame_equal(cut.reset_index(drop=True), past.reset_index(drop=True))


def test_higher_timeframe_candle_counts_only_after_it_closes():
    frames = synthetic_frames(3000)
    feats = build_features(frames).set_index("time")
    one_hour = frames["1h"].set_index("time")
    # 5m bar opening at hh:50 closes at hh:55 -> the hh:00 hour is still forming, use the previous hour
    hour_open = int(one_hour.index[20])
    bar = hour_open + 50 * 60
    prev_hour_close = one_hour["close"].loc[hour_open - 3600]
    this_hour_close = one_hour["close"].loc[hour_open]
    prev_hour_return = np.log(prev_hour_close / one_hour["close"].loc[hour_open - 7200])
    assert feats.loc[bar, "h1_ret_1"] == np.float32(prev_hour_return)
    # 5m bar opening at hh:55 closes exactly when the hour closes -> it may use that hour
    closing_bar = hour_open + 55 * 60
    assert feats.loc[closing_bar, "h1_ret_1"] == np.float32(np.log(this_hour_close / prev_hour_close))


def test_live_window_matches_full_history_on_the_last_bar():
    frames = synthetic_frames(6000)
    full = build_features(frames).iloc[-1]
    window = {tf: df.tail(1000).reset_index(drop=True) for tf, df in frames.items()}
    live = build_features(window).iloc[-1]
    np.testing.assert_allclose(live[FEATURE_COLUMNS].astype(float), full[FEATURE_COLUMNS].astype(float),
                               rtol=2e-3, atol=2e-4)


def test_stale_higher_timeframe_data_is_not_carried_forward():
    frames = synthetic_frames(3000)
    frames["1h"] = frames["1h"].iloc[:10]  # the 1h history stops early
    feats = build_features(frames)
    last_1h_close = int(frames["1h"]["time"].iloc[-1]) + 3600
    late = feats[feats["time"] + 300 > last_1h_close + 3600]
    assert late["h1_ret_1"].isna().all()


def daily_and_15m(frames):
    """15m base plus a 1d frame from the same synthetic 5m series."""
    base = frames["5m"]
    out = {}
    for tf, step in (("15m", 900), ("1d", 86400)):
        key = base["time"] // step * step
        out[tf] = base.groupby(key).agg(
            open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
            volume=("volume", "sum"), taker_buy_volume=("taker_buy_volume", "sum"),
        ).reset_index(names="time")
    return {"15m": out["15m"], "1h": frames["1h"], "4h": frames["4h"], "1d": out["1d"]}


def test_features_on_15m_base_with_daily_context():
    from src.features import feature_columns

    frames = daily_and_15m(synthetic_frames(12000))  # ~41 days
    context = ("1h", "4h", "1d")
    feats = build_features(frames, base="15m", context=context)
    assert list(feats.columns) == ["time", *feature_columns(context)]
    assert "d1_ema_dist_21" in feats.columns and len(feats) == len(frames["15m"])
    daily = frames["1d"].set_index("time")
    day_open = int(daily.index[30])
    # The 15m bar closing at 23:45 still sees the previous day; the one closing at midnight sees this day.
    before = feats.set_index("time").loc[day_open + 86400 - 1800, "d1_ret_1"]
    at_close = feats.set_index("time").loc[day_open + 86400 - 900, "d1_ret_1"]
    prev = np.log(daily["close"].loc[day_open - 86400] / daily["close"].loc[day_open - 2 * 86400])
    this = np.log(daily["close"].loc[day_open] / daily["close"].loc[day_open - 86400])
    assert before == np.float32(prev) and at_close == np.float32(this)
