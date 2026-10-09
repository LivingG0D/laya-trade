"""Phase 4: Donchian 55/20 trend following on daily and 4h bars, after costs and funding.

Long above the previous 55-bar high, short below the 55-bar low; exit on the
opposite 20-bar channel; initial stop 2 x ATR(20) fixed for the trade (= 1R).
Signals use closed bars; entries and channel exits fill at the next open, stops
intrabar (at the open on a gap). Each 8h funding event charges side * rate * price.
Parameters are fixed in docs/superpowers/specs/2026-09-27-phase4-trend-design.md.

Run:  python experiments/eval_trend.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experiments.harness import (  # noqa: E402
    HISTORY_DIR,
    PROBE_SEED,
    REPORT_DIR,
    SYMBOLS,
    load_candles,
    matched_rate,
    max_drawdown_r,
    pass_checks,
    positive_windows,
    random_sides,
    report_header,
    report_line,
    round_trip_cost_r,
    summarize_trades,
    walk_forward_schedule,
)
from experiments.history import load_funding  # noqa: E402
from src.market_snapshot import INTERVAL_SECONDS  # noqa: E402
from src.strategy_evaluator import CostModel  # noqa: E402
from src.trade_management import trigger_exit  # noqa: E402

ENTRY, EXIT, ATR_BARS, STOP_ATR = 55, 20, 20, 2.0
TAKER = CostModel()  # taker fee and slippage per side


def donchian_signals(high: np.ndarray, low: np.ndarray, close: np.ndarray, entry: int = ENTRY,
                     exit: int = EXIT) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Entry side per bar and the long/short channel exits, each from the previous bars only."""
    h, l = pd.Series(high).shift(1), pd.Series(low).shift(1)
    entry_side = (close > h.rolling(entry).max().to_numpy()).astype(int) \
        - (close < l.rolling(entry).min().to_numpy()).astype(int)
    exit_long = close < l.rolling(exit).min().to_numpy()
    exit_short = close > h.rolling(exit).max().to_numpy()
    return entry_side, exit_long, exit_short


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = ATR_BARS) -> np.ndarray:
    prev = np.r_[np.nan, close[:-1]]
    tr = np.nanmax([high - low, np.abs(high - prev), np.abs(low - prev)], axis=0)
    return pd.Series(tr).rolling(n).mean().to_numpy()


def simulate(bars: Dict[str, np.ndarray], entry_side: np.ndarray, exit_long: np.ndarray, exit_short: np.ndarray,
             atr_: np.ndarray, costs: CostModel = TAKER, stop_atr: float = STOP_ATR,
             funding: Optional[Tuple[np.ndarray, np.ndarray]] = None) -> List[Dict[str, Any]]:
    """Trades in R. Bar i acts on bar i-1's signals: channel exit at the open, then entry at the
    open if flat, then funding events inside the bar, then the intrabar stop (the shared
    trade-management fill rule). The evaluator has no exit-only signals or funding events,
    hence this simulator for the channel rule."""
    fee, slip = costs.fee_bps / 1e4, costs.slippage_bps / 1e4
    entry_fee = fee if costs.entry_fee_bps is None else costs.entry_fee_bps / 1e4
    entry_slip = slip if costs.entry_slippage_bps is None else costs.entry_slippage_bps / 1e4
    t, o, h, l, c = (bars[k] for k in ("time", "open", "high", "low", "close"))
    step = int(t[1] - t[0]) if len(t) > 1 else 0
    f_time, f_rate = funding if funding is not None else (np.array([]), np.array([]))
    trades: List[Dict[str, Any]] = []
    pos: Optional[Dict[str, Any]] = None

    def close_pos(pos: Dict[str, Any], i: int, price: float, reason: str) -> None:
        side, entry, risk = pos["side"], pos["entry"], pos["risk"]
        exit_price = price * (1 - side * slip)
        cost = entry_fee * entry + fee * exit_price + pos["funding"]
        trades.append({
            "side": "LONG" if side == 1 else "SHORT", "entry_time": pos["time"], "exit_time": int(t[i]),
            "entry_price": entry, "exit_price": exit_price, "exit_reason": reason, "bars_held": i - pos["index"],
            "r_multiple": (side * (exit_price - entry) - cost) / risk, "cost_r": cost / risk,
            "initial_risk_pct": risk / entry,
        })

    for i in range(1, len(t)):
        if pos and (exit_long[i - 1] if pos["side"] == 1 else exit_short[i - 1]):
            close_pos(pos, i, o[i], "CHANNEL")
            pos = None
        side = int(entry_side[i - 1])
        if pos is None and side and np.isfinite(atr_[i - 1]) and atr_[i - 1] > 0:
            entry = o[i] * (1 + side * entry_slip)
            pos = {"side": side, "entry": entry, "stop": entry - side * stop_atr * atr_[i - 1],
                   "risk": stop_atr * atr_[i - 1], "time": int(t[i]), "index": i, "funding": 0.0}
        if pos is None:
            continue
        lo, hi = np.searchsorted(f_time, [t[i], t[i] + step])
        # ponytail: funding priced at the bar close, not the exact event time
        pos["funding"] += pos["side"] * float(f_rate[lo:hi].sum()) * c[i]
        stop = trigger_exit(pos["side"], pos["stop"], 0.0, 0.0, False, o[i], h[i], l[i])
        if stop is not None:
            close_pos(pos, i, stop[1], "STOP")
            pos = None
    if pos:
        close_pos(pos, len(t) - 1, c[-1], "END")
    return trades


def in_period(trades: Sequence[Dict[str, Any]], period: Tuple[int, int]) -> List[Dict[str, Any]]:
    return [x for x in trades if period[0] <= x["entry_time"] < period[1]]


def period_block(model: List[dict], rand: List[dict], seeds: int, period: Tuple[int, int]) -> Dict[str, Any]:
    m, r = in_period(model, period), in_period(rand, period)
    out = {"model": summarize_trades(m), "random": summarize_trades(r)}
    out["random"]["trade_count"] /= seeds
    out["model"]["round_trip_cost_r"] = round_trip_cost_r(m, TAKER)
    return out


def run_strategy(data: Dict[str, Dict[str, Any]], long_only: bool, seeds: int) -> Tuple[List[dict], List[dict]]:
    """Strategy trades and random-entry trades (same exits, matched count) over all symbols."""
    model, rand = [], []
    for sym_idx, (symbol, d) in enumerate(data.items()):
        bars, entry_side, exit_long, exit_short, atr_ = d["bars"], d["entry"], d["exit_long"], d["exit_short"], d["atr"]
        if long_only:
            entry_side = np.maximum(entry_side, 0)
        trades = simulate(bars, entry_side, exit_long, exit_short, atr_, funding=d["funding"])
        for x in trades:
            x["symbol"] = symbol
        model += trades
        n = len(bars["time"])
        eligible = np.isfinite(atr_) & (np.arange(n) >= ENTRY)

        def random_trades(seed, rate):
            sides = random_sides(n, rate, (seed, sym_idx), eligible)
            return simulate(bars, np.abs(sides) if long_only else sides, exit_long, exit_short, atr_,
                            funding=d["funding"])

        rate = matched_rate(len(trades), max(len(trades), 1) / n,
                            lambda r: len(random_trades(PROBE_SEED, r)), iterations=3)
        for seed in range(seeds):
            rand += random_trades(seed, rate)
    return model, rand


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=float, default=2600)
    parser.add_argument("--symbols", default=SYMBOLS)
    parser.add_argument("--window-days", type=float, default=180)
    parser.add_argument("--holdout-days", type=float, default=365)
    parser.add_argument("--seeds", type=int, default=20)
    args = parser.parse_args()

    import httpx

    now = time.time()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    async def load_all():
        async with httpx.AsyncClient(timeout=30.0) as client:
            out = {}
            for s in symbols:
                funding = await load_funding(client, s, int((now - args.days * 86400) * 1000), HISTORY_DIR)
                out[s] = {"funding": funding, **(await load_candles(client, [s], ("1d", "4h"), args.days, now))[s]}
            return out

    raw = asyncio.run(load_all())
    report: Dict[str, Any] = {"args": vars(args), "strategies": {}, "buy_and_hold": {}}
    variants = [("1d", False), ("4h", False), ("1d", True)]
    for tf, long_only in variants:
        data = {}
        for symbol in symbols:
            frame = pd.DataFrame(raw[symbol][tf])
            if len(frame) <= ENTRY + 1:
                continue
            bars = {k: frame[k].to_numpy(np.int64 if k == "time" else float)
                    for k in ("time", "open", "high", "low", "close")}
            entry_side, exit_long, exit_short = donchian_signals(bars["high"], bars["low"], bars["close"])
            data[symbol] = {"bars": bars, "entry": entry_side, "exit_long": exit_long, "exit_short": exit_short,
                            "atr": atr(bars["high"], bars["low"], bars["close"]), "funding": raw[symbol]["funding"]}
        step = INTERVAL_SECONDS[tf]
        start = min(int(d["bars"]["time"][ENTRY]) for d in data.values())
        end = max(int(d["bars"]["time"][-1]) for d in data.values()) + step
        windows, holdout = walk_forward_schedule(start, end, 0, args.window_days, args.holdout_days)
        model, rand = run_strategy(data, long_only, args.seeds)
        walk_forward = (windows[0][0], windows[-1][1])
        block = {
            "windows": [period_block(model, rand, args.seeds, w) for w in windows],
            "walk_forward": period_block(model, rand, args.seeds, walk_forward),
            "holdout": period_block(model, rand, args.seeds, holdout),
        }
        block["pass_checks"] = pass_checks(block["windows"], block["walk_forward"], block["holdout"],
                                           block["walk_forward"]["model"]["round_trip_cost_r"])
        block["passed"] = None if long_only else all(block["pass_checks"].values())
        years = pd.Series([x["r_multiple"] for x in model],
                          index=pd.to_datetime([x["entry_time"] for x in model], unit="s").year)
        block["r_by_year"] = years.groupby(level=0).sum().round(2).to_dict()
        block["max_drawdown_r"] = max_drawdown_r(model)
        block["random_max_drawdown_r"] = max_drawdown_r(rand) / args.seeds ** 0.5  # rough scale, info only
        block["avg_bars_held"] = float(np.mean([x["bars_held"] for x in model])) if model else 0.0
        block["exit_reasons"] = pd.Series([x["exit_reason"] for x in model]).value_counts().to_dict()
        key = f"donchian_{tf}{'_long_only' if long_only else ''}"
        report["strategies"][key] = block
        print(f"  evaluated {key}: {len(model)} trades", file=sys.stderr)

    for symbol in symbols:
        closes = pd.DataFrame(raw[symbol]["1d"])
        hold = closes[closes["time"] >= int(now - args.holdout_days * 86400)]
        report["buy_and_hold"][symbol] = {
            "full_pct": round((closes["close"].iloc[-1] / closes["open"].iloc[0] - 1) * 100, 1),
            "holdout_pct": round((hold["close"].iloc[-1] / hold["open"].iloc[0] - 1) * 100, 1) if len(hold) else None,
            "since": time.strftime("%Y-%m-%d", time.gmtime(int(closes["time"].iloc[0]))),
        }

    out = REPORT_DIR / f"trend_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))

    print("\n" + report_header(26))
    for key, block in report["strategies"].items():
        pos = positive_windows(block["windows"])
        verdict = "info" if block["passed"] is None else "PASS" if block["passed"] else "FAIL"
        print(f"{key}  (cost {block['walk_forward']['model']['round_trip_cost_r']:.3f}R, "
              f"windows +{pos}/{len(block['windows'])}, maxDD {block['max_drawdown_r']:.1f}R, "
              f"hold {block['avg_bars_held']:.0f} bars) -> {verdict}  {block['pass_checks']}")
        for period in ("walk_forward", "holdout"):
            print(report_line(f"  {period} model", block[period]["model"], 26))
            print(report_line(f"  {period} random", block[period]["random"], 26))
        print(f"  R by year: {block['r_by_year']}  exits: {block['exit_reasons']}")
    print("buy & hold:", {s: (v["full_pct"], v["holdout_pct"]) for s, v in report["buy_and_hold"].items()})
    print(f"Saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
